from celery import shared_task

from .services import (
    RetryableRagIndexError,
    build_rag_index,
    mark_rag_index_retry_exhausted,
)


@shared_task(
    bind=True,
    name="rag.build_vector_index",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
)
def build_vector_index_task(self, index_id: str) -> dict:
    try:
        return build_rag_index(index_id)
    except RetryableRagIndexError as exc:
        if self.request.retries >= self.max_retries:
            mark_rag_index_retry_exhausted(index_id, str(exc))
            raise
        raise self.retry(exc=exc, countdown=2 ** self.request.retries) from exc
