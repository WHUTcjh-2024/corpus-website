package service

import (
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"path"
	"strings"
	"time"

	"corpus-platform/corpus-auditor/internal/audit"
)

const (
	StateQueued    = "queued"
	StateRunning   = "running"
	StateSucceeded = "succeeded"
	StateFailed    = "failed"
	StateCancelled = "cancelled"
)

var (
	ErrJobNotFound       = errors.New("audit job not found")
	ErrJobConflict       = errors.New("audit job id already exists with different input")
	ErrQueueFull         = errors.New("audit queue is full")
	ErrInvalidRequest    = errors.New("invalid audit request")
	ErrUnsupportedAction = errors.New("unsupported action for audit job state")
	ErrServiceStopping   = errors.New("audit service is stopping")
)

// AuditOptions matches the stable, file-based auditor thresholds. Defaults are
// applied by the service so callers cannot rely on zero-value ambiguity.
type AuditOptions struct {
	LowConfidence     float64 `json:"low_confidence,omitempty"`
	MinLengthRatio    float64 `json:"min_length_ratio,omitempty"`
	MaxLengthRatio    float64 `json:"max_length_ratio,omitempty"`
	MaxAnomalyRecords int     `json:"max_anomalies,omitempty"`
}

func (options AuditOptions) withDefaults() AuditOptions {
	if options.LowConfidence == 0 {
		options.LowConfidence = 0.60
	}
	if options.MinLengthRatio == 0 {
		options.MinLengthRatio = 0.12
	}
	if options.MaxLengthRatio == 0 {
		options.MaxLengthRatio = 1.80
	}
	if options.MaxAnomalyRecords == 0 {
		options.MaxAnomalyRecords = 1000
	}
	return options
}

func (options AuditOptions) validate() error {
	options = options.withDefaults()
	if options.LowConfidence < 0 || options.LowConfidence > 1 {
		return fmt.Errorf("%w: low_confidence must be between 0 and 1", ErrInvalidRequest)
	}
	if options.MinLengthRatio <= 0 || options.MaxLengthRatio <= options.MinLengthRatio {
		return fmt.Errorf("%w: length ratio thresholds must be positive and ordered", ErrInvalidRequest)
	}
	if options.MaxAnomalyRecords < 1 || options.MaxAnomalyRecords > 100_000 {
		return fmt.Errorf("%w: max_anomalies must be between 1 and 100000", ErrInvalidRequest)
	}
	return nil
}

// SubmitRequest has only data-root-relative references. The Go service never
// accepts host paths or arbitrary callback URLs from the control plane.
type SubmitRequest struct {
	JobID        string       `json:"job_id"`
	InputRef     string       `json:"input_ref"`
	OutputPrefix string       `json:"output_prefix"`
	CallbackPath string       `json:"callback_path"`
	Options      AuditOptions `json:"options"`
}

func (request SubmitRequest) normalized() (SubmitRequest, error) {
	request.JobID = strings.TrimSpace(request.JobID)
	if !validJobID(request.JobID) {
		return SubmitRequest{}, fmt.Errorf("%w: job_id must use 1 to 64 letters, digits, underscores, or hyphens", ErrInvalidRequest)
	}
	input, err := cleanReference(request.InputRef)
	if err != nil {
		return SubmitRequest{}, fmt.Errorf("%w: input_ref: %v", ErrInvalidRequest, err)
	}
	output, err := cleanReference(request.OutputPrefix)
	if err != nil {
		return SubmitRequest{}, fmt.Errorf("%w: output_prefix: %v", ErrInvalidRequest, err)
	}
	callback := strings.TrimSpace(request.CallbackPath)
	if !strings.HasPrefix(callback, "/") || strings.Contains(callback, "?") || strings.Contains(callback, "#") || strings.Contains(callback, "//") {
		return SubmitRequest{}, fmt.Errorf("%w: callback_path must be an absolute path without query or fragment", ErrInvalidRequest)
	}
	request.InputRef = input
	request.OutputPrefix = output
	request.CallbackPath = callback
	if path.Base(request.OutputPrefix) != request.JobID {
		return SubmitRequest{}, fmt.Errorf(
			"%w: output_prefix must end with job_id to prevent cross-job output collisions",
			ErrInvalidRequest,
		)
	}
	inputParts := strings.Split(request.InputRef, "/")
	outputParts := strings.Split(request.OutputPrefix, "/")
	if len(inputParts) != 3 ||
		inputParts[0] != "processed" ||
		inputParts[1] == "" ||
		inputParts[2] != "parallel_pairs.jsonl" ||
		len(outputParts) != 4 ||
		outputParts[0] != "processed" ||
		outputParts[1] != inputParts[1] ||
		outputParts[2] != "audits" ||
		outputParts[3] != request.JobID {
		return SubmitRequest{}, fmt.Errorf(
			"%w: references must target one processed corpus and its audit output directory",
			ErrInvalidRequest,
		)
	}
	expectedCallback := "/api/internal/audits/" + request.JobID + "/callback/"
	if request.CallbackPath != expectedCallback {
		return SubmitRequest{}, fmt.Errorf(
			"%w: callback_path must be %q", ErrInvalidRequest, expectedCallback,
		)
	}
	request.Options = request.Options.withDefaults()
	if err := request.Options.validate(); err != nil {
		return SubmitRequest{}, err
	}
	return request, nil
}

