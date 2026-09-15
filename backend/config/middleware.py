from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse


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
