from django.contrib import admin

from .models import AuditEvent, SavedSearch


@admin.register(SavedSearch)
class SavedSearchAdmin(admin.ModelAdmin):
    list_display = ("updated_at", "name", "kind", "user", "corpus")
    list_filter = ("kind", "updated_at")
    search_fields = ("name", "user__username", "corpus__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "actor", "corpus", "ip_address", "path")
    list_filter = ("event_type", "created_at")
    search_fields = ("actor__username", "corpus__name", "path", "ip_address")
    readonly_fields = (
        "actor",
        "event_type",
        "corpus",
        "path",
        "method",
        "ip_address",
        "user_agent",
        "metadata",
        "created_at",
    )
    date_hierarchy = "created_at"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
