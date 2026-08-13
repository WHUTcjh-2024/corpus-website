from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count, Min
from django.utils import timezone

from .models import OutboxEvent, OutboxEventStatus
from apps.agent.models import AgentRun, AgentRunStatus
from apps.audits.models import ParallelAudit, ParallelAuditStatus


@dataclass(frozen=True, slots=True)
class OutboxMetrics:
    event_counts: dict[str, int]
    oldest_pending_age_seconds: float
    agent_run_counts: dict[str, int]
    parallel_audit_counts: dict[str, int]


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
        agent_run_counts=_agent_run_counts(),
        parallel_audit_counts=_parallel_audit_counts(),
    )


def _agent_run_counts() -> dict[str, int]:
    counts = dict(
        AgentRun.objects.values("status")
        .annotate(total=Count("id"))
        .values_list("status", "total")
    )
    return {status: counts.get(status, 0) for status in AgentRunStatus.values}


def _parallel_audit_counts() -> dict[str, int]:
    counts = dict(
        ParallelAudit.objects.values("status")
        .annotate(total=Count("id"))
        .values_list("status", "total")
    )
    return {status: counts.get(status, 0) for status in ParallelAuditStatus.values}


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
            "# HELP corpus_agent_runs Number of Agent runs by state.",
            "# TYPE corpus_agent_runs gauge",
        ]
    )
    for status in AgentRunStatus.values:
        lines.append(f'corpus_agent_runs{{status="{status}"}} {snapshot.agent_run_counts[status]}')
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
    return "\n".join(lines) + "\n"
