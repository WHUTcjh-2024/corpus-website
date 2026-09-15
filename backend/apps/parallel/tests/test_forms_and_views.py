from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from apps.corpora.models import CorpusStatus, CorpusType
from apps.parallel import views
from apps.parallel.forms import ParallelSearchForm
from apps.processing.index_health import IndexRepairNotice


class ParallelSearchFormTests(SimpleTestCase):
    def test_configuration_is_validated(self) -> None:
        with self.assertRaisesMessage(ValueError, "default_alignment_unit"):
            ParallelSearchForm(default_alignment_unit="document")
        with self.assertRaisesMessage(ValueError, "available_alignment_units"):
            ParallelSearchForm(available_alignment_units=())
        with self.assertRaisesMessage(ValueError, "must be available"):
            ParallelSearchForm(
                default_alignment_unit="paragraph",
                available_alignment_units=("sentence",),
            )

    def test_form_normalizes_conditions_and_builds_typed_query(self) -> None:
        form = ParallelSearchForm(
            {
                "q": "  farmer  ",
                "search_side": "en",
                "alignment_unit": "paragraph",
                "zh_contains": " 农民 ",
                "min_confidence": "0.75",
                "nth_entry": "2",
                "page_size": "20",
                "context_size": "10",
            },
            default_alignment_unit="paragraph",
            available_alignment_units=("paragraph",),
        )

        self.assertTrue(form.is_valid(), form.errors)
        query = form.to_query()
        self.assertEqual(query.q, "farmer")
        self.assertEqual(query.zh_contains, "农民")
        self.assertEqual(query.min_confidence, 0.75)
        self.assertEqual(query.nth_entry, 2)
        self.assertEqual(form.cleaned_data["page_size"], 20)

    def test_form_rejects_empty_query(self) -> None:
        form = ParallelSearchForm(
            {"search_side": "zh", "alignment_unit": "sentence"},
            default_alignment_unit="sentence",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)


class ParallelViewTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = SimpleNamespace(pk=1, is_authenticated=True)
        self.corpus = SimpleNamespace(
            pk="00000000-0000-0000-0000-000000000001",
            corpus_type=CorpusType.PAIRED_TAGGED_ZH_EN,
            status=CorpusStatus.READY,
        )

    def request(self, query: str = ""):
        request = self.factory.get(f"/parallel/?{query}")
        request.user = self.user
        return request

    @staticmethod
    def visible(allowed: bool = True):
        queryset = Mock()
        queryset.filter.return_value = queryset
        queryset.exists.return_value = allowed
        return queryset

    def test_search_dispatches_valid_query_and_records_audit(self) -> None:
        engine = Mock()
        engine.search.return_value = SimpleNamespace(total=2)
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "visible_corpora_for", return_value=self.visible()),
            patch.object(views, "ensure_corpus_index_ready", return_value=None),
            patch.object(views, "ParallelSearchEngine", return_value=engine),
            patch.object(views, "record_audit_event") as audit,
            patch.object(
                views, "render", return_value=HttpResponse("ok")
            ) as render_mock,
        ):
            response = views.parallel_search.__wrapped__(
                self.request("q=农民&search_side=zh&alignment_unit=sentence"),
                self.corpus.pk,
            )
        self.assertEqual(response.content, b"ok")
        engine.search.assert_called_once()
        audit.assert_called_once()
        self.assertIsNotNone(render_mock.call_args.args[2]["result"])

    def test_search_handles_denial_unready_and_runtime_index_failure(self) -> None:
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(
                views, "visible_corpora_for", return_value=self.visible(False)
            ),
        ):
            response = views.parallel_search.__wrapped__(self.request(), self.corpus.pk)
        self.assertEqual(response.status_code, 403)

        self.corpus.status = CorpusStatus.PROCESSING
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "visible_corpora_for", return_value=self.visible()),
            patch.object(views, "ensure_corpus_index_ready", return_value=None),
            patch.object(
                views, "render", return_value=HttpResponse("waiting")
            ) as render_mock,
        ):
            views.parallel_search.__wrapped__(self.request(), self.corpus.pk)
        self.assertEqual(render_mock.call_args.kwargs["status"], 409)

        self.corpus.status = CorpusStatus.READY
        engine = Mock()
        engine.search.side_effect = views.ParallelIndexUnavailable("missing")
        notice = IndexRepairNotice(state="pending", message="repairing")
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "visible_corpora_for", return_value=self.visible()),
            patch.object(
                views, "ensure_corpus_index_ready", side_effect=[None, notice]
            ),
            patch.object(views, "ParallelSearchEngine", return_value=engine),
            patch.object(
                views, "render", return_value=HttpResponse("repair")
            ) as render_mock,
        ):
            views.parallel_search.__wrapped__(
                self.request("q=farmer&search_side=en&alignment_unit=sentence"),
                self.corpus.pk,
            )
        self.assertEqual(render_mock.call_args.kwargs["status"], 202)

    def test_export_validates_and_streams_safe_tsv(self) -> None:
        engine = Mock()
        engine.search.return_value = SimpleNamespace(total=1)
        engine.iter_export_rows.return_value = iter(
            [
                (
                    1,
                    1,
                    1,
                    "zh.txt",
                    "en.txt",
                    "=formula\n",
                    "farmer",
                    "sentence",
                    "manual",
                    1,
                )
            ]
        )
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "visible_corpora_for", return_value=self.visible()),
            patch.object(views, "ParallelSearchEngine", return_value=engine),
            patch.object(views, "record_audit_event") as audit,
        ):
            response = views.parallel_export.__wrapped__(
                self.request("q=农民&search_side=zh&alignment_unit=sentence"),
                self.corpus.pk,
            )
            body = b"".join(response.streaming_content).decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("'=formula ", body)
        audit.assert_called_once()

    def test_export_rejects_wrong_type_invalid_form_and_unavailable_index(self) -> None:
        self.corpus.corpus_type = CorpusType.RAW_ZH
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "visible_corpora_for", return_value=self.visible()),
        ):
            response = views.parallel_export.__wrapped__(self.request(), self.corpus.pk)
        self.assertEqual(response.status_code, 409)

        self.corpus.corpus_type = CorpusType.PAIRED_TAGGED_ZH_EN
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "visible_corpora_for", return_value=self.visible()),
        ):
            response = views.parallel_export.__wrapped__(self.request(), self.corpus.pk)
        self.assertEqual(response.status_code, 400)

        engine = Mock()
        engine.search.side_effect = views.ParallelIndexUnavailable("missing")
        notice = IndexRepairNotice(state="running", message="repairing")
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "visible_corpora_for", return_value=self.visible()),
            patch.object(views, "ParallelSearchEngine", return_value=engine),
            patch.object(views, "ensure_corpus_index_ready", return_value=notice),
        ):
            response = views.parallel_export.__wrapped__(
                self.request("q=农民&search_side=zh&alignment_unit=sentence"),
                self.corpus.pk,
            )
        self.assertEqual(response.status_code, 202)

    def test_alignment_helpers_cover_all_corpus_types(self) -> None:
        raw = SimpleNamespace(
            corpus_type=CorpusType.PAIRED_RAW_ZH_EN,
            status=CorpusStatus.READY,
        )
        tagged = SimpleNamespace(
            corpus_type=CorpusType.PAIRED_TAGGED_ZH_EN,
            status=CorpusStatus.READY,
        )
        aligned = SimpleNamespace(
            corpus_type=CorpusType.ALIGNED_TSV,
            status=CorpusStatus.READY,
        )
        self.assertEqual(views._available_alignment_units(raw), ("paragraph",))
        self.assertEqual(
            views._available_alignment_units(tagged),
            ("sentence", "paragraph"),
        )
        self.assertEqual(views._available_alignment_units(aligned), ("sentence",))
        self.assertEqual(views._availability_error(aligned), "")
        aligned.status = CorpusStatus.PROCESSING
        self.assertIn("尚未加工", views._availability_error(aligned))
        self.assertEqual(views._safe_tsv_cell("+formula\tvalue"), "'+formula value")
