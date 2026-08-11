from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from apps.exports.models import ExportJob, ExportJobStatus, ExportKind
from apps.exports.services import RetryableExportError, create_export_job
from apps.exports.tasks import build_export_task
from apps.outbox.models import OutboxEvent, OutboxTaskName


class ExportWorkflowIntegrationTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(
            username="export-user",
            password="safe-test-password",
        )
        UserProfile.objects.create(
            user=self.user,
            full_name="Export User",
            organization="Test Lab",
            email="export-user@example.test",
            role=UserRole.JUNIOR,
            use_purpose="integration tests",
            application_reason="verify exports",
            status=ApplicationStatus.APPROVED,
        )
        self.corpus = Corpus.objects.create(
            name="Export Corpus",
            source_type=CorpusSourceType.USER,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            owner=self.user,
            access_level=CorpusAccessLevel.PRIVATE,
            status=CorpusStatus.READY,
        )

    def test_repeated_identical_request_returns_the_existing_active_job(self):
        parameters = {"q": "corpus", "language": "en"}
        first = create_export_job(
            user=self.user,
            corpus=self.corpus,
            kind=ExportKind.KWIC,
            parameters=parameters,
        )
        duplicate = create_export_job(
            user=self.user,
            corpus=self.corpus,
            kind=ExportKind.KWIC,
            parameters=parameters,
        )

        self.assertEqual(first.pk, duplicate.pk)
        self.assertEqual(ExportJob.objects.count(), 1)
        self.assertEqual(
            OutboxEvent.objects.filter(
                deduplication_key=f"export:{first.pk}",
                task_name=OutboxTaskName.BUILD_EXPORT,
            ).count(),
            1,
        )

        with self.assertRaises(ValidationError):
            create_export_job(
                user=self.user,
                corpus=self.corpus,
                kind=ExportKind.KWIC,
                parameters={"q": "different", "language": "en"},
            )

    def test_duplicate_task_delivery_is_a_noop(self):
        job = ExportJob.objects.create(
            requested_by=self.user,
            corpus=self.corpus,
            kind=ExportKind.KWIC,
            query={},
            status=ExportJobStatus.SUCCESS,
            expires_at="2099-01-01T00:00:00Z",
        )

        result = build_export_task.apply(args=[str(job.pk)])
        self.assertEqual(result.get(), {"job_id": str(job.pk), "status": "skipped"})

    def test_retryable_worker_error_is_retried_with_backoff(self):
        with patch(
            "apps.exports.tasks.process_export_job",
            side_effect=[
                RetryableExportError("temporary storage error"),
                {"job_id": "job", "status": "success"},
            ],
        ) as process:
            result = build_export_task.apply(args=["job"])

        self.assertEqual(result.get(), {"job_id": "job", "status": "success"})
        self.assertEqual(process.call_count, 2)

    def test_retry_budget_exhaustion_marks_the_export_job_failed(self):
        job = ExportJob.objects.create(
            requested_by=self.user,
            corpus=self.corpus,
            kind=ExportKind.KWIC,
            query={},
            expires_at="2099-01-01T00:00:00Z",
        )
        with patch(
            "apps.exports.tasks.process_export_job",
            side_effect=RetryableExportError("persistent storage error"),
        ) as process:
            result = build_export_task.apply(args=[str(job.pk)])

        self.assertTrue(result.failed())
        self.assertEqual(process.call_count, 4)
        job.refresh_from_db()
        self.assertEqual(job.status, ExportJobStatus.FAILED)
