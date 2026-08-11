from celery import shared_task

from .services import (
    RetryableExportError,
    mark_export_retry_exhausted,
    process_export_job,
)


@shared_task(
    bind=True,
    name="exports.build_export",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
)
def build_export_task(self, job_id: str) -> dict:
    try:
        return process_export_job(job_id)
    except RetryableExportError as exc:
        if self.request.retries >= self.max_retries:
            mark_export_retry_exhausted(job_id, str(exc))
            raise
        raise self.retry(exc=exc, countdown=2 ** self.request.retries) from exc
