from django.contrib.auth import get_user_model
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.test.utils import override_settings
from unittest.mock import patch

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.agent.models import AgentApprovalStatus, AgentRunMode, AgentRunStatus, AgentStepStatus
from apps.agent.services import (
    AgentRunError,
    _pause_for_approval,
    approve_agent_action,
    create_agent_run,
    expire_pending_approvals,
    execute_agent_run,
)
from apps.agent.llm import SummaryResult
from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from apps.outbox.models import OutboxEvent, OutboxTaskName
from apps.exports.models import ExportJob
from django.utils import timezone


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=False)
class AgentRunServiceTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user("agent-user", password="safe-password")
        UserProfile.objects.create(
            user=self.user,
            full_name="Agent User",
            organization="Test Lab",
            email="agent@example.test",
            role=UserRole.JUNIOR,
            use_purpose="agent harness tests",
            application_reason="verify safe Agent execution",
            status=ApplicationStatus.APPROVED,
        )
        self.corpus = Corpus.objects.create(
            name="Personal corpus",
            source_type=CorpusSourceType.USER,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            owner=self.user,
            access_level=CorpusAccessLevel.PRIVATE,
            status=CorpusStatus.READY,
        )

    def test_identical_request_reuses_durable_run_and_outbox_command(self):
        first, created = create_agent_run(
            user=self.user,
            corpus=self.corpus,
            mode=AgentRunMode.RETRIEVE,
            query="policy",
            language="en",
            max_results=3,
            idempotency_key="retry-safe-request",
        )
        second, duplicate = create_agent_run(
            user=self.user,
            corpus=self.corpus,
            mode=AgentRunMode.RETRIEVE,
            query="policy",
            language="en",
            max_results=3,
            idempotency_key="retry-safe-request",
        )

        self.assertTrue(created)
        self.assertFalse(duplicate)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.steps.count(), 1)
        self.assertTrue(
            OutboxEvent.objects.filter(
                task_name=OutboxTaskName.RUN_CORPUS_AGENT,
                aggregate_id=first.pk,
                deduplication_key=f"agent-run:{first.pk}",
            ).exists()
        )

    def test_export_never_creates_an_export_before_human_approval(self):
        run, _ = create_agent_run(
            user=self.user,
            corpus=self.corpus,
            mode=AgentRunMode.EXPORT,
            query="policy",
            language="en",
            max_results=3,
            idempotency_key="export-requires-approval",
        )

        with self.assertRaises(AgentRunError):
            execute_agent_run(str(run.pk))

        run.refresh_from_db()
        self.assertEqual(run.status, AgentRunStatus.FAILED)
        self.assertFalse(hasattr(run, "approval"))

    def test_export_run_waits_for_approval_after_a_recovered_preview(self):
        run, _ = create_agent_run(
            user=self.user,
            corpus=self.corpus,
            mode=AgentRunMode.EXPORT,
            query="policy",
            language="en",
            max_results=3,
            idempotency_key="approval-single-use",
        )
        # Test the approval transition with a prepared, policy-validated step;
        # the concrete export implementation is covered by exports integration tests.
        first, step = run.steps.order_by("sequence")
        first.status = AgentStepStatus.SUCCEEDED
        first.output = {"hits": []}
        first.save(update_fields=["status", "output"])
        # The prepared handoff carries no direct write capability. The worker
        # must stop at a durable approval record before it can create an export.
        outcome = execute_agent_run(str(run.pk))
        run.refresh_from_db()
        self.assertEqual(run.status, AgentRunStatus.WAITING_APPROVAL)
        self.assertEqual(run.approval.status, AgentApprovalStatus.PENDING)
        self.assertEqual(outcome["status"], AgentRunStatus.WAITING_APPROVAL)

        # The write capability is intentionally unavailable to the execution
        # worker. It is exercised exactly once, by the requester, after the
        # durable approval record is created.
        with patch("apps.agent.services.dispatch_export_job") as dispatch:
            approval = approve_agent_action(run_id=run.pk, user=self.user)

        approval.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(approval.status, AgentApprovalStatus.APPROVED)
        self.assertEqual(run.status, AgentRunStatus.SUCCEEDED)
        self.assertEqual(ExportJob.objects.filter(requested_by=self.user, corpus=self.corpus).count(), 1)
        dispatch.assert_called_once()
        with self.assertRaises(ValidationError):
            approve_agent_action(run_id=run.pk, user=self.user)

    def test_model_outage_falls_back_without_failing_a_grounded_run(self):
        run, _ = create_agent_run(
            user=self.user,
            corpus=self.corpus,
            mode=AgentRunMode.RETRIEVE,
            query="policy",
            language="en",
            max_results=3,
            idempotency_key="model-fallback-is-safe",
        )
        step = run.steps.get()
        step.status = AgentStepStatus.SUCCEEDED
        step.output = {
            "hits": [{"citation_id": "kwic:1", "row_id": 1, "keyword": "policy"}]
        }
        step.save(update_fields=["status", "output"])

        with self.settings(
            AGENT_MODEL_ENABLED=True,
            AGENT_MODEL_BASE_URL="",
            AGENT_MODEL_API_KEY="",
            AGENT_MODEL_NAME="",
        ):
            outcome = execute_agent_run(str(run.pk))

        run.refresh_from_db()
        self.assertEqual(outcome["status"], AgentRunStatus.SUCCEEDED)
        self.assertEqual(run.status, AgentRunStatus.SUCCEEDED)
        self.assertTrue(run.model_usage["fallback"])
        self.assertIn("kwic:1", run.answer)

    def test_approval_expiry_is_persisted_and_never_creates_an_export(self):
        run = self._waiting_export_run("expiry-is-persisted")
        run.approval.expires_at = timezone.now() - timedelta(seconds=1)
        run.approval.save(update_fields=["expires_at"])

        self.assertEqual(expire_pending_approvals(), 1)

        run.refresh_from_db()
        run.approval.refresh_from_db()
        self.assertEqual(run.status, AgentRunStatus.CANCELLED)
        self.assertEqual(run.approval.status, AgentApprovalStatus.EXPIRED)
        self.assertFalse(ExportJob.objects.exists())
        with self.assertRaises(ValidationError):
            approve_agent_action(run_id=run.pk, user=self.user)

    def test_expired_approval_requested_by_user_is_persisted_before_conflict(self):
        run = self._waiting_export_run("expiry-on-approve-is-persisted")
        run.approval.expires_at = timezone.now() - timedelta(seconds=1)
        run.approval.save(update_fields=["expires_at"])

        with self.assertRaises(ValidationError):
            approve_agent_action(run_id=run.pk, user=self.user)

        run.refresh_from_db()
        run.approval.refresh_from_db()
        self.assertEqual(run.status, AgentRunStatus.CANCELLED)
        self.assertEqual(run.approval.status, AgentApprovalStatus.EXPIRED)
        self.assertFalse(ExportJob.objects.exists())

    def test_cancelled_run_cannot_create_an_approval_on_worker_recovery(self):
        run, _ = create_agent_run(
            user=self.user,
            corpus=self.corpus,
            mode=AgentRunMode.EXPORT,
            query="policy",
            language="en",
            max_results=3,
            idempotency_key="cancel-prevents-stale-approval",
        )
        from apps.agent.services import cancel_agent_run

        cancel_agent_run(run_id=run.pk, user=self.user)
        approval, created = _pause_for_approval(
            run_id=run.pk,
            payload={"kind": "kwic", "parameters": {"q": "policy"}},
        )

        self.assertIsNone(approval)
        self.assertFalse(created)
        self.assertFalse(ExportJob.objects.exists())

    def test_cancellation_wins_over_a_late_success_writeback(self):
        run, _ = create_agent_run(
            user=self.user,
            corpus=self.corpus,
            mode=AgentRunMode.RETRIEVE,
            query="policy",
            language="en",
            max_results=3,
            idempotency_key="cancellation-wins-over-success",
        )
        step = run.steps.get()
        step.status = AgentStepStatus.SUCCEEDED
        step.output = {"hits": [{"citation_id": "kwic:1"}]}
        step.save(update_fields=["status", "output"])

        def cancel_then_summarize(**_kwargs):
            from apps.agent.services import cancel_agent_run

            cancel_agent_run(run_id=run.pk, user=self.user)
            return SummaryResult(answer="late summary", usage={}, estimated_cost_usd=0.0)

        with patch("apps.agent.services.summarize_grounded_evidence", side_effect=cancel_then_summarize):
            outcome = execute_agent_run(str(run.pk))

        run.refresh_from_db()
        self.assertEqual(outcome["status"], AgentRunStatus.CANCELLED)
        self.assertEqual(run.status, AgentRunStatus.CANCELLED)

    def test_export_is_rejected_for_visible_non_personal_corpus(self):
        shared = Corpus.objects.create(
            name="Visible demo corpus",
            source_type=CorpusSourceType.DEMO,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            access_level=CorpusAccessLevel.DEMO,
            status=CorpusStatus.READY,
        )

        with self.assertRaises(PermissionDenied):
            create_agent_run(
                user=self.user,
                corpus=shared,
                mode=AgentRunMode.EXPORT,
                query="policy",
                language="en",
                max_results=3,
                idempotency_key="shared-export-rejected",
            )

    def _waiting_export_run(self, idempotency_key: str):
        run, _ = create_agent_run(
            user=self.user,
            corpus=self.corpus,
            mode=AgentRunMode.EXPORT,
            query="policy",
            language="en",
            max_results=3,
            idempotency_key=idempotency_key,
        )
        first, _ = run.steps.order_by("sequence")
        first.status = AgentStepStatus.SUCCEEDED
        first.output = {"hits": []}
        first.save(update_fields=["status", "output"])
        execute_agent_run(str(run.pk))
        run.refresh_from_db()
        return run
