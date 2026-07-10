from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from functools import wraps
from typing import Any

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden

from .models import ApplicationStatus, UserProfile, UserRole


class AccessScope(StrEnum):
    NONE = "none"
    DEMO_ONLY = "demo_only"
    STANDARD = "standard"
    ADMIN = "admin"


def get_user_profile(user: Any) -> UserProfile | None:
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        return user.account_profile
    except UserProfile.DoesNotExist:
        return None


def workspace_access_scope(user: Any) -> AccessScope:
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return AccessScope.NONE
    if user.is_superuser:
        return AccessScope.ADMIN

    profile = get_user_profile(user)
    if profile is None or profile.status != ApplicationStatus.APPROVED:
        return AccessScope.NONE
    if profile.role == UserRole.TEST:
        return AccessScope.DEMO_ONLY
    if profile.role == UserRole.ADMIN and user.is_staff:
        return AccessScope.ADMIN
    return AccessScope.STANDARD


def approved_user_required(
    view_func: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    @wraps(view_func)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        if workspace_access_scope(request.user) == AccessScope.NONE:
            return HttpResponseForbidden("账号尚未审核通过或已被停用。")
        return view_func(request, *args, **kwargs)

    return wrapped


def admin_user_required(
    view_func: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    @wraps(view_func)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if workspace_access_scope(request.user) != AccessScope.ADMIN:
            return HttpResponseForbidden("仅管理员可访问。")
        return view_func(request, *args, **kwargs)

    return wrapped
