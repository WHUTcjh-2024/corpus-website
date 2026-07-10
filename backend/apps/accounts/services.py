from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.utils import timezone

from .models import ApplicationStatus, UserProfile, UserRole


@dataclass(frozen=True, slots=True)
class ApplicationData:
    username: str
    password: str
    full_name: str
    organization: str
    email: str
    requested_role: str
    use_purpose: str
    application_reason: str


@transaction.atomic
def submit_application(data: ApplicationData) -> UserProfile:
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username=data.username,
        email=data.email,
        password=data.password,
    )
    return UserProfile.objects.create(
        user=user,
        full_name=data.full_name,
        organization=data.organization,
        email=data.email,
        role=data.requested_role,
        requested_role=data.requested_role,
        use_purpose=data.use_purpose,
        application_reason=data.application_reason,
        status=ApplicationStatus.PENDING,
    )


@transaction.atomic
def review_application(
    profile: UserProfile,
    *,
    status: str,
    reviewer: AbstractBaseUser,
    role: str | None = None,
) -> UserProfile:
    if status not in ApplicationStatus.values:
        raise ValueError(f"Unsupported application status: {status}")
    if role is not None and role not in UserRole.values:
        raise ValueError(f"Unsupported user role: {role}")

    profile.status = status
    if role is not None:
        profile.role = role
    profile.reviewed_by = reviewer
    profile.reviewed_at = timezone.now()
    profile.save(
        update_fields=[
            "status",
            "role",
            "reviewed_by",
            "reviewed_at",
            "updated_at",
        ]
    )
    profile.user.refresh_from_db(fields=["email", "is_active"])
    return profile


@transaction.atomic
def ensure_seed_account(
    *,
    username: str,
    email: str,
    password: str,
    role: str,
    full_name: str,
    is_admin: bool = False,
) -> tuple[AbstractBaseUser, bool]:
    if role not in UserRole.values:
        raise ValueError(f"Unsupported user role: {role}")

    user_model = get_user_model()
    user, created = user_model.objects.get_or_create(
        username=username,
        defaults={"email": email},
    )
    user.email = email
    user.is_active = True
    user.is_staff = is_admin
    user.is_superuser = is_admin
    user.set_password(password)
    user.save()

    UserProfile.objects.update_or_create(
        user=user,
        defaults={
            "full_name": full_name,
            "organization": "系统内置账号",
            "email": email,
            "role": role,
            "requested_role": "",
            "use_purpose": "系统验收",
            "application_reason": "由 seed_accounts 命令创建",
            "status": ApplicationStatus.APPROVED,
        },
    )
    return user, created
