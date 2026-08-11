from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusDocumentation,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from apps.processing.models import ProcessingTask, ProcessingTaskStatus


class CorpusListIntegrationTests(TestCase):
    """Exercise permissions, ORM eager loading, and serializer output together."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(
            username="integration-user",
            password="safe-test-password",
        )
        UserProfile.objects.create(
            user=self.user,
            full_name="Integration User",
            organization="Test Lab",
            email="integration-user@example.test",
            role=UserRole.JUNIOR,
            use_purpose="integration tests",
            application_reason="verify corpus list behavior",
            status=ApplicationStatus.APPROVED,
        )
        self.client.force_login(self.user)

        for index in range(12):
            corpus = Corpus.objects.create(
                name=f"Corpus {index:02d}",
                source_type=CorpusSourceType.USER,
                corpus_type=CorpusType.RAW_EN,
                language=CorpusLanguage.EN,
                owner=self.user,
                access_level=CorpusAccessLevel.PRIVATE,
                status=CorpusStatus.READY,
            )
            CorpusDocumentation.objects.filter(corpus=corpus).update(token_count=index + 1)
            ProcessingTask.objects.create(
                corpus=corpus,
                requested_by=self.user,
                status=ProcessingTaskStatus.SUCCESS,
                progress=10,
            )
            ProcessingTask.objects.create(
                corpus=corpus,
                requested_by=self.user,
                status=ProcessingTaskStatus.SUCCESS,
                progress=100,
            )

    def test_list_endpoint_has_a_fixed_query_budget_and_only_prefetches_latest_task(self):
        # Session/auth, profile, corpus/documentation, and one sliced task
        # prefetch. The budget must not grow with the 12 corpora above.
        with self.assertNumQueries(5):
            response = self.client.get(reverse("api:corpora-list"))

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(len(results), 12)
        self.assertTrue(
            all(
                item["latest_task"] is not None and item["latest_task"]["progress"] == 100
                for item in results
            )
        )
