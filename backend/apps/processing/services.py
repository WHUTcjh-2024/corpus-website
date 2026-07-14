from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.corpora.models import (
    Corpus,
    CorpusDocumentation,
    CorpusFile,
    CorpusFileStatus,
    CorpusSourceType,
    CorpusStatus,
)

from .artifacts import ArtifactWriter
from .contracts import SourceFile
from .exceptions import ProcessingAlreadyQueued, ProcessingError
from .importers.registry import get_importer
from .models import ProcessingTask, ProcessingTaskStatus


@transaction.atomic
def create_processing_task(*, corpus: Corpus, requested_by=None) -> ProcessingTask:
    if ProcessingTask.objects.filter(
        corpus=corpus,
        status__in=[ProcessingTaskStatus.PENDING, ProcessingTaskStatus.RUNNING],
    ).exists():
        raise ProcessingAlreadyQueued("该语料库已有等待或执行中的加工任务。")
    if requested_by is not None and ProcessingTask.objects.filter(
        requested_by=requested_by,
        status__in=[ProcessingTaskStatus.PENDING, ProcessingTaskStatus.RUNNING],
    ).exists():
        raise ProcessingAlreadyQueued("当前账号已有等待或执行中的加工任务，请完成后再提交。")
    try:
        task = ProcessingTask.objects.create(corpus=corpus, requested_by=requested_by)
    except IntegrityError as exc:
        raise ProcessingAlreadyQueued("该语料库或当前账号已有等待或执行中的加工任务。") from exc
    corpus.status = CorpusStatus.PENDING_PROCESSING
    corpus.stage = "queued"
    corpus.save(update_fields=["status", "stage", "updated_at"])
    return task


def dispatch_processing_task(task: ProcessingTask):
    from .tasks import process_corpus_task

    try:
        return process_corpus_task.delay(str(task.pk))
    except Exception as exc:
        _mark_failed(task.pk, task.corpus_id, f"Celery dispatch failed: {exc}")
        raise ProcessingError(f"Celery dispatch failed: {exc}") from exc


def process_task(task_id) -> dict[str, Any]:
    task = _mark_running(task_id)
    corpus = task.corpus
    writer = ArtifactWriter(
        data_root=settings.DATA_ROOT,
        corpus_id=str(corpus.pk),
        task_id=str(task.pk),
    )
    try:
        corpus_files = list(corpus.files.order_by("created_at", "pk"))
        if not corpus_files:
            raise ProcessingError("语料库没有已登记的 CorpusFile。")
        sources = [_to_source_file(corpus, corpus_file) for corpus_file in corpus_files]
        importer = get_importer(corpus.corpus_type)
        writer.open()
        processed_file_ids: set[str] = set()
        for result in importer.iter_import(sources):
            writer.add_result(result)
            processed_file_ids.update(result.source_file_ids)
            _update_progress(task.pk, 10 + int(75 * len(processed_file_ids) / len(sources)))

        source_metadata = [_source_metadata(corpus_file, source) for corpus_file, source in zip(corpus_files, sources, strict=True)]
        report = writer.finalize(
            corpus_meta={
                "corpus_id": str(corpus.pk),
                "name": corpus.name,
                "corpus_type": corpus.corpus_type,
                "language": corpus.language,
                "source_type": corpus.source_type,
            },
            source_files=source_metadata,
            importer_name=importer.name,
        )
        _mark_success(task.pk, report, writer.processed_output)
        return report
    except Exception as exc:
        writer.abort()
        _mark_failed(task.pk, corpus.pk, str(exc))
        if isinstance(exc, ProcessingError):
            raise
        raise ProcessingError(str(exc)) from exc


@transaction.atomic
def _mark_running(task_id) -> ProcessingTask:
    task = (
        ProcessingTask.objects.select_for_update()
        .select_related("corpus")
        .get(pk=task_id)
    )
    if task.status != ProcessingTaskStatus.PENDING:
        raise ProcessingError(f"Task must be pending, got: {task.status}")
    task.status = ProcessingTaskStatus.RUNNING
    task.progress = 5
    task.error_message = ""
    task.started_at = timezone.now()
    task.save(
        update_fields=["status", "progress", "error_message", "started_at", "updated_at"]
    )
    task.corpus.status = CorpusStatus.PROCESSING
    task.corpus.stage = "processing"
    task.corpus.save(update_fields=["status", "stage", "updated_at"])
    task.corpus.files.update(status=CorpusFileStatus.PROCESSING, error_message="")
    return task


def _update_progress(task_id, progress: int) -> None:
    ProcessingTask.objects.filter(pk=task_id, status=ProcessingTaskStatus.RUNNING).update(
        progress=min(progress, 90),
        updated_at=timezone.now(),
    )


