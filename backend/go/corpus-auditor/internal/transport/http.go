package transport

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"

	"corpus-platform/corpus-auditor/internal/service"
)

const maxRequestBytes = 1 << 20

// HTTPServer is the public control plane for the auditor. It is intentionally
// small: data itself stays on the shared, mounted data volume and only bounded
// references cross the network boundary.
type HTTPServer struct {
	service *service.Service
	token   string
	ready   func() bool
}

func NewHTTPServer(auditor *service.Service, token string) *HTTPServer {
	return NewHTTPServerWithReadiness(auditor, token, auditor.Healthy)
}

// NewHTTPServerWithReadiness keeps liveness independent from external
// dependencies while allowing the executable to reject traffic when its
// Redis Streams transport cannot accept commands.
func NewHTTPServerWithReadiness(auditor *service.Service, token string, ready func() bool) *HTTPServer {
	if ready == nil {
		ready = auditor.Healthy
	}
	return &HTTPServer{service: auditor, token: token, ready: ready}
}

func (server *HTTPServer) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", server.health)
	mux.HandleFunc("GET /readyz", server.health)
	mux.HandleFunc("POST /v1/audits", server.submit)
	mux.HandleFunc("POST /v1/audits/batch", server.submitBatch)
	mux.HandleFunc("GET /v1/audits/{jobID}", server.get)
	mux.HandleFunc("POST /v1/audits/{jobID}/cancel", server.cancel)
	return server.recover(server.authenticate(mux))
}

func (server *HTTPServer) health(writer http.ResponseWriter, request *http.Request) {
	if request.URL.Path == "/readyz" && !server.ready() {
		writeJSON(writer, http.StatusServiceUnavailable, map[string]any{"status": "not_ready", "service": "corpus-auditor"})
		return
	}
	if !server.service.Healthy() {
		writeJSON(writer, http.StatusServiceUnavailable, map[string]any{"status": "stopping", "service": "corpus-auditor"})
		return
	}
	writeJSON(writer, http.StatusOK, map[string]any{"status": "ok", "service": "corpus-auditor"})
}

func (server *HTTPServer) submit(writer http.ResponseWriter, request *http.Request) {
	var payload service.SubmitRequest
	if !decodeJSON(writer, request, &payload) {
		return
	}
	job, created, err := server.service.Submit(payload)
	if err != nil {
		writeError(writer, err)
		return
	}
	status := http.StatusAccepted
	if !created {
		status = http.StatusOK
	}
	writeJSON(writer, status, job.Public())
}

func (server *HTTPServer) submitBatch(writer http.ResponseWriter, request *http.Request) {
	var payload struct {
		Jobs []service.SubmitRequest `json:"jobs"`
	}
	if !decodeJSON(writer, request, &payload) {
		return
	}
	jobs, err := server.service.SubmitBatch(payload.Jobs)
	if err != nil {
		writeError(writer, err)
		return
	}
	items := make([]map[string]any, 0, len(jobs))
	for _, job := range jobs {
		items = append(items, job.Public())
	}
	writeJSON(writer, http.StatusAccepted, map[string]any{"jobs": items})
}

func (server *HTTPServer) get(writer http.ResponseWriter, request *http.Request) {
	job, err := server.service.Get(request.PathValue("jobID"))
	if err != nil {
		writeError(writer, err)
		return
	}
	writeJSON(writer, http.StatusOK, job.Public())
}

func (server *HTTPServer) cancel(writer http.ResponseWriter, request *http.Request) {
	job, err := server.service.Cancel(request.PathValue("jobID"))
	if err != nil {
		writeError(writer, err)
		return
	}
	writeJSON(writer, http.StatusOK, job.Public())
}

func (server *HTTPServer) authenticate(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path == "/healthz" || request.URL.Path == "/readyz" {
			next.ServeHTTP(writer, request)
			return
		}
		provided := strings.TrimPrefix(request.Header.Get("Authorization"), "Bearer ")
		if provided == "" || subtle.ConstantTimeCompare([]byte(provided), []byte(server.token)) != 1 {
			writeJSON(writer, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
			return
		}
		next.ServeHTTP(writer, request)
	})
}

func (server *HTTPServer) recover(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		defer func() {
			if recover() != nil {
				writeJSON(writer, http.StatusInternalServerError, map[string]string{"error": "internal_error"})
			}
		}()
		next.ServeHTTP(writer, request)
	})
}

func decodeJSON(writer http.ResponseWriter, request *http.Request, destination any) bool {
	defer request.Body.Close()
	decoder := json.NewDecoder(io.LimitReader(request.Body, maxRequestBytes+1))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		writeJSON(writer, http.StatusBadRequest, map[string]string{"error": "invalid_json", "detail": err.Error()})
		return false
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		writeJSON(writer, http.StatusBadRequest, map[string]string{"error": "invalid_json", "detail": "request must contain one JSON object"})
		return false
	}
	return true
}

func writeError(writer http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, service.ErrJobNotFound):
		writeJSON(writer, http.StatusNotFound, map[string]string{"error": "not_found"})
	case errors.Is(err, service.ErrJobConflict):
		writeJSON(writer, http.StatusConflict, map[string]string{"error": "idempotency_conflict"})
	case errors.Is(err, service.ErrQueueFull):
		writeJSON(writer, http.StatusServiceUnavailable, map[string]string{"error": "queue_full"})
	case errors.Is(err, service.ErrServiceStopping):
		writeJSON(writer, http.StatusServiceUnavailable, map[string]string{"error": "service_stopping"})
	case errors.Is(err, service.ErrInvalidRequest):
		writeJSON(writer, http.StatusBadRequest, map[string]string{"error": "invalid_request", "detail": err.Error()})
	default:
		writeJSON(writer, http.StatusInternalServerError, map[string]string{"error": "internal_error"})
	}
}

func writeJSON(writer http.ResponseWriter, status int, payload any) {
	writer.Header().Set("Content-Type", "application/json; charset=utf-8")
	writer.Header().Set("Cache-Control", "no-store")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(payload)
}
