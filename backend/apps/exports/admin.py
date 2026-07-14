from django.contrib import admin

from .models import ExportJob


@admin.register(ExportJob)
class ExportJobAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "requested_by",
        "corpus",
        "kind",
        "status",
        "row_count",
        "download_count",
        "expires_at",
    )
    list_filter = ("kind", "status", "created_at", "expires_at")
    search_fields = ("requested_by__username", "corpus__name", "id")
    readonly_fields = (
        "requested_by",
        "corpus",
        "kind",
        "query",
        "status",
        "progress",
        "output_path",
        "row_count",
        "download_count",
        "error_message",
        "expires_at",
        "started_at",
        "finished_at",
        "last_downloaded_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
