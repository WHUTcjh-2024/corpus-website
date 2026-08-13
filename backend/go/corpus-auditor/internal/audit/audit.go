// Package audit validates and assesses the pair-level artifacts emitted by the
// corpus processing pipeline. It deliberately has no database or HTTP dependency:
// JSONL is the versioned contract between Django and this executable.
package audit

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode"
)

const (
	ReportSchemaVersion = "1.0"
	AuditorVersion      = "1.0.0"
	maxJSONLLineBytes   = 4 * 1024 * 1024
)

// Options controls a single, reproducible audit invocation.
type Options struct {
	InputPath         string
	ReportPath        string
	AnomaliesPath     string
	LowConfidence     float64
	MinLengthRatio    float64
	MaxLengthRatio    float64
	MaxAnomalyRecords int
}

// Report is stable JSON consumed by the Django application.
type Report struct {
	SchemaVersion  string        `json:"schema_version"`
	AuditorVersion string        `json:"auditor_version"`
	GeneratedAt    time.Time     `json:"generated_at"`
	Input          InputMetadata `json:"input"`
	Thresholds     Thresholds    `json:"thresholds"`
	Summary        Summary       `json:"summary"`
}

type InputMetadata struct {
	Filename string `json:"filename"`
	SHA256   string `json:"sha256"`
}

type Thresholds struct {
	LowConfidence  float64 `json:"low_confidence"`
	MinLengthRatio float64 `json:"min_length_ratio"`
	MaxLengthRatio float64 `json:"max_length_ratio"`
	MaxAnomalyRows int     `json:"max_anomaly_rows"`
}

type Summary struct {
	TotalPairs                int     `json:"total_pairs"`
	FlaggedPairs              int     `json:"flagged_pairs"`
	EmptySidePairs            int     `json:"empty_side_pairs"`
	DuplicatePairs            int     `json:"duplicate_pairs"`
	SourceTranslationVariants int     `json:"source_translation_variants"`
	LowConfidencePairs        int     `json:"low_confidence_pairs"`
	InvalidConfidencePairs    int     `json:"invalid_confidence_pairs"`
	LengthRatioOutliers       int     `json:"length_ratio_outliers"`
	WrittenAnomalyRows        int     `json:"written_anomaly_rows"`
	SuppressedAnomalyRows     int     `json:"suppressed_anomaly_rows"`
	MeanConfidence            float64 `json:"mean_confidence"`
}

type inputPair struct {
	ID            string  `json:"id"`
	Ordinal       int     `json:"ordinal"`
	ZhText        string  `json:"zh_text"`
	EnText        string  `json:"en_text"`
	AlignmentUnit string  `json:"alignment_unit"`
	Method        string  `json:"method"`
	Confidence    float64 `json:"confidence"`
}

type anomaly struct {
	PairID        string   `json:"pair_id"`
	Ordinal       int      `json:"ordinal"`
	ZhText        string   `json:"zh_text"`
	EnText        string   `json:"en_text"`
	AlignmentUnit string   `json:"alignment_unit"`
	Method        string   `json:"method"`
	Confidence    float64  `json:"confidence"`
	LengthRatio   *float64 `json:"length_ratio,omitempty"`
	Reasons       []string `json:"reasons"`
}

type sourceVariantState struct {
	firstTarget string
}

// Run streams the JSONL input and publishes each completed output by atomic
// replacement. The caller never observes a half-written report or anomaly file.
func Run(options Options) (Report, error) {
	return RunContext(context.Background(), options)
}

