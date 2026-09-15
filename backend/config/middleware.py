from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse

from apps.accounts.login_security import (
    LoginSecurityUnavailable,
    evaluate_login_attempt,
    record_login_failure,
    record_login_success,
)


logger = logging.getLogger("corpus_platform.slow_queries")
MAX_LOGGED_SQL_LENGTH = 1_000
SQL_STRING_LITERAL = re.compile(r"'(?:''|[^'])*'")
SQL_NUMBER_LITERAL = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")


def sanitize_sql(sql: str) -> str:
    normalized = " ".join(str(sql).split())
    without_strings = SQL_STRING_LITERAL.sub("?", normalized)
    return SQL_NUMBER_LITERAL.sub("?", without_strings)[:MAX_LOGGED_SQL_LENGTH]


class SlowQueryLogger:
    """Log slow SQL without recording query parameters or user-provided values."""

    def __init__(self, threshold_ms: float) -> None:
        self.threshold_ms = threshold_ms

    def __call__(
        self,
        execute: Callable[..., Any],
        sql: str,
        params: object,
        many: bool,
        context: dict[str, Any],
    ) -> Any:
        started_at = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            duration_ms = (time.perf_counter() - started_at) * 1_000
            if duration_ms >= self.threshold_ms:
                logger.warning(
                    "slow_database_query duration_ms=%.2f vendor=%s sql=%s",
                    duration_ms,
                    connection.vendor,
                    sanitize_sql(sql),
                )


class SlowQueryLoggingMiddleware:
    """Install request-scoped SQL timing with a configurable warning threshold."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        threshold_ms = float(settings.DATABASE_SLOW_QUERY_MS)
        if threshold_ms <= 0:
            return self.get_response(request)
        with connection.execute_wrapper(SlowQueryLogger(threshold_ms)):
            return self.get_response(request)


class AdminLoginProtectionMiddleware:
    """Apply the shared brute-force guard to Django's administrator login."""

    login_path = "/admin/login/"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method != "POST" or request.path != self.login_path:
            return self.get_response(request)

        username = request.POST.get("username", "")
        try:
            decision = evaluate_login_attempt(request, username)
        except LoginSecurityUnavailable:
            return self._denied_response(
                "登录保护服务暂时不可用，请稍后重试。", status=503, retry_after=30
            )
        if not decision.allowed:
            return self._denied_response(
                "登录尝试过于频繁，请稍后重试。",
                status=429,
                retry_after=decision.retry_after_seconds,
            )

        response = self.get_response(request)
        if 200 <= response.status_code < 400:
            if request.user.is_authenticated:
                record_login_success(request, username)
            elif response.status_code == 200:
                try:
                    record_login_failure(request, username)
                except LoginSecurityUnavailable:
                    return self._denied_response(
                        "登录保护服务暂时不可用，请稍后重试。",
                        status=503,
                        retry_after=30,
                    )
        return response

    @staticmethod
    def _denied_response(message: str, *, status: int, retry_after: int) -> HttpResponse:
        response = HttpResponse(message, status=status, content_type="text/plain; charset=utf-8")
        response.headers["Retry-After"] = str(retry_after)
        response.headers["Cache-Control"] = "no-store"
        return response
