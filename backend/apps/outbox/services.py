from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from celery import current_app
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import OutboxEvent, OutboxEventStatus


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PublishResult:
    event_id: str
    outcome: str


@dataclass(frozen=True, slots=True)
class PublishSummary:
    published: int = 0
    retry_scheduled: int = 0
    dead_lettered: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class ClaimedEvent:
    id: str
    task_name: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    replayed: int = 0
    skipped: int = 0


def enqueue_task(
    *,
    task_name: str,
    aggregate_id,
    payload: dict[str, Any],
    deduplication_key: str,
) -> OutboxEvent:
    """Create the durable message in the caller's business transaction."""
    event, _ = OutboxEvent.objects.get_or_create(
        deduplication_key=deduplication_key,
        defaults={
            "task_name": task_name,
            "aggregate_id": aggregate_id,
            "payload": payload,
            "available_at": timezone.now(),
        },
    )
    return event


def publish_event_after_commit(event_id) -> PublishResult:
    """Publish now in autocommit mode, or only after the current transaction commits."""
    connection = transaction.get_connection()
    if connection.in_atomic_block:
        transaction.on_commit(lambda: publish_event(event_id))
        return PublishResult(event_id=str(event_id), outcome="scheduled")
    return publish_event(event_id)


def publish_pending_events(*, limit: int = 100) -> PublishSummary:
    if limit < 1:
        return PublishSummary()

    now = timezone.now()
    event_ids = list(
        OutboxEvent.objects.filter(_dispatchable(now))
        .order_by("available_at", "created_at")
        .values_list("pk", flat=True)[:limit]
    )
    published = retry_scheduled = dead_lettered = skipped = 0
    for event_id in event_ids:
        result = publish_event(event_id)
        if result.outcome == "published":
            published += 1
        elif result.outcome == "retry_scheduled":
            retry_scheduled += 1
        elif result.outcome == "dead_lettered":
            dead_lettered += 1
        else:
            skipped += 1
    return PublishSummary(
        published=published,
        retry_scheduled=retry_scheduled,
        dead_lettered=dead_lettered,
        skipped=skipped,
    )


def purge_published_events() -> int:
    """Retain delivery history briefly without growing the outbox forever."""
    cutoff = timezone.now() - timedelta(days=settings.OUTBOX_PUBLISHED_RETENTION_DAYS)
    deleted, _ = OutboxEvent.objects.filter(
        status=OutboxEventStatus.PUBLISHED,
        published_at__lt=cutoff,
    ).delete()
    return deleted


def replay_dead_letter_events(*, event_ids=None, limit: int = 100) -> ReplaySummary:
    """Return selected dead-letter events to the durable pending queue."""
    if limit < 1:
        return ReplaySummary()

    queryset = OutboxEvent.objects.filter(status=OutboxEventStatus.DEAD_LETTER)
    if event_ids is not None:
        queryset = queryset.filter(pk__in=event_ids)
    selected_event_ids = list(
        queryset.order_by("dead_lettered_at", "created_at").values_list("pk", flat=True)[:limit]
    )
    replayed = sum(
        1 for event_id in selected_event_ids if _requeue_dead_letter(event_id)
    )
    return ReplaySummary(replayed=replayed, skipped=len(selected_event_ids) - replayed)


def publish_event(event_id) -> PublishResult:
    claimed = _claim_event(event_id)
    if claimed is None:
        return PublishResult(event_id=str(event_id), outcome="skipped")

    try:
        # Reusing the outbox ID as the broker message ID aids traceability.
        # A process crash after this call can cause another delivery when its
        # lease expires, which is safe because consumers are idempotent.
        current_app.send_task(
            claimed.task_name,
            kwargs=claimed.payload,
            task_id=claimed.id,
        )
    except Exception as exc:  # Broker and network errors must remain recoverable.
        outcome = _release_for_retry(event_id, str(exc))
        logger.warning("Outbox event %s publish failed: %s", event_id, exc)
        return PublishResult(event_id=str(event_id), outcome=outcome)

    _mark_published(event_id)
    return PublishResult(event_id=str(event_id), outcome="published")


@transaction.atomic
def _claim_event(event_id) -> ClaimedEvent | None:
    now = timezone.now()
    event = (
        OutboxEvent.objects.select_for_update(skip_locked=True)
        .filter(pk=event_id)
        .filter(_dispatchable(now))
        .first()
    )
    if event is None:
        return None

    event.status = OutboxEventStatus.PUBLISHING
    event.attempt_count += 1
    event.locked_until = now + timedelta(seconds=settings.OUTBOX_LEASE_SECONDS)
    event.last_error = ""
    event.save(
        update_fields=[
            "status",
            "attempt_count",
            "locked_until",
            "last_error",
            "updated_at",
        ]
    )
    return ClaimedEvent(
        id=str(event.pk),
        task_name=event.task_name,
        payload=dict(event.payload),
    )


@transaction.atomic
def _release_for_retry(event_id, message: str) -> str:
    event = OutboxEvent.objects.select_for_update().get(pk=event_id)
    if event.status != OutboxEventStatus.PUBLISHING:
        return "skipped"
    now = timezone.now()
    event.locked_until = None
    event.last_error = message[:4000]
    if event.attempt_count >= settings.OUTBOX_MAX_ATTEMPTS:
        event.status = OutboxEventStatus.DEAD_LETTER
        event.dead_lettered_at = now
        event.save(
            update_fields=[
                "status",
                "locked_until",
                "last_error",
                "dead_lettered_at",
                "updated_at",
            ]
        )
        logger.error(
            "Outbox event %s moved to dead letter after %s attempts",
            event_id,
            event.attempt_count,
        )
        return "dead_lettered"

    event.status = OutboxEventStatus.PENDING
    event.available_at = now + timedelta(seconds=_retry_delay(event.attempt_count))
    event.save(
        update_fields=[
            "status",
            "available_at",
            "locked_until",
            "last_error",
            "updated_at",
        ]
    )
    return "retry_scheduled"


@transaction.atomic
def _mark_published(event_id) -> None:
    event = OutboxEvent.objects.select_for_update().get(pk=event_id)
    if event.status != OutboxEventStatus.PUBLISHING:
        return
    now = timezone.now()
    event.status = OutboxEventStatus.PUBLISHED
    event.published_at = now
    event.locked_until = None
    event.last_error = ""
    event.save(
        update_fields=[
            "status",
            "published_at",
            "locked_until",
            "last_error",
            "updated_at",
        ]
    )


@transaction.atomic
def _requeue_dead_letter(event_id) -> bool:
    event = OutboxEvent.objects.select_for_update().filter(pk=event_id).first()
    if event is None or event.status != OutboxEventStatus.DEAD_LETTER:
        return False

    event.status = OutboxEventStatus.PENDING
    event.available_at = timezone.now()
    event.locked_until = None
    event.last_error = ""
    event.dead_lettered_at = None
    event.replay_count += 1
    event.save(
        update_fields=[
            "status",
            "available_at",
            "locked_until",
            "last_error",
            "dead_lettered_at",
            "replay_count",
            "updated_at",
        ]
    )
    return True


def _dispatchable(now) -> Q:
    return Q(
        status=OutboxEventStatus.PENDING,
        available_at__lte=now,
    ) | Q(
        status=OutboxEventStatus.PUBLISHING,
        locked_until__lte=now,
    )


def _retry_delay(attempt_count: int) -> int:
    exponent = min(max(attempt_count - 1, 0), 10)
    return min(
        settings.OUTBOX_RETRY_MAX_SECONDS,
        settings.OUTBOX_RETRY_INITIAL_SECONDS * (2**exponent),
    )
