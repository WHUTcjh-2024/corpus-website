from __future__ import annotations

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from redis import Redis


def home(request: HttpRequest):
    return render(
        request,
        "home.html",
        {
            "stage": settings.PLATFORM_STAGE.replace("stage-", "Stage "),
            "data_root": settings.DATA_ROOT,
        },
    )


def healthz(request: HttpRequest) -> JsonResponse:
    return JsonResponse(
        {
            "status": "ok",
            "service": "corpus-platform",
            "stage": settings.PLATFORM_STAGE,
        }
    )


def readyz(request: HttpRequest) -> JsonResponse:
    checks = {
        "database": _database_ready(),
        "redis": _redis_ready(),
        "data_root": settings.DATA_ROOT.exists(),
    }
    status_code = 200 if all(checks.values()) else 503
    return JsonResponse({"status": "ready" if status_code == 200 else "not_ready", "checks": checks}, status=status_code)


def _database_ready() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
    except Exception:
        return False


def _redis_ready() -> bool:
    try:
        client = Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        return bool(client.ping())
    except Exception:
        return False
