from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.models import AuditEventType
from apps.audit.services import record_audit_event

from .models import (
    ApplicationStatus,
    QuotaRequestStatus,
    UploadQuotaRequest,
    UserProfile,
    UserRole,
)


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
def submit_quota_request(
    *,
    user: AbstractBaseUser,
    requested_max_file_bytes: int,
    requested_total_bytes: int,
    reason: str,
) -> UploadQuotaRequest:
    profile = UserProfile.objects.get(user=user)
    if not profile.is_approved or not user.is_active:
        raise PermissionDenied("只有已审核且启用的账号可以申请扩大上传配额。")
    if profile.role == UserRole.TEST:
        raise PermissionDenied("测试账号不能申请扩大上传配额。")
    if requested_max_file_bytes <= 0 or requested_total_bytes <= 0:
        raise ValidationError("配额必须大于 0。")
    if requested_max_file_bytes > requested_total_bytes:
        raise ValidationError("单文件配额不能超过账号总配额。")
    from apps.corpora.services import upload_limits_for

    if requested_total_bytes <= upload_limits_for(user).total_bytes:
        raise ValidationError("申请总配额必须高于当前配额。")
    if not reason.strip():
        raise ValidationError("请填写扩容理由。")
    try:
        quota_request = UploadQuotaRequest.objects.create(
            user=user,
            requested_max_file_bytes=requested_max_file_bytes,
            requested_total_bytes=requested_total_bytes,
            reason=reason.strip(),
        )
    except IntegrityError as exc:
        raise ValidationError("已有待审核的扩容申请，请勿重复提交。") from exc
    record_audit_event(
        AuditEventType.QUOTA_REQUEST,
        actor=user,
        metadata={
            "quota_request_id": str(quota_request.pk),
            "requested_max_file_bytes": requested_max_file_bytes,
            "requested_total_bytes": requested_total_bytes,
        },
    )
    return quota_request


@transaction.atomic
def review_quota_request(
    quota_request: UploadQuotaRequest,
    *,
    status: str,
    reviewer: AbstractBaseUser,
) -> UploadQuotaRequest:
    if not reviewer.is_active or not reviewer.is_staff:
        raise PermissionDenied("只有启用的后台管理员可以审核配额申请。")
    if status not in {QuotaRequestStatus.APPROVED, QuotaRequestStatus.REJECTED}:
        raise ValueError(f"Unsupported quota request status: {status}")
    locked = UploadQuotaRequest.objects.select_for_update().select_related("user").get(
        pk=quota_request.pk
    )
    if locked.status != QuotaRequestStatus.PENDING:
        raise ValidationError("只能审核待处理的配额申请。")
    locked.status = status
    locked.reviewed_by = reviewer
    locked.reviewed_at = timezone.now()
    locked.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    if status == QuotaRequestStatus.APPROVED:
        profile = UserProfile.objects.select_for_update().get(user=locked.user)
        profile.upload_max_file_bytes = locked.requested_max_file_bytes
        profile.upload_total_bytes = locked.requested_total_bytes
        profile.save(
            update_fields=["upload_max_file_bytes", "upload_total_bytes", "updated_at"]
        )
    record_audit_event(
        AuditEventType.ADMIN_ACTION,
        actor=reviewer,
        metadata={
            "action": "review_upload_quota",
            "quota_request_id": str(locked.pk),
            "user_id": str(locked.user_id),
            "status": status,
        },
    )
    return locked


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
