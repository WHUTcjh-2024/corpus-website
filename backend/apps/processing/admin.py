from django.contrib import admin

from .models import ProcessingTask


@admin.register(ProcessingTask)
class ProcessingTaskAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "corpus",
        "task_type",
        "status",
        "progress",
        "requested_by",
        "created_at",
    )
    list_filter = ("task_type", "status", "created_at")
    search_fields = ("corpus__name", "error_message", "output_path")
    autocomplete_fields = ("corpus", "requested_by")
    readonly_fields = (
        "id",
        "task_type",
        "status",
        "progress",
        "error_message",
        "output_path",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    )
