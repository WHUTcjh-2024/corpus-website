FROM golang:1.26.6-alpine@sha256:3889b425f035be855a72fb4755265311293b6d414521f0a519d819df32222d83 AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/corpus-auditor-service ./cmd/corpus-auditor-service

FROM alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce

COPY --from=build /out/corpus-auditor-service /usr/local/bin/corpus-auditor-service
RUN apk upgrade --no-cache \
    && addgroup -S auditor && adduser -S -G auditor auditor \
    && mkdir -p /var/lib/corpus-auditor/jobs \
    && chown -R auditor:auditor /var/lib/corpus-auditor
USER auditor:auditor
EXPOSE 8090 9090
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1:8090/readyz || exit 1
ENTRYPOINT ["/usr/local/bin/corpus-auditor-service"]
