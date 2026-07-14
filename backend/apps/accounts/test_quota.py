from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.audit.models import AuditEvent, AuditEventType
from apps.corpora.services import upload_limits_for

from .models import (
    ApplicationStatus,
    QuotaRequestStatus,
    UploadQuotaRequest,
    UserProfile,
    UserRole,
)
from .services import review_quota_request, submit_quota_request


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    USER_UPLOAD_MAX_FILE_BYTES=10 * 1024 * 1024,
    USER_UPLOAD_TOTAL_BYTES=30 * 1024 * 1024,
)
class UploadQuotaTests(TestCase):
    password = "StrongPass!2026"

    def create_user(
        self,
        username: str,
        *,
        role: str = UserRole.JUNIOR,
        status: str = ApplicationStatus.APPROVED,
    ):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.invalid",
            password=self.password,
        )
        UserProfile.objects.create(
            user=user,
            full_name=username,
            organization="测试单位",
            email=f"{username}@example.invalid",
            role=role,
            requested_role=role if role != UserRole.TEST else "",
            use_purpose="扩容测试",
            application_reason="验证配额审批边界",
            status=status,
        )
        return user

    def test_approved_user_submits_one_pending_request_and_is_audited(self):
        user = self.create_user("quota-owner")

        request = submit_quota_request(
            user=user,
            requested_max_file_bytes=20 * 1024 * 1024,
            requested_total_bytes=80 * 1024 * 1024,
            reason="需要分析课程作业语料",
        )

        self.assertEqual(request.status, QuotaRequestStatus.PENDING)
        event = AuditEvent.objects.get(event_type=AuditEventType.QUOTA_REQUEST)
        self.assertEqual(event.actor, user)
        self.assertEqual(event.metadata["quota_request_id"], str(request.pk))
        with self.assertRaisesMessage(ValidationError, "已有待审核"):
            submit_quota_request(
                user=user,
                requested_max_file_bytes=20 * 1024 * 1024,
                requested_total_bytes=90 * 1024 * 1024,
                reason="重复申请",
            )

    def test_request_must_expand_current_total_and_test_account_is_denied(self):
        user = self.create_user("small-request")
        test_user = self.create_user("sandbox-user", role=UserRole.TEST)

        with self.assertRaisesMessage(ValidationError, "必须高于当前配额"):
            submit_quota_request(
                user=user,
                requested_max_file_bytes=10 * 1024 * 1024,
                requested_total_bytes=30 * 1024 * 1024,
                reason="没有实际扩容",
            )
        with self.assertRaises(PermissionDenied):
            submit_quota_request(
                user=test_user,
                requested_max_file_bytes=4 * 1024 * 1024,
                requested_total_bytes=10 * 1024 * 1024,
                reason="测试账号申请",
            )

    def test_only_staff_can_approve_and_approval_updates_effective_limits(self):
        user = self.create_user("approved-quota")
        ordinary_reviewer = self.create_user("ordinary-reviewer")
        admin = get_user_model().objects.create_superuser(
            username="quota-admin",
            email="quota-admin@example.invalid",
            password=self.password,
        )
        request = submit_quota_request(
            user=user,
            requested_max_file_bytes=25 * 1024 * 1024,
            requested_total_bytes=100 * 1024 * 1024,
            reason="需要扩大课程项目语料",
        )

        with self.assertRaises(PermissionDenied):
            review_quota_request(
                request,
                status=QuotaRequestStatus.APPROVED,
                reviewer=ordinary_reviewer,
            )
        reviewed = review_quota_request(
            request,
            status=QuotaRequestStatus.APPROVED,
            reviewer=admin,
        )

        user.refresh_from_db()
        limits = upload_limits_for(user)
        self.assertEqual(reviewed.status, QuotaRequestStatus.APPROVED)
        self.assertEqual(limits.max_file_bytes, 25 * 1024 * 1024)
        self.assertEqual(limits.total_bytes, 100 * 1024 * 1024)
        self.assertTrue(
            AuditEvent.objects.filter(
                event_type=AuditEventType.ADMIN_ACTION,
                actor=admin,
                metadata__quota_request_id=str(request.pk),
            ).exists()
        )

    def test_quota_request_view_creates_request_without_exposing_admin_controls(self):
        user = self.create_user("quota-view")
        self.client.force_login(user)

        response = self.client.post(
            reverse("accounts:quota_request"),
            {
                "requested_max_file_mb": 20,
                "requested_total_mb": 60,
                "reason": "页面提交扩容",
            },
        )

        self.assertRedirects(
            response,
            reverse("corpora:mine"),
            fetch_redirect_response=False,
        )
        self.assertTrue(UploadQuotaRequest.objects.filter(user=user).exists())
