from __future__ import annotations

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound, JsonResponse
from django.shortcuts import render
from redis import Redis

from apps.outbox.metrics import collect_outbox_metrics, render_prometheus_metrics


def home(request: HttpRequest):
    return render(request, "frontend/index.html")


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
        "agent_model": _agent_model_ready(),
    }
    status_code = 200 if all(checks.values()) else 503
    return JsonResponse({"status": "ready" if status_code == 200 else "not_ready", "checks": checks}, status=status_code)


def metrics(request: HttpRequest) -> HttpResponse:
    """Expose operational metrics only to a configured metrics collector."""
    token = settings.METRICS_BEARER_TOKEN
    if not token or request.headers.get("Authorization") != f"Bearer {token}":
        return HttpResponseNotFound()
    return HttpResponse(
        render_prometheus_metrics(collect_outbox_metrics()),
        content_type="text/plain; version=0.0.4; charset=utf-8",
    )


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


def _agent_model_ready() -> bool:
    """A model is optional: deterministic grounded mode remains production-ready."""
    if not settings.AGENT_MODEL_ENABLED:
        return True
    return bool(
        settings.AGENT_MODEL_BASE_URL
        and settings.AGENT_MODEL_API_KEY
        and settings.AGENT_MODEL_NAME
    )
