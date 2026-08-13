package transport

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"corpus-platform/corpus-auditor/internal/service"
)

func TestHTTPServerRequiresBearerTokenAndRejectsUnknownFields(t *testing.T) {
	t.Parallel()
	auditor, err := service.New(service.Config{DataRoot: t.TempDir(), StateDirectory: filepath.Join(t.TempDir(), "jobs"), ResultPublisher: discardPublisher{}, WorkerCount: 1})
	if err != nil {
		t.Fatal(err)
	}
	defer auditor.Close()
	server := httptest.NewServer(NewHTTPServer(auditor, "control").Handler())
	defer server.Close()
	request, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/audits", bytes.NewBufferString(`{}`))
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusUnauthorized {
		t.Fatalf("status = %d", response.StatusCode)
	}
	request, _ = http.NewRequest(http.MethodPost, server.URL+"/v1/audits", bytes.NewBufferString(`{"unknown":true}`))
	request.Header.Set("Authorization", "Bearer control")
	response, err = http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d", response.StatusCode)
	}
	var body map[string]any
	if err := json.NewDecoder(response.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	if body["error"] != "invalid_json" {
		t.Fatalf("body = %#v", body)
	}
}
