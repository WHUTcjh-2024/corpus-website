from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.corpora.models import Corpus
from apps.outbox.models import OutboxTaskName
from apps.outbox.services import enqueue_task, publish_event_after_commit
from apps.processing.models import ProcessingTask

from .models import (
    ParallelAudit,
    ParallelAuditExecutionMode,
    ParallelAuditStatus,
)
from .queue import AuditQueue, AuditQueueUnavailable


logger = logging.getLogger(__name__)
_MAX_RESULT_PAYLOAD_BYTES = 1_048_576
_TERMINAL_WORKER_STATES = frozenset({"succeeded", "failed", "cancelled"})


class ParallelAuditError(RuntimeError):
    code = "PARALLEL_AUDIT_FAILED"


class RetryableParallelAuditError(ParallelAuditError):
    code = "PARALLEL_AUDIT_RETRYABLE"


@transaction.atomic
def create_parallel_audit(*, corpus: Corpus, processing_task: ProcessingTask) -> ParallelAudit:
    audit, _ = ParallelAudit.objects.get_or_create(
        processing_task=processing_task,
        defaults={
            "corpus": corpus,
            "execution_mode": (
                ParallelAuditExecutionMode.QUEUE
                if settings.CORPUS_AUDITOR_QUEUE_ENABLED
                else ParallelAuditExecutionMode.LOCAL
            ),
        },
    )
    _enqueue_audit_command(audit)
    return audit


def dispatch_parallel_audit(audit: ParallelAudit):
    event = _enqueue_audit_command(audit)
    return publish_event_after_commit(event.pk)


def _enqueue_audit_command(audit: ParallelAudit):
    return enqueue_task(
        task_name=OutboxTaskName.PUBLISH_AUDIT_COMMAND,
        aggregate_id=audit.pk,
        payload={"audit_id": str(audit.pk)},
        deduplication_key=f"parallel-audit-command:{audit.pk}",
    )


def publish_parallel_audit_command(audit_id: str) -> dict[str, object]:
    audit = _claim_audit_for_command(audit_id)
    if audit is None:
        return {"audit_id": str(audit_id), "status": "skipped"}
    if audit.execution_mode == ParallelAuditExecutionMode.LOCAL:
        return _run_local_parallel_audit(audit)

    command = _command_payload(audit)
    try:
        message_id = AuditQueue().publish_command(command)
    except AuditQueueUnavailable as exc:
        _release_command_for_retry(audit.pk)
        raise RetryableParallelAuditError(str(exc)) from exc
    except Exception as exc:
        _mark_failed(audit.pk, str(exc), worker_state="command_publish_failed")
        raise ParallelAuditError(str(exc)) from exc
    _persist_command_publication(audit.pk, message_id=message_id)
    return {
        "audit_id": str(audit.pk),
        "status": "published",
        "command_message_id": message_id,
    }


def consume_parallel_audit_results(*, limit: int | None = None) -> int:
    """Project durable Go worker results into Django state.

    Redis Streams retain the result until this consumer group acknowledges it.
    The database unique constraint on result_message_id and terminal payload
    hash make redelivery harmless after a process crash between DB commit and
    XACK.
    """

    if not settings.CORPUS_AUDITOR_QUEUE_ENABLED:
        return 0
    batch_size = limit if limit is not None else settings.CORPUS_AUDITOR_RESULT_BATCH_SIZE
    if batch_size < 1:
        return 0
    try:
        queue = AuditQueue()
        queue.ensure_result_group()
        entries = queue.reclaim_results(limit=batch_size)
        if not entries:
            entries = queue.read_results(limit=batch_size)
    except AuditQueueUnavailable as exc:
        logger.warning("Audit result queue is unavailable: %s", exc)
        return 0
    applied = 0
    for entry in entries:
        try:
            if apply_parallel_audit_result(
                audit_id=entry.payload.get("id", ""),
                payload=entry.payload,
                payload_hash=entry.payload_hash,
                message_id=entry.message_id,
            ):
                applied += 1
            queue.ack_result(entry.message_id)
        except (ParallelAuditError, ValueError) as exc:
            # Do not acknowledge an invalid payload; it remains inspectable in
            # the pending-entry list instead of being silently lost.
            logger.error("Rejected audit result message %s: %s", entry.message_id, exc)
    return applied


