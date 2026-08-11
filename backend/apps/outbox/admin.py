from django.contrib import admin

from .models import OutboxEvent, OutboxEventStatus
from .services import replay_dead_letter_events


@admin.register(OutboxEvent)
class OutboxEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "task_name",
        "aggregate_id",
        "status",
        "attempt_count",
        "replay_count",
        "available_at",
        "dead_lettered_at",
        "published_at",
    )
    list_filter = ("status", "task_name")
    search_fields = ("id", "aggregate_id", "deduplication_key")
    readonly_fields = (
        "id",
        "task_name",
        "aggregate_id",
        "deduplication_key",
        "payload",
        "status",
        "attempt_count",
        "replay_count",
        "available_at",
        "locked_until",
        "published_at",
        "dead_lettered_at",
        "last_error",
        "created_at",
        "updated_at",
    )
    actions = ("replay_selected_dead_letters",)

    @admin.action(description="Replay selected dead-letter events")
    def replay_selected_dead_letters(self, request, queryset) -> None:
        summary = replay_dead_letter_events(
            event_ids=queryset.filter(status=OutboxEventStatus.DEAD_LETTER).values_list(
                "pk", flat=True
            ),
            limit=queryset.count(),
        )
        self.message_user(
            request,
            f"Replayed {summary.replayed}; skipped {summary.skipped}.",
        )
