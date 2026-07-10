from celery import shared_task

from .services import process_task


@shared_task(name="processing.process_corpus")
def process_corpus_task(task_id: str) -> dict:
    return process_task(task_id)