@transaction.atomic
def apply_parallel_audit_result(
    *, audit_id, payload: dict[str, Any], payload_hash: str, message_id: str
) -> bool:
    if not isinstance(payload, dict):
        raise ParallelAuditError("Worker result must be an object.")
    try:
        audit = (
            ParallelAudit.objects.select_for_update()
            .select_related("corpus")
            .get(pk=audit_id)
        )
    except (ParallelAudit.DoesNotExist, ValueError) as exc:
        raise ParallelAuditError("Worker result does not match an active audit.") from exc
    if audit.execution_mode != ParallelAuditExecutionMode.QUEUE:
        raise ParallelAuditError("This audit is not configured for the queue worker.")
    _validate_worker_payload(audit, payload)
    state = str(payload["state"])

    if audit.result_message_id:
        if audit.result_message_id != message_id:
            if audit.result_payload_hash and audit.result_payload_hash != payload_hash:
                raise ParallelAuditError("A terminal audit received a conflicting worker result.")
            return False
        if audit.result_payload_hash and audit.result_payload_hash != payload_hash:
            raise ParallelAuditError("A worker result message ID was reused with another payload.")
        return False
    if audit.status in {ParallelAuditStatus.SUCCESS, ParallelAuditStatus.FAILED}:
        raise ParallelAuditError("A terminal audit received an untracked worker result.")

    if state == "succeeded":
        report_path = _worker_ref_path(
            audit.corpus_id, audit.pk, payload.get("report_ref"), ".json"
        )
        anomalies_path = _worker_ref_path(
            audit.corpus_id, audit.pk, payload.get("anomalies_ref"), ".jsonl"
        )
        report = _read_report(report_path)
        summary = report.get("summary")
        if not isinstance(summary, dict):
            raise ParallelAuditError("Worker report has no valid summary.")
        audit.status = ParallelAuditStatus.SUCCESS
        audit.report_path = str(report_path)
        audit.anomalies_path = str(anomalies_path)
        audit.summary = summary
        audit.error_message = ""
    else:
        audit.status = ParallelAuditStatus.FAILED
        audit.error_message = str(payload.get("error_message", state))[:4000]
    audit.worker_job_id = str(payload["id"])
    audit.worker_state = state
    audit.worker_attempt = _bounded_int(payload.get("attempt"), maximum=10_000)
    audit.result_message_id = message_id
    audit.result_received_at = timezone.now()
    audit.result_payload_hash = payload_hash
    audit.finished_at = timezone.now()
    audit.save(
        update_fields=[
            "status", "report_path", "anomalies_path", "summary", "error_message",
            "worker_job_id", "worker_state", "worker_attempt", "result_message_id",
            "result_received_at", "result_payload_hash", "finished_at", "updated_at",
        ]
    )
    return True


def _command_payload(audit: ParallelAudit) -> dict[str, Any]:
    corpus_id = str(audit.corpus_id)
    return {
        "id": str(audit.pk),
        "schema_version": 1,
        "input_ref": f"processed/{corpus_id}/parallel_pairs.jsonl",
        "output_prefix": f"processed/{corpus_id}/audits/{audit.pk}",
        "options": {
            "low_confidence": settings.PARALLEL_AUDIT_LOW_CONFIDENCE,
            "min_length_ratio": settings.PARALLEL_AUDIT_MIN_LENGTH_RATIO,
            "max_length_ratio": settings.PARALLEL_AUDIT_MAX_LENGTH_RATIO,
            "max_anomalies": settings.PARALLEL_AUDIT_MAX_ANOMALIES,
        },
    }


@transaction.atomic
def _claim_audit_for_command(audit_id: str) -> ParallelAudit | None:
    audit = ParallelAudit.objects.select_for_update().select_related("corpus").get(pk=audit_id)
    if audit.status != ParallelAuditStatus.PENDING:
        return None
    audit.status = ParallelAuditStatus.RUNNING
    audit.error_message = ""
    audit.started_at = timezone.now()
    audit.finished_at = None
    audit.save(update_fields=["status", "error_message", "started_at", "finished_at", "updated_at"])
    return audit


@transaction.atomic
def _persist_command_publication(audit_id, *, message_id: str) -> None:
    audit = ParallelAudit.objects.select_for_update().get(pk=audit_id)
    if audit.status != ParallelAuditStatus.RUNNING:
        return
    audit.worker_job_id = str(audit.pk)
    audit.worker_state = "queued"
    audit.command_message_id = message_id
    audit.command_published_at = timezone.now()
    audit.save(
        update_fields=[
            "worker_job_id", "worker_state", "command_message_id", "command_published_at", "updated_at"
        ]
    )


@transaction.atomic
def _release_command_for_retry(audit_id) -> None:
    audit = ParallelAudit.objects.select_for_update().get(pk=audit_id)
    if audit.status != ParallelAuditStatus.RUNNING or audit.command_message_id:
        return
    audit.status = ParallelAuditStatus.PENDING
    audit.started_at = None
    audit.error_message = "Audit command queue temporarily unavailable; retrying."
    audit.save(update_fields=["status", "started_at", "error_message", "updated_at"])


