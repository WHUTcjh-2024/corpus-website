from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from apps.accounts.login_security import (
    LoginSecurityUnavailable,
    client_ip,
    evaluate_login_attempt,
    record_login_failure,
    record_login_success,
)
from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from config.middleware import AdminLoginProtectionMiddleware


SECURITY_SETTINGS = {
    "LOGIN_RATE_LIMIT_ATTEMPTS": 3,
    "LOGIN_RATE_LIMIT_WINDOW_SECONDS": 60,
    "LOGIN_PAIR_FAILURE_LIMIT": 2,
    "LOGIN_USERNAME_FAILURE_LIMIT": 4,
    "LOGIN_IP_FAILURE_LIMIT": 6,
    "LOGIN_FAILURE_WINDOW_SECONDS": 300,
    "LOGIN_LOCKOUT_SECONDS": 120,
    "LOGIN_SECURITY_FAIL_CLOSED": True,
    "LOGIN_TRUSTED_PROXY_CIDRS": ["10.0.0.0/8"],
}


@override_settings(**SECURITY_SETTINGS)
class LoginSecurityTests(TestCase):
    def setUp(self) -> None:
        cache.clear()
        self.factory = RequestFactory()

    def tearDown(self) -> None:
        cache.clear()

    def request(self, *, remote_ip: str = "203.0.113.10", forwarded_ip: str = ""):
        headers = {"REMOTE_ADDR": remote_ip}
        if forwarded_ip:
            headers["HTTP_X_REAL_IP"] = forwarded_ip
        return self.factory.post("/api/auth/login/", **headers)

    def test_client_ip_trusts_only_configured_reverse_proxies(self) -> None:
        self.assertEqual(
            client_ip(self.request(remote_ip="10.1.2.3", forwarded_ip="198.51.100.8")),
            "198.51.100.8",
        )
        self.assertEqual(
            client_ip(
                self.request(remote_ip="203.0.113.10", forwarded_ip="198.51.100.8")
            ),
            "203.0.113.10",
        )

    def test_rate_limit_is_separate_and_returns_retry_window(self) -> None:
        request = self.request()
        for _ in range(3):
            self.assertTrue(evaluate_login_attempt(request, "member").allowed)

        decision = evaluate_login_attempt(request, "member")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "rate_limited")
        self.assertGreater(decision.retry_after_seconds, 0)

    def test_failed_attempts_lock_and_success_clears_principal_state(self) -> None:
        request = self.request()
        record_login_failure(request, "Member")
        record_login_failure(request, "member")
        self.assertEqual(evaluate_login_attempt(request, "MEMBER").reason, "locked")

        record_login_success(request, "member")
        self.assertTrue(evaluate_login_attempt(request, "member").allowed)

    def test_cache_outage_fails_closed_in_production_mode(self) -> None:
        with patch("apps.accounts.login_security.cache.get_many", side_effect=OSError):
            with self.assertRaises(LoginSecurityUnavailable):
                evaluate_login_attempt(self.request(), "member")


@override_settings(**SECURITY_SETTINGS)
class LoginViewProtectionTests(TestCase):
    def setUp(self) -> None:
        cache.clear()
        user = get_user_model().objects.create_user(
            username="member",
            email="member@example.com",
            password="StrongPassword!123",
        )
        UserProfile.objects.create(
            user=user,
            full_name="正式用户",
            organization="测试单位",
            email="member@example.com",
            role=UserRole.JUNIOR,
            use_purpose="研究",
            application_reason="测试",
            status=ApplicationStatus.APPROVED,
        )

    def tearDown(self) -> None:
        cache.clear()

    def test_login_endpoint_locks_repeated_failures_without_leaking_account_state(self) -> None:
        payload = {"username": "member", "password": "wrong-password"}
        for _ in range(2):
            response = self.client.post(
                "/api/auth/login/", payload, content_type="application/json"
            )
            self.assertEqual(response.status_code, 400)

        locked = self.client.post(
            "/api/auth/login/",
            {"username": "member", "password": "StrongPassword!123"},
            content_type="application/json",
        )
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(locked.headers["Retry-After"], "120")
        self.assertNotIn("member", locked.json()["detail"])


@override_settings(**SECURITY_SETTINGS)
class AdminLoginProtectionMiddlewareTests(SimpleTestCase):
    def setUp(self) -> None:
        cache.clear()
        self.factory = RequestFactory()

    def tearDown(self) -> None:
        cache.clear()

    def test_admin_login_is_locked_after_repeated_failures(self) -> None:
        def invalid_login(request):
            request.user = AnonymousUser()
            return HttpResponse("invalid")

        middleware = AdminLoginProtectionMiddleware(invalid_login)
        for _ in range(2):
            response = middleware(
                self.factory.post(
                    "/admin/login/",
                    {"username": "administrator", "password": "wrong"},
                    REMOTE_ADDR="203.0.113.50",
                )
            )
            self.assertEqual(response.status_code, 200)

        blocked = middleware(
            self.factory.post(
                "/admin/login/",
                {"username": "administrator", "password": "correct"},
                REMOTE_ADDR="203.0.113.50",
            )
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers["Cache-Control"], "no-store")
