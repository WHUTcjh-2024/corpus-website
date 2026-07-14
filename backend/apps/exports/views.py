from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.permissions import approved_user_required
from apps.corpora.models import Corpus

from .models import ExportJob, ExportKind
from .services import (
    ExportError,
    acquire_download,
    create_export_job,
    dispatch_export_job,
    expire_exports,
)


@approved_user_required
def export_list(request: HttpRequest) -> HttpResponse:
    expire_exports(user=request.user)
    jobs = ExportJob.objects.filter(requested_by=request.user).select_related("corpus")[:100]
    return render(request, "exports/export_list.html", {"jobs": jobs})


@approved_user_required
@require_POST
def create_kwic_export(request: HttpRequest, corpus_id) -> HttpResponse:
    return _create_export(request, corpus_id, ExportKind.KWIC)


@approved_user_required
@require_POST
def create_parallel_export(request: HttpRequest, corpus_id) -> HttpResponse:
    return _create_export(request, corpus_id, ExportKind.PARALLEL)


@approved_user_required
@require_GET
def export_status(request: HttpRequest, job_id) -> JsonResponse:
    expire_exports(user=request.user)
    job = get_object_or_404(ExportJob, pk=job_id, requested_by=request.user)
    return JsonResponse(
        {
            "id": str(job.pk),
            "kind": job.kind,
            "status": job.status,
            "status_label": job.get_status_display(),
            "progress": job.progress,
            "row_count": job.row_count,
            "download_count": job.download_count,
            "expires_at": job.expires_at.isoformat(),
            "error_message": job.error_message,
        }
    )


@approved_user_required
@require_GET
def export_download(request: HttpRequest, job_id) -> HttpResponse:
    try:
        job, path = acquire_download(job_id=job_id, user=request.user, request=request)
    except ExportJob.DoesNotExist:
        return HttpResponse("导出任务不存在。", status=404)
    except PermissionDenied as exc:
        return HttpResponse(str(exc), status=403)
    except ValidationError as exc:
        return HttpResponse(str(exc), status=409)
    response = FileResponse(path.open("rb"), content_type="text/tab-separated-values")
    response["Content-Disposition"] = f'attachment; filename="{job.kind}-{job.pk}.tsv"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _create_export(request: HttpRequest, corpus_id, kind: str) -> HttpResponse:
    corpus = get_object_or_404(Corpus, pk=corpus_id)
    raw_query = request.POST.get("query_string", "")
    if len(raw_query) > 5000:
        messages.error(request, "导出条件过长。")
        return _return_to_search(kind, corpus)
    parameters = QueryDict(raw_query)
    try:
        job = create_export_job(
            user=request.user,
            corpus=corpus,
            kind=kind,
            parameters=parameters,
            request=request,
        )
        dispatch_export_job(job)
    except PermissionDenied as exc:
        return HttpResponse(str(exc), status=403)
    except (ValidationError, ExportError) as exc:
        messages.error(request, str(exc))
        return _return_to_search(kind, corpus)
    messages.success(request, "导出任务已进入后台队列，完成后可在“我的导出”下载。")
    return redirect("exports:list")


def _return_to_search(kind: str, corpus: Corpus) -> HttpResponse:
    if kind == ExportKind.PARALLEL:
        return redirect("parallel:search", corpus_id=corpus.pk)
    return redirect("search:kwic", corpus_id=corpus.pk)