def _run_local_parallel_audit(audit: ParallelAudit) -> dict[str, object]:
    """Compatibility route for explicit local-development fallback only."""
    import shlex
    import subprocess

    input_path, report_path, anomalies_path = _audit_paths(audit)
    try:
        completed = subprocess.run(
            [
                *shlex.split(settings.CORPUS_AUDITOR_COMMAND, posix=False),
                "--input", str(input_path), "--report", str(report_path),
                "--anomalies", str(anomalies_path),
                "--low-confidence", str(settings.PARALLEL_AUDIT_LOW_CONFIDENCE),
                "--min-length-ratio", str(settings.PARALLEL_AUDIT_MIN_LENGTH_RATIO),
                "--max-length-ratio", str(settings.PARALLEL_AUDIT_MAX_LENGTH_RATIO),
                "--max-anomalies", str(settings.PARALLEL_AUDIT_MAX_ANOMALIES),
            ],
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.PARALLEL_AUDIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _mark_failed(audit.pk, f"Auditor executable unavailable: {exc}")
        raise ParallelAuditError(str(exc)) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Auditor returned no error detail").strip()
        _mark_failed(audit.pk, detail)
        raise ParallelAuditError(detail)
    try:
        report = _read_report(report_path)
        summary = report["summary"]
        if not isinstance(summary, dict):
            raise ValueError("summary must be an object")
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, ValueError) as exc:
        _mark_failed(audit.pk, f"Audit report is invalid: {exc}")
        raise ParallelAuditError(str(exc)) from exc
    _mark_success(audit.pk, report_path, anomalies_path, summary)
    return {"audit_id": str(audit.pk), "status": "success", "summary": summary}


@transaction.atomic
def _mark_success(audit_id, report_path: Path, anomalies_path: Path, summary: dict) -> None:
    audit = ParallelAudit.objects.select_for_update().get(pk=audit_id)
    if audit.status != ParallelAuditStatus.RUNNING:
        return
    audit.status = ParallelAuditStatus.SUCCESS
    audit.report_path = str(report_path)
    audit.anomalies_path = str(anomalies_path)
    audit.summary = summary
    audit.error_message = ""
    audit.finished_at = timezone.now()
    audit.save(update_fields=["status", "report_path", "anomalies_path", "summary", "error_message", "finished_at", "updated_at"])


@transaction.atomic
def _mark_failed(audit_id, message: str, *, worker_state: str = "") -> None:
    audit = ParallelAudit.objects.select_for_update().get(pk=audit_id)
    if audit.status not in {ParallelAuditStatus.PENDING, ParallelAuditStatus.RUNNING}:
        return
    audit.status = ParallelAuditStatus.FAILED
    audit.worker_state = worker_state or audit.worker_state
    audit.error_message = message[:4000]
    audit.finished_at = timezone.now()
    audit.save(update_fields=["status", "worker_state", "error_message", "finished_at", "updated_at"])


def _validate_worker_payload(audit: ParallelAudit, payload: dict[str, Any]) -> None:
    if str(payload.get("id", "")) != str(audit.pk):
        raise ParallelAuditError("Worker result ID does not match the audit.")
    if audit.worker_job_id and audit.worker_job_id != str(payload["id"]):
        raise ParallelAuditError("Worker result conflicts with the audit job ID.")
    if payload.get("schema_version") != 1:
        raise ParallelAuditError("Worker result schema version is not supported.")
    if str(payload.get("state", "")) not in _TERMINAL_WORKER_STATES:
        raise ParallelAuditError("Worker result must contain a terminal job state.")


def _audit_paths(audit: ParallelAudit) -> tuple[Path, Path, Path]:
    root = Path(settings.DATA_ROOT) / "processed" / str(audit.corpus_id)
    return (
        root / "parallel_pairs.jsonl",
        root / f"quality_report-{audit.pk}.json",
        root / f"audit_anomalies-{audit.pk}.jsonl",
    )


def _worker_ref_path(corpus_id, audit_id, reference: Any, suffix: str) -> Path:
    if not isinstance(reference, str):
        raise ParallelAuditError("Worker output reference is invalid.")
    root = Path(settings.DATA_ROOT).resolve()
    expected = (root / "processed" / str(corpus_id) / "audits" / str(audit_id)).resolve()
    candidate = (root / reference).resolve()
    if not reference or not candidate.is_relative_to(expected) or candidate.suffix != suffix:
        raise ParallelAuditError("Worker output reference is invalid.")
    if not candidate.is_file():
        raise ParallelAuditError("Worker output is not available on the shared data volume.")
    return candidate


def _read_report(path: Path) -> dict[str, Any]:
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("report must be an object")
    return decoded


def result_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_RESULT_PAYLOAD_BYTES:
        raise ParallelAuditError("Worker result exceeds the allowed size.")
    return hashlib.sha256(encoded).hexdigest()


def _bounded_int(value: Any, *, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return min(max(parsed, 0), maximum)
