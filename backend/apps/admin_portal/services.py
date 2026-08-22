from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.db.models import Q, QuerySet
from django.utils import timezone

from .models import Announcement, AnnouncementAudience, AnnouncementRecipient


def active_announcements_for(user: Any) -> QuerySet[Announcement]:
    """Return the currently visible announcements for one authenticated user."""

    if not getattr(user, "is_authenticated", False):
        return Announcement.objects.none()
    now = timezone.now()
    return (
        Announcement.objects.filter(
            is_published=True,
            starts_at__lte=now,
        )
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        .filter(Q(audience=AnnouncementAudience.ALL) | Q(recipients=user))
        .distinct()
    )


def replace_announcement_recipients(
    *, announcement: Announcement, recipients: Iterable[Any]
) -> int:
    """Persist the selected audience atomically without stale recipients."""

    recipient_ids = {item.pk for item in recipients if getattr(item, "pk", None)}
    announcement.recipient_links.all().delete()
    AnnouncementRecipient.objects.bulk_create(
        [AnnouncementRecipient(announcement=announcement, user_id=user_id) for user_id in recipient_ids]
    )
    return len(recipient_ids)
