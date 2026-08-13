from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.audits.models import (
    ParallelAudit,
    ParallelAuditExecutionMode,
    ParallelAuditStatus,
)
from apps.audits.services import (
    ParallelAuditError,
    apply_remote_audit_result,
    run_parallel_audit,
)
from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from apps.processing.models import ProcessingTask, ProcessingTaskStatus


@override_settings(
    CORPUS_AUDITOR_SERVICE_ENABLED=True,
    CORPUS_AUDITOR_SERVICE_BASE_URL="http://auditor.internal",
    CORPUS_AUDITOR_SERVICE_TOKEN="control-token",
    CORPUS_AUDITOR_CALLBACK_TOKEN="callback-token",
    CORPUS_AUDITOR_CALLBACK_MAX_SKEW_SECONDS=300,
)
class RemoteAuditorServiceTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.user = get_user_model().objects.create_user("remote-audit-user")
        UserProfile.objects.create(
            user=self.user,
            full_name="Remote Audit User",
            organization="Test Lab",
            email="remote-audit@example.test",
            role=UserRole.ADVANCED,
            use_purpose="tests",
            application_reason="tests",
            status=ApplicationStatus.APPROVED,
        )
        self.corpus = Corpus.objects.create(
            name="Remote audit corpus",
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
        self.audit = ParallelAudit.objects.create(
            corpus=self.corpus,
            processing_task=self.processing_task,
            execution_mode=ParallelAuditExecutionMode.REMOTE,
        )
        self.data_root = Path(self.temp_dir.name)

    def _terminal_payload(self, *, state: str = "succeeded") -> dict:
        root = self.data_root / "processed" / str(self.corpus.pk) / "audits" / str(self.audit.pk)
        root.mkdir(parents=True, exist_ok=True)
        report_ref = f"processed/{self.corpus.pk}/audits/{self.audit.pk}/quality_report.json"
        anomalies_ref = f"processed/{self.corpus.pk}/audits/{self.audit.pk}/anomalies.jsonl"
        (root / "quality_report.json").write_text(
            json.dumps({"summary": {"total_pairs": 2, "flagged_pairs": 1}}), encoding="utf-8"
        )
        (root / "anomalies.jsonl").write_text("{}\n", encoding="utf-8")
        return {
            "id": str(self.audit.pk),
            "state": state,
            "attempt": 1,
            "report_ref": report_ref,
            "anomalies_ref": anomalies_ref,
            "error_message": "failed remotely" if state != "succeeded" else "",
        }

    def test_submission_uses_only_relative_references_and_persists_remote_identity(self) -> None:
        response = {"id": str(self.audit.pk), "state": "queued", "attempt": 0}
        with override_settings(DATA_ROOT=self.data_root), patch(
            "apps.audits.services._remote_request", return_value=response
        ) as remote_request:
            result = run_parallel_audit(str(self.audit.pk))

        self.audit.refresh_from_db()
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(self.audit.status, ParallelAuditStatus.RUNNING)
        self.assertEqual(self.audit.remote_job_id, str(self.audit.pk))
        payload = remote_request.call_args.kwargs["payload"]
        self.assertEqual(payload["input_ref"], f"processed/{self.corpus.pk}/parallel_pairs.jsonl")
        self.assertNotIn(str(self.data_root), json.dumps(payload))

    def test_terminal_submission_response_converges_without_waiting_for_callback(self) -> None:
        response = self._terminal_payload()
        with override_settings(DATA_ROOT=self.data_root), patch(
            "apps.audits.services._remote_request", return_value=response
        ):
            result = run_parallel_audit(str(self.audit.pk))
        self.audit.refresh_from_db()
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.audit.status, ParallelAuditStatus.SUCCESS)

    def test_signed_callback_is_idempotent_and_persists_shared_volume_outputs(self) -> None:
        self.audit.status = ParallelAuditStatus.RUNNING
        self.audit.remote_job_id = str(self.audit.pk)
        self.audit.save(update_fields=["status", "remote_job_id"])
        payload = self._terminal_payload()
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        signature = _signature(timestamp, body)
        client = Client()
        url = reverse("api:auditor-callback", args=[self.audit.pk])
        with override_settings(DATA_ROOT=self.data_root):
            response = client.post(
                url,
                data=body,
                content_type="application/json",
                HTTP_X_CORPUS_AUDITOR_TIMESTAMP=timestamp,
                HTTP_X_CORPUS_AUDITOR_SIGNATURE=signature,
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["applied"])
        with override_settings(DATA_ROOT=self.data_root):
            duplicate = client.post(
                url,
                data=body,
                content_type="application/json",
                HTTP_X_CORPUS_AUDITOR_TIMESTAMP=timestamp,
                HTTP_X_CORPUS_AUDITOR_SIGNATURE=signature,
            )
        self.assertEqual(duplicate.status_code, 200)
        self.assertFalse(duplicate.json()["applied"])
        self.audit.refresh_from_db()
        self.assertEqual(self.audit.status, ParallelAuditStatus.SUCCESS)
        self.assertEqual(self.audit.summary["total_pairs"], 2)
        self.assertTrue(Path(self.audit.report_path).is_file())

    def test_invalid_callback_signature_does_not_disclose_or_mutate_audit(self) -> None:
        self.audit.status = ParallelAuditStatus.RUNNING
        self.audit.remote_job_id = str(self.audit.pk)
        self.audit.save(update_fields=["status", "remote_job_id"])
        body = json.dumps(self._terminal_payload(), separators=(",", ":")).encode("utf-8")
        response = Client().post(
            reverse("api:auditor-callback", args=[self.audit.pk]),
            data=body,
            content_type="application/json",
            HTTP_X_CORPUS_AUDITOR_TIMESTAMP=str(int(time.time())),
            HTTP_X_CORPUS_AUDITOR_SIGNATURE="sha256=bad",
        )
        self.assertEqual(response.status_code, 400)
        self.audit.refresh_from_db()
        self.assertEqual(self.audit.status, ParallelAuditStatus.RUNNING)

    def test_remote_report_cannot_escape_its_corpus_output_prefix(self) -> None:
        self.audit.status = ParallelAuditStatus.RUNNING
        self.audit.remote_job_id = str(self.audit.pk)
        self.audit.save(update_fields=["status", "remote_job_id"])
        payload = self._terminal_payload()
        payload["report_ref"] = "processed/other-corpus/audits/report.json"
        with override_settings(DATA_ROOT=self.data_root):
            with self.assertRaises(ParallelAuditError):
                apply_remote_audit_result(audit_id=self.audit.pk, payload=payload, payload_hash="test")

    def test_callback_converges_if_submission_response_was_lost(self) -> None:
        payload = self._terminal_payload()
        with override_settings(DATA_ROOT=self.data_root):
            applied = apply_remote_audit_result(
                audit_id=self.audit.pk,
                payload=payload,
                payload_hash="recovered-submission",
            )
        self.assertTrue(applied)
        self.audit.refresh_from_db()
        self.assertEqual(self.audit.status, ParallelAuditStatus.SUCCESS)


def _signature(timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(
        settings.CORPUS_AUDITOR_CALLBACK_TOKEN.encode("utf-8"),
        timestamp.encode("ascii") + b"\n" + body,
        hashlib.sha256,
    ).hexdigest()
