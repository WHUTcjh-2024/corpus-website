from __future__ import annotations

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.corpora.models import Corpus


class AgentRunMode(models.TextChoices):
    RETRIEVE = "retrieve", "证据检索"
    QUALITY_REVIEW = "quality_review", "质量审阅"
    EXPORT = "export", "检索后导出"


class AgentRunStatus(models.TextChoices):
    PENDING = "pending", "等待执行"
    RUNNING = "running", "执行中"
    WAITING_EXTERNAL = "waiting_external", "等待外部任务"
    WAITING_APPROVAL = "waiting_approval", "等待确认"
    SUCCEEDED = "succeeded", "已完成"
    FAILED = "failed", "失败"
    CANCELLED = "cancelled", "已取消"


class AgentStepStatus(models.TextChoices):
    PENDING = "pending", "等待执行"
    RUNNING = "running", "执行中"
    SUCCEEDED = "succeeded", "已完成"
    FAILED = "failed", "失败"
    SKIPPED = "skipped", "已跳过"


class AgentApprovalStatus(models.TextChoices):
    PENDING = "pending", "等待确认"
    APPROVED = "approved", "已确认"
    EXPIRED = "expired", "已过期"
    REJECTED = "rejected", "已拒绝"


class AgentApprovalAction(models.TextChoices):
    CREATE_EXPORT = "create_export", "创建导出任务"


class AgentExternalWaitKind(models.TextChoices):
    PARALLEL_AUDIT = "parallel_audit", "等待平行语料质检"


class AgentRun(models.Model):
    """A durable, replayable execution of a versioned corpus Agent skill.

    The plan is intentionally persisted before it reaches Celery.  A worker can
    therefore resume after a broker redelivery without asking an LLM to create a
    different plan, and every executable tool call remains auditable.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_runs",
        verbose_name="请求用户",
    )
    corpus = models.ForeignKey(
        Corpus,
        on_delete=models.CASCADE,
        related_name="agent_runs",
        verbose_name="语料库",
    )
    mode = models.CharField(max_length=30, choices=AgentRunMode.choices)
    skill = models.CharField("Skill 版本", max_length=100)
    idempotency_key = models.CharField("幂等键", max_length=128)
    request_id = models.CharField("请求追踪 ID", max_length=128, db_index=True)
    request_fingerprint = models.CharField("请求指纹", max_length=64)
    plan = models.JSONField("已验证执行计划", default=dict)
    status = models.CharField(
        "执行状态",
        max_length=30,
        choices=AgentRunStatus.choices,
        default=AgentRunStatus.PENDING,
        db_index=True,
    )
    answer = models.TextField("证据化回答", blank=True)
    evidence = models.JSONField("证据引用", default=list, blank=True)
    model_usage = models.JSONField("模型用量", default=dict, blank=True)
    estimated_cost_usd = models.DecimalField(
        "预估模型成本(USD)",
        max_digits=12,
        decimal_places=8,
        default=0,
    )
    error_code = models.CharField("错误码", max_length=80, blank=True)
    error_message = models.TextField("错误详情", blank=True)
    attempt_count = models.PositiveSmallIntegerField("执行尝试次数", default=0)
    locked_until = models.DateTimeField("执行租约截止", null=True, blank=True, db_index=True)
    external_wait_kind = models.CharField(
        "外部等待类型",
        max_length=40,
        choices=AgentExternalWaitKind.choices,
        blank=True,
    )
    external_wait_id = models.UUIDField("外部等待任务 ID", null=True, blank=True, db_index=True)
    external_wait_expires_at = models.DateTimeField(
        "外部等待超时", null=True, blank=True, db_index=True
    )
    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    finished_at = models.DateTimeField("完成时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "Agent 运行"
        verbose_name_plural = "Agent 运行"
        constraints = [
            models.UniqueConstraint(
                fields=["requested_by", "idempotency_key"],
                name="agent_run_user_idempotency_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["requested_by", "status", "-created_at"],
                name="agent_run_user_status_idx",
            ),
            models.Index(
                fields=["corpus", "-created_at"],
                name="agent_run_corpus_created_idx",
            ),
            models.Index(
                fields=["status", "locked_until"],
                name="agent_run_lease_idx",
            ),
            models.Index(
                fields=["status", "external_wait_id"],
                name="agent_run_external_wait_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.skill} · {self.get_status_display()} · {self.id}"


class AgentStep(models.Model):
    """A bounded trace step.  Inputs/outputs exclude raw prompts and secrets."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="steps",
        verbose_name="Agent 运行",
    )
    sequence = models.PositiveSmallIntegerField(
        "步骤序号", validators=[MinValueValidator(1), MaxValueValidator(20)]
    )
    node = models.CharField("工作流节点", max_length=80)
    tool_name = models.CharField("工具", max_length=100, blank=True)
    status = models.CharField(
        "状态", max_length=20, choices=AgentStepStatus.choices, default=AgentStepStatus.PENDING
    )
    input = models.JSONField("受控工具入参", default=dict)
    output = models.JSONField("脱敏工具结果", default=dict, blank=True)
    error_code = models.CharField("错误码", max_length=80, blank=True)
    error_message = models.TextField("错误详情", blank=True)
    attempt_count = models.PositiveSmallIntegerField("尝试次数", default=0)
    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    finished_at = models.DateTimeField("完成时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["sequence"]
        verbose_name = "Agent 步骤"
        verbose_name_plural = "Agent 步骤"
        constraints = [
            models.UniqueConstraint(fields=["run", "sequence"], name="agent_step_run_sequence"),
        ]

    def __str__(self) -> str:
        return f"{self.run_id}:{self.sequence}:{self.node}"


class AgentApproval(models.Model):
    """A one-time, user-bound approval for an Agent-proposed write action."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.OneToOneField(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="approval",
        verbose_name="Agent 运行",
    )
    action = models.CharField(max_length=40, choices=AgentApprovalAction.choices)
    payload = models.JSONField("待确认动作", default=dict)
    status = models.CharField(
        max_length=20,
        choices=AgentApprovalStatus.choices,
        default=AgentApprovalStatus.PENDING,
        db_index=True,
    )
    expires_at = models.DateTimeField("确认截止时间", db_index=True)
    result = models.JSONField("动作结果", default=dict, blank=True)
    resolved_at = models.DateTimeField("处理时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "Agent 审批"
        verbose_name_plural = "Agent 审批"
        indexes = [
            models.Index(fields=["status", "expires_at"], name="agent_approval_expiry_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_action_display()} · {self.get_status_display()}"
