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
from django.db.models import Q
from django.utils import timezone

from apps.audit.models import AuditEventType
from apps.audit.services import record_audit_event
from apps.corpora.models import Corpus, CorpusSourceType, CorpusStatus
from apps.corpora.services import visible_corpora_for
from apps.exports.services import dispatch_export_job
from apps.outbox.models import OutboxTaskName
from apps.outbox.services import enqueue_task, publish_event_after_commit

from .llm import summarize_grounded_evidence
from .models import (
    AgentApproval,
    AgentApprovalAction,
    AgentApprovalStatus,
    AgentExternalWaitKind,
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


class AgentRunError(RuntimeError):
    code = "AGENT_RUN_FAILED"


class RetryableAgentRunError(AgentRunError):
    code = "AGENT_RETRYABLE_FAILURE"


class AgentRunNotReady(AgentRunError):
    code = "CORPUS_NOT_READY"


class AgentRunCancelled(AgentRunError):
    code = "CANCELLED"


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
    run.external_wait_expires_at = None
    run.finished_at = timezone.now()
    run.save(
        update_fields=[
            "status", "error_code", "error_message", "locked_until", "external_wait_kind",
            "external_wait_id", "external_wait_expires_at", "finished_at", "updated_at",
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
    run.external_wait_expires_at = timezone.now() + timedelta(
        seconds=settings.AGENT_EXTERNAL_WAIT_TTL_SECONDS
    )
    run.save(
        update_fields=[
            "status", "locked_until", "external_wait_kind", "external_wait_id",
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
    run.external_wait_expires_at = None
    run.save(
        update_fields=[
            "status", "locked_until", "external_wait_kind", "external_wait_id",
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
    run.external_wait_expires_at = None
    run.finished_at = now
    run.save(
        update_fields=[
            "status",
            "error_code",
            "error_message",
            "external_wait_kind",
            "external_wait_id",
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
    run.external_wait_expires_at = None
    run.finished_at = now
    run.save(
        update_fields=[
            "status", "error_code", "error_message", "locked_until", "external_wait_kind",
            "external_wait_id", "external_wait_expires_at", "finished_at", "updated_at",
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
def advance_waiting_agent_runs_for_parallel_audit(*, audit) -> int:
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
        run.external_wait_expires_at = None
        run.save(
            update_fields=[
                "status", "locked_until", "external_wait_kind", "external_wait_id",
                "external_wait_expires_at", "updated_at",
            ]
        )
        event = _enqueue_resumed_run(run=run, audit_id=audit.pk)
        publish_event_after_commit(event.pk)
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
    run.external_wait_expires_at = None
    run.finished_at = now
    run.save(
        update_fields=[
            "status", "error_code", "error_message", "locked_until", "external_wait_kind",
            "external_wait_id", "external_wait_expires_at", "finished_at", "updated_at",
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
