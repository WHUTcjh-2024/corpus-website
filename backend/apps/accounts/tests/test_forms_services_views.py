from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase

from apps.accounts import views
from apps.accounts.forms import (
    AccountApplicationForm,
    ApprovedUserAuthenticationForm,
    UploadQuotaRequestForm,
)
from apps.accounts.models import (
    ApplicationStatus,
    QuotaRequestStatus,
    UploadQuotaRequest,
    UserProfile,
    UserRole,
)
from apps.accounts.permissions import AccessScope
from apps.accounts.services import (
    ApplicationData,
    ensure_seed_account,
    review_application,
    review_quota_request,
    submit_application,
    submit_quota_request,
)


def application_data(**overrides) -> ApplicationData:
    values = {
        "username": "applicant",
        "password": "StrongPassword!123",
        "full_name": "申请用户",
        "organization": "测试单位",
        "email": "applicant@example.com",
        "requested_role": UserRole.JUNIOR,
        "use_purpose": "语料研究",
        "application_reason": "需要使用在线语料库进行研究。",
    }
    values.update(overrides)
    return ApplicationData(**values)


class AccountFormTests(TestCase):
    def test_application_form_normalizes_and_saves(self) -> None:
        payload = {
            "username": " new-user ",
            "full_name": "新用户",
            "organization": "测试单位",
            "email": " USER@EXAMPLE.COM ",
            "requested_role": UserRole.MIDDLE,
            "use_purpose": "研究",
            "application_reason": "测试账户申请流程",
            "password1": "StrongPassword!123",
            "password2": "StrongPassword!123",
        }
        profile = SimpleNamespace(pk=1)
        with (
            patch("apps.accounts.forms.validate_password"),
            patch(
                "apps.accounts.forms.submit_application", return_value=profile
            ) as submit,
        ):
            form = AccountApplicationForm(payload)
            self.assertTrue(form.is_valid(), form.errors)
            self.assertIs(form.save(), profile)

        self.assertEqual(form.cleaned_data["username"], "new-user")
        self.assertEqual(form.cleaned_data["email"], "user@example.com")
        self.assertEqual(submit.call_args.args[0].requested_role, UserRole.MIDDLE)

    def test_application_form_rejects_duplicates_and_password_errors(self) -> None:
        user_model = get_user_model()
        user = user_model.objects.create_user("taken", email="taken@example.com")
        UserProfile.objects.create(
            user=user,
            full_name="已有用户",
            organization="测试单位",
            email="taken@example.com",
            role=UserRole.JUNIOR,
            use_purpose="测试",
            application_reason="测试",
        )
        payload = {
            "username": "TAKEN",
            "full_name": "新用户",
            "organization": "测试单位",
            "email": "TAKEN@example.com",
            "requested_role": UserRole.JUNIOR,
            "use_purpose": "研究",
            "application_reason": "测试",
            "password1": "password-one",
            "password2": "password-two",
        }
        with patch(
            "apps.accounts.forms.validate_password",
            side_effect=ValidationError("密码不合规"),
        ):
            form = AccountApplicationForm(payload)
            self.assertFalse(form.is_valid())

        self.assertIn("username", form.errors)
        self.assertIn("email", form.errors)
        self.assertIn("password1", form.errors)
        self.assertIn("password2", form.errors)
        with self.assertRaisesMessage(ValueError, "invalid account application"):
            form.save()

    def test_authentication_form_blocks_unapproved_scope(self) -> None:
        user = SimpleNamespace(is_active=True)
        form = ApprovedUserAuthenticationForm()
        with patch(
            "apps.accounts.forms.workspace_access_scope",
            return_value=AccessScope.NONE,
        ):
            with self.assertRaisesMessage(ValidationError, "尚未审核"):
                form.confirm_login_allowed(user)

    def test_quota_form_validates_limits_and_saves(self) -> None:
        user = SimpleNamespace(pk=1)
        invalid = UploadQuotaRequestForm(
            {
                "requested_max_file_mb": 300,
                "requested_total_mb": 200,
                "reason": "扩容",
            },
            user=user,
            current_max_file_bytes=100 * 1024 * 1024,
            current_total_bytes=200 * 1024 * 1024,
        )
        self.assertFalse(invalid.is_valid())
        self.assertIn("requested_max_file_mb", invalid.errors)
        self.assertIn("requested_total_mb", invalid.errors)

        quota = SimpleNamespace(pk=1)
        with patch(
            "apps.accounts.forms.submit_quota_request",
            return_value=quota,
        ) as submit:
            valid = UploadQuotaRequestForm(
                {
                    "requested_max_file_mb": 150,
                    "requested_total_mb": 300,
                    "reason": "语料扩充",
                },
                user=user,
                current_max_file_bytes=100 * 1024 * 1024,
                current_total_bytes=200 * 1024 * 1024,
            )
            self.assertTrue(valid.is_valid(), valid.errors)
            self.assertIs(valid.save(), quota)

        self.assertEqual(
            submit.call_args.kwargs["requested_total_bytes"], 300 * 1024 * 1024
        )


