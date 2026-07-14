from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import approved_user_required
from apps.audit.models import AuditEventType
from apps.audit.services import record_audit_event
from apps.parallel.engine import (
    ParallelIndexCorrupt,
    ParallelIndexUnavailable,
    ParallelSearchEngine,
)

from apps.processing.exceptions import ProcessingError
from apps.processing.models import ProcessingTask
from apps.processing.services import dispatch_processing_task

from .forms import CorpusUploadForm, PersonalCorpusForm
from .models import Corpus, CorpusStatus, CorpusType
from .services import (
    can_create_personal_corpus,
    can_upload_personal_corpus,
    delete_user_corpus,
    retry_user_corpus,
    upload_limits_for,
    uploaded_bytes_for,
    visible_corpora_for,
)


def _with_latest_tasks(queryset):
    return queryset.prefetch_related(
        Prefetch(
            "processing_tasks",
            queryset=ProcessingTask.objects.order_by("-created_at"),
            to_attr="task_history",
        )
    )


@approved_user_required
def corpus_list(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "corpora/corpus_list.html",
        {
            "corpora": _with_latest_tasks(visible_corpora_for(request.user)),
            "can_create_personal": can_create_personal_corpus(request.user),
            "can_upload_personal": can_upload_personal_corpus(request.user),
        },
    )


@approved_user_required
def my_corpora(request: HttpRequest) -> HttpResponse:
    corpora = _with_latest_tasks(
        visible_corpora_for(request.user).filter(source_type="user", owner=request.user)
    )
    limits = upload_limits_for(request.user)
    return render(
        request,
        "corpora/my_corpora.html",
        {
            "corpora": corpora,
            "used_bytes": uploaded_bytes_for(request.user),
            "total_bytes": limits.total_bytes,
            "can_upload_personal": can_upload_personal_corpus(request.user),
        },
    )


@approved_user_required
def corpus_create(request: HttpRequest) -> HttpResponse:
    if not can_create_personal_corpus(request.user):
        return HttpResponse("当前账号不能登记个人语料库。", status=403)
    if request.method == "POST":
        form = PersonalCorpusForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                corpus = form.save()
            except PermissionDenied:
                return HttpResponse("当前账号不能登记个人语料库。", status=403)
            return redirect("corpora:documentation", corpus_id=corpus.pk)
    else:
        form = PersonalCorpusForm(user=request.user)
    return render(request, "corpora/corpus_create.html", {"form": form})


@approved_user_required
def corpus_upload(request: HttpRequest) -> HttpResponse:
    if not can_upload_personal_corpus(request.user):
        return HttpResponse("当前账号不能上传个人语料库。", status=403)
    if request.method == "POST":
        form = CorpusUploadForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            corpus = None
            try:
                corpus, task = form.save()
                record_audit_event(
                    AuditEventType.CORPUS_UPLOAD,
                    request=request,
                    corpus=corpus,
                    metadata={
                        "task_id": str(task.pk),
                        "corpus_type": corpus.corpus_type,
                        "file_count": corpus.files.count(),
                        "size_bytes": sum(item.size_bytes for item in corpus.files.all()),
                    },
                )
                dispatch_processing_task(task)
            except (PermissionDenied, ValidationError) as exc:
                form.add_error(None, exc)
            except ProcessingError as exc:
                messages.error(request, f"文件已接收，但加工任务启动失败：{exc}")
                if corpus is not None:
                    return redirect("corpora:documentation", corpus_id=corpus.pk)
                form.add_error(None, "加工任务创建失败，请稍后重试。")
            else:
                messages.success(request, "上传成功，语料已进入后台加工队列。")
                return redirect("corpora:documentation", corpus_id=corpus.pk)
    else:
        form = CorpusUploadForm(user=request.user)
    limits = upload_limits_for(request.user)
    used_bytes = uploaded_bytes_for(request.user)
    return render(
        request,
        "corpora/corpus_upload.html",
        {
            "form": form,
            "max_file_mb": limits.max_file_bytes // (1024 * 1024),
            "total_mb": limits.total_bytes // (1024 * 1024),
            "used_bytes": used_bytes,
        },
    )


