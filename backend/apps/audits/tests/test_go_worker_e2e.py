from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from redis import Redis

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.agent.models import AgentRunMode, AgentRunStatus
from apps.agent.services import create_agent_run, execute_agent_run
from apps.audits.models import ParallelAudit, ParallelAuditStatus
from apps.audits.services import consume_parallel_audit_results, publish_parallel_audit_command
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


@skipUnless(
    os.getenv("REQUIRE_GO_AUDITOR_E2E") == "true",
    "Go auditor end-to-end checks run only where Redis and the worker binary are provisioned.",
)
class GoAuditorSagaE2ETests(TransactionTestCase):
    """Exercise the real Python control plane, Redis Streams, and Go data plane."""

    def setUp(self) -> None:
        super().setUp()
        binary = Path(os.environ["CORPUS_AUDITOR_E2E_BINARY"])
        if not binary.is_file():
            self.skipTest(f"Go auditor binary does not exist: {binary}")
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.data_root = Path(self.temporary_directory.name) / "data"
        self.state_directory = Path(self.temporary_directory.name) / "jobs"
        self.data_root.mkdir()
        suffix = uuid4().hex
        self.command_stream = f"e2e:{suffix}:commands"
        self.result_stream = f"e2e:{suffix}:results"
        self.command_group = f"e2e-go-worker-{suffix}"
        self.result_group = f"e2e-django-projector-{suffix}"
        self.redis_url = os.environ["REDIS_URL"]
        self.settings = override_settings(
            DATA_ROOT=self.data_root,
            CORPUS_AUDITOR_QUEUE_ENABLED=True,
            CORPUS_AUDITOR_QUEUE_URL=self.redis_url,
            CORPUS_AUDITOR_COMMAND_STREAM=self.command_stream,
            CORPUS_AUDITOR_COMMAND_GROUP=self.command_group,
            CORPUS_AUDITOR_RESULT_STREAM=self.result_stream,
            CORPUS_AUDITOR_RESULT_GROUP=self.result_group,
            CORPUS_AUDITOR_RESULT_BLOCK_MS=20,
        )
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.redis = Redis.from_url(self.redis_url, decode_responses=True)
        self.addCleanup(self._delete_streams)
        self.worker = subprocess.Popen(
            [
                str(binary),
                "--listen=127.0.0.1:0",
                "--grpc-listen=127.0.0.1:0",
                f"--data-root={self.data_root}",
                f"--state-dir={self.state_directory}",
                "--control-token=e2e-control-token",
                f"--redis-url={self.redis_url}",
                f"--command-stream={self.command_stream}",
                f"--command-group={self.command_group}",
                f"--command-consumer=e2e-worker-{suffix}",
                f"--result-stream={self.result_stream}",
                "--workers=1",
                "--queue-capacity=4",
                "--audit-timeout=10s",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._stop_worker)
        self._wait_for_worker_start()

    def test_quality_review_saga_crosses_redis_and_go_before_resuming(self) -> None:
        user = get_user_model().objects.create_user("go-e2e-user")
        UserProfile.objects.create(
            user=user,
            full_name="Go E2E User",
            organization="Test Lab",
            email="go-e2e@example.test",
            role=UserRole.ADVANCED,
            use_purpose="integration test",
            application_reason="exercise the Go audit worker",
            status=ApplicationStatus.APPROVED,
        )
        corpus = Corpus.objects.create(
            name="Go worker E2E corpus",
            source_type=CorpusSourceType.USER,
            corpus_type=CorpusType.ALIGNED_TSV,
            language=CorpusLanguage.ZH_EN,
            owner=user,
            access_level=CorpusAccessLevel.PRIVATE,
            status=CorpusStatus.READY,
        )
        processing_task = ProcessingTask.objects.create(
            corpus=corpus,
            requested_by=user,
            status=ProcessingTaskStatus.SUCCESS,
        )
        input_path = self.data_root / "processed" / str(corpus.pk) / "parallel_pairs.jsonl"
        input_path.parent.mkdir(parents=True)
        input_path.write_text(
            "\n".join(
                [
                    '{"id":"p-1","ordinal":1,"zh_text":"你好世界","en_text":"Hello world","confidence":0.95}',
                    '{"id":"p-2","ordinal":2,"zh_text":"","en_text":"Missing source","confidence":0.4}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        run, _ = create_agent_run(
            user=user,
            corpus=corpus,
            mode=AgentRunMode.QUALITY_REVIEW,
            query="",
            language=None,
            max_results=3,
            idempotency_key="go-worker-saga-e2e",
        )

        # The command is published through its ordinary Outbox-consumer entry
        # point; this avoids a separate Celery process yet exercises Redis.
        with patch("apps.agent.tools.dispatch_parallel_audit"):
            outcome = execute_agent_run(str(run.pk))
        self.assertEqual(outcome["status"], AgentRunStatus.WAITING_EXTERNAL)
        audit = ParallelAudit.objects.get(processing_task=processing_task)
        self.assertEqual(publish_parallel_audit_command(str(audit.pk))["status"], "published")

        self._wait_for_terminal_projection(audit)
        audit.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(audit.status, ParallelAuditStatus.SUCCESS)
        self.assertEqual(run.status, AgentRunStatus.PENDING)
        self.assertTrue(Path(audit.report_path).is_file())
        self.assertTrue(
            OutboxEvent.objects.filter(
                task_name=OutboxTaskName.RESUME_CORPUS_AGENT,
                deduplication_key=f"agent-resume:{run.pk}:parallel-audit:{audit.pk}",
            ).exists()
        )

        resumed = execute_agent_run(str(run.pk))
        run.refresh_from_db()
        self.assertEqual(resumed["status"], AgentRunStatus.SUCCEEDED)
        self.assertEqual(run.status, AgentRunStatus.SUCCEEDED)
        self.assertIn(f"audit:{audit.pk}", run.answer)

    def _wait_for_worker_start(self) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.worker.poll() is not None:
                self.fail(f"Go auditor exited during startup: {self._worker_error()}")
            try:
                self.redis.ping()
                groups = self.redis.xinfo_groups(self.command_stream)
                if any(group["name"] == self.command_group for group in groups):
                    return
            except Exception:
                pass
            time.sleep(0.05)
        self.fail("Redis was not reachable for the Go auditor E2E test.")

    def _wait_for_terminal_projection(self, audit: ParallelAudit) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.worker.poll() is not None:
                self.fail(f"Go auditor exited while processing work: {self._worker_error()}")
            consume_parallel_audit_results(limit=10)
            audit.refresh_from_db()
            if audit.status in {ParallelAuditStatus.SUCCESS, ParallelAuditStatus.FAILED}:
                return
            time.sleep(0.05)
        self.fail(f"Go audit was not projected before timeout (state={audit.status}).")

    def _stop_worker(self) -> None:
        if self.worker.poll() is not None:
            return
        self.worker.send_signal(signal.SIGTERM)
        try:
            self.worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.worker.kill()
            self.worker.wait(timeout=5)

    def _delete_streams(self) -> None:
        self.redis.delete(self.command_stream, self.result_stream)

    def _worker_error(self) -> str:
        if self.worker.stderr is None:
            return "no stderr captured"
        return self.worker.stderr.read()[-4000:]
