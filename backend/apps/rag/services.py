from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.corpora.models import Corpus, CorpusStatus
from apps.outbox.models import OutboxTaskName
from apps.outbox.services import enqueue_task, publish_event_after_commit
from apps.processing.models import ProcessingTask, ProcessingTaskStatus

from .models import RagIndex, RagIndexStatus
from .providers import EmbeddingProviderError, OpenAICompatibleEmbeddingProvider
from .retrieval import HybridRagIndex, RagIndexUnavailable
from .vector_store import MilvusVectorStoreUnavailable


class RagIndexError(RuntimeError):
    code = "RAG_INDEX_FAILED"


class RetryableRagIndexError(RagIndexError):
    code = "RAG_INDEX_RETRYABLE"


@transaction.atomic
def queue_rag_index(
    *,
    corpus: Corpus,
    processing_task: ProcessingTask,
    schedule: bool = True,
) -> RagIndex:
    """Replace any old index manifest after a successful immutable rebuild."""

    # A corpus has a one-to-one index manifest.  The inner savepoint makes a
    # concurrent first insert recoverable without poisoning this transaction.
    try:
        with transaction.atomic():
            index, _ = RagIndex.objects.select_for_update().get_or_create(corpus=corpus)
    except IntegrityError:
        index = RagIndex.objects.select_for_update().get(corpus=corpus)
    index.processing_task = processing_task
    index.status = RagIndexStatus.PENDING
    index.chunk_manifest_sha256 = ""
    index.embedding_model = ""
    index.vector_dimension = 0
    index.chunk_count = 0
    index.vector_count = 0
    index.artifact_path = ""
    index.error_message = ""
    index.locked_until = None
    index.started_at = None
    index.finished_at = None
    index.save(
        update_fields=[
            "processing_task",
            "status",
            "chunk_manifest_sha256",
            "embedding_model",
            "vector_dimension",
            "chunk_count",
            "vector_count",
            "artifact_path",
            "error_message",
            "locked_until",
            "started_at",
            "finished_at",
            "updated_at",
        ]
    )
    if schedule:
        event = enqueue_task(
            task_name=OutboxTaskName.BUILD_RAG_INDEX,
            aggregate_id=index.pk,
            payload={"index_id": str(index.pk)},
            deduplication_key=f"rag-index:{processing_task.pk}",
        )
        publish_event_after_commit(event.pk)
    return index


def build_rag_index(index_id: str) -> dict[str, object]:
    index = _claim_index(index_id)
    if index is None:
        return {"index_id": str(index_id), "status": "skipped"}
    try:
        provider = OpenAICompatibleEmbeddingProvider.from_settings()
        result = HybridRagIndex(
            data_root=settings.DATA_ROOT,
            corpus_id=str(index.corpus_id),
        ).build(provider=provider)
    except (EmbeddingProviderError, MilvusVectorStoreUnavailable) as exc:
        _release_for_retry(index.pk, str(exc))
        raise RetryableRagIndexError(str(exc)) from exc
    except (RagIndexUnavailable, OSError, ValueError) as exc:
        _mark_failed(index.pk, str(exc))
        raise RagIndexError(str(exc)) from exc
    except Exception as exc:
        _mark_failed(index.pk, str(exc))
        raise RagIndexError("RAG index construction failed unexpectedly.") from exc
    _mark_ready(index.pk, result)
    return {
        "index_id": str(index.pk),
        "status": RagIndexStatus.READY,
        "chunk_count": result.chunk_count,
        "vector_dimension": result.vector_dimension,
    }


@transaction.atomic
def mark_rag_index_retry_exhausted(index_id: str, message: str) -> None:
    """Persist a terminal outcome after Celery exhausts transient retries."""

    index = RagIndex.objects.select_for_update().get(pk=index_id)
    if index.status in {RagIndexStatus.PENDING, RagIndexStatus.RUNNING}:
        _mark_failed_locked(index, f"Embedding provider retry budget exhausted: {message}")


@transaction.atomic
def _claim_index(index_id: str) -> RagIndex | None:
    index = RagIndex.objects.select_for_update().select_related("corpus", "processing_task").get(pk=index_id)
    now = timezone.now()
    if index.status == RagIndexStatus.READY:
        return None
    if index.status == RagIndexStatus.RUNNING and index.locked_until and index.locked_until > now:
        return None
    if (
        index.corpus.status != CorpusStatus.READY
        or index.processing_task is None
        or index.processing_task.status != ProcessingTaskStatus.SUCCESS
    ):
        _mark_failed_locked(index, "The source corpus is not ready for RAG indexing.")
        return None
    index.status = RagIndexStatus.RUNNING
    index.attempt_count += 1
    index.error_message = ""
    index.locked_until = now + timedelta(seconds=settings.RAG_INDEX_LEASE_SECONDS)
    if index.started_at is None:
        index.started_at = now
    index.finished_at = None
    index.save(
        update_fields=[
            "status",
            "attempt_count",
            "error_message",
            "locked_until",
            "started_at",
            "finished_at",
            "updated_at",
        ]
    )
    return index


@transaction.atomic
def _release_for_retry(index_id, message: str) -> None:
    index = RagIndex.objects.select_for_update().get(pk=index_id)
    if index.status != RagIndexStatus.RUNNING:
        return
    index.status = RagIndexStatus.PENDING
    index.locked_until = None
    index.error_message = f"Embedding provider unavailable; retrying: {message}"[:4000]
    index.save(update_fields=["status", "locked_until", "error_message", "updated_at"])


@transaction.atomic
def _mark_ready(index_id, result) -> None:
    index = RagIndex.objects.select_for_update().get(pk=index_id)
    if index.status != RagIndexStatus.RUNNING:
        return
    index.status = RagIndexStatus.READY
    index.chunk_manifest_sha256 = result.chunk_manifest_sha256
    index.embedding_model = result.embedding_model
    index.vector_dimension = result.vector_dimension
    index.chunk_count = result.chunk_count
    index.vector_count = result.vector_count
    index.artifact_path = result.artifact_path
    index.error_message = ""
    index.locked_until = None
    index.finished_at = timezone.now()
    index.save(
        update_fields=[
            "status",
            "chunk_manifest_sha256",
            "embedding_model",
            "vector_dimension",
            "chunk_count",
            "vector_count",
            "artifact_path",
            "error_message",
            "locked_until",
            "finished_at",
            "updated_at",
        ]
    )


@transaction.atomic
def _mark_failed(index_id, message: str) -> None:
    index = RagIndex.objects.select_for_update().get(pk=index_id)
    _mark_failed_locked(index, message)


def _mark_failed_locked(index: RagIndex, message: str) -> None:
    index.status = RagIndexStatus.FAILED
    index.error_message = message[:4000] or "Unknown RAG index failure"
    index.locked_until = None
    index.finished_at = timezone.now()
    index.save(
        update_fields=["status", "error_message", "locked_until", "finished_at", "updated_at"]
    )
