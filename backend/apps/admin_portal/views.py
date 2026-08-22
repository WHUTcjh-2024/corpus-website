from __future__ import annotations

from functools import wraps
from typing import Any

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.accounts.models import ApplicationStatus, UserProfile
from apps.accounts.services import review_application
from apps.audit.models import AuditEventType
from apps.audit.services import record_audit_event
from apps.corpora.models import Corpus, CorpusSourceType
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
            return HttpResponseForbidden("仅平台管理员可以使用管理后台。")
        return view_func(request, *args, **kwargs)

    return wrapped


def _dashboard_url(section: str = "") -> str:
    url = reverse("admin_portal:dashboard")
    return f"{url}#{section}" if section else url


def _account_initial(profile: UserProfile) -> dict[str, str]:
    status = profile.status
    if status not in {
        ApplicationStatus.APPROVED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.DISABLED,
    }:
        status = ApplicationStatus.APPROVED
    return {"role": profile.role, "status": status}


def _render_dashboard(
    request: HttpRequest,
    *,
    upload_form: ManagedCorpusUploadForm | None = None,
    visibility_forms: dict[Any, CorpusVisibilityForm] | None = None,
    account_forms: dict[int, AccountReviewForm] | None = None,
    announcement_form: AnnouncementForm | None = None,
    announcement_forms: dict[int, AnnouncementForm] | None = None,
    feedback_forms: dict[Any, FeedbackResolutionForm] | None = None,
) -> HttpResponse:
    now = timezone.now()
    visibility_forms = visibility_forms or {}
    account_forms = account_forms or {}
    announcement_forms = announcement_forms or {}
    feedback_forms = feedback_forms or {}

    corpora = list(
        Corpus.objects.exclude(source_type=CorpusSourceType.USER)
        .annotate(grant_count=Count("access_grants"))
        .prefetch_related("access_grants__user")
        .order_by("-updated_at")
    )
    profiles = list(
        UserProfile.objects.select_related("user", "reviewed_by")
        .annotate(
            review_priority=Case(
                When(status=ApplicationStatus.PENDING, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by("review_priority", "created_at")
    )
    announcements = list(
        Announcement.objects.select_related("created_by", "published_by")
        .annotate(recipient_count=Count("recipients"))
        .order_by("-is_published", "-starts_at", "-created_at")
    )
    feedback_tickets = list(
        FeedbackTicket.objects.select_related("user")
        .annotate(
            resolution_priority=Case(
                When(status__in=[FeedbackStatus.RESOLVED, FeedbackStatus.CLOSED], then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        .order_by("resolution_priority", "-updated_at")
    )

    context = {
        "corpus_count": len(corpora),
        "pending_processing_count": ProcessingTask.objects.filter(
            status__in=[ProcessingTaskStatus.PENDING, ProcessingTaskStatus.RUNNING]
        ).count(),
        "pending_account_count": sum(profile.status == ApplicationStatus.PENDING for profile in profiles),
        "open_feedback_count": sum(
            ticket.status not in {FeedbackStatus.RESOLVED, FeedbackStatus.CLOSED}
            for ticket in feedback_tickets
        ),
        "active_announcement_count": Announcement.objects.filter(
            is_published=True,
            starts_at__lte=now,
        )
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        .count(),
        "upload_form": upload_form or ManagedCorpusUploadForm(),
        "corpus_rows": [
            {
                "corpus": corpus,
                "form": visibility_forms.get(corpus.pk)
                or CorpusVisibilityForm(corpus=corpus, prefix=f"visibility_{corpus.pk}"),
            }
            for corpus in corpora
        ],
        "account_rows": [
            {
                "profile": profile,
                "form": account_forms.get(profile.pk)
                or AccountReviewForm(initial=_account_initial(profile), prefix=f"account_{profile.pk}"),
            }
            for profile in profiles
        ],
        "announcement_form": announcement_form or AnnouncementForm(),
        "announcement_rows": [
            {
                "announcement": announcement,
                "form": announcement_forms.get(announcement.pk)
                or AnnouncementForm(instance=announcement, prefix=f"announcement_{announcement.pk}"),
            }
            for announcement in announcements
        ],
        "feedback_rows": [
            {
                "ticket": ticket,
                "form": feedback_forms.get(ticket.pk)
                or FeedbackResolutionForm(instance=ticket, prefix=f"feedback_{ticket.pk}"),
            }
            for ticket in feedback_tickets
        ],
    }
    return render(request, "admin_portal/dashboard.html", context)


@staff_required
def dashboard(request: HttpRequest) -> HttpResponse:
    return _render_dashboard(request)


@staff_required
def corpus_list(request: HttpRequest) -> HttpResponse:
    return redirect(_dashboard_url("corpora"))


@staff_required
@require_http_methods(["GET", "POST"])
def corpus_upload(request: HttpRequest) -> HttpResponse:
    if request.method == "GET":
        return redirect(_dashboard_url("corpora"))

    form = ManagedCorpusUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return _render_dashboard(request, upload_form=form)

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
        return _render_dashboard(request, upload_form=form)
    except ProcessingError as exc:
        messages.error(request, f"文件已保存，但加工任务启动失败：{exc}")
        return redirect(_dashboard_url("corpora"))

    messages.success(request, "平台语料已进入加工队列；被授权用户刷新页面后即可看到语料状态。")
    return redirect(_dashboard_url("corpora"))


@staff_required
@require_http_methods(["GET", "POST"])
def corpus_visibility(request: HttpRequest, corpus_id) -> HttpResponse:
    if request.method == "GET":
        return redirect(_dashboard_url("corpora"))

    corpus = get_object_or_404(
        Corpus.objects.exclude(source_type=CorpusSourceType.USER).prefetch_related("access_grants__user"),
        pk=corpus_id,
    )
    form = CorpusVisibilityForm(request.POST, corpus=corpus, prefix=f"visibility_{corpus.pk}")
    if not form.is_valid():
        return _render_dashboard(request, visibility_forms={corpus.pk: form})

    corpus = form.save(actor=request.user)
    record_audit_event(
        AuditEventType.ADMIN_ACTION,
        actor=request.user,
        corpus=corpus,
        metadata={"action": "update_corpus_visibility", "grant_count": corpus.access_grants.count()},
    )
    messages.success(request, "可见范围已保存；被授权用户刷新后立即生效。")
    return redirect(_dashboard_url("corpora"))


@staff_required
def user_list(request: HttpRequest) -> HttpResponse:
    return redirect(_dashboard_url("accounts"))


@staff_required
@require_http_methods(["GET", "POST"])
def user_review(request: HttpRequest, profile_id: int) -> HttpResponse:
    if request.method == "GET":
        return redirect(_dashboard_url("accounts"))

    profile = get_object_or_404(UserProfile.objects.select_related("user"), pk=profile_id)
    form = AccountReviewForm(request.POST, prefix=f"account_{profile.pk}")
    if not form.is_valid():
        return _render_dashboard(request, account_forms={profile.pk: form})

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
    return redirect(_dashboard_url("accounts"))


@staff_required
def announcement_list(request: HttpRequest) -> HttpResponse:
    return redirect(_dashboard_url("announcements"))


@staff_required
@require_http_methods(["GET", "POST"])
def announcement_edit(request: HttpRequest, announcement_id: int | None = None) -> HttpResponse:
    if request.method == "GET":
        return redirect(_dashboard_url("announcements"))

    announcement = get_object_or_404(Announcement, pk=announcement_id) if announcement_id else Announcement()
    prefix = f"announcement_{announcement.pk}" if announcement.pk else None
    form = AnnouncementForm(request.POST, instance=announcement, prefix=prefix)
    if not form.is_valid():
        if announcement.pk:
            return _render_dashboard(request, announcement_forms={announcement.pk: form})
        return _render_dashboard(request, announcement_form=form)

    announcement = form.save(actor=request.user)
    record_audit_event(
        AuditEventType.ADMIN_ACTION,
        actor=request.user,
        metadata={"action": "save_announcement", "announcement_id": announcement.pk, "published": announcement.is_published},
    )
    messages.success(request, "公告已保存。已发布公告会在目标用户刷新工作台后展示。")
    return redirect(_dashboard_url("announcements"))


@staff_required
def feedback_list(request: HttpRequest) -> HttpResponse:
    return redirect(_dashboard_url("feedback"))


@staff_required
@require_http_methods(["GET", "POST"])
def feedback_detail(request: HttpRequest, ticket_id) -> HttpResponse:
    if request.method == "GET":
        return redirect(_dashboard_url("feedback"))

    ticket = get_object_or_404(FeedbackTicket.objects.select_related("user"), pk=ticket_id)
    form = FeedbackResolutionForm(request.POST, instance=ticket, prefix=f"feedback_{ticket.pk}")
    if not form.is_valid():
        return _render_dashboard(request, feedback_forms={ticket.pk: form})

    ticket = form.save()
    record_audit_event(
        AuditEventType.ADMIN_ACTION,
        actor=request.user,
        metadata={"action": "update_feedback", "ticket_id": str(ticket.pk), "status": ticket.status},
    )
    messages.success(request, "反馈处理状态已更新。")
    return redirect(_dashboard_url("feedback"))
