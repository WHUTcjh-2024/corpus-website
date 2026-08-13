package transport

import (
	"context"
	"net"
	"path/filepath"
	"testing"

	auditorv1 "corpus-platform/corpus-auditor/api/proto/corpus_auditor/v1"
	"corpus-platform/corpus-auditor/internal/service"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/emptypb"
)

func TestGeneratedGRPCContractRequiresControlToken(t *testing.T) {
	t.Parallel()
	auditor, err := service.New(service.Config{
		DataRoot: t.TempDir(), StateDirectory: filepath.Join(t.TempDir(), "jobs"),
		ResultPublisher: discardPublisher{}, WorkerCount: 1,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer auditor.Close()
	grpcService := grpc.NewServer(grpc.UnaryInterceptor(requireGRPCToken("control")))
	auditorv1.RegisterCorpusAuditorServer(grpcService, &grpcServer{service: auditor})
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	go func() { _ = grpcService.Serve(listener) }()
	defer grpcService.Stop()
	connection, err := grpc.NewClient(listener.Addr().String(), grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	client := auditorv1.NewCorpusAuditorClient(connection)
	if _, err := client.Health(context.Background(), &emptypb.Empty{}); err != nil {
		t.Fatal(err)
	}
	_, err = client.GetAudit(context.Background(), &auditorv1.GetAuditRequest{JobId: "missing"})
	if status.Code(err) != codes.Unauthenticated {
		t.Fatalf("unauthenticated status = %v", err)
	}
	context := metadata.NewOutgoingContext(context.Background(), metadata.Pairs("authorization", "Bearer control"))
	_, err = client.GetAudit(context, &auditorv1.GetAuditRequest{JobId: "missing"})
	if status.Code(err) != codes.NotFound {
		t.Fatalf("typed gRPC status = %v", err)
	}
}

type discardPublisher struct{}

func (discardPublisher) Publish([]byte) error { return nil }
