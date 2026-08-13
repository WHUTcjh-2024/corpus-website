from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

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


logger = logging.getLogger(__name__)
_MAX_REMOTE_RESPONSE_BYTES = 1_048_576
_MAX_CALLBACK_PAYLOAD_BYTES = 1_048_576
_TERMINAL_REMOTE_STATES = frozenset({"succeeded", "failed", "cancelled"})


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
                ParallelAuditExecutionMode.REMOTE
                if settings.CORPUS_AUDITOR_SERVICE_ENABLED
                else ParallelAuditExecutionMode.LOCAL
            ),
        },
    )
    enqueue_task(
        task_name=OutboxTaskName.AUDIT_PARALLEL_CORPUS,
        aggregate_id=audit.pk,
        payload={"audit_id": str(audit.pk)},
        deduplication_key=f"parallel-audit:{audit.pk}",
    )
    return audit


def dispatch_parallel_audit(audit: ParallelAudit):
    event = enqueue_task(
        task_name=OutboxTaskName.AUDIT_PARALLEL_CORPUS,
        aggregate_id=audit.pk,
        payload={"audit_id": str(audit.pk)},
        deduplication_key=f"parallel-audit:{audit.pk}",
    )
    return publish_event_after_commit(event.pk)


def run_parallel_audit(audit_id: str) -> dict[str, object]:
    audit = _claim_audit(audit_id)
    if audit is None:
        return {"audit_id": str(audit_id), "status": "skipped"}
    if audit.execution_mode == ParallelAuditExecutionMode.LOCAL:
        return _run_local_parallel_audit(audit)
    try:
        job = _submit_remote_audit(audit)
    except RetryableParallelAuditError:
        _release_for_retry(audit.pk)
        raise
    except ParallelAuditError as exc:
        _mark_failed(audit.pk, str(exc), remote_state="submission_failed")
        raise
    remote_state = str(job.get("state", ""))
    if remote_state in _TERMINAL_REMOTE_STATES:
        apply_remote_audit_result(audit_id=audit.pk, payload=job, payload_hash="")
    return {
        "audit_id": str(audit.pk),
        "status": "success" if remote_state == "succeeded" else "submitted",
        "remote_job_id": str(job.get("id", "")),
        "remote_state": remote_state,
    }


def reconcile_remote_audits(*, limit: int | None = None) -> int:
    """Recover from a lost callback without replaying the data-plane job.

    The Go service owns job execution and sends a signed callback. This bounded
    poller is only a control-plane repair path for terminal jobs whose callback
    could not reach Django after its retry budget.
    """

    if not settings.CORPUS_AUDITOR_SERVICE_ENABLED:
        return 0
    batch_size = limit if limit is not None else settings.CORPUS_AUDITOR_RECONCILE_BATCH_SIZE
    if batch_size < 1:
        return 0
    audit_ids = list(
        ParallelAudit.objects.filter(
            execution_mode=ParallelAuditExecutionMode.REMOTE,
            status=ParallelAuditStatus.RUNNING,
        )
        .exclude(remote_job_id="")
        .order_by("started_at")
        .values_list("pk", flat=True)[:batch_size]
    )
    repaired = 0
    for audit_id in audit_ids:
        try:
            payload = _get_remote_audit(str(audit_id))
        except RetryableParallelAuditError:
            continue
        except ParallelAuditError as exc:
            logger.warning("Unable to reconcile remote audit %s: %s", audit_id, exc)
            continue
        if str(payload.get("state", "")) not in _TERMINAL_REMOTE_STATES:
            continue
        if apply_remote_audit_result(audit_id=audit_id, payload=payload, payload_hash=""):
            repaired += 1
    return repaired


def apply_remote_audit_callback(*, audit_id: str, payload: dict[str, Any], payload_hash: str) -> bool:
    return apply_remote_audit_result(
        audit_id=audit_id,
        payload=payload,
        payload_hash=payload_hash,
    )


