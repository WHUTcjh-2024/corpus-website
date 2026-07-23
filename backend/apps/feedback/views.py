from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.permissions import approved_user_required

from .forms import FeedbackTicketForm
from .models import FeedbackTicket


def _support_context() -> dict[str, str]:
    return {
        "feedback_support_name": getattr(settings, "FEEDBACK_SUPPORT_NAME", "平台管理员"),
        "feedback_support_email": getattr(
            settings,
            "FEEDBACK_SUPPORT_EMAIL",
            getattr(settings, "DEFAULT_FROM_EMAIL", "support@example.invalid"),
        ),
    }


@approved_user_required
def ticket_list(request: HttpRequest) -> HttpResponse:
    tickets = FeedbackTicket.objects.select_related("user")
    if not request.user.is_staff:
        tickets = tickets.filter(user=request.user)
    return render(
        request,
        "feedback/ticket_list.html",
        {"tickets": tickets, **_support_context()},
    )


@approved_user_required
def ticket_create(request: HttpRequest) -> HttpResponse:
    initial = {"contact_email": request.user.email}
    form = FeedbackTicketForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        ticket = form.save(commit=False)
        ticket.user = request.user
        ticket.save()
        return redirect("feedback:detail", ticket_id=ticket.pk)
    return render(
        request,
        "feedback/ticket_form.html",
        {"form": form, **_support_context()},
    )


@approved_user_required
def ticket_detail(request: HttpRequest, ticket_id) -> HttpResponse:
    ticket = get_object_or_404(FeedbackTicket.objects.select_related("user"), pk=ticket_id)
    if not request.user.is_staff and ticket.user_id != request.user.pk:
        return HttpResponseForbidden("无权查看该反馈。")
    return render(
        request,
        "feedback/ticket_detail.html",
        {"ticket": ticket, **_support_context()},
    )
