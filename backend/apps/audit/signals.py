from __future__ import annotations

from django.contrib.admin.models import LogEntry
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import AuditEventType
from .services import record_audit_event


@receiver(user_logged_in)
def audit_login_success(sender, request, user, **kwargs) -> None:
    record_audit_event(AuditEventType.LOGIN_SUCCESS, request=request, actor=user)


@receiver(user_login_failed)
def audit_login_failed(sender, credentials, request, **kwargs) -> None:
    record_audit_event(
        AuditEventType.LOGIN_FAILED,
        request=request,
        metadata={"username": str(credentials.get("username", ""))[:150]},
    )


@receiver(user_logged_out)
def audit_logout(sender, request, user, **kwargs) -> None:
    record_audit_event(AuditEventType.LOGOUT, request=request, actor=user)


@receiver(post_save, sender=LogEntry)
def audit_admin_log_entry(sender, instance: LogEntry, created: bool, **kwargs) -> None:
    if not created:
        return
    record_audit_event(
        AuditEventType.ADMIN_ACTION,
        actor=instance.user,
        metadata={
            "action_flag": instance.action_flag,
            "content_type": str(instance.content_type_id or ""),
            "object_id": instance.object_id,
            "object_repr": instance.object_repr,
            "change_message": instance.change_message,
        },
    )
