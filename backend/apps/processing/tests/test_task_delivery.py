from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusFile,
    CorpusFileStatus,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from apps.processing.exceptions import RetryableProcessingError
from apps.processing.models import ProcessingTask, ProcessingTaskStatus
from apps.processing.services import (
    create_processing_task,
    dispatch_processing_task,
    process_task,
)
from apps.processing.tasks import process_corpus_task
from apps.outbox.models import OutboxEvent, OutboxEventStatus, OutboxTaskName
from apps.rag.models import RagIndex, RagIndexStatus


class ProcessingTaskDeliveryIntegrationTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.settings_override = override_settings(DATA_ROOT=Path(self.temp_dir.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        self.user = get_user_model().objects.create_user(
            username="processing-user",
            password="safe-test-password",
        )
        UserProfile.objects.create(
            user=self.user,
            full_name="Processing User",
            organization="Test Lab",
            email="processing-user@example.test",
            role=UserRole.JUNIOR,
            use_purpose="integration tests",
            application_reason="verify asynchronous processing",
            status=ApplicationStatus.APPROVED,
        )
        self.corpus = Corpus.objects.create(
            name="Processing Corpus",
            source_type=CorpusSourceType.USER,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            owner=self.user,
            access_level=CorpusAccessLevel.PRIVATE,
            status=CorpusStatus.PENDING_PROCESSING,
        )
        source_dir = Path(self.temp_dir.name) / "user_uploads" / str(self.user.pk)
        source_dir.mkdir(parents=True)
        source_path = source_dir / "sample.txt"
        source_path.write_text("The corpus platform processes this sentence.", encoding="utf-8")
        CorpusFile.objects.create(
            corpus=self.corpus,
            original_filename=source_path.name,
            stored_path=str(source_path),
            detected_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            size_bytes=source_path.stat().st_size,
            encoding="utf-8",
            status=CorpusFileStatus.PENDING,
        )
        self.task = ProcessingTask.objects.create(corpus=self.corpus, requested_by=self.user)

    def test_duplicate_delivery_is_a_noop_after_successful_processing(self):
        first = process_corpus_task.apply(args=[str(self.task.pk)])
        self.assertTrue(first.successful())

        self.task.refresh_from_db()
        self.corpus.refresh_from_db()
        self.assertEqual(self.task.status, ProcessingTaskStatus.SUCCESS)
        self.assertEqual(self.corpus.status, CorpusStatus.READY)

        duplicate = process_corpus_task.apply(args=[str(self.task.pk)])
        self.assertEqual(duplicate.get(), {"task_id": str(self.task.pk), "status": "skipped"})
        self.assertEqual(ProcessingTask.objects.filter(corpus=self.corpus).count(), 1)

    def test_retryable_worker_error_is_retried_with_backoff(self):
        with patch(
            "apps.processing.tasks.process_task",
            side_effect=[
                RetryableProcessingError("temporary storage error"),
                {"task_id": str(self.task.pk), "status": "success"},
            ],
        ) as process:
            result = process_corpus_task.apply(args=[str(self.task.pk)])

        self.assertEqual(result.get(), {"task_id": str(self.task.pk), "status": "success"})
        self.assertEqual(process.call_count, 2)

    def test_retry_budget_exhaustion_marks_the_processing_task_failed(self):
        with patch(
            "apps.processing.tasks.process_task",
            side_effect=RetryableProcessingError("persistent storage error"),
        ) as process:
            result = process_corpus_task.apply(args=[str(self.task.pk)])

        self.assertTrue(result.failed())
        self.assertEqual(process.call_count, 4)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, ProcessingTaskStatus.FAILED)

    def test_transient_filesystem_error_releases_the_task_for_retry(self):
        with patch("apps.processing.services.ArtifactWriter.open", side_effect=OSError("disk busy")):
            with self.assertRaises(RetryableProcessingError):
                process_task(self.task.pk)

        self.task.refresh_from_db()
        self.corpus.refresh_from_db()
        self.assertEqual(self.task.status, ProcessingTaskStatus.PENDING)
        self.assertEqual(self.task.progress, 0)
        self.assertEqual(self.corpus.status, CorpusStatus.PENDING_PROCESSING)

    def test_broker_failure_does_not_fail_the_committed_processing_task(self):
        self.task.delete()
        task = create_processing_task(corpus=self.corpus, requested_by=self.user)
        event = OutboxEvent.objects.get(deduplication_key=f"processing:{task.pk}")

        with patch(
            "apps.outbox.services.current_app.send_task",
            side_effect=ConnectionError("broker down"),
        ), self.captureOnCommitCallbacks(execute=True):
            result = dispatch_processing_task(task)

        self.assertEqual(result.outcome, "scheduled")
        task.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(task.status, ProcessingTaskStatus.PENDING)
        self.assertEqual(event.status, OutboxEventStatus.PENDING)
        self.assertEqual(event.task_name, OutboxTaskName.PROCESS_CORPUS)

    def test_successful_processing_durably_queues_rag_indexing_when_enabled(self):
        with self.settings(RAG_INDEXING_ENABLED=True), patch(
            "apps.rag.services.publish_event_after_commit"
        ) as publish:
            process_task(self.task.pk)

        index = RagIndex.objects.get(corpus=self.corpus)
        event = OutboxEvent.objects.get(deduplication_key=f"rag-index:{self.task.pk}")
        self.assertEqual(index.status, RagIndexStatus.PENDING)
        self.assertEqual(index.processing_task_id, self.task.pk)
        self.assertEqual(event.task_name, OutboxTaskName.BUILD_RAG_INDEX)
        self.assertEqual(event.payload, {"index_id": str(index.pk)})
        publish.assert_called_once_with(event.pk)