// RunContext is the cancellable form of Run.  It keeps the file-level contract
// unchanged while allowing a service worker to release resources promptly when
// an operator cancels a queued or running job.
func RunContext(ctx context.Context, options Options) (Report, error) {
	if err := options.validate(); err != nil {
		return Report{}, err
	}

	input, err := os.Open(options.InputPath)
	if err != nil {
		return Report{}, fmt.Errorf("open input: %w", err)
	}
	defer input.Close()

	reportTemp, commitReport, discardReport, err := atomicOutput(options.ReportPath)
	if err != nil {
		return Report{}, err
	}
	defer discardReport()
	anomaliesTemp, commitAnomalies, discardAnomalies, err := atomicOutput(options.AnomaliesPath)
	if err != nil {
		return Report{}, err
	}
	defer discardAnomalies()

	hash := sha256.New()
	scanner := bufio.NewScanner(io.TeeReader(input, hash))
	scanner.Buffer(make([]byte, 64*1024), maxJSONLLineBytes)

	summary := Summary{}
	seenPairs := make(map[[32]byte]struct{})
	sourceVariants := make(map[string]sourceVariantState)
	confidenceCount := 0
	confidenceMean := 0.0

	for line := 1; scanner.Scan(); line++ {
		if err := ctx.Err(); err != nil {
			return Report{}, err
		}
		if strings.TrimSpace(scanner.Text()) == "" {
			continue
		}
		var pair inputPair
		if err := json.Unmarshal(scanner.Bytes(), &pair); err != nil {
			return Report{}, fmt.Errorf("decode input line %d: %w", line, err)
		}
		if err := validatePair(pair); err != nil {
			return Report{}, fmt.Errorf("validate input line %d: %w", line, err)
		}

		summary.TotalPairs++
		reasons, ratio := assessPair(pair, options, seenPairs, sourceVariants, &summary)
		if pair.Confidence >= 0 && pair.Confidence <= 1 {
			confidenceCount++
			confidenceMean += (pair.Confidence - confidenceMean) / float64(confidenceCount)
		}
		if len(reasons) == 0 {
			continue
		}

		summary.FlaggedPairs++
		if summary.WrittenAnomalyRows >= options.MaxAnomalyRecords {
			summary.SuppressedAnomalyRows++
			continue
		}
		row := anomaly{
			PairID: pair.ID, Ordinal: pair.Ordinal, ZhText: pair.ZhText, EnText: pair.EnText,
			AlignmentUnit: pair.AlignmentUnit, Method: pair.Method, Confidence: pair.Confidence,
			LengthRatio: ratio, Reasons: reasons,
		}
		payload, err := json.Marshal(row)
		if err != nil {
			return Report{}, fmt.Errorf("encode anomaly at line %d: %w", line, err)
		}
		if _, err := anomaliesTemp.Write(append(payload, '\n')); err != nil {
			return Report{}, fmt.Errorf("write anomaly: %w", err)
		}
		summary.WrittenAnomalyRows++
	}
	if err := scanner.Err(); err != nil {
		return Report{}, fmt.Errorf("read input: %w", err)
	}
	summary.MeanConfidence = round(confidenceMean, 6)

	report := Report{
		SchemaVersion:  ReportSchemaVersion,
		AuditorVersion: AuditorVersion,
		GeneratedAt:    time.Now().UTC(),
		Input:          InputMetadata{Filename: filepath.Base(options.InputPath), SHA256: hex.EncodeToString(hash.Sum(nil))},
		Thresholds:     Thresholds{LowConfidence: options.LowConfidence, MinLengthRatio: options.MinLengthRatio, MaxLengthRatio: options.MaxLengthRatio, MaxAnomalyRows: options.MaxAnomalyRecords},
		Summary:        summary,
	}
	payload, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		return Report{}, fmt.Errorf("encode report: %w", err)
	}
	if _, err := reportTemp.Write(append(payload, '\n')); err != nil {
		return Report{}, fmt.Errorf("write report: %w", err)
	}
	if err := reportTemp.Close(); err != nil {
		return Report{}, fmt.Errorf("close report: %w", err)
	}
	if err := anomaliesTemp.Close(); err != nil {
		return Report{}, fmt.Errorf("close anomalies: %w", err)
	}
	if err := commitAnomalies(); err != nil {
		return Report{}, err
	}
	if err := commitReport(); err != nil {
		return Report{}, err
	}
	return report, nil
}