@transaction.atomic
def _mark_success(
    task_id,
    report: dict[str, Any],
    output_path: Path,
) -> None:
    task = ProcessingTask.objects.select_for_update().select_related("corpus").get(pk=task_id)
    task.status = ProcessingTaskStatus.SUCCESS
    task.progress = 100
    task.output_path = str(output_path)
    task.error_message = ""
    task.finished_at = timezone.now()
    task.save(
        update_fields=[
            "status",
            "progress",
            "output_path",
            "error_message",
            "finished_at",
            "updated_at",
        ]
    )
    task.corpus.status = CorpusStatus.READY
    task.corpus.stage = "processed"
    task.corpus.save(update_fields=["status", "stage", "updated_at"])

    counts = report["counts"]
    CorpusDocumentation.objects.update_or_create(
        corpus=task.corpus,
        defaults={
            "file_count": counts["file_count"],
            "document_count": counts["document_count"],
            "paragraph_count": counts["paragraph_count"],
            "sentence_count": counts["sentence_count"],
            "token_count": counts["token_count"],
            "type_count": counts["type_count"],
            "segmentation_tool": _segmentation_tool(
                report.get("importer", ""),
                task.corpus.language,
            ),
            "processing_notes": "; ".join(report["warnings"]),
        },
    )
    for source_file in report["source_files"]:
        CorpusFile.objects.filter(pk=source_file["corpus_file_id"]).update(
            status=CorpusFileStatus.READY,
            size_bytes=source_file["size_bytes"],
            checksum_sha256=source_file["sha256"],
            error_message="",
            updated_at=timezone.now(),
        )


@transaction.atomic
def _mark_failed(task_id, corpus_id, message: str) -> None:
    error_message = message[:4000] or "Unknown processing error"
    ProcessingTask.objects.filter(pk=task_id).update(
        status=ProcessingTaskStatus.FAILED,
        error_message=error_message,
        finished_at=timezone.now(),
        updated_at=timezone.now(),
    )
    Corpus.objects.filter(pk=corpus_id).update(
        status=CorpusStatus.FAILED,
        stage="processing_failed",
        updated_at=timezone.now(),
    )
    CorpusFile.objects.filter(corpus_id=corpus_id, status=CorpusFileStatus.PROCESSING).update(
        status=CorpusFileStatus.FAILED,
        error_message=error_message,
        updated_at=timezone.now(),
    )


def _to_source_file(corpus: Corpus, corpus_file: CorpusFile) -> SourceFile:
    path = _resolve_source_path(corpus, corpus_file.stored_path)
    if not path.is_file():
        raise ProcessingError(f"Source file does not exist: {path}")
    if path.suffix.lower() not in {".txt", ".tsv"}:
        raise ProcessingError(f"Unsupported source suffix: {path.suffix}")
    return SourceFile(
        id=str(corpus_file.pk),
        filename=corpus_file.original_filename,
        path=path,
        detected_type=corpus_file.detected_type,
        language=corpus_file.language,
        encoding=corpus_file.encoding,
        size_bytes=corpus_file.size_bytes,
    )


def _resolve_source_path(corpus: Corpus, stored_path: str) -> Path:
    configured = Path(stored_path)
    data_root = settings.DATA_ROOT.resolve()
    path = configured.resolve() if configured.is_absolute() else (data_root / configured).resolve()
    if corpus.source_type == CorpusSourceType.USER:
        user_upload_root = (data_root / "user_uploads").resolve()
        if not path.is_relative_to(user_upload_root):
            raise ProcessingError("User corpus source must stay under DATA_ROOT/user_uploads.")
    elif not configured.is_absolute() and not path.is_relative_to(data_root):
        raise ProcessingError("Relative source path escapes DATA_ROOT.")
    return path


def _source_metadata(corpus_file: CorpusFile, source: SourceFile) -> dict[str, Any]:
    return {
        "corpus_file_id": str(corpus_file.pk),
        "filename": source.filename,
        "detected_type": source.detected_type,
        "language": source.language,
        "encoding": source.encoding,
        "size_bytes": source.path.stat().st_size,
        "sha256": _sha256(source.path),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _segmentation_tool(importer_name: str, language: str) -> str:
    if "tagged" in importer_name:
        if language == "zh_en":
            return "zh:source-provided-pos-v1;en:source-provided-pos-v1"
        return f"{language}:source-provided-pos-v1"
    if language == "zh_en":
        return "zh:jieba-0.42.1;en:regex-baseline-v1"
    if language == "zh":
        return "zh:jieba-0.42.1"
    return "en:regex-baseline-v1"
