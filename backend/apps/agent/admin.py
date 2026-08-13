from django.contrib import admin

from .models import AgentApproval, AgentRun, AgentStep


class AgentStepInline(admin.TabularInline):
    model = AgentStep
    extra = 0
    can_delete = False
    readonly_fields = (
        "sequence", "node", "tool_name", "status", "input", "output", "error_code",
        "error_message", "attempt_count", "started_at", "finished_at", "created_at", "updated_at",
    )


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ("id", "skill", "mode", "status", "requested_by", "corpus", "attempt_count", "created_at")
    list_filter = ("mode", "status", "skill")
    search_fields = ("id", "request_id", "requested_by__username", "corpus__name", "idempotency_key")
    autocomplete_fields = ("requested_by", "corpus")
    readonly_fields = (
        "id", "requested_by", "corpus", "mode", "skill", "idempotency_key", "request_id",
        "request_fingerprint", "plan", "status", "answer", "evidence", "model_usage",
        "estimated_cost_usd", "error_code", "error_message", "attempt_count", "locked_until",
        "external_wait_kind", "external_wait_id", "external_wait_started_at", "external_wait_expires_at",
        "started_at", "finished_at", "created_at", "updated_at",
    )
    inlines = (AgentStepInline,)

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(AgentApproval)
class AgentApprovalAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "action", "status", "expires_at", "resolved_at")
    list_filter = ("action", "status")
    search_fields = ("id", "run__id", "run__requested_by__username")
    autocomplete_fields = ("run",)
    readonly_fields = ("id", "run", "action", "payload", "status", "expires_at", "result", "resolved_at", "created_at", "updated_at")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
