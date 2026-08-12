from django.contrib import admin

from .models import ParallelAudit


@admin.register(ParallelAudit)
class ParallelAuditAdmin(admin.ModelAdmin):
    list_display = ("corpus", "status", "processing_task", "created_at", "finished_at")
    list_filter = ("status",)
    search_fields = ("corpus__name", "processing_task__id")
    autocomplete_fields = ("corpus", "processing_task")
    readonly_fields = (
        "created_at", "updated_at", "started_at", "finished_at", "report_path", "anomalies_path", "summary", "error_message",
    )