func validJobID(value string) bool {
	if value == "" || len(value) > 64 {
		return false
	}
	for _, character := range value {
		if !(character >= 'a' && character <= 'z' || character >= 'A' && character <= 'Z' || character >= '0' && character <= '9' || character == '_' || character == '-') {
			return false
		}
	}
	return true
}

func cleanReference(reference string) (string, error) {
	value := strings.TrimSpace(strings.ReplaceAll(reference, "\\", "/"))
	if value == "" || strings.HasPrefix(value, "/") {
		return "", errors.New("must be a non-empty relative path")
	}
	cleaned := path.Clean(value)
	if cleaned == "." || cleaned == ".." || strings.HasPrefix(cleaned, "../") {
		return "", errors.New("must stay under the configured data root")
	}
	if len(cleaned) >= 2 && cleaned[1] == ':' {
		return "", errors.New("must not include a volume name")
	}
	return cleaned, nil
}

type CallbackDelivery struct {
	Delivered bool      `json:"delivered"`
	Attempts  int       `json:"attempts"`
	NextAt    time.Time `json:"next_at,omitempty"`
	LastError string    `json:"last_error,omitempty"`
}

// Job is persisted as an atomic JSON document. A single service instance owns
// its state directory; the durable documents make queued work and callbacks
// recoverable across a process restart without coupling the executor to Django.
type Job struct {
	ID           string           `json:"id"`
	Request      SubmitRequest    `json:"request"`
	Fingerprint  string           `json:"fingerprint"`
	State        string           `json:"state"`
	Attempt      int              `json:"attempt"`
	ReportRef    string           `json:"report_ref,omitempty"`
	AnomaliesRef string           `json:"anomalies_ref,omitempty"`
	Report       *audit.Report    `json:"report,omitempty"`
	ErrorCode    string           `json:"error_code,omitempty"`
	ErrorMessage string           `json:"error_message,omitempty"`
	Callback     CallbackDelivery `json:"callback"`
	CreatedAt    time.Time        `json:"created_at"`
	UpdatedAt    time.Time        `json:"updated_at"`
	StartedAt    *time.Time       `json:"started_at,omitempty"`
	FinishedAt   *time.Time       `json:"finished_at,omitempty"`
}

func (job Job) terminal() bool {
	return job.State == StateSucceeded || job.State == StateFailed || job.State == StateCancelled
}

func (job Job) Public() map[string]any {
	value := map[string]any{
		"id":            job.ID,
		"state":         job.State,
		"attempt":       job.Attempt,
		"input_ref":     job.Request.InputRef,
		"output_prefix": job.Request.OutputPrefix,
		"report_ref":    job.ReportRef,
		"anomalies_ref": job.AnomaliesRef,
		"error_code":    job.ErrorCode,
		"error_message": job.ErrorMessage,
		"callback":      job.Callback,
		"created_at":    job.CreatedAt,
		"updated_at":    job.UpdatedAt,
	}
	if job.Report != nil {
		value["summary"] = job.Report.Summary
		value["auditor_version"] = job.Report.AuditorVersion
	}
	return value
}

func canonicalFingerprint(request SubmitRequest) (string, error) {
	payload, err := json.Marshal(request)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(payload)
	return fmt.Sprintf("%x", digest), nil
}
