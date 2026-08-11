from celery import shared_task

from .exceptions import RetryableProcessingError
from .services import mark_processing_retry_exhausted, process_task


@shared_task(
    bind=True,
    name="processing.process_corpus",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
)
def process_corpus_task(self, task_id: str) -> dict:
    try:
        return process_task(task_id)
    except RetryableProcessingError as exc:
        if self.request.retries >= self.max_retries:
            mark_processing_retry_exhausted(task_id, str(exc))
            raise
        raise self.retry(exc=exc, countdown=2 ** self.request.retries) from exc
