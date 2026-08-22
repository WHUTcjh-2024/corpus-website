from __future__ import annotations

from functools import wraps
from typing import Any

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.views import redirect_to_login
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.accounts.models import ApplicationStatus, UserProfile
from apps.accounts.services import review_application
from apps.audit.models import AuditEventType
from apps.audit.services import record_audit_event
from apps.corpora.models import Corpus, CorpusSourceType, CorpusStatus
from apps.feedback.models import FeedbackStatus, FeedbackTicket
from apps.processing.exceptions import ProcessingError
from apps.processing.models import ProcessingTask, ProcessingTaskStatus
from apps.processing.services import dispatch_processing_task

from .forms import (
    AccountReviewForm,
    AnnouncementForm,
    CorpusVisibilityForm,
    FeedbackResolutionForm,
    ManagedCorpusUploadForm,
)
from .models import Announcement


def staff_required(view_func):
    @wraps(view_func)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_active or not request.user.is_staff:
            return HttpResponseForbidden("仅平台管理员可以使用管理工作台。")
        return view_func(request, *args, **kwargs)

    return wrapped


def _portal_context(**extra: Any) -> dict[str, Any]:
    return extra


@staff_required
def dashboard(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    return render(
        request,
        "admin_portal/dashboard.html",
        _portal_context(
            corpus_count=Corpus.objects.exclude(source_type=CorpusSourceType.USER).count(),
            pending_processing_count=ProcessingTask.objects.filter(
                status__in=[ProcessingTaskStatus.PENDING, ProcessingTaskStatus.RUNNING]
            ).count(),
            pending_account_count=UserProfile.objects.filter(status=ApplicationStatus.PENDING).count(),
            open_feedback_count=FeedbackTicket.objects.exclude(
                status__in=[FeedbackStatus.RESOLVED, FeedbackStatus.CLOSED]
            ).count(),
            active_announcement_count=Announcement.objects.filter(
                is_published=True,
                starts_at__lte=now,
            )
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
            .count(),
            recent_corpora=Corpus.objects.exclude(source_type=CorpusSourceType.USER)
            .annotate(grant_count=Count("access_grants"))
            .order_by("-updated_at")[:6],
            pending_profiles=UserProfile.objects.filter(status=ApplicationStatus.PENDING)
            .select_related("user")
            .order_by("created_at")[:5],
            recent_feedback=FeedbackTicket.objects.exclude(
                status__in=[FeedbackStatus.RESOLVED, FeedbackStatus.CLOSED]
            )
            .select_related("user")
            .order_by("-updated_at")[:5],
        ),
    )


@staff_required
def corpus_list(request: HttpRequest) -> HttpResponse:
    queryset = (
        Corpus.objects.exclude(source_type=CorpusSourceType.USER)
        .annotate(grant_count=Count("access_grants"))
        .select_related("documentation")
        .order_by("-updated_at")
    )
    status = request.GET.get("status", "")
    if status in CorpusStatus.values:
        queryset = queryset.filter(status=status)
    return render(
        request,
        "admin_portal/corpus_list.html",
        _portal_context(corpora=queryset, selected_status=status, statuses=CorpusStatus.choices),
    )


@staff_required
@require_http_methods(["GET", "POST"])
def corpus_upload(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = ManagedCorpusUploadForm(request.POST, request.FILES)
        if form.is_valid():
            corpus = None
            try:
                corpus, task = form.save(actor=request.user)
                record_audit_event(
                    AuditEventType.ADMIN_ACTION,
                    actor=request.user,
                    corpus=corpus,
                    metadata={
                        "action": "managed_corpus_upload",
                        "task_id": str(task.pk),
                        "recipient_count": corpus.access_grants.count(),
                    },
                )
                dispatch_processing_task(task)
            except (PermissionDenied, ValidationError) as exc:
                form.add_error(None, str(exc))
            except ProcessingError as exc:
                messages.error(request, f"文件已保存，但加工任务启动失败：{exc}")
                if corpus:
                    return redirect("admin_portal:corpus_visibility", corpus_id=corpus.pk)
                form.add_error(None, "加工任务创建失败，请稍后重试。")
            else:
                messages.success(request, "平台语料已入队加工；授权用户刷新后即可看到该语料的状态。")
                return redirect("admin_portal:corpus_visibility", corpus_id=corpus.pk)
    else:
        form = ManagedCorpusUploadForm()
    return render(request, "admin_portal/corpus_upload.html", _portal_context(form=form))


@staff_required
@require_http_methods(["GET", "POST"])
def corpus_visibility(request: HttpRequest, corpus_id) -> HttpResponse:
    corpus = get_object_or_404(
        Corpus.objects.exclude(source_type=CorpusSourceType.USER).prefetch_related("access_grants__user"),
        pk=corpus_id,
    )
    if request.method == "POST":
        form = CorpusVisibilityForm(request.POST, corpus=corpus)
        if form.is_valid():
            corpus = form.save(actor=request.user)
            record_audit_event(
                AuditEventType.ADMIN_ACTION,
                actor=request.user,
                corpus=corpus,
                metadata={"action": "update_corpus_visibility", "grant_count": corpus.access_grants.count()},
            )
            messages.success(request, "可见范围已保存；被授权用户刷新页面后立即生效。")
            return redirect("admin_portal:corpus_visibility", corpus_id=corpus.pk)
    else:
        form = CorpusVisibilityForm(corpus=corpus)
    return render(
        request,
        "admin_portal/corpus_visibility.html",
        _portal_context(corpus=corpus, form=form, current_grants=corpus.access_grants.select_related("user")),
    )


@staff_required
def user_list(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    profiles = UserProfile.objects.select_related("user", "reviewed_by").order_by("status", "created_at")
    if status in ApplicationStatus.values:
        profiles = profiles.filter(status=status)
    return render(
        request,
        "admin_portal/user_list.html",
        _portal_context(profiles=profiles, selected_status=status, statuses=ApplicationStatus.choices),
    )


@staff_required
@require_http_methods(["GET", "POST"])
def user_review(request: HttpRequest, profile_id: int) -> HttpResponse:
    profile = get_object_or_404(UserProfile.objects.select_related("user"), pk=profile_id)
    if request.method == "POST":
        form = AccountReviewForm(request.POST)
        if form.is_valid():
            review_application(
                profile,
                status=form.cleaned_data["status"],
                reviewer=request.user,
                role=form.cleaned_data["role"],
            )
            record_audit_event(
                AuditEventType.ADMIN_ACTION,
                actor=request.user,
                metadata={"action": "review_account", "user_id": profile.user_id, "status": form.cleaned_data["status"]},
            )
            messages.success(request, "账号审核结果已保存。")
            return redirect("admin_portal:user_list")
    else:
        default_status = (
            profile.status
            if profile.status in {ApplicationStatus.APPROVED, ApplicationStatus.REJECTED, ApplicationStatus.DISABLED}
            else ApplicationStatus.APPROVED
        )
        form = AccountReviewForm(initial={"role": profile.role, "status": default_status})
    return render(request, "admin_portal/user_review.html", _portal_context(profile=profile, form=form))


@staff_required
def announcement_list(request: HttpRequest) -> HttpResponse:
    announcements = Announcement.objects.select_related("created_by", "published_by").annotate(
        recipient_count=Count("recipients")
    )
    return render(request, "admin_portal/announcement_list.html", _portal_context(announcements=announcements))


@staff_required
@require_http_methods(["GET", "POST"])
def announcement_edit(request: HttpRequest, announcement_id: int | None = None) -> HttpResponse:
    announcement = get_object_or_404(Announcement, pk=announcement_id) if announcement_id else Announcement()
    if request.method == "POST":
        form = AnnouncementForm(request.POST, instance=announcement)
        if form.is_valid():
            announcement = form.save(actor=request.user)
            record_audit_event(
                AuditEventType.ADMIN_ACTION,
                actor=request.user,
                metadata={"action": "save_announcement", "announcement_id": announcement.pk, "published": announcement.is_published},
            )
            messages.success(request, "公告已保存。已发布公告会在目标用户刷新工作台后展示。")
            return redirect("admin_portal:announcement_list")
    else:
        form = AnnouncementForm(instance=announcement)
    return render(
        request,
        "admin_portal/announcement_form.html",
        _portal_context(form=form, announcement=announcement),
    )


@staff_required
def feedback_list(request: HttpRequest) -> HttpResponse:
    tickets = FeedbackTicket.objects.select_related("user").order_by("status", "-updated_at")
    return render(request, "admin_portal/feedback_list.html", _portal_context(tickets=tickets))


@staff_required
@require_http_methods(["GET", "POST"])
def feedback_detail(request: HttpRequest, ticket_id) -> HttpResponse:
    ticket = get_object_or_404(FeedbackTicket.objects.select_related("user"), pk=ticket_id)
    if request.method == "POST":
        form = FeedbackResolutionForm(request.POST, instance=ticket)
        if form.is_valid():
            ticket = form.save()
            record_audit_event(
                AuditEventType.ADMIN_ACTION,
                actor=request.user,
                metadata={"action": "update_feedback", "ticket_id": str(ticket.pk), "status": ticket.status},
            )
            messages.success(request, "反馈处理状态已更新。")
            return redirect("admin_portal:feedback_list")
    else:
        form = FeedbackResolutionForm(instance=ticket)
    return render(request, "admin_portal/feedback_detail.html", _portal_context(ticket=ticket, form=form))
