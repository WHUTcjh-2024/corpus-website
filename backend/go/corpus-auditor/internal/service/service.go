package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"corpus-platform/corpus-auditor/internal/audit"
)

// Config is deliberately small. The service shares a data volume and a broker
// with Django, but never receives database credentials.
type Config struct {
	DataRoot             string
	StateDirectory       string
	WorkerCount          int
	QueueCapacity        int
	ResultMaxAttempts    int
	ResultRetryBase      time.Duration
	AuditTimeout         time.Duration
	MaxPendingResultScan int
	ResultPublisher      ResultPublisher
	Now                  func() time.Time
}

func (config Config) withDefaults() Config {
	if config.WorkerCount < 1 {
		config.WorkerCount = 2
	}
	if config.QueueCapacity < 1 {
		config.QueueCapacity = 100
	}
	if config.ResultMaxAttempts < 1 {
		config.ResultMaxAttempts = 8
	}
	if config.ResultRetryBase <= 0 {
		config.ResultRetryBase = 5 * time.Second
	}
	if config.AuditTimeout <= 0 {
		config.AuditTimeout = 10 * time.Minute
	}
	if config.MaxPendingResultScan < 1 {
		config.MaxPendingResultScan = 100
	}
	if config.Now == nil {
		config.Now = time.Now
	}
	return config
}

func (config Config) validate() error {
	if config.DataRoot == "" || config.StateDirectory == "" {
		return errors.New("data root and state directory are required")
	}
	if config.ResultPublisher == nil {
		return errors.New("result publisher is required")
	}
	return nil
}

type Service struct {
	config Config
	store  *Store

	mu       sync.Mutex
	jobs     map[string]*Job
	cancel   map[string]context.CancelFunc
	queue    chan string
	closed   chan struct{}
	stopping bool
	waiters  sync.WaitGroup
}

func New(config Config) (*Service, error) {
	config = config.withDefaults()
	if err := config.validate(); err != nil {
		return nil, err
	}
	dataRoot, err := filepath.Abs(config.DataRoot)
	if err != nil {
		return nil, fmt.Errorf("resolve data root: %w", err)
	}
	stateDirectory, err := filepath.Abs(config.StateDirectory)
	if err != nil {
		return nil, fmt.Errorf("resolve state directory: %w", err)
	}
	config.DataRoot = dataRoot
	config.StateDirectory = stateDirectory
	store, err := NewStore(config.StateDirectory)
	if err != nil {
		return nil, err
	}
	service := &Service{
		config: config,
		store:  store,
		jobs:   make(map[string]*Job),
		cancel: make(map[string]context.CancelFunc),
		queue:  make(chan string, config.QueueCapacity),
		closed: make(chan struct{}),
	}
	if err := service.restore(); err != nil {
		return nil, err
	}
	for index := 0; index < config.WorkerCount; index++ {
		service.waiters.Add(1)
		go service.worker()
	}
	return service, nil
}

func (service *Service) Close() {
	select {
	case <-service.closed:
		return
	default:
		close(service.closed)
	}
	service.mu.Lock()
	service.stopping = true
	for _, cancel := range service.cancel {
		cancel()
	}
	service.mu.Unlock()
	service.waiters.Wait()
}

func (service *Service) Healthy() bool {
	service.mu.Lock()
	defer service.mu.Unlock()
	return !service.stopping
}

func (service *Service) Submit(request SubmitRequest) (Job, bool, error) {
	normalized, err := request.normalized()
	if err != nil {
		return Job{}, false, err
	}
	fingerprint, err := canonicalFingerprint(normalized)
	if err != nil {
		return Job{}, false, fmt.Errorf("fingerprint audit request: %w", err)
	}
	service.mu.Lock()
	defer service.mu.Unlock()
	if service.stopping {
		return Job{}, false, ErrServiceStopping
	}
	select {
	case <-service.closed:
		return Job{}, false, ErrServiceStopping
	default:
	}
	if existing, exists := service.jobs[normalized.JobID]; exists {
		if existing.Fingerprint != fingerprint {
			return Job{}, false, ErrJobConflict
		}
		return *existing, false, nil
	}
	if len(service.queue) >= cap(service.queue) {
		return Job{}, false, ErrQueueFull
	}
	now := service.config.Now().UTC()
	job := Job{
		ID: normalized.JobID, Request: normalized, Fingerprint: fingerprint,
		State: StateQueued, CreatedAt: now, UpdatedAt: now,
	}
	if err := service.store.Save(job); err != nil {
		return Job{}, false, err
	}
	service.jobs[job.ID] = &job
	service.queue <- job.ID
	return job, true, nil
}

