from __future__ import annotations

import uuid

from django.db import models


class OutboxEventStatus(models.TextChoices):
    PENDING = "pending", "等待投递"
    PUBLISHING = "publishing", "投递中"
    PUBLISHED = "published", "已投递"


    DEAD_LETTER = "dead_letter", "Dead letter"


class OutboxTaskName(models.TextChoices):
    PROCESS_CORPUS = "processing.process_corpus", "加工语料"
    BUILD_EXPORT = "exports.build_export", "生成导出"


    AUDIT_PARALLEL_CORPUS = "audits.audit_parallel_corpus", "平行语料审计"


class OutboxEvent(models.Model):
    """A durable command that is published to Celery after its DB transaction commits."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_name = models.CharField("Celery 任务", max_length=100, choices=OutboxTaskName.choices)
    aggregate_id = models.UUIDField("业务任务 ID", db_index=True)
    deduplication_key = models.CharField("幂等键", max_length=180, unique=True)
    payload = models.JSONField("任务参数", default=dict)
    status = models.CharField(
        "投递状态",
        max_length=20,
        choices=OutboxEventStatus.choices,
        default=OutboxEventStatus.PENDING,
    )
    attempt_count = models.PositiveIntegerField("投递尝试次数", default=0)
    available_at = models.DateTimeField("下次可投递时间", db_index=True)
    locked_until = models.DateTimeField("投递租约截止", null=True, blank=True, db_index=True)
    published_at = models.DateTimeField("投递完成时间", null=True, blank=True)
    last_error = models.TextField("最近错误", blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    dead_lettered_at = models.DateTimeField(null=True, blank=True)
    replay_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["available_at", "created_at"]
        verbose_name = "事务外盒事件"
        verbose_name_plural = "事务外盒事件"
        indexes = [
            models.Index(fields=["status", "available_at"], name="outbox_pending_idx"),
            models.Index(fields=["status", "locked_until"], name="outbox_lease_idx"),
            models.Index(fields=["status", "published_at"], name="outbox_cleanup_idx"),
            models.Index(fields=["status", "dead_lettered_at"], name="outbox_dead_letter_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.task_name} · {self.get_status_display()} · {self.aggregate_id}"