class AccountServiceTests(TestCase):
    def setUp(self) -> None:
        self.user_model = get_user_model()
        self.reviewer = self.user_model.objects.create_user(
            "reviewer",
            is_staff=True,
        )
        self.user = self.user_model.objects.create_user("member")
        self.profile = UserProfile.objects.create(
            user=self.user,
            full_name="成员",
            organization="测试单位",
            email="member@example.com",
            role=UserRole.JUNIOR,
            requested_role=UserRole.JUNIOR,
            use_purpose="研究",
            application_reason="测试",
            status=ApplicationStatus.APPROVED,
        )
        self.user.refresh_from_db()

    def test_application_submission_and_review(self) -> None:
        profile = submit_application(application_data())
        self.assertEqual(profile.status, ApplicationStatus.PENDING)
        self.assertTrue(profile.user.check_password("StrongPassword!123"))

        with self.assertRaisesMessage(ValueError, "Unsupported application status"):
            review_application(profile, status="unknown", reviewer=self.reviewer)
        with self.assertRaisesMessage(ValueError, "Unsupported user role"):
            review_application(
                profile,
                status=ApplicationStatus.APPROVED,
                reviewer=self.reviewer,
                role="unknown",
            )

        reviewed = review_application(
            profile,
            status=ApplicationStatus.APPROVED,
            reviewer=self.reviewer,
            role=UserRole.MIDDLE,
        )
        self.assertEqual(reviewed.role, UserRole.MIDDLE)
        self.assertEqual(reviewed.reviewed_by, self.reviewer)

    def test_quota_submission_validates_account_and_values(self) -> None:
        with patch(
            "apps.corpora.services.upload_limits_for",
            return_value=SimpleNamespace(total_bytes=200),
        ):
            self.profile.status = ApplicationStatus.PENDING
            self.profile.save()
            self.user.refresh_from_db()
            with self.assertRaises(PermissionDenied):
                submit_quota_request(
                    user=self.user,
                    requested_max_file_bytes=100,
                    requested_total_bytes=300,
                    reason="扩容",
                )

            self.profile.status = ApplicationStatus.APPROVED
            self.profile.role = UserRole.TEST
            self.profile.save()
            self.user.refresh_from_db()
            with self.assertRaises(PermissionDenied):
                submit_quota_request(
                    user=self.user,
                    requested_max_file_bytes=100,
                    requested_total_bytes=300,
                    reason="扩容",
                )

            self.profile.role = UserRole.JUNIOR
            self.profile.save()
            for maximum, total, reason in (
                (0, 300, "扩容"),
                (400, 300, "扩容"),
                (100, 200, "扩容"),
                (100, 300, "   "),
            ):
                with self.subTest(maximum=maximum, total=total, reason=reason):
                    with self.assertRaises(ValidationError):
                        submit_quota_request(
                            user=self.user,
                            requested_max_file_bytes=maximum,
                            requested_total_bytes=total,
                            reason=reason,
                        )

    def test_quota_submission_and_review_approval(self) -> None:
        with (
            patch(
                "apps.corpora.services.upload_limits_for",
                return_value=SimpleNamespace(total_bytes=200),
            ),
            patch("apps.accounts.services.record_audit_event") as audit,
        ):
            quota = submit_quota_request(
                user=self.user,
                requested_max_file_bytes=300,
                requested_total_bytes=500,
                reason="  扩充研究语料  ",
            )
            reviewed = review_quota_request(
                quota,
                status=QuotaRequestStatus.APPROVED,
                reviewer=self.reviewer,
            )

        self.assertEqual(quota.reason, "扩充研究语料")
        self.assertEqual(reviewed.status, QuotaRequestStatus.APPROVED)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.upload_total_bytes, 500)
        self.assertEqual(audit.call_count, 2)

    def test_quota_review_rejects_invalid_actor_status_and_state(self) -> None:
        quota = UploadQuotaRequest.objects.create(
            user=self.user,
            requested_max_file_bytes=300,
            requested_total_bytes=500,
            reason="扩容",
        )
        inactive = self.user_model.objects.create_user("inactive", is_active=False)
        with self.assertRaises(PermissionDenied):
            review_quota_request(
                quota,
                status=QuotaRequestStatus.APPROVED,
                reviewer=inactive,
            )
        with self.assertRaisesMessage(ValueError, "Unsupported quota request status"):
            review_quota_request(quota, status="pending", reviewer=self.reviewer)

        quota.status = QuotaRequestStatus.REJECTED
        quota.save(update_fields=["status"])
        with self.assertRaisesMessage(ValidationError, "待处理"):
            review_quota_request(
                quota,
                status=QuotaRequestStatus.REJECTED,
                reviewer=self.reviewer,
            )

    def test_seed_account_is_idempotent_and_validates_role(self) -> None:
        with self.assertRaisesMessage(ValueError, "Unsupported user role"):
            ensure_seed_account(
                username="seed",
                email="seed@example.com",
                password="password",
                role="unknown",
                full_name="种子用户",
            )

        user, created = ensure_seed_account(
            username="seed",
            email="seed@example.com",
            password="StrongPassword!123",
            role=UserRole.ADMIN,
            full_name="管理员",
            is_admin=True,
        )
        updated, created_again = ensure_seed_account(
            username="seed",
            email="updated@example.com",
            password="NewStrongPassword!123",
            role=UserRole.ADMIN,
            full_name="更新管理员",
            is_admin=True,
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(user.pk, updated.pk)
        self.assertTrue(updated.is_superuser)
        self.assertTrue(updated.check_password("NewStrongPassword!123"))


class AccountViewTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = SimpleNamespace(pk=1, is_authenticated=True)

    def test_apply_handles_get_and_valid_post(self) -> None:
        with patch.object(
            views, "render", return_value=HttpResponse("form")
        ) as render_mock:
            response = views.apply(self.factory.get("/apply/"))
        self.assertEqual(response.content, b"form")
        self.assertFalse(render_mock.call_args.args[2]["form"].is_bound)

        form = Mock()
        form.is_valid.return_value = True
        with (
            patch.object(views, "AccountApplicationForm", return_value=form),
            patch.object(views.messages, "success") as success,
            patch.object(views, "redirect", return_value=HttpResponse("submitted")),
        ):
            response = views.apply(self.factory.post("/apply/", {}))
        self.assertEqual(response.content, b"submitted")
        form.save.assert_called_once()
        success.assert_called_once()

    def test_login_redirect_accepts_only_local_next_url(self) -> None:
        with patch.object(views, "reverse", return_value="/"):
            local = views.login_redirect(self.factory.get("/login/?next=/workspace/"))
            external = views.login_redirect(
                self.factory.get("/login/?next=https://example.org/steal")
            )

        self.assertIn("next=%2Fworkspace%2F", local.url)
        self.assertNotIn("example.org", external.url)

    def test_dashboard_builds_summary_context(self) -> None:
        class FakeQuerySet:
            def select_related(self, *_args):
                return self

            def order_by(self, *_args):
                return self

            def filter(self, **_kwargs):
                return self

            def aggregate(self, **_kwargs):
                return {"total": 25}

            def count(self):
                return 2

            def __getitem__(self, _item):
                return []

        request = self.factory.get("/dashboard/")
        request.user = self.user
        with (
            patch.object(views, "visible_corpora_for", return_value=FakeQuerySet()),
            patch.object(
                views.ExportJob.objects, "filter", return_value=FakeQuerySet()
            ),
            patch.object(views, "get_user_profile", return_value="profile"),
            patch.object(
                views, "workspace_access_scope", return_value=AccessScope.STANDARD
            ),
            patch.object(views, "active_announcements_for", return_value=["notice"]),
            patch.object(
                views, "render", return_value=HttpResponse("dashboard")
            ) as render_mock,
        ):
            response = views.dashboard.__wrapped__(request)

        self.assertEqual(response.content, b"dashboard")
        context = render_mock.call_args.args[2]
        self.assertEqual(context["corpus_count"], 2)
        self.assertEqual(context["token_count"], 25)
        self.assertEqual(context["announcements"], ["notice"])

    def test_quota_request_handles_permission_error_and_success(self) -> None:
        request = self.factory.post("/quota/", {})
        request.user = self.user
        limits = SimpleNamespace(max_file_bytes=100, total_bytes=200)
        form = Mock()
        form.is_valid.return_value = True
        form.save.side_effect = PermissionDenied("forbidden")
        with (
            patch.object(views, "upload_limits_for", return_value=limits),
            patch.object(views.UploadQuotaRequest.objects, "filter") as query,
            patch.object(views, "UploadQuotaRequestForm", return_value=form),
        ):
            query.return_value.first.return_value = None
            response = views.quota_request.__wrapped__(request)
        self.assertEqual(response.status_code, 403)

        form.save.side_effect = None
        with (
            patch.object(views, "upload_limits_for", return_value=limits),
            patch.object(views.UploadQuotaRequest.objects, "filter") as query,
            patch.object(views, "UploadQuotaRequestForm", return_value=form),
            patch.object(views.messages, "success"),
            patch.object(views, "redirect", return_value=HttpResponse("done")),
        ):
            query.return_value.first.return_value = None
            response = views.quota_request.__wrapped__(request)
        self.assertEqual(response.content, b"done")
