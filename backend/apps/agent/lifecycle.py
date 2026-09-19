from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.models import AuditEventType
from apps.audit.services import record_audit_event

from .errors import AgentRunCancelled
from .models import (
    AgentApproval,
    AgentApprovalAction,
    AgentApprovalStatus,
    AgentExternalWaitKind,
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
)
from .tools import AgentToolError


logger = logging.getLogger(__name__)


@transaction.atomic
def _claim_run(run_id: str) -> AgentRun | None:
    now = timezone.now()
    run = (
        AgentRun.objects.select_for_update()
        .select_related("requested_by", "corpus")
        .filter(pk=run_id)
        .filter(
            Q(status=AgentRunStatus.PENDING)
            | Q(status=AgentRunStatus.RUNNING, locked_until__lte=now)
        )
        .first()
    )
    if run is None:
        return None
    run.status = AgentRunStatus.RUNNING
    run.locked_until = now + timedelta(seconds=settings.AGENT_RUN_LEASE_SECONDS)
    run.attempt_count += 1
    if run.started_at is None:
        run.started_at = now
    run.error_code = ""
    run.error_message = ""
    run.save(
        update_fields=[
            "status", "locked_until", "attempt_count", "started_at", "error_code", "error_message", "updated_at"
        ]
    )
    return run


@transaction.atomic
def _mark_step_running(step_id) -> None:
    step = AgentStep.objects.select_for_update().get(pk=step_id)
    if step.status == AgentStepStatus.SUCCEEDED:
        return
    step.status = AgentStepStatus.RUNNING
    step.attempt_count += 1
    step.error_code = ""
    step.error_message = ""
    step.started_at = timezone.now()
    step.finished_at = None
    step.save(
        update_fields=["status", "attempt_count", "error_code", "error_message", "started_at", "finished_at", "updated_at"]
    )


@transaction.atomic
def _mark_step_success(step_id, *, output: dict[str, Any]) -> None:
    AgentStep.objects.filter(pk=step_id).update(
        status=AgentStepStatus.SUCCEEDED,
        output=output,
        error_code="",
        error_message="",
        finished_at=timezone.now(),
    )


def _resolved_step_input(*, run_id, step: AgentStep) -> dict[str, Any]:
    """Derive bounded cross-step input without allowing the model to mutate plans."""
    input = dict(step.input)
    if step.tool_name != "get_latest_quality_report" or "audit_id" in input:
        return input
    predecessor = (
        AgentStep.objects.filter(
            run_id=run_id,
            tool_name="request_quality_audit",
            status=AgentStepStatus.SUCCEEDED,
            sequence__lt=step.sequence,
        )
        .order_by("-sequence")
        .first()
    )
    if predecessor is None:
        return input
    audit_id = predecessor.output.get("audit_id")
    if audit_id:
        input["audit_id"] = str(audit_id)
    return input