@transaction.atomic
def apply_remote_audit_result(*, audit_id, payload: dict[str, Any], payload_hash: str) -> bool:
    try:
        audit = (
            ParallelAudit.objects.select_for_update()
            .select_related("corpus")
            .get(pk=audit_id)
        )
    except ParallelAudit.DoesNotExist as exc:
        raise ParallelAuditError("Remote callback does not match an active audit.") from exc
    if audit.execution_mode != ParallelAuditExecutionMode.REMOTE:
        raise ParallelAuditError("This audit is not configured for the remote auditor service.")
    _validate_remote_payload(audit, payload)
    state = str(payload["state"])
    if audit.status == ParallelAuditStatus.PENDING and not audit.remote_job_id:
        # The executor may complete before a transient control-plane failure
        # prevents the submission response from committing. The authenticated
        # terminal callback is enough to converge that narrow race safely.
        audit.status = ParallelAuditStatus.RUNNING
    if audit.status in {ParallelAuditStatus.SUCCESS, ParallelAuditStatus.FAILED}:
        if audit.remote_callback_payload_hash and payload_hash and not hmac.compare_digest(
            audit.remote_callback_payload_hash, payload_hash
        ):
            raise ParallelAuditError("A terminal audit received a mismatched callback payload.")
        return False
    if audit.status != ParallelAuditStatus.RUNNING:
        raise ParallelAuditError("Remote callback does not match a running audit.")

    if state == "succeeded":
        report_path = _remote_ref_path(audit.corpus_id, payload.get("report_ref"), ".json")
        anomalies_path = _remote_ref_path(audit.corpus_id, payload.get("anomalies_ref"), ".jsonl")
        report = _read_report(report_path)
        summary = report.get("summary")
        if not isinstance(summary, dict):
            raise ParallelAuditError("Remote audit report has no valid summary.")
        audit.status = ParallelAuditStatus.SUCCESS
        audit.report_path = str(report_path)
        audit.anomalies_path = str(anomalies_path)
        audit.summary = summary
        audit.error_message = ""
    elif state in {"failed", "cancelled"}:
        audit.status = ParallelAuditStatus.FAILED
        audit.error_message = str(payload.get("error_message", state))[:4000]
    else:
        # A callback should only carry terminal state. Treat any other state as
        # an invalid remote response rather than regressing a durable record.
        raise ParallelAuditError("Remote callback must contain a terminal state.")
    audit.remote_state = state
    audit.remote_attempt = _bounded_int(payload.get("attempt"), maximum=10_000)
    audit.remote_callback_received_at = timezone.now()
    if payload_hash:
        audit.remote_callback_payload_hash = payload_hash
    audit.finished_at = timezone.now()
    audit.save(
        update_fields=[
            "status", "report_path", "anomalies_path", "summary", "error_message",
            "remote_state", "remote_attempt", "remote_callback_received_at",
            "remote_callback_payload_hash", "finished_at", "updated_at",
        ]
    )
    return True


