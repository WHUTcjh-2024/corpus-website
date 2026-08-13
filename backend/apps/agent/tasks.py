from celery import shared_task

from .services import execute_agent_run


@shared_task(
    name="agent.run_corpus_agent",
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_corpus_agent_task(run_id: str) -> dict:
    """Execute a durable Agent plan; duplicate broker deliveries are no-ops."""
    return execute_agent_run(run_id)


@shared_task(
    name="agent.resume_corpus_agent",
    acks_late=True,
    reject_on_worker_lost=True,
)
def resume_corpus_agent_task(run_id: str) -> dict:
    """Continue a run only after its correlated external result is projected."""
    return execute_agent_run(run_id)
