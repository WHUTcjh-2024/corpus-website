from django.contrib import admin

from .models import ParallelAudit


@admin.register(ParallelAudit)
class ParallelAuditAdmin(admin.ModelAdmin):
    list_display = ("corpus", "status", "worker_state", "processing_task", "created_at", "finished_at")
    list_filter = ("status", "execution_mode", "worker_state")
    search_fields = ("corpus__name", "processing_task__id", "worker_job_id", "command_message_id")
    autocomplete_fields = ("corpus", "processing_task")
    readonly_fields = (
        "created_at", "updated_at", "started_at", "finished_at", "report_path", "anomalies_path", "summary", "error_message",
        "worker_job_id", "worker_state", "worker_attempt", "command_message_id", "command_published_at",
        "result_message_id", "result_received_at", "result_payload_hash",
    )
