package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"corpus-platform/corpus-auditor/internal/service"
	"corpus-platform/corpus-auditor/internal/transport"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "corpus-auditor-service:", err)
		os.Exit(1)
	}
}

func run(arguments []string) error {
	flags := flag.NewFlagSet("corpus-auditor-service", flag.ContinueOnError)
	listenAddress := flags.String("listen", env("AUDITOR_LISTEN_ADDR", ":8090"), "HTTP listen address")
	grpcListenAddress := flags.String("grpc-listen", env("AUDITOR_GRPC_LISTEN_ADDR", ":9090"), "gRPC listen address")
	dataRoot := flags.String("data-root", env("AUDITOR_DATA_ROOT", "/data"), "shared data root")
	stateDirectory := flags.String("state-dir", env("AUDITOR_STATE_DIR", "/var/lib/corpus-auditor/jobs"), "durable job state directory")
	controlToken := flags.String("control-token", os.Getenv("AUDITOR_CONTROL_TOKEN"), "Bearer token accepted from the control plane")
	callbackBaseURL := flags.String("callback-base-url", os.Getenv("AUDITOR_CALLBACK_BASE_URL"), "fixed Django callback base URL")
	callbackToken := flags.String("callback-token", os.Getenv("AUDITOR_CALLBACK_TOKEN"), "HMAC key for Django callbacks")
	workers := flags.Int("workers", envInt("AUDITOR_WORKERS", 2), "concurrent audit workers")
	queueCapacity := flags.Int("queue-capacity", envInt("AUDITOR_QUEUE_CAPACITY", 100), "bounded queued audit jobs")
	auditTimeout := flags.Duration("audit-timeout", envDuration("AUDITOR_AUDIT_TIMEOUT", 10*time.Minute), "maximum duration of one audit job")
	if err := flags.Parse(arguments); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return nil
		}
		return err
	}
	if *controlToken == "" {
		return errors.New("--control-token or AUDITOR_CONTROL_TOKEN is required")
	}
	auditor, err := service.New(service.Config{
		DataRoot: *dataRoot, StateDirectory: *stateDirectory,
		CallbackBaseURL: *callbackBaseURL, CallbackToken: *callbackToken,
		WorkerCount: *workers, QueueCapacity: *queueCapacity, AuditTimeout: *auditTimeout,
	})
	if err != nil {
		return err
	}
	defer auditor.Close()

	server := &http.Server{
		Addr:              *listenAddress,
		Handler:           transport.NewHTTPServer(auditor, *controlToken).Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      15 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    16 << 10,
	}
	grpcServer, grpcListener, err := transport.ListenAndServeGRPC(
		*grpcListenAddress, auditor, *controlToken,
	)
	if err != nil {
		return err
	}
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	go func() {
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				auditor.RetryPendingCallbacks()
			case <-stop:
				return
			}
		}
	}()
	go func() {
		<-stop
		context, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		_ = server.Shutdown(context)
		grpcServer.GracefulStop()
		_ = grpcListener.Close()
	}()
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

func env(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func envInt(name string, fallback int) int {
	value := os.Getenv(name)
	if value == "" {
		return fallback
	}
	var parsed int
	if _, err := fmt.Sscanf(value, "%d", &parsed); err != nil || parsed < 1 {
		return fallback
	}
	return parsed
}

func envDuration(name string, fallback time.Duration) time.Duration {
	value := os.Getenv(name)
	if value == "" {
		return fallback
	}
	parsed, err := time.ParseDuration(value)
	if err != nil || parsed <= 0 {
		return fallback
	}
	return parsed
}