@transaction.atomic
def _synchronize_parallel_audit_wait(*, run_id, step_id) -> str:
    """Atomically decide whether an audit step must park its Agent run.

    The audit is rechecked while holding the Agent lock because a Go result can
    be projected between tool execution and this transition.
    """
    from apps.audits.models import ParallelAudit, ParallelAuditStatus

    saved_step = AgentStep.objects.select_related("run").get(pk=step_id, run_id=run_id)
    output = dict(saved_step.output)
    audit_id = output.get("audit_id")
    if not audit_id:
        raise AgentToolError("Quality audit step did not return an audit ID.")
    try:
        audit = ParallelAudit.objects.select_for_update().get(
            pk=audit_id,
            corpus_id=saved_step.run.corpus_id,
        )
    except (ParallelAudit.DoesNotExist, ValueError) as exc:
        raise AgentToolError("Quality audit step refers to an invalid audit.") from exc
    run = AgentRun.objects.select_for_update().get(pk=run_id)
    step = AgentStep.objects.select_for_update().get(pk=step_id, run=run)

    if audit.status == ParallelAuditStatus.FAILED:
        step.status = AgentStepStatus.FAILED
        step.error_code = "EXTERNAL_AUDIT_FAILED"
        step.error_message = (audit.error_message or "The quality audit failed.")[:4000]
        step.finished_at = timezone.now()
        step.save(
            update_fields=["status", "error_code", "error_message", "finished_at", "updated_at"]
        )
        raise AgentToolError("The quality audit failed before the Agent could resume.")
    if audit.status == ParallelAuditStatus.SUCCESS:
        if output.get("await_external_result"):
            output["await_external_result"] = False
            output["status"] = audit.status
            step.output = output
            step.save(update_fields=["output", "updated_at"])
        return "ready"
    if run.status != AgentRunStatus.RUNNING:
        raise AgentRunCancelled("The Agent run is no longer active.")

    run.status = AgentRunStatus.WAITING_EXTERNAL
    run.locked_until = None
    run.external_wait_kind = AgentExternalWaitKind.PARALLEL_AUDIT
    run.external_wait_id = audit.pk
    run.external_wait_started_at = timezone.now()
    run.external_wait_expires_at = run.external_wait_started_at + timedelta(
        seconds=settings.AGENT_EXTERNAL_WAIT_TTL_SECONDS
    )
    run.save(
        update_fields=[
            "status", "locked_until", "external_wait_kind", "external_wait_id", "external_wait_started_at",
            "external_wait_expires_at", "updated_at",
        ]
    )
    _record_external_wait(run=run, audit_id=audit.pk)
    return "waiting"


@transaction.atomic
def _mark_current_step_failed(run_id, *, code: str, message: str) -> None:
    step = (
        AgentStep.objects.select_for_update()
        .filter(run_id=run_id, status=AgentStepStatus.RUNNING)
        .order_by("sequence")
        .first()
    )
    if step is not None:
        step.status = AgentStepStatus.FAILED
        step.error_code = code[:80]
        step.error_message = message[:4000]
        step.finished_at = timezone.now()
        step.save(update_fields=["status", "error_code", "error_message", "finished_at", "updated_at"])


@transaction.atomic
def _pause_for_approval(*, run_id, payload: dict[str, Any]) -> tuple[AgentApproval | None, bool]:
    """Atomically turn a running plan into one user-bound approval.

    The same run lock is also taken by cancellation and approval. This closes
    the race where a cancelled worker could otherwise create a stale approval
    after the requester had already cancelled the run.
    """

    run = AgentRun.objects.select_for_update().get(pk=run_id)
    if run.status != AgentRunStatus.RUNNING:
        return None, False
    approval, created = AgentApproval.objects.get_or_create(
        run=run,
        defaults={
            "action": AgentApprovalAction.CREATE_EXPORT,
            "payload": payload,
            "expires_at": timezone.now() + timedelta(seconds=settings.AGENT_APPROVAL_TTL_SECONDS),
        },
    )
    run.status = AgentRunStatus.WAITING_APPROVAL
    run.locked_until = None
    run.external_wait_kind = ""
    run.external_wait_id = None
    run.external_wait_started_at = None
    run.external_wait_expires_at = None
    run.save(
        update_fields=[
            "status", "locked_until", "external_wait_kind", "external_wait_id", "external_wait_started_at",
            "external_wait_expires_at", "updated_at",
        ]
    )
    return approval, created


@transaction.atomic
def _expire_pending_approval(*, run_id, now) -> bool:
    run = AgentRun.objects.select_for_update().select_related("corpus", "requested_by").get(pk=run_id)
    try:
        approval = AgentApproval.objects.select_for_update().get(run=run)
    except AgentApproval.DoesNotExist:
        return False
    if (
        run.status != AgentRunStatus.WAITING_APPROVAL
        or approval.status != AgentApprovalStatus.PENDING
        or approval.expires_at > now
    ):
        return False
    approval.status = AgentApprovalStatus.EXPIRED
    approval.resolved_at = now
    approval.save(update_fields=["status", "resolved_at", "updated_at"])
    run.status = AgentRunStatus.CANCELLED
    run.error_code = "APPROVAL_EXPIRED"
    run.error_message = "The approval window expired."
    run.external_wait_kind = ""
    run.external_wait_id = None
    run.external_wait_started_at = None
    run.external_wait_expires_at = None
    run.finished_at = now
    run.save(
        update_fields=[
            "status",
            "error_code",
            "error_message",
            "external_wait_kind",
            "external_wait_id",
            "external_wait_started_at",
            "external_wait_expires_at",
            "finished_at",
            "updated_at",
        ]
    )
    record_audit_event(
        AuditEventType.AGENT_APPROVAL_EXPIRED,
        actor=run.requested_by,
        corpus=run.corpus,
        metadata={"run_id": str(run.pk), "approval_id": str(approval.pk)},
    )
    return True


