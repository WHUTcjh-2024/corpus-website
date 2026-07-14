from __future__ import annotations

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.corpora.models import Corpus


class ProcessingTaskType(models.TextChoices):
    PROCESS_CORPUS = "process_corpus", "加工语料库"


class ProcessingTaskStatus(models.TextChoices):
    PENDING = "pending", "等待执行"
    RUNNING = "running", "执行中"
    SUCCESS = "success", "成功"
    FAILED = "failed", "失败"


class ProcessingTask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    corpus = models.ForeignKey(
        Corpus,
        on_delete=models.CASCADE,
        related_name="processing_tasks",
        verbose_name="语料库",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="requested_processing_tasks",
        null=True,
        blank=True,
        verbose_name="发起人",
    )
    task_type = models.CharField(
        "任务类型",
        max_length=30,
        choices=ProcessingTaskType.choices,
        default=ProcessingTaskType.PROCESS_CORPUS,
    )
    status = models.CharField(
        "状态",
        max_length=20,
        choices=ProcessingTaskStatus.choices,
        default=ProcessingTaskStatus.PENDING,
        db_index=True,
    )
    progress = models.PositiveSmallIntegerField(
        "进度",
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    error_message = models.TextField("错误信息", blank=True)
    output_path = models.CharField("输出路径", max_length=1500, blank=True)
    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    finished_at = models.DateTimeField("结束时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "加工任务"
        verbose_name_plural = "加工任务"
        constraints = [
            models.UniqueConstraint(
                fields=["corpus"],
                condition=Q(status__in=[ProcessingTaskStatus.PENDING, ProcessingTaskStatus.RUNNING]),
                name="one_active_processing_task_per_corpus",
            ),
            models.UniqueConstraint(
                fields=["requested_by"],
                condition=(
                    Q(requested_by__isnull=False)
                    & Q(status__in=[ProcessingTaskStatus.PENDING, ProcessingTaskStatus.RUNNING])
                ),
                name="one_active_processing_task_per_user",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.corpus.name}: {self.get_status_display()}"
