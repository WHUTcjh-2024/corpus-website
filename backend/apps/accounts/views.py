from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.db.models import Sum

from apps.corpora.models import CorpusStatus
from apps.corpora.services import upload_limits_for, visible_corpora_for
from apps.exports.models import ExportJob

from .forms import (
    AccountApplicationForm,
    ApprovedUserAuthenticationForm,
    UploadQuotaRequestForm,
)
from .models import QuotaRequestStatus, UploadQuotaRequest
from .permissions import approved_user_required, get_user_profile, workspace_access_scope


def apply(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = AccountApplicationForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "申请已提交，请等待管理员审核，或您可以联系管理员进行审核。")
            return redirect("accounts:application_submitted")
    else:
        form = AccountApplicationForm()
    return render(request, "accounts/apply.html", {"form": form})


def application_submitted(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/application_submitted.html")


class AccountLoginView(LoginView):
    authentication_form = ApprovedUserAuthenticationForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


class AccountLogoutView(LogoutView):
    next_page = reverse_lazy("home")


@approved_user_required
def dashboard(request: HttpRequest) -> HttpResponse:
    visible_corpora = visible_corpora_for(request.user).select_related("documentation")
    recent_corpora = list(visible_corpora.order_by("-updated_at")[:5])
    export_jobs = ExportJob.objects.filter(requested_by=request.user).select_related("corpus")
    return render(
        request,
        "accounts/dashboard.html",
        {
            "profile": get_user_profile(request.user),
            "access_scope": workspace_access_scope(request.user),
            "corpus_count": visible_corpora.count(),
            "ready_corpus_count": visible_corpora.filter(status=CorpusStatus.READY).count(),
            "token_count": visible_corpora.aggregate(total=Sum("documentation__token_count"))["total"] or 0,
            "export_count": export_jobs.count(),
            "recent_corpora": recent_corpora,
            "recent_exports": list(export_jobs[:5]),
        },
    )


@approved_user_required
def quota_request(request: HttpRequest) -> HttpResponse:
    limits = upload_limits_for(request.user)
    pending = UploadQuotaRequest.objects.filter(
        user=request.user,
        status=QuotaRequestStatus.PENDING,
    ).first()
    if request.method == "POST" and pending is None:
        form = UploadQuotaRequestForm(
            request.POST,
            user=request.user,
            current_max_file_bytes=limits.max_file_bytes,
            current_total_bytes=limits.total_bytes,
        )
        if form.is_valid():
            try:
                form.save()
            except PermissionDenied as exc:
                return HttpResponse(str(exc), status=403)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "扩容申请已提交，请等待管理员审核。")
                return redirect("corpora:mine")
    else:
        form = UploadQuotaRequestForm(
            user=request.user,
            current_max_file_bytes=limits.max_file_bytes,
            current_total_bytes=limits.total_bytes,
        )
    return render(
        request,
        "accounts/quota_request.html",
        {"form": form, "limits": limits, "pending_request": pending},
    )