@transaction.atomic
def _expire_external_wait(*, run_id, now) -> bool:
    run = (
        AgentRun.objects.select_for_update()
        .select_related("corpus", "requested_by")
        .filter(pk=run_id, status=AgentRunStatus.WAITING_EXTERNAL)
        .first()
    )
    if run is None or run.external_wait_expires_at is None or run.external_wait_expires_at > now:
        return False
    external_wait_id = run.external_wait_id
    step = (
        AgentStep.objects.select_for_update()
        .filter(run=run, tool_name="request_quality_audit", status=AgentStepStatus.SUCCEEDED)
        .order_by("-sequence")
        .first()
    )
    if step is not None:
        step.status = AgentStepStatus.FAILED
        step.error_code = "EXTERNAL_WAIT_TIMEOUT"
        step.error_message = "Timed out while waiting for the quality audit result."
        step.finished_at = now
        step.save(
            update_fields=["status", "error_code", "error_message", "finished_at", "updated_at"]
        )
    run.status = AgentRunStatus.FAILED
    run.error_code = "EXTERNAL_WAIT_TIMEOUT"
    run.error_message = "Timed out while waiting for the quality audit result."
    run.locked_until = None
    run.external_wait_kind = ""
    run.external_wait_id = None
    run.external_wait_started_at = None
    run.external_wait_expires_at = None
    run.finished_at = now
    run.save(
        update_fields=[
            "status", "error_code", "error_message", "locked_until", "external_wait_kind",
            "external_wait_id", "external_wait_started_at", "external_wait_expires_at", "finished_at", "updated_at",
        ]
    )
    record_audit_event(
        AuditEventType.AGENT_EXTERNAL_FAILED,
        actor=run.requested_by,
        corpus=run.corpus,
        metadata={
            "run_id": str(run.pk),
            "request_id": run.request_id,
            "audit_id": str(external_wait_id) if external_wait_id else "",
            "error_code": run.error_code,
        },
    )
    _record_completion(run=run, latency_ms=0, failed=True)
    return True


def _record_approval_requested(*, run: AgentRun, approval: AgentApproval) -> None:
    record_audit_event(
        AuditEventType.AGENT_APPROVAL_REQUESTED,
        actor=run.requested_by,
        corpus=run.corpus,
        metadata={
            "run_id": str(run.pk),
            "approval_id": str(approval.pk),
            "action": approval.action,
        },
    )


def _record_external_wait(*, run: AgentRun, audit_id) -> None:
    record_audit_event(
        AuditEventType.AGENT_EXTERNAL_WAITING,
        actor=run.requested_by,
        corpus=run.corpus,
        metadata={
            "run_id": str(run.pk),
            "request_id": run.request_id,
            "wait_kind": AgentExternalWaitKind.PARALLEL_AUDIT,
            "audit_id": str(audit_id),
        },
    )
    logger.info(
        "Agent run %s is waiting for parallel audit %s (request_id=%s)",
        run.pk,
        audit_id,
        run.request_id,
    )


