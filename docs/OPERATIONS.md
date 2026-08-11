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

## Metrics and alerting

`GET /metrics` returns Prometheus text metrics only when the request includes
`Authorization: Bearer $METRICS_BEARER_TOKEN`. Keep this endpoint behind an
internal network boundary as well as the bearer token.

Create alerts for:

- `corpus_outbox_events{status="dead_letter"} > 0` for 5 minutes.
- `corpus_outbox_oldest_pending_age_seconds > 300` for 10 minutes.
- sustained growth of `corpus_outbox_events{status="pending"}`.

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
