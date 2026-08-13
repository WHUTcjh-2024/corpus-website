package service

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func TestServiceExecutesIdempotentJobAndDeliversSignedCallback(t *testing.T) {
	t.Parallel()
	var mutex sync.Mutex
	callbacks := make([]callbackPayload, 0, 1)
	callback := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		payload := callbackPayload{Signature: request.Header.Get("X-Corpus-Auditor-Signature"), Timestamp: request.Header.Get("X-Corpus-Auditor-Timestamp")}
		if err := json.NewDecoder(request.Body).Decode(&payload.Body); err != nil {
			t.Errorf("decode callback: %v", err)
		}
		mutex.Lock()
		callbacks = append(callbacks, payload)
		mutex.Unlock()
		writer.WriteHeader(http.StatusNoContent)
	}))
	defer callback.Close()

	dataRoot := t.TempDir()
	writePairs(t, dataRoot, "processed/corpus-one/parallel_pairs.jsonl")
	service, err := New(Config{
		DataRoot: dataRoot, StateDirectory: filepath.Join(t.TempDir(), "jobs"),
		CallbackBaseURL: callback.URL, CallbackToken: "callback-secret", WorkerCount: 1,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	request := testRequest("job-one")
	job, created, err := service.Submit(request)
	if err != nil || !created || job.State != StateQueued {
		t.Fatalf("submit = %#v, %t, %v", job, created, err)
	}
	duplicate, created, err := service.Submit(request)
	if err != nil || created || duplicate.ID != job.ID {
		t.Fatalf("idempotent submit = %#v, %t, %v", duplicate, created, err)
	}

	completed := waitFor(t, service, job.ID, StateSucceeded)
	if completed.Report == nil || completed.Report.Summary.TotalPairs != 2 {
		t.Fatalf("unexpected completed job: %#v", completed)
	}
	if _, err := os.Stat(filepath.Join(dataRoot, "processed/corpus-one/audits/job-one/quality_report.json")); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dataRoot, "processed/corpus-one/audits/job-one/anomalies.jsonl")); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		mutex.Lock()
		count := len(callbacks)
		mutex.Unlock()
		if count == 1 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	mutex.Lock()
	defer mutex.Unlock()
	if len(callbacks) != 1 || callbacks[0].Body["state"] != StateSucceeded {
		t.Fatalf("callbacks = %#v", callbacks)
	}
	if callbacks[0].Signature == "" || callbacks[0].Timestamp == "" {
		t.Fatalf("callback signature missing: %#v", callbacks[0])
	}
}

func TestServiceRejectsPathTraversalAndBatchAtomically(t *testing.T) {
	t.Parallel()
	service, err := New(Config{DataRoot: t.TempDir(), StateDirectory: filepath.Join(t.TempDir(), "jobs"), CallbackBaseURL: "http://localhost", CallbackToken: "token", WorkerCount: 1})
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	bad := testRequest("bad")
	bad.InputRef = "../secrets.jsonl"
	if _, _, err := service.Submit(bad); err == nil {
		t.Fatal("path traversal accepted")
	}
	first := testRequest("first")
	second := testRequest("second")
	second.OutputPrefix = "processed/other-corpus/audits/second"
	if _, err := service.SubmitBatch([]SubmitRequest{first, second}); err == nil {
		t.Fatal("invalid batch accepted")
	}
	if _, err := service.Get("first"); err != ErrJobNotFound {
		t.Fatalf("partial batch persisted: %v", err)
	}
}

func TestCancelledQueuedJobDoesNotWriteOutputs(t *testing.T) {
	t.Parallel()
	dataRoot := t.TempDir()
	writePairs(t, dataRoot, "processed/corpus-one/parallel_pairs.jsonl")
	service, err := New(Config{DataRoot: dataRoot, StateDirectory: filepath.Join(t.TempDir(), "jobs"), CallbackBaseURL: "http://127.0.0.1:1", CallbackToken: "token", WorkerCount: 1, QueueCapacity: 2})
	if err != nil {
		t.Fatal(err)
	}
	defer service.Close()
	job, _, err := service.Submit(testRequest("cancel-me"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := service.Cancel(job.ID); err != nil {
		t.Fatal(err)
	}
	completed := waitFor(t, service, job.ID, StateCancelled)
	if completed.State != StateCancelled {
		t.Fatalf("job state = %s", completed.State)
	}
}

type callbackPayload struct {
	Signature string
	Timestamp string
	Body      map[string]any
}

func testRequest(id string) SubmitRequest {
	return SubmitRequest{
		JobID: id, InputRef: "processed/corpus-one/parallel_pairs.jsonl",
		OutputPrefix: "processed/corpus-one/audits/" + id,
		CallbackPath: "/api/internal/audits/" + id + "/callback/",
	}
}

func writePairs(t *testing.T, root, reference string) {
	t.Helper()
	path := filepath.Join(root, filepath.FromSlash(reference))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	pairs := []byte("{\"id\":\"one\",\"ordinal\":1,\"zh_text\":\"中文句子\",\"en_text\":\"English sentence\",\"confidence\":0.9}\n{\"id\":\"two\",\"ordinal\":2,\"zh_text\":\"\",\"en_text\":\"missing\",\"confidence\":0.2}\n")
	if err := os.WriteFile(path, bytes.TrimSpace(pairs), 0o600); err != nil {
		t.Fatal(err)
	}
}

func waitFor(t *testing.T, service *Service, id, expected string) Job {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		job, err := service.Get(id)
		if err != nil {
			t.Fatal(err)
		}
		if job.State == expected {
			return job
		}
		time.Sleep(10 * time.Millisecond)
	}
	job, _ := service.Get(id)
	t.Fatalf("job %s did not reach %s; current=%s", id, expected, job.State)
	return Job{}
}
