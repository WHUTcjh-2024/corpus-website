from __future__ import annotations

from django.contrib import admin
from django.utils import timezone

from .models import (
    ApplicationStatus,
    QuotaRequestStatus,
    UploadQuotaRequest,
    UserProfile,
)
from .services import review_application, review_quota_request


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
                    "upload_max_file_bytes",
                    "upload_total_bytes",
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


@admin.register(UploadQuotaRequest)
class UploadQuotaRequestAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "requested_max_file_bytes",
        "requested_total_bytes",
        "status",
        "created_at",
        "reviewed_by",
    )
    list_filter = ("status", "created_at")
    search_fields = ("user__username", "reason")
    readonly_fields = (
        "user",
        "requested_max_file_bytes",
        "requested_total_bytes",
        "reason",
        "status",
        "created_at",
        "updated_at",
        "reviewed_by",
        "reviewed_at",
    )
    actions = ("approve_requests", "reject_requests")

    def has_add_permission(self, request) -> bool:
        return False

    @admin.action(description="批准所选扩容申请")
    def approve_requests(self, request, queryset) -> None:
        for item in queryset.filter(status=QuotaRequestStatus.PENDING):
            review_quota_request(
                item,
                status=QuotaRequestStatus.APPROVED,
                reviewer=request.user,
            )

    @admin.action(description="拒绝所选扩容申请")
    def reject_requests(self, request, queryset) -> None:
        for item in queryset.filter(status=QuotaRequestStatus.PENDING):
            review_quota_request(
                item,
                status=QuotaRequestStatus.REJECTED,
                reviewer=request.user,
            )
