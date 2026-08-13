# Operations runbook

## Reliable task delivery

Processing and export requests are committed with an Outbox event in the same
PostgreSQL transaction. The `outbox` service publishes these events to Celery.
This provides at-least-once delivery: a publisher crash after broker acceptance
may create a duplicate delivery, so consumers must remain idempotent.

An event retries with exponential backoff. After `OUTBOX_MAX_ATTEMPTS` failures
it becomes `dead_letter` and is never retried automatically. Inspect the error
before replaying a single event:

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml exec outbox \
  python manage.py replay_outbox --event-id <event-uuid>
```

Use `--all --limit 100` only after fixing a shared failure, such as a restored
broker or corrected network policy. Replayed events return to `pending`; the
publisher sends them on its next polling cycle.

## Dedicated worker queues

Corpus processing and export jobs are routed to separate Celery queues:
`processing.process_corpus` to `processing`, and `exports.build_export` to
`exports`. Production Compose runs a `processing-worker` and an
`export-worker`; a long-running corpus import therefore cannot consume export
capacity.

Scale the workers independently according to their bottleneck. For example,
increase parallel corpus processing while retaining one rate-limited exporter:

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d \
  --scale processing-worker=3 --scale export-worker=1
```

Use `PROCESSING_WORKER_CONCURRENCY` and `EXPORT_WORKER_CONCURRENCY` to bound
parallelism within one replica. Keep export concurrency low unless storage and
database load have been measured; exports can read many indexed rows at once.

The `agent` queue is consumed by `processing-worker` because Agent tools access
the same immutable corpus indexes and audit artifacts. Agent runs are durable
state machines, not long-lived HTTP requests: inspect an `AgentRun` and its
step trace before replaying any dead-letter Outbox event. A run in
`waiting_approval` is healthy and must never be replayed to bypass the user
confirmation boundary. See [CORPUS_AGENT_HARNESS.md](CORPUS_AGENT_HARNESS.md).

## Metrics and alerting

`GET /metrics` returns Prometheus text metrics only when the request includes
`Authorization: Bearer $METRICS_BEARER_TOKEN`. Keep this endpoint behind an
internal network boundary as well as the bearer token.

Create alerts for:

- `corpus_outbox_events{status="dead_letter"} > 0` for 5 minutes.
- `corpus_outbox_oldest_pending_age_seconds > 300` for 10 minutes.
- sustained growth of `corpus_outbox_events{status="pending"}`.
- `corpus_agent_runs{status="failed"} > 0` for 10 minutes.
- a sustained `corpus_agent_runs{status="waiting_approval"}` backlog above the
  operating threshold; expired approvals should be allowed to age out rather
  than auto-approved.

The default retention is seven days for successfully published events. Dead
letters are retained for investigation and manual replay.

## Prometheus profile

The production Compose file includes a `monitoring` profile with Prometheus and
three outbox alert rules. Before enabling it, write the same token used by the
web service into an untracked file:

```bash
umask 077
printf %s "$METRICS_BEARER_TOKEN" > deploy/secrets/metrics_bearer_token
docker compose --env-file .env.prod -f docker-compose.prod.yml \
  --profile monitoring up -d
```

Prometheus is intentionally internal-only. Connect it to an organization
Alertmanager or notification service before treating the bundled alert rules as
an on-call notification system.
