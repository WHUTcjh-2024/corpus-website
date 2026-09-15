from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import approved_user_required

from .forms import SaveSearchForm
from .models import AuditEvent, AuditEventType, SavedSearch
from .services_saved_search import (
    history_replay_url,
    replay_url,
    save_search,
    saved_search_usage,
)


SEARCH_EVENT_TYPES = (
    AuditEventType.KWIC_SEARCH,
    AuditEventType.PARALLEL_SEARCH,
    AuditEventType.STATISTICS_QUERY,
)


@approved_user_required
def search_history(request: HttpRequest) -> HttpResponse:
    saved = list(
        SavedSearch.objects.filter(user=request.user)
        .select_related("corpus")
        .order_by("-updated_at")[:100]
    )
    saved_rows = [(item, replay_url(item)) for item in saved]
    events = list(
        AuditEvent.objects.filter(actor=request.user, event_type__in=SEARCH_EVENT_TYPES)
        .select_related("corpus")
        .order_by("-created_at")[:100]
    )
    history_rows = []
    for event in events:
        parameters = event.metadata.get("parameters", {})
        if not isinstance(parameters, dict):
            parameters = {}
        history_rows.append(
            {
                "event": event,
                "query": parameters.get("q") or parameters.get("query") or "—",
                "result_count": event.metadata.get("result_count", "—"),
                "replay_url": history_replay_url(path=event.path, parameters=parameters),
            }
        )
    return render(
        request,
        "audit/search_history.html",
        {
            "saved_rows": saved_rows,
            "history_rows": history_rows,
            "usage": saved_search_usage(request.user),
        },
    )


@approved_user_required
@require_POST
def save_search_view(request: HttpRequest) -> HttpResponse:
    form = SaveSearchForm(request.POST)
    if not form.is_valid():
        messages.error(request, "保存检索失败：提交参数无效。")
        return redirect("research_history:list")
    try:
        saved = save_search(user=request.user, **form.cleaned_data)
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, f"已保存检索“{saved.name}”。")
    return redirect("research_history:list")


@approved_user_required
@require_POST
def delete_saved_search(request: HttpRequest, saved_id: int) -> HttpResponse:
    saved = get_object_or_404(SavedSearch, pk=saved_id)
    if saved.user_id != request.user.pk:
        raise PermissionDenied("只能删除本人保存的检索。")
    saved.delete()
    messages.success(request, "已删除保存的检索。")
    return redirect("research_history:list")
