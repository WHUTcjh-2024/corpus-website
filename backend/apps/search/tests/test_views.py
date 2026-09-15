from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from apps.corpora.models import CorpusLanguage
from apps.processing.index_health import IndexRepairNotice
from apps.search import views
from apps.search.forms import KwicSearchForm


class KwicSearchFormBranchTests(SimpleTestCase):
    def test_rejects_invalid_language_configuration(self) -> None:
        with self.assertRaisesMessage(ValueError, "available_languages"):
            KwicSearchForm(available_languages=())
        with self.assertRaisesMessage(ValueError, "available_languages"):
            KwicSearchForm(available_languages=("fr",))

    def test_detects_language_and_normalizes_defaults(self) -> None:
        form = KwicSearchForm(
            {"q": "  农民   协会 ", "query_mode": "simple"},
            available_languages=("zh", "en"),
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["language"], "zh")
        self.assertEqual(form.cleaned_data["q"], "农民 协会")
        self.assertTrue(form.cleaned_data["whole_words"])
        self.assertEqual(form.cleaned_data["context"], 5)

    def test_rejects_empty_and_incompatible_cqp_options(self) -> None:
        empty = KwicSearchForm(
            {"query_mode": "simple", "language": "en"},
            available_languages=("en",),
        )
        incompatible = KwicSearchForm(
            {
                "q": '[word="the"]',
                "query_mode": "cqp",
                "language": "en",
                "regex": "on",
            },
            available_languages=("en",),
        )

        self.assertFalse(empty.is_valid())
        self.assertIn("q", empty.errors)
        self.assertFalse(incompatible.is_valid())
        self.assertIn("__all__", incompatible.errors)

    def test_full_regex_sets_matching_flags_and_rejects_pos(self) -> None:
        valid = KwicSearchForm(
            {"q": r"farmer\s+worker", "query_mode": "full_regex", "language": "en"},
            available_languages=("en",),
        )
        invalid = KwicSearchForm(
            {
                "q": r"farmer.*",
                "query_mode": "full_regex",
                "language": "en",
                "pos": "NN1",
            },
            available_languages=("en",),
        )

        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertFalse(valid.cleaned_data["whole_words"])
        self.assertTrue(valid.cleaned_data["regex"])
        self.assertFalse(invalid.is_valid())

    def test_query_lists_are_deduplicated_and_bounded(self) -> None:
        form = KwicSearchForm(
            {
                "query_mode": "simple",
                "language": "en",
                "query_list": "farmer\n worker \nfarmer",
            },
            available_languages=("en",),
        )
        too_many = KwicSearchForm(
            {
                "query_mode": "simple",
                "language": "en",
                "query_list": "\n".join(f"term-{index}" for index in range(101)),
            },
            available_languages=("en",),
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["query_list"], ("farmer", "worker"))
        self.assertFalse(too_many.is_valid())


class SearchViewTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = SimpleNamespace(pk=1, is_authenticated=True)
        self.corpus = SimpleNamespace(
            pk="00000000-0000-0000-0000-000000000001",
            language=CorpusLanguage.ZH_EN,
        )

    def request(self, query: str = ""):
        request = self.factory.get(f"/search/?{query}")
        request.user = self.user
        return request

    @staticmethod
    def visible(allowed: bool = True):
        queryset = Mock()
        queryset.filter.return_value = queryset
        queryset.exists.return_value = allowed
        return queryset

    def test_kwic_dispatches_simple_advanced_and_cqp_queries(self) -> None:
        for query, engine_name, method_name in (
            ("language=en&q=farmer&query_mode=simple", "KwicSearchEngine", "search"),
            (
                "language=en&query_list=farmer%0Aworker&query_mode=simple",
                "KwicSearchEngine",
                "search_advanced",
            ),
            (
                "language=en&q=%5Bword%3D%22the%22%5D&query_mode=cqp",
                "ComplexQueryEngine",
                "search",
            ),
        ):
            with self.subTest(query=query):
                engine = Mock()
                getattr(engine, method_name).return_value = SimpleNamespace(total=3)
                with (
                    patch.object(views, "get_object_or_404", return_value=self.corpus),
                    patch.object(
                        views, "visible_corpora_for", return_value=self.visible()
                    ),
                    patch.object(views, "ensure_corpus_index_ready", return_value=None),
                    patch.object(views, engine_name, return_value=engine),
                    patch.object(views, "record_audit_event") as audit,
                    patch.object(
                        views, "render", return_value=HttpResponse("ok")
                    ) as render_mock,
                ):
                    response = views.kwic_search.__wrapped__(
                        self.request(query), self.corpus.pk
                    )

                self.assertEqual(response.content, b"ok")
                getattr(engine, method_name).assert_called_once()
                audit.assert_called_once()
                self.assertIsNotNone(render_mock.call_args.args[2]["result"])

    def test_kwic_handles_denial_query_error_and_index_repair(self) -> None:
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(
                views, "visible_corpora_for", return_value=self.visible(False)
            ),
        ):
            response = views.kwic_search.__wrapped__(self.request(), self.corpus.pk)
        self.assertEqual(response.status_code, 403)

        engine = Mock()
        engine.search.side_effect = views.KwicQueryError("bad query")
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "visible_corpora_for", return_value=self.visible()),
            patch.object(views, "ensure_corpus_index_ready", return_value=None),
            patch.object(views, "KwicSearchEngine", return_value=engine),
            patch.object(
                views, "render", return_value=HttpResponse("bad")
            ) as render_mock,
        ):
            views.kwic_search.__wrapped__(
                self.request("language=en&q=farmer&query_mode=simple"),
                self.corpus.pk,
            )
        self.assertEqual(render_mock.call_args.args[2]["search_error"], "bad query")

        notice = IndexRepairNotice(state="pending", message="repairing")
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "visible_corpora_for", return_value=self.visible()),
            patch.object(views, "ensure_corpus_index_ready", return_value=notice),
            patch.object(
                views, "render", return_value=HttpResponse("repair")
            ) as render_mock,
        ):
            views.kwic_search.__wrapped__(self.request(), self.corpus.pk)
        self.assertEqual(render_mock.call_args.kwargs["status"], 202)

    def test_file_view_validates_rows_and_handles_engine_results(self) -> None:
        common = {
            "document_id": "doc-1",
            "language": "en",
            "q": "farmer",
        }
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "visible_corpora_for", return_value=self.visible()),
        ):
            response = views.file_view.__wrapped__(
                self.request("language=en&row=0"), self.corpus.pk, "doc-1"
            )
        self.assertEqual(response.status_code, 400)

        engine = Mock()
        engine.file_view.return_value = SimpleNamespace(**common)
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "visible_corpora_for", return_value=self.visible()),
            patch.object(views, "KwicSearchEngine", return_value=engine),
            patch.object(views, "render", return_value=HttpResponse("file")),
        ):
            response = views.file_view.__wrapped__(
                self.request(
                    "language=en&q=farmer&row=2&whole_words=0&case_sensitive=1"
                ),
                self.corpus.pk,
                "doc-1",
            )
        self.assertEqual(response.content, b"file")
        self.assertEqual(engine.file_view.call_args.kwargs["row_id"], 2)
        self.assertFalse(engine.file_view.call_args.kwargs["whole_words"])

        engine.file_view.side_effect = views.KwicQueryError("not found")
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "visible_corpora_for", return_value=self.visible()),
            patch.object(views, "KwicSearchEngine", return_value=engine),
        ):
            response = views.file_view.__wrapped__(
                self.request(), self.corpus.pk, "doc-1"
            )
        self.assertEqual(response.status_code, 404)

    def test_file_view_repairs_unavailable_index(self) -> None:
        engine = Mock()
        engine.file_view.side_effect = views.KwicIndexUnavailable("missing")
        notice = IndexRepairNotice(state="running", message="repairing")
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "visible_corpora_for", return_value=self.visible()),
            patch.object(views, "KwicSearchEngine", return_value=engine),
            patch.object(views, "ensure_corpus_index_ready", return_value=notice),
        ):
            response = views.file_view.__wrapped__(
                self.request(), self.corpus.pk, "doc-1"
            )
        self.assertEqual(response.status_code, 202)

    def test_available_languages_handles_mono_unknown_and_bilingual(self) -> None:
        self.assertEqual(
            views._available_languages(SimpleNamespace(language=CorpusLanguage.ZH)),
            (CorpusLanguage.ZH,),
        )
        self.assertEqual(
            views._available_languages(SimpleNamespace(language="unknown")),
            (CorpusLanguage.ZH, CorpusLanguage.EN),
        )
        self.assertEqual(
            views._available_languages(self.corpus),
            (CorpusLanguage.ZH, CorpusLanguage.EN),
        )
