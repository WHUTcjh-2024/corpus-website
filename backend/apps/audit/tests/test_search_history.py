from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)

from ..models import AuditEvent, AuditEventType, SavedSearch, SavedSearchKind
from ..services_saved_search import replay_url, save_search


class SearchHistoryTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(
            username="researcher",
            password="safe-test-password",
        )
        UserProfile.objects.create(
            user=self.user,
            full_name="Researcher",
            organization="Test Lab",
            email="researcher@example.test",
            role=UserRole.JUNIOR,
            use_purpose="contract tests",
            application_reason="verify search history",
            status=ApplicationStatus.APPROVED,
        )
        self.corpus = Corpus.objects.create(
            name="Contract Corpus",
            source_type=CorpusSourceType.TEACHER,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            access_level=CorpusAccessLevel.JUNIOR,
            status=CorpusStatus.READY,
        )
        self.client.force_login(self.user)

    def test_user_can_save_and_replay_an_authorized_query(self):
        saved = save_search(
            user=self.user,
            corpus_id=self.corpus.pk,
            kind=SavedSearchKind.KWIC,
            name="  Key   terms  ",
            query_string="q=corpus&page=4&page_size=50&language=en",
        )

        self.assertEqual(saved.name, "Key terms")
        self.assertNotIn("page=", saved.query_string)
        self.assertNotIn("page_size=", saved.query_string)
        self.assertEqual(
            replay_url(saved),
            f"{reverse('search:kwic', kwargs={'corpus_id': self.corpus.pk})}?q=corpus&language=en",
        )

    def test_duplicate_query_is_idempotent_and_renames_existing_record(self):
        first = save_search(
            user=self.user,
            corpus_id=self.corpus.pk,
            kind=SavedSearchKind.KWIC,
            name="First",
            query_string="q=corpus&language=en",
        )
        second = save_search(
            user=self.user,
            corpus_id=self.corpus.pk,
            kind=SavedSearchKind.KWIC,
            name="Updated",
            query_string="q=corpus&language=en",
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.name, "Updated")
        self.assertEqual(SavedSearch.objects.count(), 1)

    @override_settings(SAVED_SEARCH_MAX_ITEMS=1)
    def test_saved_search_count_is_bounded_per_account(self):
        save_search(
            user=self.user,
            corpus_id=self.corpus.pk,
            kind=SavedSearchKind.KWIC,
            name="One",
            query_string="q=one",
        )

        with self.assertRaisesMessage(ValidationError, "最多保存 1 条检索"):
            save_search(
                user=self.user,
                corpus_id=self.corpus.pk,
                kind=SavedSearchKind.KWIC,
                name="Two",
                query_string="q=two",
            )

    @override_settings(SAVED_SEARCH_MAX_QUERY_BYTES=5)
    def test_single_saved_query_payload_is_byte_bounded(self):
        with self.assertRaisesMessage(ValidationError, "单条检索条件超过保存上限"):
            save_search(
                user=self.user,
                corpus_id=self.corpus.pk,
                kind=SavedSearchKind.KWIC,
                name="Too large",
                query_string="q=abcdef",
            )

    def test_user_cannot_save_a_hidden_corpus_query(self):
        hidden = Corpus.objects.create(
            name="Advanced Corpus",
            source_type=CorpusSourceType.TEACHER,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            access_level=CorpusAccessLevel.ADVANCED,
            status=CorpusStatus.READY,
        )
        with self.assertRaises(PermissionDenied):
            save_search(
                user=self.user,
                corpus_id=hidden.pk,
                kind=SavedSearchKind.KWIC,
                name="Hidden",
                query_string="q=secret",
            )

    def test_history_page_lists_only_the_current_users_searches(self):
        AuditEvent.objects.create(
            actor=self.user,
            corpus=self.corpus,
            event_type=AuditEventType.KWIC_SEARCH,
            path=reverse("search:kwic", kwargs={"corpus_id": self.corpus.pk}),
            metadata={"parameters": {"q": "contract", "language": "en"}, "result_count": 12},
        )
        other = get_user_model().objects.create_user(username="other")
        AuditEvent.objects.create(
            actor=other,
            corpus=self.corpus,
            event_type=AuditEventType.KWIC_SEARCH,
            path="/search/hidden/",
            metadata={"parameters": {"q": "private"}, "result_count": 1},
        )

        response = self.client.get(reverse("research_history:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "contract")
        self.assertContains(response, "12")
        self.assertNotContains(response, "private")

    def test_save_and_delete_endpoints_are_scoped_to_the_current_user(self):
        response = self.client.post(
            reverse("research_history:save"),
            {
                "corpus_id": self.corpus.pk,
                "kind": SavedSearchKind.KWIC,
                "query_string": "q=contract&language=en",
            },
        )
        self.assertRedirects(response, reverse("research_history:list"))
        saved = SavedSearch.objects.get(user=self.user)

        delete = self.client.post(reverse("research_history:delete", args=[saved.pk]))
        self.assertRedirects(delete, reverse("research_history:list"))
        self.assertFalse(SavedSearch.objects.filter(pk=saved.pk).exists())
