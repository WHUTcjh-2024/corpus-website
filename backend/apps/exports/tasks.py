from celery import shared_task

from .services import process_export_job


@shared_task(name="exports.build_export")
def build_export_task(job_id: str) -> dict:
    return process_export_job(job_id)
