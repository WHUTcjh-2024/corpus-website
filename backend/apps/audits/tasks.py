from celery import shared_task

from .services import RetryableParallelAuditError, run_parallel_audit


@shared_task(
	bind=True,
    name="audits.audit_parallel_corpus",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=5,
)
def audit_parallel_corpus_task(self, audit_id: str) -> dict:
    try:
        return run_parallel_audit(audit_id)
    except RetryableParallelAuditError as exc:
        raise self.retry(exc=exc, countdown=min(2 ** self.request.retries, 60)) from exc