def verify_remote_callback(*, body: bytes, timestamp: str, signature: str) -> str:
    if len(body) > _MAX_CALLBACK_PAYLOAD_BYTES:
        raise ParallelAuditError("Remote callback body exceeds the allowed size.")
    try:
        epoch_seconds = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise ParallelAuditError("Remote callback timestamp is invalid.") from exc
    if abs(time.time() - epoch_seconds) > settings.CORPUS_AUDITOR_CALLBACK_MAX_SKEW_SECONDS:
        raise ParallelAuditError("Remote callback timestamp is outside the accepted window.")
    expected = "sha256=" + hmac.new(
        settings.CORPUS_AUDITOR_CALLBACK_TOKEN.encode("utf-8"),
        timestamp.encode("ascii") + b"\n" + body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        raise ParallelAuditError("Remote callback signature is invalid.")
    return hashlib.sha256(body).hexdigest()


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
def _claim_audit(audit_id: str) -> ParallelAudit | None:
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
def _release_for_retry(audit_id) -> None:
    audit = ParallelAudit.objects.select_for_update().get(pk=audit_id)
    if audit.status != ParallelAuditStatus.RUNNING:
        return
    audit.status = ParallelAuditStatus.PENDING
    audit.started_at = None
    audit.error_message = "Remote auditor temporarily unavailable; retrying."
    audit.save(update_fields=["status", "started_at", "error_message", "updated_at"])


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
def _mark_failed(audit_id, message: str, *, remote_state: str = "") -> None:
    audit = ParallelAudit.objects.select_for_update().get(pk=audit_id)
    if audit.status not in {ParallelAuditStatus.PENDING, ParallelAuditStatus.RUNNING}:
        return
    audit.status = ParallelAuditStatus.FAILED
    audit.remote_state = remote_state or audit.remote_state
    audit.error_message = message[:4000]
    audit.finished_at = timezone.now()
    audit.save(update_fields=["status", "remote_state", "error_message", "finished_at", "updated_at"])


def _submit_remote_audit(audit: ParallelAudit) -> dict[str, Any]:
    corpus_id = str(audit.corpus_id)
    payload = {
        "job_id": str(audit.pk),
        "input_ref": f"processed/{corpus_id}/parallel_pairs.jsonl",
        "output_prefix": f"processed/{corpus_id}/audits/{audit.pk}",
        "callback_path": f"/api/internal/audits/{audit.pk}/callback/",
        "options": {
            "low_confidence": settings.PARALLEL_AUDIT_LOW_CONFIDENCE,
            "min_length_ratio": settings.PARALLEL_AUDIT_MIN_LENGTH_RATIO,
            "max_length_ratio": settings.PARALLEL_AUDIT_MAX_LENGTH_RATIO,
            "max_anomalies": settings.PARALLEL_AUDIT_MAX_ANOMALIES,
        },
    }
    response = _remote_request(method="POST", path="/v1/audits", payload=payload)
    _persist_submission(audit.pk, response)
    return response


def _get_remote_audit(audit_id: str) -> dict[str, Any]:
    return _remote_request(method="GET", path=f"/v1/audits/{audit_id}")


def _remote_request(*, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8") if payload is not None else None
    request = urlrequest.Request(
        f"{settings.CORPUS_AUDITOR_SERVICE_BASE_URL.rstrip('/')}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {settings.CORPUS_AUDITOR_SERVICE_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlrequest.urlopen(request, timeout=settings.CORPUS_AUDITOR_SERVICE_TIMEOUT_SECONDS) as response:
            body = response.read(_MAX_REMOTE_RESPONSE_BYTES + 1)
            if len(body) > _MAX_REMOTE_RESPONSE_BYTES:
                raise ParallelAuditError("Remote auditor response exceeds the allowed size.")
    except urlerror.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        if exc.code in {408, 429, 500, 502, 503, 504}:
            raise RetryableParallelAuditError(f"Remote auditor returned HTTP {exc.code}: {detail}") from exc
        raise ParallelAuditError(f"Remote auditor rejected request with HTTP {exc.code}: {detail}") from exc
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        raise RetryableParallelAuditError(f"Remote auditor is unavailable: {exc}") from exc
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ParallelAuditError("Remote auditor returned invalid JSON.") from exc
    if not isinstance(decoded, dict):
        raise ParallelAuditError("Remote auditor response must be an object.")
    return decoded


@transaction.atomic
def _persist_submission(audit_id, payload: dict[str, Any]) -> None:
    audit = ParallelAudit.objects.select_for_update().get(pk=audit_id)
    job_id = str(payload.get("id", ""))
    state = str(payload.get("state", ""))
    if job_id != str(audit.pk) or state not in {"queued", "running", "succeeded", "failed", "cancelled"}:
        raise ParallelAuditError("Remote auditor returned an invalid submission response.")
    audit.remote_job_id = job_id
    audit.remote_state = state
    audit.remote_attempt = _bounded_int(payload.get("attempt"), maximum=10_000)
    audit.save(update_fields=["remote_job_id", "remote_state", "remote_attempt", "updated_at"])


def _validate_remote_payload(audit: ParallelAudit, payload: dict[str, Any]) -> None:
    if str(payload.get("id", "")) != str(audit.pk):
        raise ParallelAuditError("Remote callback job ID does not match the audit.")
    if audit.remote_job_id and audit.remote_job_id != str(payload["id"]):
        raise ParallelAuditError("Remote callback conflicts with the submitted job ID.")
    if str(payload.get("state", "")) not in _TERMINAL_REMOTE_STATES:
        raise ParallelAuditError("Remote callback must contain a terminal job state.")


def _audit_paths(audit: ParallelAudit) -> tuple[Path, Path, Path]:
    root = Path(settings.DATA_ROOT) / "processed" / str(audit.corpus_id)
    return (
        root / "parallel_pairs.jsonl",
        root / f"quality_report-{audit.pk}.json",
        root / f"audit_anomalies-{audit.pk}.jsonl",
    )


def _remote_ref_path(corpus_id, reference: Any, suffix: str) -> Path:
    if not isinstance(reference, str):
        raise ParallelAuditError("Remote auditor output reference is invalid.")
    root = Path(settings.DATA_ROOT).resolve()
    expected = (root / "processed" / str(corpus_id) / "audits").resolve()
    candidate = (root / reference).resolve()
    if not reference or not candidate.is_relative_to(expected) or candidate.suffix != suffix:
        raise ParallelAuditError("Remote auditor output reference is invalid.")
    if not candidate.is_file():
        raise ParallelAuditError("Remote auditor output is not available on the shared data volume.")
    return candidate


def _read_report(path: Path) -> dict[str, Any]:
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("report must be an object")
    return decoded


def _bounded_int(value: Any, *, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return min(max(parsed, 0), maximum)
