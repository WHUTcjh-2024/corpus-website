from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.corpora.models import Corpus
from apps.outbox.models import OutboxTaskName
from apps.outbox.services import enqueue_task, publish_event_after_commit
from apps.processing.models import ProcessingTask

from .models import ParallelAudit, ParallelAuditStatus


class ParallelAuditError(RuntimeError):
    pass


@transaction.atomic
def create_parallel_audit(*, corpus: Corpus, processing_task: ProcessingTask) -> ParallelAudit:
    audit, _ = ParallelAudit.objects.get_or_create(
        processing_task=processing_task,
        defaults={"corpus": corpus},
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

    corpus_id = str(audit.corpus_id)
    input_path = Path(settings.DATA_ROOT) / "processed" / corpus_id / "parallel_pairs.jsonl"
    output_dir = Path(settings.DATA_ROOT) / "processed" / corpus_id
    # Keep every result tied to its durable audit ID. This avoids overwriting a
    # prior report when a corpus is reprocessed and keeps each DB record
    # reproducible even after later versions of the source are imported.
    report_path = output_dir / f"quality_report-{audit.pk}.json"
    anomalies_path = output_dir / f"audit_anomalies-{audit.pk}.jsonl"
    try:
        completed = subprocess.run(
            _command(input_path, report_path, anomalies_path),
            # The Go module lives under backend/go so local `go run` and the
            # production binary share the same command contract.
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.PARALLEL_AUDIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _mark_failed(audit.pk, f"审计器无法执行：{exc}")
        raise ParallelAuditError(str(exc)) from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "审计器未返回错误详情").strip()
        _mark_failed(audit.pk, detail)
        raise ParallelAuditError(detail)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        summary = report["summary"]
        if not isinstance(summary, dict):
            raise ValueError("summary must be an object")
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, ValueError) as exc:
        _mark_failed(audit.pk, f"审计报告无效：{exc}")
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
def _mark_failed(audit_id, message: str) -> None:
    audit = ParallelAudit.objects.select_for_update().get(pk=audit_id)
    if audit.status not in {ParallelAuditStatus.PENDING, ParallelAuditStatus.RUNNING}:
        return
    audit.status = ParallelAuditStatus.FAILED
    audit.error_message = message[:4000]
    audit.finished_at = timezone.now()
    audit.save(update_fields=["status", "error_message", "finished_at", "updated_at"])


def _command(input_path: Path, report_path: Path, anomalies_path: Path) -> list[str]:
    command = shlex.split(settings.CORPUS_AUDITOR_COMMAND, posix=False)
    if not command:
        raise ParallelAuditError("CORPUS_AUDITOR_COMMAND 不能为空")
    return [
        *command,
        "--input", str(input_path),
        "--report", str(report_path),
        "--anomalies", str(anomalies_path),
        "--low-confidence", str(settings.PARALLEL_AUDIT_LOW_CONFIDENCE),
        "--min-length-ratio", str(settings.PARALLEL_AUDIT_MIN_LENGTH_RATIO),
        "--max-length-ratio", str(settings.PARALLEL_AUDIT_MAX_LENGTH_RATIO),
        "--max-anomalies", str(settings.PARALLEL_AUDIT_MAX_ANOMALIES),
    ]
