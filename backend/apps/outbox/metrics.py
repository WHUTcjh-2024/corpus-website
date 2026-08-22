from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count, Min, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import OutboxEvent, OutboxEventStatus
from apps.agent.models import AgentRun, AgentRunStatus
from apps.audits.models import ParallelAudit, ParallelAuditStatus
from apps.rag.models import RagIndex, RagIndexStatus


@dataclass(frozen=True, slots=True)
class OutboxMetrics:
    event_counts: dict[str, int]
    oldest_pending_age_seconds: float
    agent_run_counts: dict[str, int]
    parallel_audit_counts: dict[str, int]
    rag_index_counts: dict[str, int]
    oldest_agent_external_wait_age_seconds: float
    oldest_parallel_audit_age_seconds: float
    oldest_rag_index_age_seconds: float
    model_fallback_run_count: int
    estimated_model_cost_usd: float


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
        rag_index_counts=_rag_index_counts(),
        oldest_agent_external_wait_age_seconds=_oldest_agent_external_wait_age_seconds(),
        oldest_parallel_audit_age_seconds=_oldest_parallel_audit_age_seconds(),
        oldest_rag_index_age_seconds=_oldest_rag_index_age_seconds(),
        model_fallback_run_count=_model_fallback_run_count(),
        estimated_model_cost_usd=float(
            AgentRun.objects.aggregate(total=Sum("estimated_cost_usd"))["total"] or 0
        ),
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


def _rag_index_counts() -> dict[str, int]:
    counts = dict(
        RagIndex.objects.values("status")
        .annotate(total=Count("id"))
        .values_list("status", "total")
    )
    return {status: counts.get(status, 0) for status in RagIndexStatus.values}


def _oldest_agent_external_wait_age_seconds() -> float:
    oldest = AgentRun.objects.filter(status=AgentRunStatus.WAITING_EXTERNAL).aggregate(
        oldest=Min(Coalesce("external_wait_started_at", "created_at"))
    )["oldest"]
    return _age_seconds(oldest)


def _oldest_parallel_audit_age_seconds() -> float:
    oldest = ParallelAudit.objects.filter(
        status__in=(ParallelAuditStatus.PENDING, ParallelAuditStatus.RUNNING)
    ).aggregate(oldest=Min(Coalesce("started_at", "created_at")))["oldest"]
    return _age_seconds(oldest)


def _oldest_rag_index_age_seconds() -> float:
    oldest = RagIndex.objects.filter(
        status__in=(RagIndexStatus.PENDING, RagIndexStatus.RUNNING)
    ).aggregate(oldest=Min(Coalesce("started_at", "created_at")))["oldest"]
    return _age_seconds(oldest)


def _age_seconds(value) -> float:
    if value is None:
        return 0.0
    return max((timezone.now() - value).total_seconds(), 0.0)


def _model_fallback_run_count() -> int:
    return AgentRun.objects.filter(model_usage__fallback=True).count()


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
            "# HELP corpus_agent_external_wait_oldest_age_seconds Age of the oldest Agent run waiting on an external result.",
            "# TYPE corpus_agent_external_wait_oldest_age_seconds gauge",
            f"corpus_agent_external_wait_oldest_age_seconds {snapshot.oldest_agent_external_wait_age_seconds:.6f}",
            "# HELP corpus_agent_model_fallback_runs Number of Agent runs that completed with deterministic model fallback.",
            "# TYPE corpus_agent_model_fallback_runs gauge",
            f"corpus_agent_model_fallback_runs {snapshot.model_fallback_run_count}",
            "# HELP corpus_agent_estimated_model_cost_usd Persisted estimated model cost across Agent runs.",
            "# TYPE corpus_agent_estimated_model_cost_usd gauge",
            f"corpus_agent_estimated_model_cost_usd {snapshot.estimated_model_cost_usd:.8f}",
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
    lines.extend(
        [
            "# HELP corpus_rag_indexes Number of RAG index manifests by state.",
            "# TYPE corpus_rag_indexes gauge",
        ]
    )
    for status in RagIndexStatus.values:
        lines.append(f'corpus_rag_indexes{{status="{status}"}} {snapshot.rag_index_counts[status]}')
    lines.extend(
        [
            "# HELP corpus_rag_index_oldest_active_age_seconds Age of the oldest pending or running RAG index.",
            "# TYPE corpus_rag_index_oldest_active_age_seconds gauge",
            f"corpus_rag_index_oldest_active_age_seconds {snapshot.oldest_rag_index_age_seconds:.6f}",
        ]
    )
    return "\n".join(lines) + "\n"