func (options Options) validate() error {
	if options.InputPath == "" || options.ReportPath == "" || options.AnomaliesPath == "" {
		return errors.New("input, report, and anomalies paths are required")
	}
	if options.LowConfidence < 0 || options.LowConfidence > 1 {
		return errors.New("low confidence threshold must be between 0 and 1")
	}
	if options.MinLengthRatio <= 0 || options.MaxLengthRatio <= options.MinLengthRatio {
		return errors.New("length ratio thresholds must be positive and ordered")
	}
	if options.MaxAnomalyRecords < 1 {
		return errors.New("max anomaly records must be greater than zero")
	}
	return nil
}

func validatePair(pair inputPair) error {
	if pair.ID == "" {
		return errors.New("id is required")
	}
	if pair.Ordinal < 1 {
		return errors.New("ordinal must be positive")
	}
	return nil
}

func assessPair(pair inputPair, options Options, seenPairs map[[32]byte]struct{}, sourceVariants map[string]sourceVariantState, summary *Summary) ([]string, *float64) {
	reasons := make([]string, 0, 4)
	zh := normalize(pair.ZhText)
	en := normalize(pair.EnText)
	if zh == "" || en == "" {
		reasons = append(reasons, "empty_side")
		summary.EmptySidePairs++
	} else {
		key := sha256.Sum256([]byte(zh + "\x00" + en))
		if _, exists := seenPairs[key]; exists {
			reasons = append(reasons, "duplicate_pair")
			summary.DuplicatePairs++
		} else {
			seenPairs[key] = struct{}{}
		}

		state, exists := sourceVariants[zh]
		if !exists {
			sourceVariants[zh] = sourceVariantState{firstTarget: en}
		} else if state.firstTarget != en {
			reasons = append(reasons, "source_translation_variant")
			summary.SourceTranslationVariants++
		}
	}

	if pair.Confidence < 0 || pair.Confidence > 1 {
		reasons = append(reasons, "invalid_confidence")
		summary.InvalidConfidencePairs++
	} else if pair.Confidence < options.LowConfidence {
		reasons = append(reasons, "low_confidence")
		summary.LowConfidencePairs++
	}

	var ratio *float64
	if zhUnits, enTokens := chineseCharacters(pair.ZhText), englishTokens(pair.EnText); zhUnits > 0 && enTokens > 0 {
		value := round(float64(enTokens)/float64(zhUnits), 4)
		ratio = &value
		if value < options.MinLengthRatio || value > options.MaxLengthRatio {
			reasons = append(reasons, "length_ratio_outlier")
			summary.LengthRatioOutliers++
		}
	}
	return reasons, ratio
}

func normalize(value string) string {
	return strings.Join(strings.Fields(strings.ToLower(value)), " ")
}

func chineseCharacters(value string) int {
	count := 0
	for _, r := range value {
		if !unicode.IsSpace(r) && !unicode.IsPunct(r) && !unicode.IsSymbol(r) {
			count++
		}
	}
	return count
}

func englishTokens(value string) int {
	count, inToken := 0, false
	for _, r := range value {
		if unicode.IsLetter(r) || unicode.IsNumber(r) {
			if !inToken {
				count++
				inToken = true
			}
		} else {
			inToken = false
		}
	}
	return count
}

func round(value float64, decimals int) float64 {
	scale := math.Pow10(decimals)
	return math.Round(value*scale) / scale
}

func atomicOutput(path string) (*os.File, func() error, func(), error) {
	directory := filepath.Dir(path)
	if err := os.MkdirAll(directory, 0o755); err != nil {
		return nil, nil, nil, fmt.Errorf("create output directory: %w", err)
	}
	file, err := os.CreateTemp(directory, ".corpus-auditor-*")
	if err != nil {
		return nil, nil, nil, fmt.Errorf("create temporary output: %w", err)
	}
	committed := false
	commit := func() error {
		if committed {
			return nil
		}
		if err := os.Rename(file.Name(), path); err != nil {
			return fmt.Errorf("publish output: %w", err)
		}
		committed = true
		return nil
	}
	discard := func() {
		if !committed {
			_ = file.Close()
			_ = os.Remove(file.Name())
		}
	}
	return file, commit, discard, nil
}
