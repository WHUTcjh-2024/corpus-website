from __future__ import annotations

import csv
import os
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.audit.models import AuditEventType
from apps.audit.services import record_audit_event
from apps.corpora.models import (
    Corpus,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from apps.parallel.engine import ParallelQuery, ParallelSearchEngine
from apps.parallel.forms import ParallelSearchForm
from apps.search.forms import KwicSearchForm
from apps.search.kwic import KwicSearchEngine
from apps.search.query_engine import ComplexQueryEngine

from .models import ExportJob, ExportJobStatus, ExportKind


class ExportError(RuntimeError):
    pass


PARALLEL_TYPES = {
    CorpusType.ALIGNED_TSV,
    CorpusType.PAIRED_RAW_ZH_EN,
    CorpusType.PAIRED_TAGGED_ZH_EN,
}


@transaction.atomic
def create_export_job(
    *,
    user,
    corpus: Corpus,
    kind: str,
    parameters: Mapping[str, Any],
    request: HttpRequest | None = None,
) -> ExportJob:
    if kind not in ExportKind.values:
        raise ValidationError("不支持的导出类型。")
    locked_user = get_user_model().objects.select_for_update().get(pk=user.pk)
    locked_corpus = Corpus.objects.select_for_update().get(pk=corpus.pk)
    _require_export_permission(locked_user, locked_corpus)
    normalized_query = _normalize_query(locked_corpus, kind, parameters)

    now = timezone.now()
    _expire_due_jobs(ExportJob.objects.filter(requested_by=locked_user), now=now)
    if ExportJob.objects.filter(
        requested_by=locked_user,
        status__in=[ExportJobStatus.PENDING, ExportJobStatus.RUNNING],
    ).exists():
        raise ValidationError("当前账号已有等待或执行中的导出任务。")
    recent_count = ExportJob.objects.filter(
        requested_by=locked_user,
        created_at__gte=now - timedelta(hours=1),
    ).count()
    if recent_count >= settings.EXPORT_MAX_JOBS_PER_HOUR:
        raise ValidationError("导出请求过于频繁，请稍后再试。")

    try:
        job = ExportJob.objects.create(
            requested_by=locked_user,
            corpus=locked_corpus,
            kind=kind,
            query=normalized_query,
            expires_at=now + timedelta(seconds=settings.EXPORT_TTL_SECONDS),
        )
    except IntegrityError as exc:
        raise ValidationError("当前账号已有等待或执行中的导出任务。") from exc

    record_audit_event(
        AuditEventType.EXPORT_CREATED,
        request=request,
        actor=locked_user,
        corpus=locked_corpus,
        metadata={"job_id": str(job.pk), "kind": kind, "query": normalized_query},
    )
    return job


def dispatch_export_job(job: ExportJob):
    from .tasks import build_export_task

    try:
        return build_export_task.delay(str(job.pk))
    except Exception as exc:
        _mark_failed(job.pk, f"Celery dispatch failed: {exc}")
        raise ExportError(f"Celery dispatch failed: {exc}") from exc


def process_export_job(job_id) -> dict[str, Any]:
    job = _mark_running(job_id)
    output_root = (settings.DATA_ROOT / "exports").resolve()
    output_dir = (
        output_root / str(job.corpus_id) / str(job.requested_by_id)
    ).resolve()
    if not output_dir.is_relative_to(output_root):
        _mark_failed(job.pk, "导出路径无效。")
        raise ExportError("导出路径无效。")
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = (output_dir / f"{job.pk}-{job.kind}.tsv").resolve()
    temporary_path = (output_dir / f".{uuid.uuid4().hex}.exporting").resolve()

    try:
        headers, rows = _export_rows(job)
        row_count = 0
        with temporary_path.open("x", encoding="utf-8-sig", newline="") as destination:
            writer = csv.writer(
                destination,
                delimiter="\t",
                lineterminator="\r\n",
                quoting=csv.QUOTE_MINIMAL,
            )
            writer.writerow(headers)
            for row in rows:
                row_count += 1
                if row_count > settings.EXPORT_MAX_ROWS:
                    raise ExportError(
                        f"结果超过 {settings.EXPORT_MAX_ROWS} 行，请缩小检索条件。"
                    )
                writer.writerow([_safe_spreadsheet_cell(value) for value in row])
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary_path, final_path)
        _mark_success(job.pk, final_path=final_path, row_count=row_count)
        record_audit_event(
            AuditEventType.EXPORT_COMPLETED,
            actor=job.requested_by,
            corpus=job.corpus,
            metadata={"job_id": str(job.pk), "kind": job.kind, "row_count": row_count},
        )
        return {"job_id": str(job.pk), "row_count": row_count, "path": str(final_path)}
    except Exception as exc:
        temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        _mark_failed(job.pk, str(exc))
        record_audit_event(
            AuditEventType.EXPORT_FAILED,
            actor=job.requested_by,
            corpus=job.corpus,
            metadata={"job_id": str(job.pk), "kind": job.kind, "error": str(exc)},
        )
        if isinstance(exc, ExportError):
            raise
        raise ExportError(str(exc)) from exc


