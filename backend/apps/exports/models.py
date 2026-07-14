from __future__ import annotations

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.corpora.models import Corpus


class ExportKind(models.TextChoices):
    KWIC = "kwic", "KWIC"
    PARALLEL = "parallel", "ParaConc"


class ExportJobStatus(models.TextChoices):
    PENDING = "pending", "等待执行"
    RUNNING = "running", "执行中"
    SUCCESS = "success", "可下载"
    FAILED = "failed", "失败"
    EXPIRED = "expired", "已过期"


class ExportJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="export_jobs",
        verbose_name="申请人",
    )
    corpus = models.ForeignKey(
        Corpus,
        on_delete=models.CASCADE,
        related_name="export_jobs",
        verbose_name="语料库",
    )
    kind = models.CharField("导出类型", max_length=20, choices=ExportKind.choices)
    query = models.JSONField("查询条件", default=dict)
    status = models.CharField(
        "状态",
        max_length=20,
        choices=ExportJobStatus.choices,
        default=ExportJobStatus.PENDING,
        db_index=True,
    )
    progress = models.PositiveSmallIntegerField(
        "进度",
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    output_path = models.CharField("导出文件", max_length=1500, blank=True)
    row_count = models.PositiveBigIntegerField("结果行数", default=0)
    download_count = models.PositiveIntegerField("下载次数", default=0)
    error_message = models.TextField("错误信息", blank=True)
    expires_at = models.DateTimeField("过期时间", db_index=True)
    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    finished_at = models.DateTimeField("完成时间", null=True, blank=True)
    last_downloaded_at = models.DateTimeField("最近下载", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "导出任务"
        verbose_name_plural = "导出任务"
        constraints = [
            models.UniqueConstraint(
                fields=["requested_by"],
                condition=Q(status__in=[ExportJobStatus.PENDING, ExportJobStatus.RUNNING]),
                name="one_active_export_job_per_user",
            )
        ]
        indexes = [
            models.Index(fields=["requested_by", "created_at"], name="export_user_created_idx"),
            models.Index(fields=["corpus", "created_at"], name="export_corpus_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.corpus.name} · {self.get_status_display()}"