@transaction.atomic
def advance_waiting_agent_runs_for_parallel_audit(
    *, audit, enqueue_resumed_run, publish_event
) -> int:
    """Resume or fail Agent runs correlated with one projected terminal audit.

    The caller invokes this from the same transaction that writes the terminal
    audit. The state transition and the continuation Outbox event therefore
    commit atomically; broker loss is handled by normal Outbox recovery.
    """
    from apps.audits.models import ParallelAuditStatus

    if audit.status not in {ParallelAuditStatus.SUCCESS, ParallelAuditStatus.FAILED}:
        return 0
    runs = list(
        AgentRun.objects.select_for_update()
        .select_related("corpus", "requested_by")
        .filter(
            status=AgentRunStatus.WAITING_EXTERNAL,
            external_wait_kind=AgentExternalWaitKind.PARALLEL_AUDIT,
            external_wait_id=audit.pk,
        )
        .order_by("created_at")
    )
    if audit.status == ParallelAuditStatus.FAILED:
        for run in runs:
            _fail_parallel_audit_wait(run=run, audit=audit)
        return len(runs)

    for run in runs:
        try:
            _mark_parallel_audit_step_resumed(run=run, audit=audit)
        except AgentToolError as exc:
            _fail_parallel_audit_wait(run=run, audit=audit, reason=str(exc))
            continue
        run.status = AgentRunStatus.PENDING
        run.locked_until = None
        run.external_wait_kind = ""
        run.external_wait_id = None
        run.external_wait_started_at = None
        run.external_wait_expires_at = None
        run.save(
            update_fields=[
                "status", "locked_until", "external_wait_kind", "external_wait_id", "external_wait_started_at",
                "external_wait_expires_at", "updated_at",
            ]
        )
        event = enqueue_resumed_run(run=run, audit_id=audit.pk)
        publish_event(event.pk)
        record_audit_event(
            AuditEventType.AGENT_EXTERNAL_RESUMED,
            actor=run.requested_by,
            corpus=run.corpus,
            metadata={
                "run_id": str(run.pk),
                "request_id": run.request_id,
                "audit_id": str(audit.pk),
                "outbox_event_id": str(event.pk),
            },
        )
        logger.info(
            "Resuming Agent run %s from parallel audit %s (request_id=%s)",
            run.pk,
            audit.pk,
            run.request_id,
        )
    return len(runs)


def _mark_parallel_audit_step_resumed(*, run: AgentRun, audit) -> None:
    step = (
        AgentStep.objects.select_for_update()
        .filter(run=run, tool_name="request_quality_audit", status=AgentStepStatus.SUCCEEDED)
        .order_by("-sequence")
        .first()
    )
    if step is None:
        raise AgentToolError("Waiting Agent run has no completed quality audit step.")
    output = dict(step.output)
    if str(output.get("audit_id", "")) != str(audit.pk):
        raise AgentToolError("Waiting Agent run is correlated with a different quality audit.")
    output["await_external_result"] = False
    output["status"] = audit.status
    output["worker_state"] = audit.worker_state
    step.output = output
    step.save(update_fields=["output", "updated_at"])


def _fail_parallel_audit_wait(*, run: AgentRun, audit, reason: str | None = None) -> None:
    now = timezone.now()
    step = (
        AgentStep.objects.select_for_update()
        .filter(run=run, tool_name="request_quality_audit", status=AgentStepStatus.SUCCEEDED)
        .order_by("-sequence")
        .first()
    )
    if step is not None:
        step.status = AgentStepStatus.FAILED
        step.error_code = "EXTERNAL_AUDIT_FAILED" if audit.error_message else "EXTERNAL_AUDIT_CORRELATION_ERROR"
        step.error_message = (reason or audit.error_message or "The quality audit failed.")[:4000]
        step.finished_at = now
        step.save(
            update_fields=["status", "error_code", "error_message", "finished_at", "updated_at"]
        )
    run.status = AgentRunStatus.FAILED
    run.error_code = "EXTERNAL_AUDIT_FAILED" if audit.error_message else "EXTERNAL_AUDIT_CORRELATION_ERROR"
    run.error_message = (reason or audit.error_message or "The quality audit failed.")[:4000]
    run.locked_until = None
    run.external_wait_kind = ""
    run.external_wait_id = None
    run.external_wait_started_at = None
    run.external_wait_expires_at = None
    run.finished_at = now
    run.save(
        update_fields=[
            "status", "error_code", "error_message", "locked_until", "external_wait_kind",
            "external_wait_id", "external_wait_started_at", "external_wait_expires_at", "finished_at", "updated_at",
        ]
    )
    record_audit_event(
        AuditEventType.AGENT_EXTERNAL_FAILED,
        actor=run.requested_by,
        corpus=run.corpus,
        metadata={
            "run_id": str(run.pk),
            "request_id": run.request_id,
            "audit_id": str(audit.pk),
            "error_code": run.error_code,
        },
    )
    _record_completion(run=run, latency_ms=0, failed=True)


