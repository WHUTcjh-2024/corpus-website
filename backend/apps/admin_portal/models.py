from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class AnnouncementAudience(models.TextChoices):
    ALL = "all", "全部已审核用户"
    SELECTED = "selected", "指定用户"


class Announcement(models.Model):
    title = models.CharField("标题", max_length=160)
    body = models.TextField("正文")
    audience = models.CharField(
        "发布范围",
        max_length=20,
        choices=AnnouncementAudience.choices,
        default=AnnouncementAudience.ALL,
    )
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="AnnouncementRecipient",
        blank=True,
        related_name="received_announcements",
        verbose_name="指定接收用户",
    )
    is_published = models.BooleanField("已发布", default=False, db_index=True)
    starts_at = models.DateTimeField("开始展示", default=timezone.now)
    ends_at = models.DateTimeField("结束展示", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_announcements",
        null=True,
        blank=True,
        verbose_name="创建管理员",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="published_announcements",
        null=True,
        blank=True,
        verbose_name="发布管理员",
    )
    published_at = models.DateTimeField("发布时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-is_published", "-starts_at", "-created_at"]
        verbose_name = "平台公告"
        verbose_name_plural = "平台公告"
        indexes = [
            models.Index(fields=["is_published", "starts_at"], name="announcement_active_idx"),
        ]

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        super().clean()
        if self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "结束展示时间必须晚于开始展示时间。"})


class AnnouncementRecipient(models.Model):
    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.CASCADE,
        related_name="recipient_links",
        verbose_name="公告",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="announcement_recipient_links",
        verbose_name="用户",
    )
    created_at = models.DateTimeField("添加时间", auto_now_add=True)

    class Meta:
        verbose_name = "公告接收人"
        verbose_name_plural = "公告接收人"
        constraints = [
            models.UniqueConstraint(
                fields=["announcement", "user"],
                name="unique_announcement_recipient",
            )
        ]

    def __str__(self) -> str:
        return f"{self.announcement.title} → {self.user.get_username()}"
