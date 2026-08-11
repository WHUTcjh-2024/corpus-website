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
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class ClaimedEvent:
    id: str
    task_name: str
    payload: dict[str, Any]


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
    published = retry_scheduled = skipped = 0
    for event_id in event_ids:
        result = publish_event(event_id)
        if result.outcome == "published":
            published += 1
        elif result.outcome == "retry_scheduled":
            retry_scheduled += 1
        else:
            skipped += 1
    return PublishSummary(
        published=published,
        retry_scheduled=retry_scheduled,
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
        _release_for_retry(event_id, str(exc))
        logger.warning("Outbox event %s publish failed: %s", event_id, exc)
        return PublishResult(event_id=str(event_id), outcome="retry_scheduled")

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
def _release_for_retry(event_id, message: str) -> None:
    event = OutboxEvent.objects.select_for_update().get(pk=event_id)
    if event.status != OutboxEventStatus.PUBLISHING:
        return
    event.status = OutboxEventStatus.PENDING
    event.available_at = timezone.now() + timedelta(seconds=_retry_delay(event.attempt_count))
    event.locked_until = None
    event.last_error = message[:4000]
    event.save(
        update_fields=[
            "status",
            "available_at",
            "locked_until",
            "last_error",
            "updated_at",
        ]
    )


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
