from django.contrib import admin

from .models import Announcement, AnnouncementRecipient


class AnnouncementRecipientInline(admin.TabularInline):
    model = AnnouncementRecipient
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "audience", "is_published", "starts_at", "ends_at", "published_by")
    list_filter = ("audience", "is_published")
    search_fields = ("title", "body")
    readonly_fields = ("created_at", "updated_at", "published_at")
    inlines = (AnnouncementRecipientInline,)
