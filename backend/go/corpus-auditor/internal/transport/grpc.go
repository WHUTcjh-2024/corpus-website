package transport

import (
	"context"
	"crypto/subtle"
	"errors"
	"fmt"
	"net"
	"time"

	auditorv1 "corpus-platform/corpus-auditor/api/proto/corpus_auditor/v1"
	"corpus-platform/corpus-auditor/internal/audit"
	"corpus-platform/corpus-auditor/internal/service"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/emptypb"
	"google.golang.org/protobuf/types/known/structpb"
)

// grpcServer implements the generated versioned contract. HTTP remains the
// Django-facing transport; gRPC gives other services a typed interface without
// serializing a local-path or database capability into the data plane.
type grpcServer struct {
	auditorv1.UnimplementedCorpusAuditorServer
	service *service.Service
}

func ListenAndServeGRPC(address string, auditor *service.Service, controlToken string) (*grpc.Server, net.Listener, error) {
	listener, err := net.Listen("tcp", address)
	if err != nil {
		return nil, nil, fmt.Errorf("listen for gRPC: %w", err)
	}
	server := grpc.NewServer(
		grpc.MaxRecvMsgSize(1<<20),
		grpc.MaxSendMsgSize(1<<20),
		grpc.UnaryInterceptor(requireGRPCToken(controlToken)),
	)
	auditorv1.RegisterCorpusAuditorServer(server, &grpcServer{service: auditor})
	go func() { _ = server.Serve(listener) }()
	return server, listener, nil
}

func requireGRPCToken(token string) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, request any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		if info.FullMethod == auditorv1.CorpusAuditor_Health_FullMethodName {
			return handler(ctx, request)
		}
		values := metadata.ValueFromIncomingContext(ctx, "authorization")
		if token == "" || len(values) != 1 || subtle.ConstantTimeCompare([]byte(values[0]), []byte("Bearer "+token)) != 1 {
			return nil, status.Error(codes.Unauthenticated, "unauthenticated")
		}
		return handler(ctx, request)
	}
}

func (server *grpcServer) SubmitAudit(_ context.Context, request *auditorv1.SubmitAuditRequest) (*auditorv1.AuditJob, error) {
	if request == nil {
		return nil, status.Error(codes.InvalidArgument, "request is required")
	}
	job, _, err := server.service.Submit(fromProtoRequest(request))
	if err != nil {
		return nil, grpcError(err)
	}
	return toProtoJob(job)
}

func (server *grpcServer) GetAudit(_ context.Context, request *auditorv1.GetAuditRequest) (*auditorv1.AuditJob, error) {
	if request == nil || request.GetJobId() == "" {
		return nil, status.Error(codes.InvalidArgument, "job_id is required")
	}
	job, err := server.service.Get(request.GetJobId())
	if err != nil {
		return nil, grpcError(err)
	}
	return toProtoJob(job)
}

func (server *grpcServer) CancelAudit(_ context.Context, request *auditorv1.CancelAuditRequest) (*auditorv1.AuditJob, error) {
	if request == nil || request.GetJobId() == "" {
		return nil, status.Error(codes.InvalidArgument, "job_id is required")
	}
	job, err := server.service.Cancel(request.GetJobId())
	if err != nil {
		return nil, grpcError(err)
	}
	return toProtoJob(job)
}

func (server *grpcServer) SubmitBatch(_ context.Context, request *auditorv1.SubmitBatchRequest) (*auditorv1.SubmitBatchResponse, error) {
	if request == nil {
		return nil, status.Error(codes.InvalidArgument, "jobs are required")
	}
	requests := make([]service.SubmitRequest, 0, len(request.GetJobs()))
	for _, item := range request.GetJobs() {
		requests = append(requests, fromProtoRequest(item))
	}
	jobs, err := server.service.SubmitBatch(requests)
	if err != nil {
		return nil, grpcError(err)
	}
	response := &auditorv1.SubmitBatchResponse{Jobs: make([]*auditorv1.AuditJob, 0, len(jobs))}
	for _, job := range jobs {
		item, err := toProtoJob(job)
		if err != nil {
			return nil, err
		}
		response.Jobs = append(response.Jobs, item)
	}
	return response, nil
}

