package service

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

type Store struct {
	directory string
}

func NewStore(directory string) (*Store, error) {
	if err := os.MkdirAll(directory, 0o750); err != nil {
		return nil, fmt.Errorf("create job state directory: %w", err)
	}
	return &Store{directory: directory}, nil
}

func (store *Store) Load(id string) (Job, error) {
	payload, err := os.ReadFile(store.path(id))
	if os.IsNotExist(err) {
		return Job{}, ErrJobNotFound
	}
	if err != nil {
		return Job{}, fmt.Errorf("read job state: %w", err)
	}
	var job Job
	if err := json.Unmarshal(payload, &job); err != nil {
		return Job{}, fmt.Errorf("decode job state: %w", err)
	}
	return job, nil
}

func (store *Store) Save(job Job) error {
	payload, err := json.MarshalIndent(job, "", "  ")
	if err != nil {
		return fmt.Errorf("encode job state: %w", err)
	}
	temporary, err := os.CreateTemp(store.directory, ".audit-job-*")
	if err != nil {
		return fmt.Errorf("create temporary job state: %w", err)
	}
	name := temporary.Name()
	defer os.Remove(name)
	if _, err := temporary.Write(append(payload, '\n')); err != nil {
		temporary.Close()
		return fmt.Errorf("write temporary job state: %w", err)
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return fmt.Errorf("sync temporary job state: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("close temporary job state: %w", err)
	}
	if err := os.Rename(name, store.path(job.ID)); err != nil {
		return fmt.Errorf("publish job state: %w", err)
	}
	return nil
}

func (store *Store) Delete(id string) error {
	err := os.Remove(store.path(id))
	if err == nil || os.IsNotExist(err) {
		return nil
	}
	return fmt.Errorf("delete job state: %w", err)
}

func (store *Store) All() ([]Job, error) {
	entries, err := os.ReadDir(store.directory)
	if err != nil {
		return nil, fmt.Errorf("read job state directory: %w", err)
	}
	jobs := make([]Job, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		id := strings.TrimSuffix(entry.Name(), ".json")
		job, err := store.Load(id)
		if err != nil {
			return nil, err
		}
		jobs = append(jobs, job)
	}
	sort.Slice(jobs, func(left, right int) bool { return jobs[left].CreatedAt.Before(jobs[right].CreatedAt) })
	return jobs, nil
}

func (store *Store) path(id string) string {
	return filepath.Join(store.directory, id+".json")
}
