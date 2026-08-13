package service

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func TestServiceExecutesIdempotentJobAndPublishesTerminalResult(t *testing.T) {
	t.Parallel()
	publisher := &memoryPublisher{}
	dataRoot := t.TempDir()
	writePairs(t, dataRoot, "processed/corpus-one/parallel_pairs.jsonl")
	auditor, err := New(Config{
		DataRoot: dataRoot, StateDirectory: filepath.Join(t.TempDir(), "jobs"),
		ResultPublisher: publisher, WorkerCount: 1,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer auditor.Close()
	job, created, err := auditor.Submit(testRequest("job-one"))
	if err != nil || !created || job.State != StateQueued {
		t.Fatalf("submit = %#v, %t, %v", job, created, err)
	}
	duplicate, created, err := auditor.Submit(testRequest("job-one"))
	if err != nil || created || duplicate.ID != job.ID {
		t.Fatalf("idempotent submit = %#v, %t, %v", duplicate, created, err)
	}

	completed := waitFor(t, auditor, job.ID, StateSucceeded)
	if completed.Report == nil || completed.Report.Summary.TotalPairs != 2 {
		t.Fatalf("unexpected completed job: %#v", completed)
	}
	if _, err := os.Stat(filepath.Join(dataRoot, "processed/corpus-one/audits/job-one/quality_report.json")); err != nil {
		t.Fatal(err)
	}
	payload := publisher.waitForPayload(t)
	var result map[string]any
	if err := json.Unmarshal(payload, &result); err != nil {
		t.Fatal(err)
	}
	if result["id"] != "job-one" || result["state"] != StateSucceeded || result["schema_version"] != float64(1) {
		t.Fatalf("unexpected result payload: %#v", result)
	}
}

func TestServiceRejectsPathTraversalAndBatchAtomically(t *testing.T) {
	t.Parallel()
	auditor, err := New(Config{DataRoot: t.TempDir(), StateDirectory: filepath.Join(t.TempDir(), "jobs"), ResultPublisher: &memoryPublisher{}, WorkerCount: 1})
	if err != nil {
		t.Fatal(err)
	}
	defer auditor.Close()
	bad := testRequest("bad")
	bad.InputRef = "../secrets.jsonl"
	if _, _, err := auditor.Submit(bad); err == nil {
		t.Fatal("path traversal accepted")
	}
	first := testRequest("first")
	second := testRequest("second")
	second.OutputPrefix = "processed/other-corpus/audits/second"
	if _, err := auditor.SubmitBatch([]SubmitRequest{first, second}); err == nil {
		t.Fatal("invalid batch accepted")
	}
	if _, err := auditor.Get("first"); err != ErrJobNotFound {
		t.Fatalf("partial batch persisted: %v", err)
	}
}

func TestCancelledQueuedJobDoesNotWriteOutputs(t *testing.T) {
	t.Parallel()
	dataRoot := t.TempDir()
	writePairs(t, dataRoot, "processed/corpus-one/parallel_pairs.jsonl")
	auditor, err := New(Config{DataRoot: dataRoot, StateDirectory: filepath.Join(t.TempDir(), "jobs"), ResultPublisher: &memoryPublisher{}, WorkerCount: 1, QueueCapacity: 2})
	if err != nil {
		t.Fatal(err)
	}
	defer auditor.Close()
	job, _, err := auditor.Submit(testRequest("cancel-me"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := auditor.Cancel(job.ID); err != nil {
		t.Fatal(err)
	}
	if completed := waitFor(t, auditor, job.ID, StateCancelled); completed.State != StateCancelled {
		t.Fatalf("job state = %s", completed.State)
	}
}

type memoryPublisher struct {
	mutex    sync.Mutex
	payloads [][]byte
}

func (publisher *memoryPublisher) Publish(payload []byte) error {
	publisher.mutex.Lock()
	defer publisher.mutex.Unlock()
	publisher.payloads = append(publisher.payloads, append([]byte(nil), payload...))
	return nil
}

func (publisher *memoryPublisher) waitForPayload(t *testing.T) []byte {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		publisher.mutex.Lock()
		if len(publisher.payloads) > 0 {
			payload := append([]byte(nil), publisher.payloads[0]...)
			publisher.mutex.Unlock()
			return payload
		}
		publisher.mutex.Unlock()
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("no terminal result was published")
	return nil
}

func testRequest(id string) SubmitRequest {
	return SubmitRequest{
		JobID: id, InputRef: "processed/corpus-one/parallel_pairs.jsonl",
		OutputPrefix: "processed/corpus-one/audits/" + id,
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

func waitFor(t *testing.T, auditor *Service, id, expected string) Job {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		job, err := auditor.Get(id)
		if err != nil {
			t.Fatal(err)
		}
		if job.State == expected {
			return job
		}
		time.Sleep(10 * time.Millisecond)
	}
	job, _ := auditor.Get(id)
	t.Fatalf("job %s did not reach %s; current=%s", id, expected, job.State)
	return Job{}
}