@transaction.atomic
def acquire_download(*, job_id, user, request: HttpRequest | None = None) -> tuple[ExportJob, Path]:
    job = (
        ExportJob.objects.select_for_update()
        .select_related("corpus", "requested_by")
        .get(pk=job_id)
    )
    if job.requested_by_id != user.pk:
        raise PermissionDenied("只能下载本人创建的导出文件。")
    now = timezone.now()
    if job.expires_at <= now:
        _expire_job(job)
        raise ValidationError("导出文件已过期，请重新创建。")
    if job.status != ExportJobStatus.SUCCESS:
        raise ValidationError("导出任务尚未生成可下载文件。")
    if job.download_count >= settings.EXPORT_MAX_DOWNLOADS:
        raise PermissionDenied("该导出文件已达到最大下载次数。")
    path = _validated_output_path(job.output_path)
    if not path.is_file():
        job.status = ExportJobStatus.FAILED
        job.error_message = "导出文件缺失。"
        job.save(update_fields=["status", "error_message", "updated_at"])
        raise ValidationError("导出文件缺失，请重新创建。")

    job.download_count += 1
    job.last_downloaded_at = now
    job.save(update_fields=["download_count", "last_downloaded_at", "updated_at"])
    record_audit_event(
        AuditEventType.EXPORT_DOWNLOADED,
        request=request,
        actor=user,
        corpus=job.corpus,
        metadata={
            "job_id": str(job.pk),
            "kind": job.kind,
            "download_count": job.download_count,
        },
    )
    return job, path


def expire_exports(*, user=None) -> int:
    queryset = ExportJob.objects.all()
    if user is not None:
        queryset = queryset.filter(requested_by=user)
    return _expire_due_jobs(queryset, now=timezone.now())


