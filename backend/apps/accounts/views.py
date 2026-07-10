from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.views import LoginView, LogoutView
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy

from .forms import AccountApplicationForm, ApprovedUserAuthenticationForm
from .permissions import approved_user_required, get_user_profile, workspace_access_scope


def apply(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = AccountApplicationForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "申请已提交，请等待管理员审核。")
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
    return render(
        request,
        "accounts/dashboard.html",
        {
            "profile": get_user_profile(request.user),
            "access_scope": workspace_access_scope(request.user),
        },
    )
