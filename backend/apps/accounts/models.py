from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class UserRole(models.TextChoices):
    TEST = "test", "测试用户"
    JUNIOR = "junior", "初级用户"
    MIDDLE = "middle", "中级用户"
    ADVANCED = "advanced", "高级用户"
    ADMIN = "admin", "管理员"


class ApplicationStatus(models.TextChoices):
    PENDING = "pending", "待审核"
    APPROVED = "approved", "已通过"
    REJECTED = "rejected", "已拒绝"
    DISABLED = "disabled", "已停用"


class QuotaRequestStatus(models.TextChoices):
    PENDING = "pending", "待审核"
    APPROVED = "approved", "已通过"
    REJECTED = "rejected", "已拒绝"


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account_profile",
        verbose_name="用户",
    )
    full_name = models.CharField("姓名", max_length=100)
    organization = models.CharField("单位", max_length=200)
    email = models.EmailField("申请邮箱", unique=True)
    role = models.CharField(
        "当前等级",
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.JUNIOR,
        db_index=True,
    )
    requested_role = models.CharField(
        "申请等级",
        max_length=20,
        choices=[
            (UserRole.JUNIOR, UserRole.JUNIOR.label),
            (UserRole.MIDDLE, UserRole.MIDDLE.label),
            (UserRole.ADVANCED, UserRole.ADVANCED.label),
        ],
        blank=True,
    )
    use_purpose = models.CharField("使用目的", max_length=200)
    application_reason = models.TextField("申请理由")
    status = models.CharField(
        "申请状态",
        max_length=20,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="reviewed_account_profiles",
        null=True,
        blank=True,
        verbose_name="审核人",
    )
    reviewed_at = models.DateTimeField("审核时间", null=True, blank=True)
    upload_max_file_bytes = models.PositiveBigIntegerField(
        "单文件配额覆盖（字节）",
        null=True,
        blank=True,
        help_text="留空使用角色默认值。",
    )
    upload_total_bytes = models.PositiveBigIntegerField(
        "总上传配额覆盖（字节）",
        null=True,
        blank=True,
        help_text="留空使用角色默认值。",
    )
    created_at = models.DateTimeField("申请时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "用户申请与权限"
        verbose_name_plural = "用户申请与权限"

    def __str__(self) -> str:
        return f"{self.full_name} ({self.user.username})"

    @property
    def is_approved(self) -> bool:
        return self.status == ApplicationStatus.APPROVED

    def save(self, *args, **kwargs) -> None:
        super().save(*args, **kwargs)
        should_be_active = self.status not in {
            ApplicationStatus.REJECTED,
            ApplicationStatus.DISABLED,
        }
        type(self.user).objects.filter(pk=self.user_id).update(
            email=self.email,
            is_active=should_be_active,
        )

    def clean(self) -> None:
        super().clean()
        if (
            self.upload_max_file_bytes is not None
            and self.upload_total_bytes is not None
            and self.upload_max_file_bytes > self.upload_total_bytes
        ):
            raise ValidationError(
                {"upload_max_file_bytes": "单文件配额不能超过账号总配额。"}
            )


class UploadQuotaRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="upload_quota_requests",
        verbose_name="申请人",
    )
    requested_max_file_bytes = models.PositiveBigIntegerField("申请单文件配额")
    requested_total_bytes = models.PositiveBigIntegerField("申请总配额")
    reason = models.TextField("申请理由")
    status = models.CharField(
        "状态",
        max_length=20,
        choices=QuotaRequestStatus.choices,
        default=QuotaRequestStatus.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="reviewed_upload_quota_requests",
        null=True,
        blank=True,
        verbose_name="审核人",
    )
    reviewed_at = models.DateTimeField("审核时间", null=True, blank=True)
    created_at = models.DateTimeField("申请时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "上传配额申请"
        verbose_name_plural = "上传配额申请"
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status=QuotaRequestStatus.PENDING),
                name="one_pending_upload_quota_request_per_user",
            ),
            models.CheckConstraint(
                condition=Q(requested_max_file_bytes__lte=models.F("requested_total_bytes")),
                name="upload_quota_request_max_lte_total",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user.get_username()} · {self.get_status_display()}"

    def clean(self) -> None:
        super().clean()
        if self.requested_max_file_bytes > self.requested_total_bytes:
            raise ValidationError(
                {"requested_max_file_bytes": "单文件配额不能超过账号总配额。"}
            )
