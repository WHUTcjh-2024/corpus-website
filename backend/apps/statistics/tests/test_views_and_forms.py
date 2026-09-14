from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.http import HttpResponse, QueryDict
from django.test import RequestFactory, SimpleTestCase

from apps.corpora.models import CorpusLanguage, CorpusStatus
from apps.processing.index_health import IndexRepairNotice
from apps.statistics import views
from apps.statistics.forms import (
    ClusterForm,
    CollocateForm,
    ConcordancePlotForm,
    KeywordForm,
    LanguageForm,
    NgramForm,
    WordListForm,
    WordcloudForm,
)


class StatisticsFormTests(SimpleTestCase):
    def test_language_form_rejects_empty_or_unknown_languages(self) -> None:
        with self.assertRaisesMessage(ValueError, "available_languages"):
            LanguageForm(available_languages=())
        with self.assertRaisesMessage(ValueError, "available_languages"):
            LanguageForm(available_languages=("fr",))

    def test_word_list_normalizes_defaults_and_lists(self) -> None:
        form = WordListForm(
            {
                "language": "en",
                "filter": "  farm   worker ",
                "pos": " NN1 ",
                "list_mode": "allow",
                "list_terms": "farmer, worker farmer",
            },
            available_languages=("en",),
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["filter"], "farm worker")
        self.assertEqual(form.cleaned_data["pos"], "NN1")
        self.assertEqual(form.cleaned_data["list_terms"], ("farmer", "worker"))
        self.assertEqual(form.cleaned_data["min_frequency"], 1)
        self.assertEqual(form.cleaned_data["page_size"], 50)

    def test_word_list_requires_terms_for_enabled_list(self) -> None:
        form = WordListForm(
            {"language": "zh", "list_mode": "stop"},
            available_languages=("zh",),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("list_terms", form.errors)

    def test_word_list_limits_term_count(self) -> None:
        terms = " ".join(f"term-{index}" for index in range(501))
        form = WordListForm(
            {"language": "en", "list_mode": "allow", "list_terms": terms},
            available_languages=("en",),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("list_terms", form.errors)

    def test_ngram_rejects_open_slot_beyond_length(self) -> None:
        form = NgramForm(
            {"language": "en", "n": "2", "open_slot": "3"},
            available_languages=("en",),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("open_slot", form.errors)

    def test_ngram_and_cluster_defaults_are_typed(self) -> None:
        ngram = NgramForm({"language": "zh"}, available_languages=("zh",))
        cluster = ClusterForm(
            {"language": "en", "q": "farm"},
            available_languages=("en",),
        )

        self.assertTrue(ngram.is_valid(), ngram.errors)
        self.assertTrue(cluster.is_valid(), cluster.errors)
        self.assertEqual(ngram.cleaned_data["n"], 2)
        self.assertEqual(ngram.cleaned_data["open_slot"], 0)
        self.assertEqual(cluster.cleaned_data["cluster_size"], 3)
        self.assertEqual(cluster.cleaned_data["query_position"], "left")

    def test_keyword_rejects_language_missing_from_reference(self) -> None:
        form = KeywordForm(
            {"language": "en", "reference_corpus": "reference"},
            available_languages=("zh", "en"),
            reference_corpora=(("reference", "中文参照", ("zh",)),),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("参照语料不包含所选语言", form.non_field_errors()[0])

    def test_wordcloud_normalizes_and_limits_stopwords(self) -> None:
        form = WordcloudForm(
            {"language": "zh", "stopwords": "的，是 的"},
            available_languages=("zh",),
        )
        too_many = WordcloudForm(
            {
                "language": "en",
                "stopwords": " ".join(f"term-{index}" for index in range(201)),
            },
            available_languages=("en",),
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["stopwords"], ("的", "是"))
        self.assertEqual(form.cleaned_data["max_words"], 50)
        self.assertFalse(too_many.is_valid())

    def test_collocate_validates_language_query_and_span(self) -> None:
        mismatch = CollocateForm(
            {"language": "zh", "q": "farmer"},
            available_languages=("zh",),
        )
        empty_span = CollocateForm(
            {"language": "zh", "q": "农民", "left_span": 0, "right_span": 0},
            available_languages=("zh",),
        )
        valid = CollocateForm(
            {"language": "zh", "q": "  农民  ", "left_span": 2},
            available_languages=("zh",),
        )

        self.assertFalse(mismatch.is_valid())
        self.assertFalse(empty_span.is_valid())
        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertEqual(valid.cleaned_data["q"], "农民")
        self.assertEqual(valid.cleaned_data["right_span"], 5)

    def test_concordance_plot_validates_both_query_languages(self) -> None:
        main_mismatch = ConcordancePlotForm(
            {"language": "en", "q": "农民"},
            available_languages=("en",),
        )
        overlay_mismatch = ConcordancePlotForm(
            {"language": "en", "q": "farmer", "overlay_q": "农民"},
            available_languages=("en",),
        )
        valid = ConcordancePlotForm(
            {"language": "en", "q": " farmer ", "overlay_q": " worker "},
            available_languages=("en",),
        )

        self.assertFalse(main_mismatch.is_valid())
        self.assertFalse(overlay_mismatch.is_valid())
        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertEqual(valid.cleaned_data["q"], "farmer")
        self.assertEqual(valid.cleaned_data["overlay_q"], "worker")
        self.assertEqual(valid.cleaned_data["bin_count"], 100)


class StatisticsViewTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.corpus = SimpleNamespace(
            pk="00000000-0000-0000-0000-000000000001",
            name="测试语料",
            language=CorpusLanguage.ZH_EN,
            status=CorpusStatus.READY,
            documentation=SimpleNamespace(segmentation_tool="zh:jieba;en:nltk"),
        )

    def _request(self, query: str = ""):
        request = self.factory.get(f"/statistics/?{query}")
        request.user = SimpleNamespace(is_authenticated=True)
        return request

    def _exercise_view(self, view, method_name: str, query: str) -> Mock:
        engine = Mock()
        getattr(engine, method_name).return_value = SimpleNamespace(total=2)
        with (
            patch.object(views, "_authorized_corpus", return_value=(self.corpus, None)),
            patch.object(views, "_index_availability", return_value=("", None)),
            patch.object(views, "_engine", return_value=engine),
            patch.object(views, "_render", return_value=HttpResponse("ok")) as render_mock,
        ):
            response = view.__wrapped__(self._request(query), self.corpus.pk)

        self.assertEqual(response.status_code, 200)
        getattr(engine, method_name).assert_called_once()
        self.assertIs(render_mock.call_args.args[4], getattr(engine, method_name).return_value)
        return engine

    def test_statistical_views_dispatch_valid_forms(self) -> None:
        cases = (
            (views.word_list, "word_list", "language=zh"),
            (views.clusters, "clusters", "language=zh&q=农民"),
            (views.ngrams, "ngrams", "language=en&n=3&open_slot=2"),
            (views.wordcloud, "wordcloud", "language=zh&max_words=25"),
            (
                views.collocates,
                "collocates",
                "language=zh&q=农民&left_span=2&right_span=2",
            ),
            (
                views.concordance_plot,
                "concordance_plot",
                "language=en&q=farmer&overlay_q=worker",
            ),
        )

        for view, method_name, query in cases:
            with self.subTest(view=view.__name__):
                self._exercise_view(view, method_name, query)

    def test_view_stops_after_authorization_failure(self) -> None:
        denied = HttpResponse("denied", status=403)
        with patch.object(
            views,
            "_authorized_corpus",
            return_value=(self.corpus, denied),
        ):
            response = views.word_list.__wrapped__(self._request(), self.corpus.pk)

        self.assertIs(response, denied)

    def test_word_list_reports_value_and_runtime_index_errors(self) -> None:
        engine = Mock()
        engine.word_list.side_effect = ValueError("invalid statistics options")
        with (
            patch.object(views, "_authorized_corpus", return_value=(self.corpus, None)),
            patch.object(views, "_index_availability", return_value=("", None)),
            patch.object(views, "_engine", return_value=engine),
            patch.object(views, "_render", return_value=HttpResponse("ok")) as render_mock,
        ):
            views.word_list.__wrapped__(self._request("language=zh"), self.corpus.pk)

        form = render_mock.call_args.args[3]
        self.assertIn("invalid statistics options", form.non_field_errors())

        engine.word_list.side_effect = views.StatisticsIndexUnavailable("missing")
        with (
            patch.object(views, "_authorized_corpus", return_value=(self.corpus, None)),
            patch.object(views, "_index_availability", return_value=("", None)),
            patch.object(views, "_engine", return_value=engine),
            patch.object(
                views,
                "_runtime_index_failure",
                return_value=("repairing", SimpleNamespace()),
            ) as failure_mock,
            patch.object(views, "_render", return_value=HttpResponse("ok")),
        ):
            views.word_list.__wrapped__(self._request("language=zh"), self.corpus.pk)

        failure_mock.assert_called_once_with(self.corpus)

    def test_keywords_handles_empty_references_and_reference_repair(self) -> None:
        class FakeQuerySet:
            def __init__(self, values):
                self.values = values

            def filter(self, **_kwargs):
                return self

            def exclude(self, **_kwargs):
                return self

            def select_related(self, *_args):
                return self

            def order_by(self, *_args):
                return self

            def __iter__(self):
                return iter(self.values)

        with (
            patch.object(views, "_authorized_corpus", return_value=(self.corpus, None)),
            patch.object(views, "_index_availability", return_value=("", None)),
            patch.object(views, "_render", return_value=HttpResponse("ok")),
            patch.object(views, "visible_corpora_for", return_value=FakeQuerySet([])),
        ):
            views.keywords.__wrapped__(self._request(), self.corpus.pk)

        reference = SimpleNamespace(
            pk="00000000-0000-0000-0000-000000000002",
            name="参照语料",
            language=CorpusLanguage.ZH_EN,
            documentation=SimpleNamespace(segmentation_tool="zh:jieba;en:nltk"),
        )
        repair = IndexRepairNotice(state="running", message="repairing")
        query = f"language=zh&reference_corpus={reference.pk}"
        with (
            patch.object(views, "_authorized_corpus", return_value=(self.corpus, None)),
            patch.object(views, "_index_availability", return_value=("", None)),
            patch.object(views, "_render", return_value=HttpResponse("ok")),
            patch.object(
                views,
                "visible_corpora_for",
                return_value=FakeQuerySet([reference]),
            ),
            patch.object(views, "ensure_corpus_index_ready", return_value=repair),
        ):
            views.keywords.__wrapped__(self._request(query), self.corpus.pk)

    def test_helpers_cover_language_index_and_query_state(self) -> None:
        zh = SimpleNamespace(language=CorpusLanguage.ZH)
        en = SimpleNamespace(language=CorpusLanguage.EN)
        self.assertEqual(views._available_languages(zh), ("zh",))
        self.assertEqual(views._available_languages(en), ("en",))
        self.assertEqual(views._available_languages(self.corpus), ("zh", "en"))
        self.assertEqual(views._segmentation_for_language(self.corpus, "en"), "nltk")

        request = self._request()
        self.assertIsNone(views._form_data(request, "zh", bind_empty=False))
        self.assertEqual(views._form_data(request, "zh")["language"], "zh")
        self.assertEqual(views._availability_error(self.corpus), "")
        self.corpus.status = CorpusStatus.PROCESSING
        self.assertTrue(views._availability_error(self.corpus))

        repair = IndexRepairNotice(state="pending", message="repairing")
        with patch.object(views, "ensure_corpus_index_ready", return_value=repair):
            self.assertEqual(views._index_availability(self.corpus), ("repairing", repair))
            self.assertEqual(
                views._runtime_index_failure(self.corpus),
                ("repairing", repair),
            )
        with patch.object(views, "ensure_corpus_index_ready", return_value=None):
            self.assertIn("暂时不可用", views._runtime_index_failure(self.corpus)[0])

    def test_render_records_results_and_uses_status_codes(self) -> None:
        request = self._request("page=2&language=zh")
        request.resolver_match = SimpleNamespace(url_name="word_list")
        form = SimpleNamespace(cleaned_data={"language": "zh"})
        result = SimpleNamespace(total_types=4)
        with (
            patch.object(views, "record_audit_event") as audit_mock,
            patch.object(views, "serializable_form_data", return_value={"language": "zh"}),
            patch.object(views, "render", return_value=HttpResponse("ok")) as render_mock,
        ):
            views._render(
                request,
                "statistics/word_list.html",
                self.corpus,
                form,
                result,
                "",
                QueryDict("page=2&language=zh"),
            )

        audit_mock.assert_called_once()
        context = render_mock.call_args.args[2]
        self.assertEqual(context["query_string"], "language=zh")
        self.assertEqual(audit_mock.call_args.kwargs["metadata"]["result_count"], 4)

        active = IndexRepairNotice(state="running", message="repairing")
        with patch.object(views, "render", return_value=HttpResponse("accepted")) as render_mock:
            views._render(
                request,
                "statistics/word_list.html",
                self.corpus,
                form,
                None,
                "repairing",
                QueryDict(),
                active,
            )
        self.assertEqual(render_mock.call_args.kwargs["status"], 202)

        with patch.object(views, "render", return_value=HttpResponse("conflict")) as render_mock:
            views._render(
                request,
                "statistics/word_list.html",
                self.corpus,
                form,
                None,
                "unavailable",
                QueryDict(),
            )
        self.assertEqual(render_mock.call_args.kwargs["status"], 409)
