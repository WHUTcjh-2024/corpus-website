from celery import shared_task

from .services import run_parallel_audit


@shared_task(
    name="audits.audit_parallel_corpus",
    acks_late=True,
    reject_on_worker_lost=True,
)
def audit_parallel_corpus_task(audit_id: str) -> dict:
    return run_parallel_audit(audit_id)
