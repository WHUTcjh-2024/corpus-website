from __future__ import annotations

from uuid import uuid4

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.outbox.models import OutboxEvent, OutboxEventStatus, OutboxTaskName


@override_settings(METRICS_BEARER_TOKEN="metrics-test-token")
class MetricsIntegrationTests(TestCase):
    def test_metrics_requires_the_configured_bearer_token(self):
        response = self.client.get("/metrics")

        self.assertEqual(response.status_code, 404)

    def test_metrics_exposes_outbox_backlog_and_dead_letters(self):
        pending_id = uuid4()
        OutboxEvent.objects.create(
            task_name=OutboxTaskName.PROCESS_CORPUS,
            aggregate_id=pending_id,
            deduplication_key=f"processing:{pending_id}",
            payload={"task_id": str(pending_id)},
            available_at=timezone.now(),
        )
        dead_letter_id = uuid4()
        OutboxEvent.objects.create(
            task_name=OutboxTaskName.BUILD_EXPORT,
            aggregate_id=dead_letter_id,
            deduplication_key=f"export:{dead_letter_id}",
            payload={"job_id": str(dead_letter_id)},
            status=OutboxEventStatus.DEAD_LETTER,
            available_at=timezone.now(),
            dead_lettered_at=timezone.now(),
        )

        response = self.client.get(
            "/metrics",
            headers={"Authorization": "Bearer metrics-test-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"], "text/plain; version=0.0.4; charset=utf-8"
        )
        body = response.content.decode()
        self.assertIn('corpus_outbox_events{status="pending"} 1', body)
        self.assertIn('corpus_outbox_events{status="dead_letter"} 1', body)
        self.assertIn("corpus_outbox_oldest_pending_age_seconds", body)