func (service *Service) SubmitBatch(requests []SubmitRequest) ([]Job, error) {
	if len(requests) == 0 || len(requests) > 100 {
		return nil, fmt.Errorf("%w: batch contains 1 to 100 jobs", ErrInvalidRequest)
	}
	normalized := make([]SubmitRequest, 0, len(requests))
	fingerprints := make([]string, 0, len(requests))
	seen := make(map[string]struct{}, len(requests))
	for _, request := range requests {
		item, err := request.normalized()
		if err != nil {
			return nil, err
		}
		if _, exists := seen[item.JobID]; exists {
			return nil, fmt.Errorf("%w: duplicate job_id in batch", ErrInvalidRequest)
		}
		fingerprint, err := canonicalFingerprint(item)
		if err != nil {
			return nil, fmt.Errorf("fingerprint audit request: %w", err)
		}
		seen[item.JobID] = struct{}{}
		normalized = append(normalized, item)
		fingerprints = append(fingerprints, fingerprint)
	}

	service.mu.Lock()
	defer service.mu.Unlock()
	if service.stopping {
		return nil, ErrServiceStopping
	}
	newCount := 0
	for index, request := range normalized {
		if existing, exists := service.jobs[request.JobID]; exists {
			if existing.Fingerprint != fingerprints[index] {
				return nil, ErrJobConflict
			}
			continue
		}
		newCount++
	}
	if cap(service.queue)-len(service.queue) < newCount {
		return nil, ErrQueueFull
	}
	now := service.config.Now().UTC()
	jobs := make([]Job, 0, len(normalized))
	created := make([]string, 0, newCount)
	for index, request := range normalized {
		if existing, exists := service.jobs[request.JobID]; exists {
			jobs = append(jobs, *existing)
			continue
		}
		job := Job{ID: request.JobID, Request: request, Fingerprint: fingerprints[index], State: StateQueued, CreatedAt: now, UpdatedAt: now}
		if err := service.store.Save(job); err != nil {
			for _, id := range created {
				delete(service.jobs, id)
				_ = service.store.Delete(id)
			}
			return nil, err
		}
		copy := job
		service.jobs[job.ID] = &copy
		created = append(created, job.ID)
		jobs = append(jobs, job)
	}
	for _, id := range created {
		service.queue <- id
	}
	return jobs, nil
}

func (service *Service) Get(id string) (Job, error) {
	service.mu.Lock()
	defer service.mu.Unlock()
	job, exists := service.jobs[id]
	if !exists {
		return Job{}, ErrJobNotFound
	}
	return *job, nil
}

func (service *Service) Cancel(id string) (Job, error) {
	service.mu.Lock()
	job, exists := service.jobs[id]
	if !exists {
		service.mu.Unlock()
		return Job{}, ErrJobNotFound
	}
	if job.terminal() {
		service.mu.Unlock()
		return *job, nil
	}
	now := service.config.Now().UTC()
	job.State = StateCancelled
	job.ErrorCode = "cancelled"
	job.ErrorMessage = "cancelled by control plane"
	job.UpdatedAt = now
	job.FinishedAt = &now
	if err := service.store.Save(*job); err != nil {
		service.mu.Unlock()
		return Job{}, err
	}
	if cancel := service.cancel[id]; cancel != nil {
		cancel()
	}
	result := *job
	service.mu.Unlock()
	go service.publishResult(id)
	return result, nil
}

func (service *Service) RetryPendingResults() int {
	service.mu.Lock()
	ids := make([]string, 0, service.config.MaxPendingResultScan)
	for id, job := range service.jobs {
		if len(ids) == service.config.MaxPendingResultScan {
			break
		}
		if job.terminal() && !job.Result.Delivered && job.Result.Attempts < service.config.ResultMaxAttempts && (job.Result.NextAt.IsZero() || !job.Result.NextAt.After(service.config.Now())) {
			ids = append(ids, id)
		}
	}
	service.mu.Unlock()
	sort.Strings(ids)
	for _, id := range ids {
		service.publishResult(id)
	}
	return len(ids)
}

func (service *Service) restore() error {
	jobs, err := service.store.All()
	if err != nil {
		return err
	}
	for _, job := range jobs {
		copy := job
		if copy.State == StateRunning {
			copy.State = StateQueued
			copy.ErrorCode = ""
			copy.ErrorMessage = ""
			copy.StartedAt = nil
			copy.UpdatedAt = service.config.Now().UTC()
			if err := service.store.Save(copy); err != nil {
				return err
			}
		}
		service.jobs[copy.ID] = &copy
		if copy.State == StateQueued {
			select {
			case service.queue <- copy.ID:
			default:
				return ErrQueueFull
			}
		}
	}
	return nil
}

func (service *Service) worker() {
	defer service.waiters.Done()
	for {
		select {
		case <-service.closed:
			return
		case id := <-service.queue:
			service.execute(id)
		}
	}
}

