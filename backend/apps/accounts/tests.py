from __future__ import annotations

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import ApplicationStatus, UserProfile, UserRole
from .permissions import AccessScope, workspace_access_scope
from .services import review_application


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class AccountsModuleTests(TestCase):
    password = "StrongPass!2026"

    def setUp(self) -> None:
        self.reviewer = get_user_model().objects.create_superuser(
            username="reviewer",
            email="reviewer@example.invalid",
            password=self.password,
        )

    def create_profile(
        self,
        *,
        username: str,
        status: str = ApplicationStatus.PENDING,
        role: str = UserRole.JUNIOR,
    ) -> UserProfile:
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.invalid",
            password=self.password,
        )
        return UserProfile.objects.create(
            user=user,
            full_name=f"{username} 姓名",
            organization="测试单位",
            email=f"{username}@example.invalid",
            role=role,
            requested_role=(role if role in {UserRole.JUNIOR, UserRole.MIDDLE, UserRole.ADVANCED} else ""),
            use_purpose="教学研究",
            application_reason="阶段 2 自动测试",
            status=status,
        )

    def test_application_form_creates_pending_profile_with_required_fields(self):
        response = self.client.post(
            reverse("accounts:apply"),
            {
                "username": "applicant",
                "full_name": "申请人",
                "organization": "武汉理工大学",
                "email": "applicant@example.invalid",
                "requested_role": UserRole.MIDDLE,
                "use_purpose": "翻译教学研究",
                "application_reason": "需要使用语料进行课程研究。",
                "password1": self.password,
                "password2": self.password,
            },
        )

        self.assertRedirects(
            response,
            reverse("accounts:application_submitted"),
            fetch_redirect_response=False,
        )
        profile = UserProfile.objects.select_related("user").get(user__username="applicant")
        self.assertEqual(profile.full_name, "申请人")
        self.assertEqual(profile.organization, "武汉理工大学")
        self.assertEqual(profile.email, "applicant@example.invalid")
        self.assertEqual(profile.requested_role, UserRole.MIDDLE)
        self.assertEqual(profile.role, UserRole.MIDDLE)
        self.assertEqual(profile.status, ApplicationStatus.PENDING)
        self.assertTrue(profile.user.check_password(self.password))

    def test_duplicate_application_email_is_rejected(self):
        self.create_profile(username="existing")
        response = self.client.post(
            reverse("accounts:apply"),
            {
                "username": "another",
                "full_name": "另一个申请人",
                "organization": "测试单位",
                "email": "EXISTING@example.invalid",
                "requested_role": UserRole.JUNIOR,
                "use_purpose": "研究",
                "application_reason": "测试重复邮箱。",
                "password1": self.password,
                "password2": self.password,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "该邮箱已提交过申请。")
        self.assertFalse(get_user_model().objects.filter(username="another").exists())

    def test_pending_user_cannot_log_in_or_force_access_dashboard(self):
        profile = self.create_profile(username="pending")

        login_response = self.client.post(
            reverse("accounts:login"),
            {"username": "pending", "password": self.password},
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertContains(login_response, "账号尚未审核通过或已被停用。")

        self.client.force_login(profile.user)
        dashboard_response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(dashboard_response.status_code, 403)

    def test_approved_user_can_log_in_and_access_dashboard(self):
        self.create_profile(username="approved", status=ApplicationStatus.APPROVED)

        response = self.client.post(
            reverse("accounts:login"),
            {"username": "approved", "password": self.password},
        )

        self.assertRedirects(
            response,
            reverse("accounts:dashboard"),
            fetch_redirect_response=False,
        )
        dashboard = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "已审核用户范围")

    def test_disabled_user_cannot_log_in_or_access_dashboard(self):
        profile = self.create_profile(
            username="disabled",
            status=ApplicationStatus.DISABLED,
        )
        profile.user.refresh_from_db()
        self.assertFalse(profile.user.is_active)

        login_response = self.client.post(
            reverse("accounts:login"),
            {"username": "disabled", "password": self.password},
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

        self.client.force_login(profile.user)
        dashboard_response = self.client.get(reverse("accounts:dashboard"))
        self.assertRedirects(
            dashboard_response,
            f"{reverse('accounts:login')}?next={reverse('accounts:dashboard')}",
            fetch_redirect_response=False,
        )

    def test_review_service_approves_and_reactivates_user(self):
        profile = self.create_profile(
            username="reviewed",
            status=ApplicationStatus.DISABLED,
        )

        reviewed = review_application(
            profile,
            status=ApplicationStatus.APPROVED,
            reviewer=self.reviewer,
            role=UserRole.ADVANCED,
        )

        self.assertEqual(reviewed.status, ApplicationStatus.APPROVED)
        self.assertEqual(reviewed.role, UserRole.ADVANCED)
        self.assertEqual(reviewed.reviewed_by, self.reviewer)
        self.assertIsNotNone(reviewed.reviewed_at)
        self.assertTrue(reviewed.user.is_active)

    def test_test_user_is_limited_to_demo_scope(self):
        profile = self.create_profile(
            username="test_user",
            status=ApplicationStatus.APPROVED,
            role=UserRole.TEST,
        )

        self.assertEqual(workspace_access_scope(profile.user), AccessScope.DEMO_ONLY)
        self.client.force_login(profile.user)
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "仅限 demo 语料范围")

    def test_ordinary_user_cannot_access_admin_page(self):
        profile = self.create_profile(
            username="ordinary",
            status=ApplicationStatus.APPROVED,
        )
        self.client.force_login(profile.user)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)

    def test_user_without_profile_is_denied_by_backend_permission(self):
        user = get_user_model().objects.create_user(
            username="profileless",
            password=self.password,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("accounts:dashboard"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(workspace_access_scope(user), AccessScope.NONE)

    def test_seed_accounts_is_idempotent_and_assigns_expected_permissions(self):
        output = StringIO()
        command_options = {
            "test_password": "TestSeedPass!2026",
            "admin_password": "AdminSeedPass!2026",
            "stdout": output,
        }

        call_command("seed_accounts", **command_options)
        call_command("seed_accounts", **command_options)

        self.assertEqual(
            get_user_model().objects.filter(username__in=["test_user", "admin"]).count(),
            2,
        )
        test_user = get_user_model().objects.get(username="test_user")
        admin_user = get_user_model().objects.get(username="admin")
        self.assertEqual(test_user.account_profile.role, UserRole.TEST)
        self.assertEqual(workspace_access_scope(test_user), AccessScope.DEMO_ONLY)
        self.assertTrue(admin_user.is_staff)
        self.assertTrue(admin_user.is_superuser)
        self.assertEqual(admin_user.account_profile.role, UserRole.ADMIN)
        self.assertEqual(workspace_access_scope(admin_user), AccessScope.ADMIN)
        self.assertIn("seed_accounts 完成", output.getvalue())
