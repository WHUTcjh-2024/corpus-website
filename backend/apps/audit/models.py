from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.corpora.models import Corpus


class AuditEventType(models.TextChoices):
    LOGIN_SUCCESS = "login.success", "登录成功"
    LOGIN_FAILED = "login.failed", "登录失败"
    LOGOUT = "logout", "退出登录"
    KWIC_SEARCH = "search.kwic", "KWIC 检索"
    PARALLEL_SEARCH = "search.parallel", "ParaConc 检索"
    STATISTICS_QUERY = "search.statistics", "统计分析"
    CORPUS_UPLOAD = "corpus.upload", "上传语料"
    CORPUS_RETRY = "corpus.retry", "重试加工"
    CORPUS_DELETE = "corpus.delete", "删除语料"
    QUOTA_REQUEST = "quota.request", "申请扩容"
    EXPORT_CREATED = "export.created", "创建导出"
    EXPORT_COMPLETED = "export.completed", "导出完成"
    EXPORT_FAILED = "export.failed", "导出失败"
    EXPORT_DOWNLOADED = "export.downloaded", "下载导出"
    ADMIN_ACTION = "admin.action", "管理员操作"


class AuditEvent(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="audit_events",
        null=True,
        blank=True,
        verbose_name="操作用户",
    )
    event_type = models.CharField(
        "事件类型",
        max_length=40,
        choices=AuditEventType.choices,
        db_index=True,
    )
    corpus = models.ForeignKey(
        Corpus,
        on_delete=models.SET_NULL,
        related_name="audit_events",
        null=True,
        blank=True,
        verbose_name="语料库",
    )
    path = models.CharField("请求路径", max_length=500, blank=True)
    method = models.CharField("请求方法", max_length=10, blank=True)
    ip_address = models.GenericIPAddressField("IP 地址", null=True, blank=True)
    user_agent = models.CharField("User-Agent", max_length=500, blank=True)
    metadata = models.JSONField("事件数据", default=dict, blank=True)
    created_at = models.DateTimeField("发生时间", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "审计事件"
        verbose_name_plural = "审计事件"
        indexes = [
            models.Index(fields=["actor", "created_at"], name="audit_actor_created_idx"),
            models.Index(fields=["corpus", "created_at"], name="audit_corpus_created_idx"),
            models.Index(fields=["event_type", "created_at"], name="audit_type_created_idx"),
        ]

    def __str__(self) -> str:
        actor = self.actor.get_username() if self.actor_id else "anonymous"
        return f"{self.get_event_type_display()} · {actor}"
