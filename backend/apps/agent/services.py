from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import timedelta
from time import perf_counter
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.models import AuditEventType
from apps.audit.services import record_audit_event
from apps.corpora.models import Corpus, CorpusSourceType, CorpusStatus
from apps.corpora.services import visible_corpora_for
from apps.exports.services import dispatch_export_job
from apps.outbox.models import OutboxTaskName
from apps.outbox.services import enqueue_task, publish_event_after_commit

from .errors import (
    AgentRunCancelled,
    AgentRunError,
    AgentRunNotReady,
    RetryableAgentRunError,
)
from .llm import summarize_grounded_evidence
from .lifecycle import (
    _claim_run,
    _current_run_outcome,
    _evidence_from_step,
    _expire_external_wait,
    _expire_pending_approval,
    _mark_current_step_failed,
    _mark_run_failed,
    _mark_run_success,
    _mark_step_running,
    _mark_step_success,
    _pause_for_approval,
    _raise_if_cancelled,
    _record_approval_requested,
    _record_completion,
    _resolved_step_input,
    _run_is_cancelled,
    _synchronize_parallel_audit_wait,
    advance_waiting_agent_runs_for_parallel_audit as _advance_waiting_agent_runs,
)
from .models import (
    AgentApproval,
    AgentApprovalStatus,
    AgentRun,
    AgentRunMode,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
)
from .policy import AgentPolicyError, plan_run, skill_from_plan
from .tools import (
    AgentToolError,
    CorpusToolRegistry,
    ToolContext,
    commit_export,
)


logger = logging.getLogger(__name__)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

__all__ = [
    "AgentRunCancelled",
    "AgentRunError",
    "AgentRunNotReady",
    "RetryableAgentRunError",
    "_pause_for_approval",
    "advance_waiting_agent_runs_for_parallel_audit",
    "approve_agent_action",
    "cancel_agent_run",
    "create_agent_run",
    "dispatch_agent_run",
    "execute_agent_run",
    "expire_external_waits",
    "expire_pending_approvals",
    "normalize_request_id",
    "request_fingerprint",
]


def normalize_request_id(value: str | None) -> str:
    candidate = (value or "").strip()
    return candidate if _REQUEST_ID_RE.fullmatch(candidate) else str(uuid4())


