from celery import shared_task

from .services import RetryableParallelAuditError, publish_parallel_audit_command


@shared_task(
	bind=True,
    name="audits.publish_parallel_audit_command",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=5,
)
def publish_parallel_audit_command_task(self, audit_id: str) -> dict:
    try:
        return publish_parallel_audit_command(audit_id)
    except RetryableParallelAuditError as exc:
        raise self.retry(exc=exc, countdown=min(2 ** self.request.retries, 60)) from exc
