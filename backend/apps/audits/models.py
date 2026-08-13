from __future__ import annotations

import uuid

from django.db import models

from apps.corpora.models import Corpus
from apps.processing.models import ProcessingTask


class ParallelAuditStatus(models.TextChoices):
    PENDING = "pending", "等待审计"
    RUNNING = "running", "审计中"
    SUCCESS = "success", "审计完成"
    FAILED = "failed", "审计失败"


class ParallelAuditExecutionMode(models.TextChoices):
    QUEUE = "queue", "Redis Streams + Go worker"
    LOCAL = "local", "Local fallback"


class ParallelAudit(models.Model):
    """A durable, immutable audit attempt for one completed processing task."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    corpus = models.ForeignKey(
        Corpus,
        on_delete=models.CASCADE,
        related_name="parallel_audits",
        verbose_name="语料库",
    )
    processing_task = models.OneToOneField(
        ProcessingTask,
        on_delete=models.CASCADE,
        related_name="parallel_audit",
        verbose_name="来源加工任务",
    )
    status = models.CharField(
        "状态",
        max_length=20,
        choices=ParallelAuditStatus.choices,
        default=ParallelAuditStatus.PENDING,
        db_index=True,
    )
    execution_mode = models.CharField(
        "Execution mode",
        max_length=20,
        choices=ParallelAuditExecutionMode.choices,
        default=ParallelAuditExecutionMode.QUEUE,
    )
    worker_job_id = models.CharField(
        "Worker job ID", max_length=64, blank=True, unique=True, null=True
    )
    worker_state = models.CharField("Worker state", max_length=32, blank=True)
    worker_attempt = models.PositiveIntegerField("Worker attempt", default=0)
    command_message_id = models.CharField("Command stream message ID", max_length=64, blank=True)
    command_published_at = models.DateTimeField("Command published at", null=True, blank=True)
    result_message_id = models.CharField(
        "Result stream message ID", max_length=64, blank=True, unique=True, null=True
    )
    result_received_at = models.DateTimeField("Result received at", null=True, blank=True)
    result_payload_hash = models.CharField("Result payload hash", max_length=64, blank=True)
    report_path = models.CharField("报告路径", max_length=1500, blank=True)
    anomalies_path = models.CharField("异常明细路径", max_length=1500, blank=True)
    summary = models.JSONField("审计摘要", default=dict, blank=True)
    error_message = models.TextField("错误信息", blank=True)
    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    finished_at = models.DateTimeField("完成时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "平行语料审计"
        verbose_name_plural = "平行语料审计"
        indexes = [
            models.Index(fields=["corpus", "-created_at"], name="parallel_audit_corpus_idx"),
            models.Index(fields=["status", "-created_at"], name="parallel_audit_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.corpus.name}: {self.get_status_display()}"
