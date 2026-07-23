from django.contrib import admin

from .models import FeedbackTicket


@admin.register(FeedbackTicket)
class FeedbackTicketAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "category", "severity", "status", "created_at")
    list_filter = ("category", "severity", "status", "created_at")
    search_fields = ("title", "description", "user__username", "contact_email")
    readonly_fields = ("created_at", "updated_at")
