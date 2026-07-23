from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class FeedbackCategory(models.TextChoices):
    BUG = "bug", "问题报告"
    DATA = "data", "数据异常"
    FEATURE = "feature", "功能建议"
    ACCOUNT = "account", "账号权限"
    OTHER = "other", "其他"


class FeedbackSeverity(models.TextChoices):
    LOW = "low", "一般"
    MEDIUM = "medium", "影响使用"
    HIGH = "high", "严重阻塞"


class FeedbackStatus(models.TextChoices):
    OPEN = "open", "待处理"
    TRIAGED = "triaged", "已确认"
    IN_PROGRESS = "in_progress", "处理中"
    RESOLVED = "resolved", "已解决"
    CLOSED = "closed", "已关闭"


class FeedbackTicket(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feedback_tickets",
        verbose_name="提交用户",
    )
    title = models.CharField("标题", max_length=160)
    category = models.CharField(
        "类型",
        max_length=20,
        choices=FeedbackCategory.choices,
        default=FeedbackCategory.BUG,
    )
    severity = models.CharField(
        "影响程度",
        max_length=20,
        choices=FeedbackSeverity.choices,
        default=FeedbackSeverity.MEDIUM,
    )
    status = models.CharField(
        "状态",
        max_length=20,
        choices=FeedbackStatus.choices,
        default=FeedbackStatus.OPEN,
        db_index=True,
    )
    page_url = models.CharField("相关页面", max_length=500, blank=True)
    contact_email = models.EmailField("联系邮箱", blank=True)
    description = models.TextField("详细说明")
    admin_note = models.TextField("处理备注", blank=True)
    resolved_at = models.DateTimeField("解决时间", null=True, blank=True)
    created_at = models.DateTimeField("提交时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "反馈问题"
        verbose_name_plural = "反馈问题"

    def __str__(self) -> str:
        return self.title