@approved_user_required
def corpus_documentation(request: HttpRequest, corpus_id) -> HttpResponse:
    corpus = get_object_or_404(
        visible_corpora_for(request.user).select_related("documentation"),
        pk=corpus_id,
    )
    latest_task = corpus.processing_tasks.order_by("-created_at").first()
    alignment_preview = ()
    alignment_preview_error = ""
    parallel_types = {
        CorpusType.ALIGNED_TSV,
        CorpusType.PAIRED_RAW_ZH_EN,
        CorpusType.PAIRED_TAGGED_ZH_EN,
    }
    if corpus.status == CorpusStatus.READY and corpus.corpus_type in parallel_types:
        alignment_unit = (
            "paragraph" if corpus.corpus_type == CorpusType.PAIRED_RAW_ZH_EN else "sentence"
        )
        try:
            alignment_preview = ParallelSearchEngine(
                data_root=settings.DATA_ROOT,
                corpus_id=str(corpus.pk),
            ).preview(alignment_unit=alignment_unit)
        except (ParallelIndexUnavailable, ParallelIndexCorrupt) as exc:
            alignment_preview_error = str(exc)
    return render(
        request,
        "corpora/documentation.html",
        {
            "corpus": corpus,
            "documentation": corpus.documentation,
            "latest_task": latest_task,
            "can_manage": corpus.source_type == "user" and corpus.owner_id == request.user.pk,
            "alignment_preview": alignment_preview,
            "alignment_preview_error": alignment_preview_error,
        },
    )


@approved_user_required
def corpus_status(request: HttpRequest, corpus_id) -> JsonResponse:
    corpus = get_object_or_404(visible_corpora_for(request.user), pk=corpus_id)
    task = corpus.processing_tasks.order_by("-created_at").first()
    return JsonResponse(
        {
            "corpus_id": str(corpus.pk),
            "status": corpus.status,
            "status_label": corpus.get_status_display(),
            "stage": corpus.stage,
            "task": (
                {
                    "id": str(task.pk),
                    "status": task.status,
                    "status_label": task.get_status_display(),
                    "progress": task.progress,
                    "error_message": task.error_message,
                }
                if task
                else None
            ),
        }
    )


@approved_user_required
@require_POST
def corpus_retry(request: HttpRequest, corpus_id) -> HttpResponse:
    corpus = get_object_or_404(Corpus, pk=corpus_id)
    try:
        task = retry_user_corpus(corpus=corpus, user=request.user)
        dispatch_processing_task(task)
    except PermissionDenied as exc:
        return HttpResponse(str(exc), status=403)
    except (ValidationError, ProcessingError) as exc:
        messages.error(request, str(exc))
    else:
        record_audit_event(
            AuditEventType.CORPUS_RETRY,
            request=request,
            corpus=corpus,
            metadata={"task_id": str(task.pk)},
        )
        messages.success(request, "已重新提交后台加工任务。")
    return redirect("corpora:documentation", corpus_id=corpus.pk)


@approved_user_required
@require_POST
def corpus_delete(request: HttpRequest, corpus_id) -> HttpResponse:
    corpus = get_object_or_404(Corpus, pk=corpus_id)
    deleted_metadata = {
        "corpus_id": str(corpus.pk),
        "corpus_name": corpus.name,
        "corpus_type": corpus.corpus_type,
    }
    try:
        delete_user_corpus(corpus=corpus, user=request.user)
    except PermissionDenied as exc:
        return HttpResponse(str(exc), status=403)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect("corpora:documentation", corpus_id=corpus.pk)
    record_audit_event(
        AuditEventType.CORPUS_DELETE,
        request=request,
        metadata=deleted_metadata,
    )
    messages.success(request, "个人语料及其加工索引已安全删除。")
    return redirect("corpora:mine")
