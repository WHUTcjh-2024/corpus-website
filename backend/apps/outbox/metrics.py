from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count, Min
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import OutboxEvent, OutboxEventStatus
from apps.audits.models import ParallelAudit, ParallelAuditStatus


@dataclass(frozen=True, slots=True)
class OutboxMetrics:
    event_counts: dict[str, int]
    oldest_pending_age_seconds: float
    parallel_audit_counts: dict[str, int]
    oldest_parallel_audit_age_seconds: float


def collect_outbox_metrics() -> OutboxMetrics:
    """Collect a small, bounded operational snapshot for a Prometheus scrape."""
    counts = dict(
        OutboxEvent.objects.values("status")
        .annotate(total=Count("id"))
        .values_list("status", "total")
    )
    oldest_pending_at = OutboxEvent.objects.filter(
        status=OutboxEventStatus.PENDING
    ).aggregate(oldest=Min("created_at"))["oldest"]
    oldest_pending_age_seconds = 0.0
    if oldest_pending_at is not None:
        oldest_pending_age_seconds = max(
            (timezone.now() - oldest_pending_at).total_seconds(),
            0.0,
        )
    return OutboxMetrics(
        event_counts={status: counts.get(status, 0) for status in OutboxEventStatus.values},
        oldest_pending_age_seconds=oldest_pending_age_seconds,
        parallel_audit_counts=_parallel_audit_counts(),
        oldest_parallel_audit_age_seconds=_oldest_parallel_audit_age_seconds(),
    )


def _parallel_audit_counts() -> dict[str, int]:
    counts = dict(
        ParallelAudit.objects.values("status")
        .annotate(total=Count("id"))
        .values_list("status", "total")
    )
    return {status: counts.get(status, 0) for status in ParallelAuditStatus.values}


def _oldest_parallel_audit_age_seconds() -> float:
    oldest = ParallelAudit.objects.filter(
        status__in=(ParallelAuditStatus.PENDING, ParallelAuditStatus.RUNNING)
    ).aggregate(oldest=Min(Coalesce("started_at", "created_at")))["oldest"]
    return _age_seconds(oldest)


def _age_seconds(value) -> float:
    if value is None:
        return 0.0
    return max((timezone.now() - value).total_seconds(), 0.0)


def render_prometheus_metrics(snapshot: OutboxMetrics) -> str:
    lines = [
        "# HELP corpus_outbox_events Number of durable outbox events by status.",
        "# TYPE corpus_outbox_events gauge",
    ]
    for status in OutboxEventStatus.values:
        lines.append(f'corpus_outbox_events{{status="{status}"}} {snapshot.event_counts[status]}')
    lines.extend(
        [
            "# HELP corpus_outbox_oldest_pending_age_seconds Age of the oldest pending event.",
            "# TYPE corpus_outbox_oldest_pending_age_seconds gauge",
            f"corpus_outbox_oldest_pending_age_seconds {snapshot.oldest_pending_age_seconds:.6f}",
        ]
    )
    lines.extend(
        [
            "# HELP corpus_parallel_audits Number of parallel audit jobs by state.",
            "# TYPE corpus_parallel_audits gauge",
        ]
    )
    for status in ParallelAuditStatus.values:
        lines.append(
            f'corpus_parallel_audits{{status="{status}"}} {snapshot.parallel_audit_counts[status]}'
        )
    lines.extend(
        [
            "# HELP corpus_parallel_audit_oldest_active_age_seconds Age of the oldest pending or running parallel audit.",
            "# TYPE corpus_parallel_audit_oldest_active_age_seconds gauge",
            f"corpus_parallel_audit_oldest_active_age_seconds {snapshot.oldest_parallel_audit_age_seconds:.6f}",
        ]
    )
    return "\n".join(lines) + "\n"