@transaction.atomic
def _mark_run_success(
    run_id,
    *,
    answer: str,
    evidence: list[dict[str, Any]],
    model_usage: dict[str, Any],
    estimated_cost_usd: float,
) -> bool:
    updated = AgentRun.objects.filter(pk=run_id, status=AgentRunStatus.RUNNING).update(
        status=AgentRunStatus.SUCCEEDED,
        answer=answer,
        evidence=evidence,
        model_usage=model_usage,
        estimated_cost_usd=estimated_cost_usd,
        locked_until=None,
        external_wait_kind="",
        external_wait_id=None,
        external_wait_started_at=None,
        external_wait_expires_at=None,
        finished_at=timezone.now(),
        error_code="",
        error_message="",
        updated_at=timezone.now(),
    )
    return bool(updated)


@transaction.atomic
def _mark_run_failed(run_id, *, code: str, message: str) -> bool:
    updated = AgentRun.objects.filter(pk=run_id, status=AgentRunStatus.RUNNING).update(
        status=AgentRunStatus.FAILED,
        error_code=code[:80],
        error_message=message[:4000],
        locked_until=None,
        external_wait_kind="",
        external_wait_id=None,
        external_wait_started_at=None,
        external_wait_expires_at=None,
        finished_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return bool(updated)


def _raise_if_cancelled(run_id) -> None:
    if _run_is_cancelled(run_id):
        raise AgentRunCancelled("The Agent run was cancelled.")


def _run_is_cancelled(run_id) -> bool:
    return AgentRun.objects.filter(pk=run_id, status=AgentRunStatus.CANCELLED).exists()


def _current_run_outcome(run_id) -> dict[str, Any]:
    status = AgentRun.objects.only("status").get(pk=run_id).status
    return {"run_id": str(run_id), "status": status}


def _evidence_from_step(step: AgentStep) -> list[dict[str, Any]]:
    """Reconstruct bounded evidence if a redelivered run resumes mid-plan."""
    if step.tool_name in {"search_kwic", "search_parallel"}:
        hits = step.output.get("hits", [])
        return [item for item in hits if isinstance(item, dict) and "citation_id" in item]
    if step.tool_name == "get_latest_quality_report":
        audit_id = step.output.get("audit_id")
        summary = step.output.get("summary")
        if audit_id and isinstance(summary, dict):
            return [{"citation_id": f"audit:{audit_id}", "audit_id": str(audit_id), "summary": summary}]
    if step.tool_name == "request_quality_audit":
        audit_id = step.output.get("audit_id")
        if audit_id:
            return [{
                "citation_id": f"audit-request:{audit_id}",
                "audit_id": str(audit_id),
                "status": str(step.output.get("status", "pending")),
            }]
    return []


def _record_completion(*, run: AgentRun, latency_ms: float, failed: bool = False) -> None:
    final_run = AgentRun.objects.only("attempt_count", "status").get(pk=run.pk)
    record_audit_event(
        AuditEventType.AGENT_RUN_FAILED if failed else AuditEventType.AGENT_RUN_COMPLETED,
        actor=run.requested_by,
        corpus=run.corpus,
        metadata={
            "run_id": str(run.pk),
            "request_id": run.request_id,
            "skill": run.skill,
            "latency_ms": round(latency_ms, 3),
            "attempt_count": final_run.attempt_count,
            "status": final_run.status,
        },
    )