def _normalize_query(corpus: Corpus, kind: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
    if kind == ExportKind.KWIC:
        languages = _available_languages(corpus)
        data = parameters.dict() if hasattr(parameters, "dict") else dict(parameters)
        data.update({"page": 1, "page_size": 100})
        form = KwicSearchForm(data, available_languages=languages)
        if not form.is_valid() or not form.cleaned_data.get("q"):
            raise ValidationError(f"KWIC 导出条件无效：{form.errors.as_text()}")
        return {
            "q": form.cleaned_data["q"],
            "query_mode": form.cleaned_data["query_mode"],
            "language": form.cleaned_data["language"],
            "context": form.cleaned_data["context"],
            "sort_by": form.cleaned_data["sort_by"],
            "pos": form.cleaned_data["pos"],
        }

    if corpus.corpus_type not in PARALLEL_TYPES:
        raise ValidationError("该语料库不是中英平行语料。")
    units = _available_alignment_units(corpus)
    data = parameters.dict() if hasattr(parameters, "dict") else dict(parameters)
    data.update({"page": 1, "page_size": "100"})
    data.setdefault("alignment_unit", units[0])
    form = ParallelSearchForm(
        data,
        default_alignment_unit=units[0],
        available_alignment_units=units,
    )
    if not form.is_valid():
        raise ValidationError(f"ParaConc 导出条件无效：{form.errors.as_text()}")
    query = form.to_query()
    return {
        "q": query.q,
        "search_side": query.search_side,
        "zh_contains": query.zh_contains,
        "en_contains": query.en_contains,
        "zh_not_contains": query.zh_not_contains,
        "en_not_contains": query.en_not_contains,
        "alignment_unit": query.alignment_unit,
    }


def _require_export_permission(user, corpus: Corpus) -> None:
    if corpus.source_type != CorpusSourceType.USER or corpus.owner_id != user.pk:
        raise PermissionDenied("教师和演示语料禁止导出；只能导出本人语料。")
    if corpus.status != CorpusStatus.READY:
        raise ValidationError("语料库尚未加工完成。")


def _export_rows(job: ExportJob) -> tuple[Sequence[str], Iterator[Sequence[object]]]:
    if job.kind == ExportKind.KWIC:
        return (
            (
                "左文",
                "命中词",
                "右文",
                "来源文件",
                "段落序号",
                "句子序号",
                "L3",
                "L2",
                "L1",
                "R1",
                "R2",
                "R3",
            ),
            _kwic_rows(job),
        )
    return (
        ("全局序号", "语料内序号", "中文", "英文", "对齐单元", "对齐方法", "置信度"),
        _parallel_rows(job),
    )


def _kwic_rows(job: ExportJob) -> Iterator[Sequence[object]]:
    query = job.query
    engine_class = ComplexQueryEngine if query["query_mode"] == "cqp" else KwicSearchEngine
    engine = engine_class(data_root=settings.DATA_ROOT, corpus_id=str(job.corpus_id))
    page = 1
    while True:
        options = {
            "query": query["q"],
            "context_size": query["context"],
            "page": page,
            "page_size": 100,
            "sort_by": query["sort_by"],
            "pos": query["pos"],
        }
        if query["query_mode"] == "cqp":
            options["language"] = query["language"]
        result = engine.search(**options)
        for hit in result.hits:
            yield (
                hit.left,
                hit.keyword,
                hit.right,
                hit.source_filename,
                hit.paragraph_ordinal,
                hit.sentence_ordinal,
                hit.l3,
                hit.l2,
                hit.l1,
                hit.r1,
                hit.r2,
                hit.r3,
            )
        if page >= result.num_pages:
            break
        page += 1


def _parallel_rows(job: ExportJob) -> Iterator[Sequence[object]]:
    query = ParallelQuery(**job.query)
    engine = ParallelSearchEngine(data_root=settings.DATA_ROOT, corpus_id=str(job.corpus_id))
    yield from engine.iter_export_rows(query)


@transaction.atomic
def _mark_running(job_id) -> ExportJob:
    job = (
        ExportJob.objects.select_for_update()
        .select_related("corpus", "requested_by")
        .get(pk=job_id)
    )
    if job.status != ExportJobStatus.PENDING:
        raise ExportError(f"Export job must be pending, got: {job.status}")
    if job.expires_at <= timezone.now():
        _expire_job(job)
        raise ExportError("导出任务已过期。")
    job.status = ExportJobStatus.RUNNING
    job.progress = 10
    job.started_at = timezone.now()
    job.error_message = ""
    job.save(
        update_fields=["status", "progress", "started_at", "error_message", "updated_at"]
    )
    return job


@transaction.atomic
def _mark_success(job_id, *, final_path: Path, row_count: int) -> None:
    job = ExportJob.objects.select_for_update().get(pk=job_id)
    job.status = ExportJobStatus.SUCCESS
    job.progress = 100
    job.output_path = str(final_path)
    job.row_count = row_count
    job.finished_at = timezone.now()
    job.error_message = ""
    job.save(
        update_fields=[
            "status",
            "progress",
            "output_path",
            "row_count",
            "finished_at",
            "error_message",
            "updated_at",
        ]
    )


@transaction.atomic
def _mark_failed(job_id, message: str) -> None:
    job = ExportJob.objects.select_for_update().get(pk=job_id)
    job.status = ExportJobStatus.FAILED
    job.progress = 100
    job.error_message = message[:4000]
    job.finished_at = timezone.now()
    job.save(
        update_fields=["status", "progress", "error_message", "finished_at", "updated_at"]
    )


def _expire_due_jobs(queryset, *, now) -> int:
    expired = list(
        queryset.filter(
            expires_at__lte=now,
            status__in=[
                ExportJobStatus.PENDING,
                ExportJobStatus.SUCCESS,
                ExportJobStatus.FAILED,
            ],
        )
    )
    for job in expired:
        _expire_job(job)
    return len(expired)


def _expire_job(job: ExportJob) -> None:
    output_path = job.output_path
    job.status = ExportJobStatus.EXPIRED
    job.output_path = ""
    job.save(update_fields=["status", "output_path", "updated_at"])
    if output_path:
        with suppress(ValidationError):
            _validated_output_path(output_path).unlink(missing_ok=True)


def _validated_output_path(value: str) -> Path:
    root = (settings.DATA_ROOT / "exports").resolve()
    path = Path(value).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValidationError("导出文件路径无效。")
    return path


def _safe_spreadsheet_cell(value: object) -> str:
    text = str(value).replace("\x00", "").replace("\r", " ").replace("\n", " ")
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def _available_languages(corpus: Corpus) -> tuple[str, ...]:
    if corpus.language == CorpusLanguage.ZH_EN:
        return (CorpusLanguage.ZH, CorpusLanguage.EN)
    if corpus.language in {CorpusLanguage.ZH, CorpusLanguage.EN}:
        return (corpus.language,)
    return (CorpusLanguage.ZH, CorpusLanguage.EN)


def _available_alignment_units(corpus: Corpus) -> tuple[str, ...]:
    if corpus.corpus_type == CorpusType.PAIRED_RAW_ZH_EN:
        return ("paragraph",)
    if corpus.corpus_type == CorpusType.PAIRED_TAGGED_ZH_EN:
        return ("sentence", "paragraph")
    return ("sentence",)
