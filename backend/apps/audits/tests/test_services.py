import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.audits.models import ParallelAuditStatus
from apps.audits.services import create_parallel_audit, run_parallel_audit
from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from apps.outbox.models import OutboxEvent, OutboxTaskName
from apps.processing.models import ProcessingTask, ProcessingTaskStatus


class ParallelAuditIntegrationTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.user = get_user_model().objects.create_user("audit-user")
        UserProfile.objects.create(
            user=self.user,
            full_name="Audit User",
            organization="Test Lab",
            email="audit-user@example.test",
            role=UserRole.ADVANCED,
            use_purpose="tests",
            application_reason="tests",
            status=ApplicationStatus.APPROVED,
        )
        self.corpus = Corpus.objects.create(
            name="Parallel audit corpus",
            source_type=CorpusSourceType.USER,
            corpus_type=CorpusType.ALIGNED_TSV,
            language=CorpusLanguage.ZH_EN,
            owner=self.user,
            access_level=CorpusAccessLevel.PRIVATE,
            status=CorpusStatus.READY,
        )
        self.processing_task = ProcessingTask.objects.create(
            corpus=self.corpus,
            requested_by=self.user,
            status=ProcessingTaskStatus.SUCCESS,
        )
        self.data_root = Path(self.temp_dir.name)
        output = self.data_root / "processed" / str(self.corpus.pk)
        output.mkdir(parents=True)
        (output / "parallel_pairs.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"id": "pair-1", "ordinal": 1, "zh_text": "这是中文句子", "en_text": "This is an English sentence.", "alignment_unit": "sentence", "method": "provided", "confidence": 0.95}),
                    json.dumps({"id": "pair-2", "ordinal": 2, "zh_text": "", "en_text": "Missing Chinese side.", "alignment_unit": "sentence", "method": "provided", "confidence": 0.40}),
                ]
            ) + "\n",
            encoding="utf-8",
        )

    @override_settings(CORPUS_AUDITOR_COMMAND="go -C ./go/corpus-auditor run ./cmd/corpus-auditor")
    def test_go_auditor_is_invoked_and_result_is_persisted(self) -> None:
        with override_settings(DATA_ROOT=self.data_root, PARALLEL_AUDIT_TIMEOUT_SECONDS=60):
            audit = create_parallel_audit(corpus=self.corpus, processing_task=self.processing_task)
            result = run_parallel_audit(str(audit.pk))

            audit.refresh_from_db()
            self.assertEqual(result["status"], "success")
            self.assertEqual(audit.status, ParallelAuditStatus.SUCCESS)
            self.assertEqual(audit.summary["total_pairs"], 2)
            self.assertEqual(audit.summary["empty_side_pairs"], 1)
            self.assertTrue(Path(audit.report_path).is_file())
            self.assertTrue(Path(audit.anomalies_path).is_file())
            event = OutboxEvent.objects.get(deduplication_key=f"parallel-audit:{audit.pk}")
            self.assertEqual(event.task_name, OutboxTaskName.AUDIT_PARALLEL_CORPUS)
            self.client.force_login(self.user)
            response = self.client.get(reverse("corpora:audit_anomalies", args=[self.corpus.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/x-ndjson; charset=utf-8")
            self.assertIn(b'"pair_id":"pair-2"', b"".join(response.streaming_content))

    def test_duplicate_delivery_is_a_noop(self) -> None:
        audit = create_parallel_audit(corpus=self.corpus, processing_task=self.processing_task)
        audit.status = ParallelAuditStatus.SUCCESS
        audit.save(update_fields=["status"])
        self.assertEqual(run_parallel_audit(str(audit.pk))["status"], "skipped")
