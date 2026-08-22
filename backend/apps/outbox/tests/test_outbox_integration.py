import os
from datetime import timedelta
from io import StringIO
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, override_settings
from django.core.management import call_command
from django.utils import timezone

from apps.outbox.models import OutboxEvent, OutboxEventStatus, OutboxTaskName
from apps.outbox.services import (
    enqueue_task,
    publish_event,
    publish_pending_events,
    purge_published_events,
    replay_dead_letter_events,
)


class OutboxIntegrationTests(TestCase):
    def create_event(self) -> OutboxEvent:
        task_id = uuid4()
        return enqueue_task(
            task_name=OutboxTaskName.PROCESS_CORPUS,
            aggregate_id=task_id,
            payload={"task_id": str(task_id)},
            deduplication_key=f"processing:{task_id}",
        )

    def test_enqueue_is_idempotent_by_business_key(self):
        event = self.create_event()
        duplicate = enqueue_task(
            task_name=OutboxTaskName.PROCESS_CORPUS,
            aggregate_id=event.aggregate_id,
            payload={"task_id": str(event.aggregate_id)},
            deduplication_key=event.deduplication_key,
        )

        self.assertEqual(event.pk, duplicate.pk)
        self.assertEqual(OutboxEvent.objects.count(), 1)

    @patch("apps.outbox.services.current_app.send_task")
    def test_successful_publish_marks_event_as_published(self, send_task):
        event = self.create_event()

        result = publish_event(event.pk)

        self.assertEqual(result.outcome, "published")
        send_task.assert_called_once_with(
            OutboxTaskName.PROCESS_CORPUS,
            kwargs={"task_id": str(event.aggregate_id)},
            task_id=str(event.pk),
        )
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxEventStatus.PUBLISHED)
        self.assertEqual(event.attempt_count, 1)
        self.assertIsNotNone(event.published_at)

    @patch("apps.outbox.services.current_app.send_task", side_effect=ConnectionError("broker down"))
    def test_broker_failure_keeps_event_for_later_retry(self, _send_task):
        event = self.create_event()

        result = publish_event(event.pk)

        self.assertEqual(result.outcome, "retry_scheduled")
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxEventStatus.PENDING)
        self.assertEqual(event.attempt_count, 1)
        self.assertGreater(event.available_at, timezone.now())
        self.assertEqual(event.last_error, "broker down")

    @override_settings(OUTBOX_MAX_ATTEMPTS=1)
    @patch("apps.outbox.services.current_app.send_task", side_effect=ConnectionError("broker down"))
    def test_terminal_broker_failure_moves_event_to_dead_letter(self, _send_task):
        event = self.create_event()

        result = publish_event(event.pk)

        self.assertEqual(result.outcome, "dead_lettered")
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxEventStatus.DEAD_LETTER)
        self.assertEqual(event.attempt_count, 1)
        self.assertIsNotNone(event.dead_lettered_at)

    def test_dead_letter_replay_returns_event_to_pending(self):
        event = self.create_event()
        OutboxEvent.objects.filter(pk=event.pk).update(
            status=OutboxEventStatus.DEAD_LETTER,
            dead_lettered_at=timezone.now(),
            attempt_count=12,
            last_error="broker unavailable",
        )

        summary = replay_dead_letter_events(event_ids=[event.pk])

        self.assertEqual(summary.replayed, 1)
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxEventStatus.PENDING)
        self.assertEqual(event.replay_count, 1)
        self.assertIsNone(event.dead_lettered_at)
        self.assertEqual(event.last_error, "")

    def test_replay_command_requires_and_replays_explicit_event_id(self):
        event = self.create_event()
        OutboxEvent.objects.filter(pk=event.pk).update(
            status=OutboxEventStatus.DEAD_LETTER,
            dead_lettered_at=timezone.now(),
        )

        output = StringIO()
        call_command("replay_outbox", "--event-id", str(event.pk), stdout=output)

        self.assertEqual(output.getvalue(), "replayed=1 skipped=0\n")
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxEventStatus.PENDING)

    def test_publish_command_runs_with_queue_backed_audits(self):
        output = StringIO()

        call_command("publish_outbox", "--limit", "1", stdout=output)

        self.assertEqual(output.getvalue(), "")

    @patch("apps.outbox.services.current_app.send_task")
    def test_expired_publish_lease_is_recovered(self, send_task):
        event = self.create_event()
        OutboxEvent.objects.filter(pk=event.pk).update(
            status=OutboxEventStatus.PUBLISHING,
            attempt_count=1,
            locked_until=timezone.now() - timedelta(seconds=1),
        )

        summary = publish_pending_events(limit=10)

        self.assertEqual(summary.published, 1)
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxEventStatus.PUBLISHED)
        self.assertEqual(event.attempt_count, 2)
        send_task.assert_called_once()

    def test_purge_removes_only_expired_published_events(self):
        expired = self.create_event()
        recent = enqueue_task(
            task_name=OutboxTaskName.BUILD_EXPORT,
            aggregate_id=uuid4(),
            payload={"job_id": str(uuid4())},
            deduplication_key=f"export:{uuid4()}",
        )
        OutboxEvent.objects.filter(pk=expired.pk).update(
            status=OutboxEventStatus.PUBLISHED,
            published_at=timezone.now() - timedelta(days=8),
        )
        OutboxEvent.objects.filter(pk=recent.pk).update(
            status=OutboxEventStatus.PUBLISHED,
            published_at=timezone.now() - timedelta(days=1),
        )

        self.assertEqual(purge_published_events(), 1)
        self.assertFalse(OutboxEvent.objects.filter(pk=expired.pk).exists())
        self.assertTrue(OutboxEvent.objects.filter(pk=recent.pk).exists())

    @skipUnless(
        os.getenv("REQUIRE_REDIS_INTEGRATION") == "true",
        "Real broker publishing runs only where Redis is provisioned.",
    )
    def test_event_is_accepted_by_the_configured_celery_broker(self):
        event = self.create_event()

        result = publish_event(event.pk)

        self.assertEqual(result.outcome, "published")
        event.refresh_from_db()
        self.assertEqual(event.status, OutboxEventStatus.PUBLISHED)
