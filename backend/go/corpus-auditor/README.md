# Corpus Auditor

`corpus-auditor` is the Go data-plane service for parallel-corpus quality
inspection. Django remains the control plane: it authorizes work, persists
audit state, and exposes results. The auditor never receives database
credentials or absolute host paths.

It checks empty sides, duplicate pairs, conflicting translations for the same
source, low/invalid confidence, and Chinese-English length-ratio outliers.
Reports and anomaly rows are atomically published into the shared data volume.

## Service contract

The service exposes both HTTP/JSON and a generated gRPC API:

- `POST /v1/audits` — submit an idempotent audit job.
- `GET /v1/audits/{jobID}` — inspect durable execution state.
- `POST /v1/audits/{jobID}/cancel` — cancel queued/running work.
- `POST /v1/audits/batch` — atomically validate and enqueue up to 100 jobs.
- `GET /healthz`, `GET /readyz` — liveness/readiness.
- `api/proto/corpus_auditor/v1/auditor.proto` — versioned typed gRPC contract.

Requests contain only `DATA_ROOT`-relative `input_ref` / `output_prefix` values.
The fixed callback target is configured at service startup, not accepted from
clients. A terminal result is POSTed back with HMAC-SHA256 over
`timestamp + "\n" + raw_body`; Django verifies timestamp skew, signature,
job identity, output prefix, and callback idempotency.

Each job is stored as an atomically replaced JSON document. On restart,
in-flight jobs return to `queued`; terminal callbacks retry with bounded
exponential backoff, and Django performs bounded remote-state reconciliation if
the callback retry budget is exhausted.

## Run locally

```powershell
go test ./...
go build -o bin/corpus-auditor-service.exe ./cmd/corpus-auditor-service

$env:AUDITOR_CONTROL_TOKEN = "local-control-token"
$env:AUDITOR_CALLBACK_BASE_URL = "http://127.0.0.1:8000"
$env:AUDITOR_CALLBACK_TOKEN = "local-callback-token"
.\bin\corpus-auditor-service.exe --data-root ..\..\..\data --state-dir .\var\jobs
```

For the old, single-process development fallback only:

```powershell
go build -o bin/corpus-auditor.exe ./cmd/corpus-auditor
.\bin\corpus-auditor.exe --input sample.jsonl --report quality_report.json --anomalies anomalies.jsonl
```

Regenerate protobuf bindings after changing the contract:

```powershell
$env:Path = "$env:USERPROFILE\go\bin;$env:Path"
protoc --go_out=. --go_opt=paths=source_relative --go-grpc_out=. --go-grpc_opt=paths=source_relative api/proto/corpus_auditor/v1/auditor.proto
```
