FROM golang:1.26-alpine AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/corpus-auditor-service ./cmd/corpus-auditor-service

FROM gcr.io/distroless/static-debian12:nonroot

COPY --from=build /out/corpus-auditor-service /usr/local/bin/corpus-auditor-service
USER nonroot:nonroot
EXPOSE 8090 9090
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD ["/usr/local/bin/corpus-auditor-service", "-h"]
ENTRYPOINT ["/usr/local/bin/corpus-auditor-service"]
