from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.agent.models import AgentRun, AgentRunMode
from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)


@override_settings(CELERY_TASK_ALWAYS_EAGER=False)
class AgentRunApiTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user("agent-api", password="safe-password")
        UserProfile.objects.create(
            user=self.user,
            full_name="Agent API",
            organization="Test Lab",
            email="agent-api@example.test",
            role=UserRole.JUNIOR,
            use_purpose="api tests",
            application_reason="validate Agent API",
            status=ApplicationStatus.APPROVED,
        )
        self.corpus = Corpus.objects.create(
            name="API corpus",
            source_type=CorpusSourceType.USER,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            owner=self.user,
            access_level=CorpusAccessLevel.PRIVATE,
            status=CorpusStatus.READY,
        )
        self.client.force_login(self.user)

    def test_create_is_idempotent_and_returns_request_trace_id(self):
        payload = {
            "corpus_id": str(self.corpus.pk),
            "mode": AgentRunMode.RETRIEVE,
            "query": "policy",
            "language": "en",
            "max_results": 3,
        }
        headers = {"HTTP_IDEMPOTENCY_KEY": "api-idempotency-key", "HTTP_X_REQUEST_ID": "client-trace-1"}
        first = self.client.post(reverse("api:agent:run-list"), payload, content_type="application/json", **headers)
        second = self.client.post(reverse("api:agent:run-list"), payload, content_type="application/json", **headers)

        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(first["X-Request-Id"], "client-trace-1")
        self.assertEqual(AgentRun.objects.count(), 1)

    def test_create_requires_idempotency_key(self):
        response = self.client.post(
            reverse("api:agent:run-list"),
            {"corpus_id": str(self.corpus.pk), "mode": "retrieve", "query": "policy"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_approve_without_an_approval_returns_a_conflict(self):
        run = AgentRun.objects.create(
            requested_by=self.user,
            corpus=self.corpus,
            mode=AgentRunMode.RETRIEVE,
            skill="corpus_retrieval@v1",
            idempotency_key="no-approval",
            request_id="no-approval",
            request_fingerprint="a" * 64,
            plan={"skill": "corpus_retrieval@v1"},
        )

        response = self.client.post(reverse("api:agent:run-approve", kwargs={"pk": run.pk}))

        self.assertEqual(response.status_code, 409)

    def test_revoked_corpus_hides_historical_agent_evidence(self):
        run = AgentRun.objects.create(
            requested_by=self.user,
            corpus=self.corpus,
            mode=AgentRunMode.RETRIEVE,
            skill="corpus_retrieval@v1",
            idempotency_key="hidden-after-revocation",
            request_id="hidden-after-revocation",
            request_fingerprint="b" * 64,
            plan={"skill": "corpus_retrieval@v1"},
            evidence=[{"citation_id": "kwic:1", "keyword": "policy"}],
        )
        self.corpus.status = CorpusStatus.DISABLED
        self.corpus.save(update_fields=["status"])

        listing = self.client.get(reverse("api:agent:run-list"))
        detail = self.client.get(reverse("api:agent:run-detail", kwargs={"pk": run.pk}))

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["results"], [])
        self.assertEqual(detail.status_code, 404)

    def test_cancel_after_access_revocation_returns_forbidden_without_trace(self):
        run = AgentRun.objects.create(
            requested_by=self.user,
            corpus=self.corpus,
            mode=AgentRunMode.RETRIEVE,
            skill="corpus_retrieval@v1",
            idempotency_key="cancel-hidden-after-revocation",
            request_id="cancel-hidden-after-revocation",
            request_fingerprint="c" * 64,
            plan={"skill": "corpus_retrieval@v1"},
            evidence=[{"citation_id": "kwic:1", "keyword": "policy"}],
        )
        self.corpus.status = CorpusStatus.DISABLED
        self.corpus.save(update_fields=["status"])

        response = self.client.post(reverse("api:agent:run-cancel", kwargs={"pk": run.pk}))

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("evidence", response.json())
