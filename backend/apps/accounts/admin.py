from __future__ import annotations

from django.contrib import admin
from django.utils import timezone

from .models import ApplicationStatus, UserProfile
from .services import review_application


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "full_name",
        "organization",
        "requested_role",
        "role",
        "status",
        "created_at",
    )
    list_filter = ("status", "role", "requested_role", "created_at")
    search_fields = ("user__username", "full_name", "organization", "email")
    readonly_fields = ("created_at", "updated_at", "reviewed_at", "reviewed_by")
    autocomplete_fields = ("user",)
    actions = ("approve_profiles", "reject_profiles", "disable_profiles")
    fieldsets = (
        (
            "账号",
            {"fields": ("user", "full_name", "organization", "email")},
        ),
        (
            "申请信息",
            {
                "fields": (
                    "requested_role",
                    "use_purpose",
                    "application_reason",
                )
            },
        ),
        (
            "审核与权限",
            {
                "fields": (
                    "role",
                    "status",
                    "reviewed_by",
                    "reviewed_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def save_model(self, request, obj, form, change) -> None:
        if change and "status" in form.changed_data:
            obj.reviewed_by = request.user
            obj.reviewed_at = timezone.now()
        super().save_model(request, obj, form, change)

    @admin.action(description="审核通过所选申请")
    def approve_profiles(self, request, queryset) -> None:
        for profile in queryset.select_related("user"):
            review_application(
                profile,
                status=ApplicationStatus.APPROVED,
                reviewer=request.user,
                role=profile.role,
            )

    @admin.action(description="拒绝所选申请")
    def reject_profiles(self, request, queryset) -> None:
        for profile in queryset.select_related("user"):
            review_application(
                profile,
                status=ApplicationStatus.REJECTED,
                reviewer=request.user,
            )

    @admin.action(description="停用所选账号")
    def disable_profiles(self, request, queryset) -> None:
        for profile in queryset.select_related("user"):
            review_application(
                profile,
                status=ApplicationStatus.DISABLED,
                reviewer=request.user,
            )