func (server *grpcServer) Health(_ context.Context, _ *emptypb.Empty) (*auditorv1.HealthResponse, error) {
	if !server.service.Healthy() {
		return nil, status.Error(codes.Unavailable, "service is stopping")
	}
	return &auditorv1.HealthResponse{Status: "ok", Service: "corpus-auditor"}, nil
}

func fromProtoRequest(request *auditorv1.SubmitAuditRequest) service.SubmitRequest {
	if request == nil {
		return service.SubmitRequest{}
	}
	options := request.GetOptions()
	if options == nil {
		options = &auditorv1.AuditOptions{}
	}
	return service.SubmitRequest{
		JobID: request.GetJobId(), InputRef: request.GetInputRef(), OutputPrefix: request.GetOutputPrefix(), CallbackPath: request.GetCallbackPath(),
		Options: service.AuditOptions{
			LowConfidence: options.GetLowConfidence(), MinLengthRatio: options.GetMinLengthRatio(),
			MaxLengthRatio: options.GetMaxLengthRatio(), MaxAnomalyRecords: int(options.GetMaxAnomalies()),
		},
	}
}

func toProtoJob(job service.Job) (*auditorv1.AuditJob, error) {
	response := &auditorv1.AuditJob{
		Id: job.ID, State: job.State, Attempt: int32(job.Attempt), InputRef: job.Request.InputRef,
		OutputPrefix: job.Request.OutputPrefix, ReportRef: job.ReportRef, AnomaliesRef: job.AnomaliesRef,
		ErrorCode: job.ErrorCode, ErrorMessage: job.ErrorMessage,
		Callback: &auditorv1.CallbackDelivery{
			Delivered: job.Callback.Delivered, Attempts: int32(job.Callback.Attempts),
			NextAt: formatTime(job.Callback.NextAt), LastError: job.Callback.LastError,
		},
		CreatedAt: formatTime(job.CreatedAt), UpdatedAt: formatTime(job.UpdatedAt),
	}
	if job.Report != nil {
		summary, err := structpb.NewStruct(summaryMap(job.Report.Summary))
		if err != nil {
			return nil, status.Error(codes.Internal, "encode audit summary")
		}
		response.Summary = summary
		response.AuditorVersion = job.Report.AuditorVersion
	}
	return response, nil
}

func summaryMap(summary audit.Summary) map[string]any {
	return map[string]any{
		"total_pairs":                 summary.TotalPairs,
		"flagged_pairs":               summary.FlaggedPairs,
		"empty_side_pairs":            summary.EmptySidePairs,
		"duplicate_pairs":             summary.DuplicatePairs,
		"source_translation_variants": summary.SourceTranslationVariants,
		"low_confidence_pairs":        summary.LowConfidencePairs,
		"invalid_confidence_pairs":    summary.InvalidConfidencePairs,
		"length_ratio_outliers":       summary.LengthRatioOutliers,
		"written_anomaly_rows":        summary.WrittenAnomalyRows,
		"suppressed_anomaly_rows":     summary.SuppressedAnomalyRows,
		"mean_confidence":             summary.MeanConfidence,
	}
}

func formatTime(value time.Time) string {
	if value.IsZero() {
		return ""
	}
	return value.UTC().Format(time.RFC3339Nano)
}

func grpcError(err error) error {
	switch {
	case errors.Is(err, service.ErrJobNotFound):
		return status.Error(codes.NotFound, err.Error())
	case errors.Is(err, service.ErrJobConflict):
		return status.Error(codes.AlreadyExists, err.Error())
	case errors.Is(err, service.ErrQueueFull):
		return status.Error(codes.ResourceExhausted, err.Error())
	case errors.Is(err, service.ErrInvalidRequest):
		return status.Error(codes.InvalidArgument, err.Error())
	case errors.Is(err, service.ErrServiceStopping):
		return status.Error(codes.Unavailable, err.Error())
	default:
		return status.Error(codes.Internal, "internal error")
	}
}
