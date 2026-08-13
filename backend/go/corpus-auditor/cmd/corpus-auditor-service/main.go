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

	"corpus-platform/corpus-auditor/internal/queue"
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
	redisURL := flags.String("redis-url", os.Getenv("AUDITOR_REDIS_URL"), "Redis Streams URL")
	commandStream := flags.String("command-stream", env("AUDITOR_COMMAND_STREAM", "corpus:audit:commands:v1"), "audit command stream")
	commandGroup := flags.String("command-group", env("AUDITOR_COMMAND_GROUP", "corpus-auditor-v1"), "audit command consumer group")
	commandConsumer := flags.String("command-consumer", env("AUDITOR_COMMAND_CONSUMER", hostname()), "audit command consumer name")
	resultStream := flags.String("result-stream", env("AUDITOR_RESULT_STREAM", "corpus:audit:results:v1"), "audit result stream")
	workers := flags.Int("workers", envInt("AUDITOR_WORKERS", 2), "concurrent audit workers")
	queueCapacity := flags.Int("queue-capacity", envInt("AUDITOR_QUEUE_CAPACITY", 100), "bounded queued audit jobs")
	auditTimeout := flags.Duration("audit-timeout", envDuration("AUDITOR_AUDIT_TIMEOUT", 10*time.Minute), "maximum duration of one audit job")
	if err := flags.Parse(arguments); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return nil
		}
		return err
	}
	if *redisURL == "" {
		return errors.New("--redis-url or AUDITOR_REDIS_URL is required")
	}
	if *controlToken == "" {
		return errors.New("--control-token or AUDITOR_CONTROL_TOKEN is required for compatibility APIs")
	}
	streams, err := queue.New(queue.Config{
		URL: *redisURL, CommandStream: *commandStream, CommandGroup: *commandGroup,
		Consumer: *commandConsumer, ResultStream: *resultStream,
	})
	if err != nil {
		return err
	}
	defer streams.Close()
	if err := streams.EnsureCommandGroup(context.Background()); err != nil {
		return err
	}
	auditor, err := service.New(service.Config{
		DataRoot: *dataRoot, StateDirectory: *stateDirectory,
		WorkerCount: *workers, QueueCapacity: *queueCapacity, AuditTimeout: *auditTimeout,
		ResultPublisher: streams,
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
	shutdown := make(chan struct{})
	go func() {
		<-stop
		close(shutdown)
		context, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		_ = server.Shutdown(context)
		grpcServer.GracefulStop()
		_ = grpcListener.Close()
	}()
	go func() {
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				auditor.RetryPendingResults()
			case <-shutdown:
				return
			}
		}
	}()
	go consumeCommands(shutdown, streams, auditor)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

func consumeCommands(shutdown <-chan struct{}, streams *queue.Streams, auditor *service.Service) {
	context := context.Background()
	reclaimTicker := time.NewTicker(30 * time.Second)
	defer reclaimTicker.Stop()
	firstPass := true
	for {
		select {
		case <-shutdown:
			return
		default:
		}
		var messages []queue.Message
		var err error
		if firstPass {
			messages, err = streams.ReclaimCommands(context)
			firstPass = false
		} else {
			messages, err = streams.ReadCommands(context)
		}
		if err != nil {
			fmt.Fprintln(os.Stderr, "corpus-auditor-service: command consume:", err)
			time.Sleep(time.Second)
			continue
		}
		for _, message := range messages {
			_, _, submitErr := auditor.Submit(message.Request)
			if submitErr != nil {
				fmt.Fprintln(os.Stderr, "corpus-auditor-service: command rejected:", submitErr)
				if err := publishRejectedCommand(streams, message.Request, submitErr); err != nil {
					fmt.Fprintln(os.Stderr, "corpus-auditor-service: rejected command result:", err)
					continue
				}
				if err := streams.AckCommand(context, message.ID); err != nil {
					fmt.Fprintln(os.Stderr, "corpus-auditor-service: rejected command acknowledge:", err)
				}
				continue
			}
			if err := streams.AckCommand(context, message.ID); err != nil {
				fmt.Fprintln(os.Stderr, "corpus-auditor-service: command acknowledge:", err)
			}
		}
		select {
		case <-reclaimTicker.C:
			firstPass = true
		default:
		}
	}
}

func publishRejectedCommand(streams *queue.Streams, request service.SubmitRequest, cause error) error {
	payload := fmt.Sprintf(
		`{"id":%q,"schema_version":1,"state":"failed","attempt":0,"error_code":"command_rejected","error_message":%q}`,
		request.JobID,
		cause.Error(),
	)
	return streams.Publish([]byte(payload))
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

func hostname() string {
	value, err := os.Hostname()
	if err != nil || value == "" {
		return "corpus-auditor"
	}
	return value
}
