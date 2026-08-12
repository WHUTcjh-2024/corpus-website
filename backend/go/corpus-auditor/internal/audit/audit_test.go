package audit

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRunWritesStableReportAndBoundedAnomalies(t *testing.T) {
	dir := t.TempDir()
	input := filepath.Join(dir, "parallel_pairs.jsonl")
	lines := []string{
		`{"id":"p-1","ordinal":1,"zh_text":"你好世界","en_text":"Hello world","alignment_unit":"sentence","method":"provided","confidence":0.95}`,
		`{"id":"p-2","ordinal":2,"zh_text":"你好世界","en_text":"Hello world","alignment_unit":"sentence","method":"provided","confidence":0.40}`,
		`{"id":"p-3","ordinal":3,"zh_text":"你好世界","en_text":"Greetings, world","alignment_unit":"sentence","method":"provided","confidence":1.2}`,
		`{"id":"p-4","ordinal":4,"zh_text":"","en_text":"Missing source","alignment_unit":"sentence","method":"provided","confidence":0.9}`,
	}
	if err := os.WriteFile(input, []byte(strings.Join(lines, "\n")+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	report, err := Run(Options{
		InputPath: input, ReportPath: filepath.Join(dir, "quality_report.json"), AnomaliesPath: filepath.Join(dir, "anomalies.jsonl"),
		LowConfidence: 0.6, MinLengthRatio: 0.12, MaxLengthRatio: 1.8, MaxAnomalyRecords: 2,
	})
	if err != nil {
		t.Fatal(err)
	}
	if report.SchemaVersion != ReportSchemaVersion || report.Summary.TotalPairs != 4 {
		t.Fatalf("unexpected report: %#v", report)
	}
	if report.Summary.DuplicatePairs != 1 || report.Summary.SourceTranslationVariants != 1 || report.Summary.EmptySidePairs != 1 {
		t.Fatalf("unexpected summary: %#v", report.Summary)
	}
	if report.Summary.WrittenAnomalyRows != 2 || report.Summary.SuppressedAnomalyRows != 1 {
		t.Fatalf("anomaly cap was not applied: %#v", report.Summary)
	}

	payload, err := os.ReadFile(filepath.Join(dir, "quality_report.json"))
	if err != nil {
		t.Fatal(err)
	}
	var decoded Report
	if err := json.Unmarshal(payload, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded.Input.SHA256 == "" || decoded.AuditorVersion != AuditorVersion {
		t.Fatalf("report metadata was not persisted: %#v", decoded)
	}
}

func TestRunRejectsMalformedInputWithoutPublishingOutput(t *testing.T) {
	dir := t.TempDir()
	input := filepath.Join(dir, "parallel_pairs.jsonl")
	if err := os.WriteFile(input, []byte("not-json\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	reportPath := filepath.Join(dir, "quality_report.json")
	_, err := Run(Options{InputPath: input, ReportPath: reportPath, AnomaliesPath: filepath.Join(dir, "anomalies.jsonl"), LowConfidence: 0.6, MinLengthRatio: 0.12, MaxLengthRatio: 1.8, MaxAnomalyRecords: 10})
	if err == nil {
		t.Fatal("expected malformed input to fail")
	}
	if _, statErr := os.Stat(reportPath); !os.IsNotExist(statErr) {
		t.Fatalf("report should not be published, stat error: %v", statErr)
	}
}
