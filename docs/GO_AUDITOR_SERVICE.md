# Queue-backed Go corpus auditor

## Execution boundary

```mermaid
sequenceDiagram
  participant A as Agent / processing completion
  participant O as Django transactional outbox
  participant C as Celery command publisher
  participant Q as Redis Streams
  participant G as Go audit workers
  participant V as Shared data volume
  participant P as Django result projector

  A->>O: create ParallelAudit + durable command
  O->>C: publish after commit
  C->>Q: XADD corpus:audit:commands:v1
  G->>Q: XREADGROUP command
  G->>V: read pairs; atomically write report/anomalies
  G->>Q: XADD corpus:audit:results:v1
  P->>Q: XREADGROUP result
  P->>P: validate artifact refs and commit terminal state
  P->>O: write Agent continuation Outbox event in same transaction
  P->>Q: XACK result after DB commit
```

Django owns authorization, task creation, and status projection. The Go
service owns execution only: it receives bounded, data-root-relative artifact
references and has no Django database access. There is no synchronous Python →
Go request and no HTTP callback in the production workflow.

## Delivery semantics

- The transactional Outbox prevents a committed `ParallelAudit` from being lost
  before its Redis command is published.
- The command stream uses a Go consumer group. A command is acknowledged only
  after its durable Go job record is created; `job_id + fingerprint` makes
  redelivery idempotent.
- The Go service atomically writes report files, then publishes one terminal
  result event. Failed result publication is retried from the durable job
  record with bounded exponential backoff.
- Django's result projector writes `ParallelAudit` terminal state transactionally
  before `XACK`. `result_message_id` and the payload hash make a crash between
  commit and acknowledgment harmless.
- When a projected audit belongs to a waiting `quality_review` Agent, Django
  atomically creates `agent.resume_corpus_agent` in the same transaction. The
  resumed Agent reads the persisted report rather than trusting the queue
  message as evidence.
- Invalid command/result messages remain in the relevant pending-entry list for
  operational inspection; they are not silently discarded.

## Operations

The queue contracts are versioned by stream name and `schema_version=1`:

| Stream | Producer | Consumer group | Purpose |
| --- | --- | --- | --- |
| `corpus:audit:commands:v1` | Django Outbox/Celery | `corpus-auditor-v1` | launch audit jobs |
| `corpus:audit:results:v1` | Go workers | `django-audit-projector-v1` | terminal job results |

Local Compose starts a dedicated `audit-result-projector` process. Production
must use a Redis endpoint reachable by both control and data planes, with ACLs
limited to the two audit streams and consumer-group commands. The Go worker
exposes health plus its compatibility HTTP/gRPC API, but Django no longer uses
those APIs for job delivery.

Useful checks:

```powershell
docker compose --env-file .env.local.example -f docker-compose.local.yml up --build
docker compose --env-file .env.local.example -f docker-compose.local.yml exec redis redis-cli XPENDING corpus:audit:commands:v1 corpus-auditor-v1
docker compose --env-file .env.local.example -f docker-compose.local.yml exec redis redis-cli XPENDING corpus:audit:results:v1 django-audit-projector-v1
```
