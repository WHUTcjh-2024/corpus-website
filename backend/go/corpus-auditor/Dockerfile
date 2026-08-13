FROM golang:1.26-alpine AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/corpus-auditor-service ./cmd/corpus-auditor-service

FROM alpine:3.22

COPY --from=build /out/corpus-auditor-service /usr/local/bin/corpus-auditor-service
RUN addgroup -S auditor && adduser -S -G auditor auditor \
    && mkdir -p /var/lib/corpus-auditor/jobs \
    && chown -R auditor:auditor /var/lib/corpus-auditor
USER auditor:auditor
EXPOSE 8090 9090
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1:8090/readyz || exit 1
ENTRYPOINT ["/usr/local/bin/corpus-auditor-service"]
