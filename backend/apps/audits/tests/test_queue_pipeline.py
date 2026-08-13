from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.audits.models import ParallelAudit, ParallelAuditExecutionMode, ParallelAuditStatus
from apps.audits.services import (
    consume_parallel_audit_results,
    create_parallel_audit,
    publish_parallel_audit_command,
)
from apps.audits.queue import ResultEntry
from apps.agent.models import AgentExternalWaitKind, AgentRunMode, AgentRunStatus, AgentStepStatus
from apps.agent.services import create_agent_run, execute_agent_run
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


class FakeAuditQueue:
    def __init__(self) -> None:
        self.commands: list[dict] = []
        self.results: list = []
        self.acknowledgements: list[str] = []

    def publish_command(self, payload: dict) -> str:
        self.commands.append(payload)
        return "1700000000000-0"

    def ensure_result_group(self) -> None:
        return None

    def read_results(self, *, limit: int):
        return self.results[:limit]

    def reclaim_results(self, *, limit: int):
        return []

    def ack_result(self, message_id: str) -> None:
        self.acknowledgements.append(message_id)


@override_settings(CORPUS_AUDITOR_QUEUE_ENABLED=True)
class QueueBackedAuditTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.data_root = Path(self.temp_dir.name)
        self.queue = FakeAuditQueue()
        self.user = get_user_model().objects.create_user("queue-audit-user")
        UserProfile.objects.create(
            user=self.user,
            full_name="Queue Audit User",
            organization="Test Lab",
            email="queue-audit@example.test",
            role=UserRole.ADVANCED,
            use_purpose="tests",
            application_reason="tests",
            status=ApplicationStatus.APPROVED,
        )
        self.corpus = Corpus.objects.create(
            name="Queue audit corpus",
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

    @patch("apps.audits.services.AuditQueue")
    def test_outbox_command_is_published_without_sync_go_call(self, audit_queue) -> None:
        audit_queue.return_value = self.queue
        audit = create_parallel_audit(corpus=self.corpus, processing_task=self.processing_task)

        self.assertEqual(audit.execution_mode, ParallelAuditExecutionMode.QUEUE)
        event = OutboxEvent.objects.get(deduplication_key=f"parallel-audit-command:{audit.pk}")
        self.assertEqual(event.task_name, OutboxTaskName.PUBLISH_AUDIT_COMMAND)

        outcome = publish_parallel_audit_command(str(audit.pk))

        self.assertEqual(outcome["status"], "published")
        self.assertEqual(self.queue.commands[0]["id"], str(audit.pk))
        self.assertNotIn(str(self.data_root), json.dumps(self.queue.commands[0]))
        self.assertNotIn("callback_path", self.queue.commands[0])
        audit.refresh_from_db()
        self.assertEqual(audit.status, ParallelAuditStatus.RUNNING)
        self.assertEqual(audit.worker_state, "queued")
        self.assertEqual(audit.command_message_id, "1700000000000-0")

    @patch("apps.audits.services.AuditQueue")
    def test_result_is_written_to_django_then_acknowledged_idempotently(self, audit_queue) -> None:
        audit_queue.return_value = self.queue
        audit = ParallelAudit.objects.create(
            corpus=self.corpus,
            processing_task=self.processing_task,
            execution_mode=ParallelAuditExecutionMode.QUEUE,
            status=ParallelAuditStatus.RUNNING,
        )
        output = self.data_root / "processed" / str(self.corpus.pk) / "audits" / str(audit.pk)
        output.mkdir(parents=True)
        (output / "quality_report.json").write_text(
            json.dumps({"summary": {"total_pairs": 2, "flagged_pairs": 1}}), encoding="utf-8"
        )
        (output / "anomalies.jsonl").write_text("{}\n", encoding="utf-8")
        payload = {
            "id": str(audit.pk), "schema_version": 1, "state": "succeeded", "attempt": 1,
            "report_ref": f"processed/{self.corpus.pk}/audits/{audit.pk}/quality_report.json",
            "anomalies_ref": f"processed/{self.corpus.pk}/audits/{audit.pk}/anomalies.jsonl",
            "error_code": "", "error_message": "",
        }
        from apps.audits.queue import ResultEntry
        from apps.audits.services import result_payload_hash

        entry = ResultEntry("1700000000001-0", payload, result_payload_hash(payload))
        self.queue.results = [entry]

        with override_settings(DATA_ROOT=self.data_root):
            self.assertEqual(consume_parallel_audit_results(), 1)
            self.assertEqual(consume_parallel_audit_results(), 0)

        audit.refresh_from_db()
        self.assertEqual(audit.status, ParallelAuditStatus.SUCCESS)
        self.assertEqual(audit.summary["total_pairs"], 2)
        self.assertEqual(audit.result_message_id, entry.message_id)
        self.assertEqual(self.queue.acknowledgements, [entry.message_id, entry.message_id])

    @patch("apps.audits.services.AuditQueue")
    def test_result_cannot_write_into_another_audit_directory(self, audit_queue) -> None:
        audit_queue.return_value = self.queue
        audit = ParallelAudit.objects.create(
            corpus=self.corpus,
            processing_task=self.processing_task,
            execution_mode=ParallelAuditExecutionMode.QUEUE,
            status=ParallelAuditStatus.RUNNING,
        )
        payload = {
            "id": str(audit.pk), "schema_version": 1, "state": "succeeded", "attempt": 1,
            "report_ref": f"processed/{self.corpus.pk}/audits/another-job/quality_report.json",
            "anomalies_ref": f"processed/{self.corpus.pk}/audits/another-job/anomalies.jsonl",
        }
        from apps.audits.queue import ResultEntry
        from apps.audits.services import result_payload_hash

        self.queue.results = [ResultEntry("1700000000002-0", payload, result_payload_hash(payload))]
        with override_settings(DATA_ROOT=self.data_root):
            self.assertEqual(consume_parallel_audit_results(), 0)
        audit.refresh_from_db()
        self.assertEqual(audit.status, ParallelAuditStatus.RUNNING)
        self.assertEqual(self.queue.acknowledgements, [])

    @patch("apps.audits.services.AuditQueue")
    def test_projected_result_resumes_waiting_agent_once(self, audit_queue) -> None:
        audit_queue.return_value = self.queue
        run, _ = create_agent_run(
            user=self.user,
            corpus=self.corpus,
            mode=AgentRunMode.QUALITY_REVIEW,
            query="",
            language=None,
            max_results=3,
            idempotency_key="queue-result-resumes-agent",
        )
        with patch("apps.agent.tools.dispatch_parallel_audit"):
            self.assertEqual(execute_agent_run(str(run.pk))["status"], AgentRunStatus.WAITING_EXTERNAL)
        audit = ParallelAudit.objects.get(processing_task=self.processing_task)
        run.refresh_from_db()
        self.assertEqual(run.external_wait_kind, AgentExternalWaitKind.PARALLEL_AUDIT)
        output = self.data_root / "processed" / str(self.corpus.pk) / "audits" / str(audit.pk)
        output.mkdir(parents=True)
        (output / "quality_report.json").write_text(
            json.dumps({"summary": {"total_pairs": 2}}), encoding="utf-8"
        )
        (output / "anomalies.jsonl").write_text("{}\n", encoding="utf-8")
        payload = {
            "id": str(audit.pk), "schema_version": 1, "state": "succeeded", "attempt": 1,
            "report_ref": f"processed/{self.corpus.pk}/audits/{audit.pk}/quality_report.json",
            "anomalies_ref": f"processed/{self.corpus.pk}/audits/{audit.pk}/anomalies.jsonl",
        }
        from apps.audits.services import result_payload_hash

        entry = ResultEntry("1700000000003-0", payload, result_payload_hash(payload))
        self.queue.results = [entry]
        with override_settings(DATA_ROOT=self.data_root):
            self.assertEqual(consume_parallel_audit_results(), 1)
            self.assertEqual(consume_parallel_audit_results(), 0)

        run.refresh_from_db()
        audit.refresh_from_db()
        self.assertEqual(audit.status, ParallelAuditStatus.SUCCESS)
        self.assertEqual(run.status, AgentRunStatus.PENDING)
        self.assertIsNone(run.external_wait_id)
        self.assertEqual(run.steps.first().status, AgentStepStatus.SUCCEEDED)
        self.assertTrue(
            OutboxEvent.objects.filter(
                task_name=OutboxTaskName.RESUME_CORPUS_AGENT,
                deduplication_key=f"agent-resume:{run.pk}:parallel-audit:{audit.pk}",
            ).exists()
        )

    @patch("apps.audits.services.AuditQueue")
    def test_failed_result_fails_waiting_agent_without_resume_command(self, audit_queue) -> None:
        audit_queue.return_value = self.queue
        run, _ = create_agent_run(
            user=self.user,
            corpus=self.corpus,
            mode=AgentRunMode.QUALITY_REVIEW,
            query="",
            language=None,
            max_results=3,
            idempotency_key="queue-result-fails-agent",
        )
        with patch("apps.agent.tools.dispatch_parallel_audit"):
            self.assertEqual(execute_agent_run(str(run.pk))["status"], AgentRunStatus.WAITING_EXTERNAL)
        audit = ParallelAudit.objects.get(processing_task=self.processing_task)
        from apps.audits.services import result_payload_hash

        payload = {
            "id": str(audit.pk),
            "schema_version": 1,
            "state": "failed",
            "attempt": 2,
            "error_message": "worker input was malformed",
        }
        self.queue.results = [ResultEntry("1700000000004-0", payload, result_payload_hash(payload))]

        self.assertEqual(consume_parallel_audit_results(), 1)

        run.refresh_from_db()
        audit.refresh_from_db()
        self.assertEqual(audit.status, ParallelAuditStatus.FAILED)
        self.assertEqual(run.status, AgentRunStatus.FAILED)
        self.assertEqual(run.error_code, "EXTERNAL_AUDIT_FAILED")
        self.assertEqual(run.steps.first().status, AgentStepStatus.FAILED)
        self.assertFalse(
            OutboxEvent.objects.filter(
                task_name=OutboxTaskName.RESUME_CORPUS_AGENT,
                deduplication_key=f"agent-resume:{run.pk}:parallel-audit:{audit.pk}",
            ).exists()
        )
