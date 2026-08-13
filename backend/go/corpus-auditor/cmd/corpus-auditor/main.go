package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"

	"corpus-platform/corpus-auditor/internal/audit"
)

func main() {
	if err := run(os.Args[1:], os.Stdout, os.Stderr); err != nil {
		fmt.Fprintln(os.Stderr, "corpus-auditor:", err)
		os.Exit(1)
	}
}

func run(args []string, stdout, stderr io.Writer) error {
	flags := flag.NewFlagSet("corpus-auditor", flag.ContinueOnError)
	flags.SetOutput(stderr)

	input := flags.String("input", "", "path to parallel_pairs.jsonl")
	report := flags.String("report", "", "path for quality_report.json")
	anomalies := flags.String("anomalies", "", "path for anomalies.jsonl")
	lowConfidence := flags.Float64("low-confidence", 0.60, "flag confidences below this value")
	minLengthRatio := flags.Float64("min-length-ratio", 0.12, "minimum English-token / Chinese-character ratio")
	maxLengthRatio := flags.Float64("max-length-ratio", 1.80, "maximum English-token / Chinese-character ratio")
	maxAnomalies := flags.Int("max-anomalies", 1000, "maximum anomaly rows to write")

	if err := flags.Parse(args); err != nil {
		return err
	}
	if *input == "" || *report == "" || *anomalies == "" {
		return errors.New("--input, --report, and --anomalies are required")
	}

	reportData, err := audit.Run(audit.Options{
		InputPath:         *input,
		ReportPath:        *report,
		AnomaliesPath:     *anomalies,
		LowConfidence:     *lowConfidence,
		MinLengthRatio:    *minLengthRatio,
		MaxLengthRatio:    *maxLengthRatio,
		MaxAnomalyRecords: *maxAnomalies,
	})
	if err != nil {
		return err
	}
	_, err = fmt.Fprintf(stdout, "audited_pairs=%d flagged_pairs=%d\n", reportData.Summary.TotalPairs, reportData.Summary.FlaggedPairs)
	return err
}