func (service *Service) execute(id string) {
	ctx, cancel := context.WithTimeout(context.Background(), service.config.AuditTimeout)
	service.mu.Lock()
	job, exists := service.jobs[id]
	if !exists || job.State != StateQueued || service.stopping {
		service.mu.Unlock()
		cancel()
		return
	}
	now := service.config.Now().UTC()
	job.State = StateRunning
	job.Attempt++
	job.StartedAt = &now
	job.UpdatedAt = now
	service.cancel[id] = cancel
	if err := service.store.Save(*job); err != nil {
		job.State = StateFailed
		job.ErrorCode = "state_write_failed"
		job.ErrorMessage = err.Error()
		job.FinishedAt = &now
		_ = service.store.Save(*job)
		delete(service.cancel, id)
		service.mu.Unlock()
		cancel()
		go service.publishResult(id)
		return
	}
	request := job.Request
	service.mu.Unlock()

	reportRef := filepath.ToSlash(filepath.Join(request.OutputPrefix, "quality_report.json"))
	anomaliesRef := filepath.ToSlash(filepath.Join(request.OutputPrefix, "anomalies.jsonl"))
	report, err := audit.RunContext(ctx, audit.Options{
		InputPath:         service.dataPath(request.InputRef),
		ReportPath:        service.dataPath(reportRef),
		AnomaliesPath:     service.dataPath(anomaliesRef),
		LowConfidence:     request.Options.LowConfidence,
		MinLengthRatio:    request.Options.MinLengthRatio,
		MaxLengthRatio:    request.Options.MaxLengthRatio,
		MaxAnomalyRecords: request.Options.MaxAnomalyRecords,
	})
	cancel()

	service.mu.Lock()
	defer service.mu.Unlock()
	delete(service.cancel, id)
	job = service.jobs[id]
	if service.stopping && job != nil && job.State == StateRunning {
		job.State = StateQueued
		job.StartedAt = nil
		job.UpdatedAt = service.config.Now().UTC()
		_ = service.store.Save(*job)
		return
	}
	if job == nil || job.State == StateCancelled {
		return
	}
	finished := service.config.Now().UTC()
	job.UpdatedAt = finished
	job.FinishedAt = &finished
	if err != nil {
		job.State = StateFailed
		if errors.Is(err, context.Canceled) {
			job.ErrorCode = "cancelled"
			job.ErrorMessage = "cancelled while running"
		} else {
			job.ErrorCode = "audit_failed"
			job.ErrorMessage = err.Error()
		}
	} else {
		job.State = StateSucceeded
		job.ReportRef = reportRef
		job.AnomaliesRef = anomaliesRef
		job.Report = &report
		job.ErrorCode = ""
		job.ErrorMessage = ""
	}
	if err := service.store.Save(*job); err != nil {
		job.State = StateFailed
		job.ErrorCode = "state_write_failed"
		job.ErrorMessage = err.Error()
		_ = service.store.Save(*job)
	}
	go service.publishResult(id)
}

func (service *Service) publishResult(id string) {
	service.mu.Lock()
	job := service.jobs[id]
	if job == nil || !job.terminal() || job.Result.Delivered || job.Result.Attempts >= service.config.ResultMaxAttempts || (!job.Result.NextAt.IsZero() && job.Result.NextAt.After(service.config.Now())) {
		service.mu.Unlock()
		return
	}
	payload, err := json.Marshal(job.Public())
	service.mu.Unlock()
	if err == nil {
		err = service.config.ResultPublisher.Publish(payload)
	}
	service.markResult(id, err == nil, errorText(err))
}

func (service *Service) markResult(id string, delivered bool, message string) {
	service.mu.Lock()
	defer service.mu.Unlock()
	job := service.jobs[id]
	if job == nil || job.Result.Delivered {
		return
	}
	job.Result.Attempts++
	job.Result.Delivered = delivered
	job.Result.LastError = ""
	job.Result.NextAt = time.Time{}
	if !delivered {
		job.Result.LastError = message
		if job.Result.Attempts < service.config.ResultMaxAttempts {
			exponent := min(job.Result.Attempts-1, 10)
			job.Result.NextAt = service.config.Now().UTC().Add(service.config.ResultRetryBase * time.Duration(1<<exponent))
		}
	}
	if job.Result.Attempts >= service.config.ResultMaxAttempts && !delivered {
		job.Result.NextAt = time.Time{}
	}
	job.UpdatedAt = service.config.Now().UTC()
	_ = service.store.Save(*job)
}

func (service *Service) dataPath(reference string) string {
	candidate := filepath.Join(service.config.DataRoot, filepath.FromSlash(reference))
	relative, err := filepath.Rel(service.config.DataRoot, candidate)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		panic("validated reference escaped data root")
	}
	return candidate
}

func errorText(err error) string {
	if err == nil {
		return "result publish could not be created"
	}
	return err.Error()
}

func min(left, right int) int {
	if left < right {
		return left
	}
	return right
}
