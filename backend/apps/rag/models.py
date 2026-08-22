from __future__ import annotations

import uuid

from django.db import models

from apps.corpora.models import Corpus
from apps.processing.models import ProcessingTask


class RagIndexStatus(models.TextChoices):
    PENDING = "pending", "等待索引"
    RUNNING = "running", "索引构建中"
    READY = "ready", "索引就绪"
    FAILED = "failed", "索引失败"


class RagIndex(models.Model):
    """A durable manifest for one corpus' versioned dense-vector index.

    Corpus text remains in the immutable processed artifact.  This model stores
    control-plane metadata only, so a database backup never becomes the source
    of truth for embedded document content.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    corpus = models.OneToOneField(
        Corpus,
        on_delete=models.CASCADE,
        related_name="rag_index",
        verbose_name="语料库",
    )
    processing_task = models.ForeignKey(
        ProcessingTask,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rag_indexes",
        verbose_name="来源加工任务",
    )
    status = models.CharField(
        max_length=20,
        choices=RagIndexStatus.choices,
        default=RagIndexStatus.PENDING,
        db_index=True,
    )
    chunk_manifest_sha256 = models.CharField(max_length=64, blank=True)
    embedding_model = models.CharField(max_length=200, blank=True)
    vector_dimension = models.PositiveIntegerField(default=0)
    chunk_count = models.PositiveIntegerField(default=0)
    vector_count = models.PositiveIntegerField(default=0)
    artifact_path = models.CharField(max_length=1500, blank=True)
    error_message = models.TextField(blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "RAG 索引"
        verbose_name_plural = "RAG 索引"
        indexes = [
            models.Index(fields=["status", "locked_until"], name="rag_index_lease_idx"),
            models.Index(fields=["status", "-updated_at"], name="rag_index_status_updated_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.corpus.name} · {self.get_status_display()}"