def request_fingerprint(*, corpus_id, mode: str, query: str, language: str | None, max_results: int) -> str:
    payload = {
        "corpus_id": str(corpus_id),
        "mode": mode,
        "query": " ".join(query.split()),
        "language": language or "",
        "max_results": max_results,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@transaction.atomic
def create_agent_run(
    *,
    user,
    corpus: Corpus,
    mode: str,
    query: str,
    language: str | None,
    max_results: int,
    idempotency_key: str,
    request_id: str | None = None,
    request=None,
) -> tuple[AgentRun, bool]:
    """Persist a plan and durable command in one transaction.

    Repeated POSTs with the same idempotency key return the original run only
    when their semantic request fingerprint matches exactly.
    """

    if not 1 <= len(idempotency_key.strip()) <= 128:
        raise ValidationError("Idempotency-Key must contain 1 to 128 characters.")
    locked_user = get_user_model().objects.select_for_update().get(pk=user.pk)
    locked_corpus = Corpus.objects.select_for_update().get(pk=corpus.pk)
    if not visible_corpora_for(locked_user).filter(pk=locked_corpus.pk).exists():
        raise PermissionDenied("You are not allowed to access this corpus.")
    if locked_corpus.status != CorpusStatus.READY:
        raise AgentRunNotReady("The corpus must be processed before the Agent can use it.")
    if (
        mode == AgentRunMode.EXPORT
        and (
            locked_corpus.source_type != CorpusSourceType.USER
            or locked_corpus.owner_id != locked_user.pk
        )
    ):
        raise PermissionDenied("Only a personal corpus owned by the requester can be exported.")

    normalized_query = " ".join(query.split())
    fingerprint = request_fingerprint(
        corpus_id=locked_corpus.pk,
        mode=mode,
        query=normalized_query,
        language=language,
        max_results=max_results,
    )
    existing = AgentRun.objects.filter(
        requested_by=locked_user, idempotency_key=idempotency_key.strip()
    ).first()
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise ValidationError("Idempotency-Key was already used with a different request.")
        return existing, False
    recent_run_count = AgentRun.objects.filter(
        requested_by=locked_user,
        created_at__gte=timezone.now() - timedelta(hours=1),
    ).count()
    if recent_run_count >= settings.AGENT_MAX_RUNS_PER_HOUR:
        raise ValidationError("Agent requests are too frequent; please retry later.")

    try:
        plan = plan_run(
            corpus=locked_corpus,
            mode=mode,
            query=normalized_query,
            language=language,
            max_results=max_results,
        )
    except AgentPolicyError as exc:
        raise ValidationError(str(exc)) from exc
    resolved_request_id = normalize_request_id(request_id)
    try:
        # Keep the uniqueness race inside a savepoint.  The enclosing business
        # transaction remains usable to fetch the winner after a concurrent
        # request hits the database constraint.
        with transaction.atomic():
            run = AgentRun.objects.create(
                requested_by=locked_user,
                corpus=locked_corpus,
                mode=mode,
                skill=str(plan["skill"]),
                idempotency_key=idempotency_key.strip(),
                request_id=resolved_request_id,
                request_fingerprint=fingerprint,
                plan=plan,
            )
    except IntegrityError:
        # A concurrent duplicate is resolved through the unique constraint.
        existing = AgentRun.objects.get(
            requested_by=locked_user, idempotency_key=idempotency_key.strip()
        )
        if existing.request_fingerprint != fingerprint:
            raise ValidationError("Idempotency-Key was already used with a different request.")
        return existing, False

    for sequence, specification in enumerate(plan["steps"], start=1):
        AgentStep.objects.create(
            run=run,
            sequence=sequence,
            node=str(specification["node"]),
            tool_name=str(specification["tool"]),
            input=dict(specification["input"]),
        )
    _enqueue_run(run)
    record_audit_event(
        AuditEventType.AGENT_RUN_CREATED,
        request=request,
        actor=locked_user,
        corpus=locked_corpus,
        metadata={
            "run_id": str(run.pk),
            "request_id": resolved_request_id,
            "mode": mode,
            "skill": run.skill,
            "request_fingerprint": fingerprint,
        },
    )
    return run, True


def dispatch_agent_run(run: AgentRun):
    event = _enqueue_run(run)
    return publish_event_after_commit(event.pk)


def _enqueue_run(run: AgentRun):
    return enqueue_task(
        task_name=OutboxTaskName.RUN_CORPUS_AGENT,
        aggregate_id=run.pk,
        payload={"run_id": str(run.pk)},
        deduplication_key=f"agent-run:{run.pk}",
    )


def _enqueue_resumed_run(*, run: AgentRun, audit_id):
    """Create exactly one durable continuation command for an audit result."""
    return enqueue_task(
        task_name=OutboxTaskName.RESUME_CORPUS_AGENT,
        aggregate_id=run.pk,
        payload={"run_id": str(run.pk)},
        deduplication_key=f"agent-resume:{run.pk}:parallel-audit:{audit_id}",
    )


def execute_agent_run(run_id: str) -> dict[str, Any]:
    run = _claim_run(run_id)
    if run is None:
        return {"run_id": str(run_id), "status": "skipped"}

    started = perf_counter()
    registry = CorpusToolRegistry()
    evidence: list[dict[str, Any]] = []
    try:
        skill = skill_from_plan(run.plan)
        context = ToolContext(user=run.requested_by, corpus=run.corpus, skill=skill)
        for step in run.steps.order_by("sequence"):
            _raise_if_cancelled(run.pk)
            if step.status == AgentStepStatus.SUCCEEDED:
                evidence.extend(_evidence_from_step(step))
                if step.tool_name == "prepare_export":
                    approval, approval_created = _pause_for_approval(
                        run_id=run.pk,
                        payload=step.output,
                    )
                    if approval is None:
                        return {"run_id": str(run.pk), "status": AgentRunStatus.CANCELLED}
                    if approval_created:
                        _record_approval_requested(run=run, approval=approval)
                    return {
                        "run_id": str(run.pk),
                        "status": AgentRunStatus.WAITING_APPROVAL,
                        "approval_id": str(approval.pk),
                    }
                if step.tool_name == "request_quality_audit":
                    if _synchronize_parallel_audit_wait(run_id=run.pk, step_id=step.pk) == "waiting":
                        return {
                            "run_id": str(run.pk),
                            "status": AgentRunStatus.WAITING_EXTERNAL,
                            "audit_id": str(step.output["audit_id"]),
                        }
                continue
            _mark_step_running(step.pk)
            if step.tool_name == "prepare_export":
                prepared = registry.execute(
                    context=context,
                    tool_name=step.tool_name,
                    input=_resolved_step_input(run_id=run.pk, step=step),
                )
                _mark_step_success(step.pk, output=prepared.output)
                approval, approval_created = _pause_for_approval(
                    run_id=run.pk,
                    payload=prepared.output,
                )
                if approval is None:
                    return {"run_id": str(run.pk), "status": AgentRunStatus.CANCELLED}
                if approval_created:
                    _record_approval_requested(run=run, approval=approval)
                return {
                    "run_id": str(run.pk),
                    "status": AgentRunStatus.WAITING_APPROVAL,
                    "approval_id": str(approval.pk),
                }
            result = registry.execute(
                context=context,
                tool_name=step.tool_name,
                input=_resolved_step_input(run_id=run.pk, step=step),
            )
            evidence.extend(result.evidence)
            _mark_step_success(step.pk, output=result.output)
            if step.tool_name == "request_quality_audit":
                if _synchronize_parallel_audit_wait(run_id=run.pk, step_id=step.pk) == "waiting":
                    return {
                        "run_id": str(run.pk),
                        "status": AgentRunStatus.WAITING_EXTERNAL,
                        "audit_id": str(result.output["audit_id"]),
                    }

        summary = summarize_grounded_evidence(mode=run.mode, evidence=evidence)
        completed = _mark_run_success(
            run.pk,
            answer=summary.answer,
            evidence=evidence,
            model_usage=summary.usage,
            estimated_cost_usd=summary.estimated_cost_usd,
        )
        if not completed:
            return _current_run_outcome(run.pk)
        _record_completion(run=run, latency_ms=(perf_counter() - started) * 1000)
        return {"run_id": str(run.pk), "status": AgentRunStatus.SUCCEEDED, "evidence_count": len(evidence)}
    except AgentRunCancelled:
        return {"run_id": str(run.pk), "status": AgentRunStatus.CANCELLED}
    except (AgentPolicyError, AgentToolError, PermissionDenied, ValidationError) as exc:
        if _run_is_cancelled(run.pk):
            return {"run_id": str(run.pk), "status": AgentRunStatus.CANCELLED}
        _mark_current_step_failed(run.pk, code=getattr(exc, "code", "AGENT_POLICY_ERROR"), message=str(exc))
        failed = _mark_run_failed(run.pk, code=getattr(exc, "code", "AGENT_RUN_FAILED"), message=str(exc))
        if not failed:
            return _current_run_outcome(run.pk)
        _record_completion(run=run, latency_ms=(perf_counter() - started) * 1000, failed=True)
        raise AgentRunError(str(exc)) from exc
    except Exception as exc:
        logger.exception("Agent run %s failed unexpectedly", run_id)
        if _run_is_cancelled(run.pk):
            return {"run_id": str(run.pk), "status": AgentRunStatus.CANCELLED}
        _mark_current_step_failed(run.pk, code="INTERNAL_ERROR", message=str(exc))
        failed = _mark_run_failed(run.pk, code="INTERNAL_ERROR", message=str(exc))
        if not failed:
            return _current_run_outcome(run.pk)
        _record_completion(run=run, latency_ms=(perf_counter() - started) * 1000, failed=True)
        raise AgentRunError("Agent run failed unexpectedly.") from exc


def approve_agent_action(*, run_id, user, request=None) -> AgentApproval:
    approval = _approve_agent_action(run_id=run_id, user=user, request=request)
    if approval is None:
        raise ValidationError("The approval window has expired.")
    return approval


@transaction.atomic
def _approve_agent_action(*, run_id, user, request=None) -> AgentApproval | None:
    run = (
        AgentRun.objects.select_for_update()
        .select_related("requested_by", "corpus")
        .get(pk=run_id)
    )
    if run.requested_by_id != user.pk:
        raise PermissionDenied("Only the user who created this Agent run can approve it.")
    if not visible_corpora_for(user).filter(pk=run.corpus_id).exists():
        raise PermissionDenied("You are no longer allowed to access this corpus.")
    if run.status != AgentRunStatus.WAITING_APPROVAL:
        raise ValidationError("This Agent run is not waiting for approval.")
    approval = AgentApproval.objects.select_for_update().get(run=run)
    now = timezone.now()
    if approval.status != AgentApprovalStatus.PENDING:
        raise ValidationError("This Agent action has already been resolved.")
    if approval.expires_at <= now:
        approval.status = AgentApprovalStatus.EXPIRED
        approval.resolved_at = now
        approval.save(update_fields=["status", "resolved_at", "updated_at"])
        run.status = AgentRunStatus.CANCELLED
        run.error_code = "APPROVAL_EXPIRED"
        run.error_message = "The approval window expired."
        run.finished_at = now
        run.save(update_fields=["status", "error_code", "error_message", "finished_at", "updated_at"])
        record_audit_event(
            AuditEventType.AGENT_APPROVAL_EXPIRED,
            request=request,
            actor=user,
            corpus=run.corpus,
            metadata={"run_id": str(run.pk), "approval_id": str(approval.pk)},
        )
        return None

    job = commit_export(user=user, corpus=run.corpus, payload=approval.payload, request=request)
    approval.status = AgentApprovalStatus.APPROVED
    approval.result = {"export_job_id": str(job.pk), "status": job.status}
    approval.resolved_at = now
    approval.save(update_fields=["status", "result", "resolved_at", "updated_at"])
    run.status = AgentRunStatus.SUCCEEDED
    run.answer = "Export request was approved and queued."
    run.evidence = [*run.evidence, {"citation_id": f"export:{job.pk}", "export_job_id": str(job.pk)}]
    run.finished_at = now
    run.save(update_fields=["status", "answer", "evidence", "finished_at", "updated_at"])
    dispatch_export_job(job)
    record_audit_event(
        AuditEventType.AGENT_APPROVAL_APPROVED,
        request=request,
        actor=user,
        corpus=run.corpus,
        metadata={"run_id": str(run.pk), "approval_id": str(approval.pk), "export_job_id": str(job.pk)},
    )
    return approval


@transaction.atomic
def cancel_agent_run(*, run_id, user, request=None) -> AgentRun:
    run = AgentRun.objects.select_for_update().select_related("corpus").get(pk=run_id)
    if run.requested_by_id != user.pk:
        raise PermissionDenied("Only the user who created this Agent run can cancel it.")
    if not visible_corpora_for(user).filter(pk=run.corpus_id).exists():
        raise PermissionDenied("You are no longer allowed to access this corpus.")
    if run.status in {AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}:
        return run
    run.status = AgentRunStatus.CANCELLED
    run.error_code = "CANCELLED_BY_USER"
    run.error_message = "Cancelled by the requester."
    run.locked_until = None
    run.external_wait_kind = ""
    run.external_wait_id = None
    run.external_wait_started_at = None
    run.external_wait_expires_at = None
    run.finished_at = timezone.now()
    run.save(
        update_fields=[
            "status", "error_code", "error_message", "locked_until", "external_wait_kind",
            "external_wait_id", "external_wait_started_at", "external_wait_expires_at", "finished_at", "updated_at",
        ]
    )
    AgentApproval.objects.filter(run=run, status=AgentApprovalStatus.PENDING).update(
        status=AgentApprovalStatus.REJECTED, resolved_at=timezone.now()
    )
    record_audit_event(
        AuditEventType.AGENT_RUN_CANCELLED,
        request=request,
        actor=user,
        corpus=run.corpus,
        metadata={"run_id": str(run.pk)},
    )
    return run


def expire_pending_approvals(*, limit: int | None = None) -> int:
    """Expire unconfirmed write proposals without ever executing them.

    The outbox service calls this periodically so abandoned approval records do
    not remain operationally indistinguishable from active user work.
    """

    batch_size = limit if limit is not None else settings.AGENT_APPROVAL_CLEANUP_BATCH_SIZE
    if batch_size < 1:
        return 0
    now = timezone.now()
    run_ids = list(
        AgentRun.objects.filter(
            status=AgentRunStatus.WAITING_APPROVAL,
            approval__status=AgentApprovalStatus.PENDING,
            approval__expires_at__lte=now,
        )
        .order_by("approval__expires_at")
        .values_list("pk", flat=True)[:batch_size]
    )
    return sum(1 for run_id in run_ids if _expire_pending_approval(run_id=run_id, now=now))


def expire_external_waits(*, limit: int | None = None) -> int:
    """Fail abandoned waits without reissuing the independently durable audit."""
    batch_size = limit if limit is not None else settings.AGENT_EXTERNAL_WAIT_CLEANUP_BATCH_SIZE
    if batch_size < 1:
        return 0
    now = timezone.now()
    run_ids = list(
        AgentRun.objects.filter(
            status=AgentRunStatus.WAITING_EXTERNAL,
            external_wait_expires_at__lte=now,
        )
        .order_by("external_wait_expires_at")
        .values_list("pk", flat=True)[:batch_size]
    )
    return sum(1 for run_id in run_ids if _expire_external_wait(run_id=run_id, now=now))


def advance_waiting_agent_runs_for_parallel_audit(*, audit) -> int:
    return _advance_waiting_agent_runs(
        audit=audit,
        enqueue_resumed_run=_enqueue_resumed_run,
        publish_event=publish_event_after_commit,
    )
