from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404, HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.corpora import views
from apps.corpora.forms import CorpusUploadForm, PersonalCorpusForm
from apps.corpora.models import (
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from apps.processing.exceptions import ProcessingError
from apps.processing.index_health import IndexRepairNotice


class CorpusFormTests(SimpleTestCase):
    def setUp(self) -> None:
        self.user = SimpleNamespace(pk=1)
        self.limit = SimpleNamespace(max_file_bytes=16)

    def test_personal_form_validates_save_contract(self) -> None:
        invalid = PersonalCorpusForm(user=self.user)
        with self.assertRaisesMessage(ValueError, "commit=False"):
            invalid.save(commit=False)
        with self.assertRaisesMessage(ValueError, "invalid personal corpus"):
            invalid.save()

        result = SimpleNamespace(pk=1)
        with patch(
            "apps.corpora.forms.create_personal_corpus", return_value=result
        ) as create:
            form = PersonalCorpusForm(
                {
                    "name": "研究语料",
                    "corpus_type": CorpusType.RAW_ZH,
                    "language": CorpusLanguage.ZH,
                    "description": "测试",
                },
                user=self.user,
            )
            self.assertTrue(form.is_valid(), form.errors)
            self.assertIs(form.save(), result)
        self.assertEqual(create.call_args.kwargs["data"].name, "研究语料")

    def test_upload_form_rejects_missing_invalid_empty_and_large_files(self) -> None:
        missing = CorpusUploadForm(
            {"name": "语料", "upload_mode": "monolingual"},
            user=self.user,
        )
        self.assertFalse(missing.is_valid())
        self.assertIn("language", missing.errors)
        self.assertIn("source_file", missing.errors)

        cases = (
            (SimpleUploadedFile("sample.pdf", b"text"), "invalid_extension"),
            (SimpleUploadedFile("empty.txt", b""), "empty"),
            (SimpleUploadedFile("large.txt", b"x" * 17), "file_too_large"),
        )
        for uploaded, code in cases:
            with (
                self.subTest(code=code),
                patch(
                    "apps.corpora.forms.upload_limits_for",
                    return_value=self.limit,
                ),
            ):
                form = CorpusUploadForm(
                    {
                        "name": "语料",
                        "upload_mode": "monolingual",
                        "language": CorpusLanguage.ZH,
                    },
                    {"source_file": uploaded},
                    user=self.user,
                )
                self.assertFalse(form.is_valid())
                self.assertEqual(form.errors.as_data()["source_file"][0].code, code)

    def test_upload_form_saves_monolingual_and_parallel_inputs(self) -> None:
        mono_file = SimpleUploadedFile("zh.txt", "农民".encode())
        result = (SimpleNamespace(pk=1), SimpleNamespace(pk=2))
        with (
            patch("apps.corpora.forms.upload_limits_for", return_value=self.limit),
            patch(
                "apps.corpora.forms.create_uploaded_corpus", return_value=result
            ) as create,
        ):
            form = CorpusUploadForm(
                {
                    "name": "单语语料",
                    "upload_mode": "monolingual",
                    "language": CorpusLanguage.ZH,
                    "description": "测试",
                },
                {"source_file": mono_file},
                user=self.user,
            )
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.save(), result)
        self.assertEqual(create.call_args.kwargs["data"].language, CorpusLanguage.ZH)

        zh_file = SimpleUploadedFile("zh.txt", "农民".encode())
        en_file = SimpleUploadedFile("en.txt", b"farmer")
        with (
            patch("apps.corpora.forms.upload_limits_for", return_value=self.limit),
            patch(
                "apps.corpora.forms.create_uploaded_parallel_corpus",
                return_value=result,
            ) as create_parallel,
        ):
            form = CorpusUploadForm(
                {
                    "name": "双语语料",
                    "upload_mode": "paired_tagged",
                    "description": "测试",
                },
                {"zh_file": zh_file, "en_file": en_file},
                user=self.user,
            )
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.save(), result)
        self.assertEqual(
            create_parallel.call_args.kwargs["corpus_type"],
            CorpusType.PAIRED_TAGGED_ZH_EN,
        )


class CorpusViewTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = SimpleNamespace(pk=1, is_authenticated=True)
        self.corpus = SimpleNamespace(
            pk="00000000-0000-0000-0000-000000000001",
            name="测试语料",
            source_type=CorpusSourceType.USER,
            owner_id=1,
            corpus_type=CorpusType.PAIRED_RAW_ZH_EN,
            status=CorpusStatus.READY,
            stage="ready",
            documentation=SimpleNamespace(token_count=20),
            has_access=True,
        )

    def request(self, method: str = "get"):
        request = getattr(self.factory, method)("/corpora/")
        request.user = self.user
        return request

    def test_list_and_mine_build_access_summaries(self) -> None:
        corpora = [SimpleNamespace(has_access=True), SimpleNamespace(has_access=False)]
        with (
            patch.object(
                views, "catalog_corpora_with_access_for", return_value="queryset"
            ),
            patch.object(views, "_with_latest_tasks", return_value=corpora),
            patch.object(views, "can_create_personal_corpus", return_value=True),
            patch.object(views, "can_upload_personal_corpus", return_value=False),
            patch.object(
                views, "render", return_value=HttpResponse("list")
            ) as render_mock,
        ):
            views.corpus_list.__wrapped__(self.request())
        self.assertEqual(render_mock.call_args.args[2]["accessible_corpus_count"], 1)

        query = Mock()
        query.filter.return_value = query
        limits = SimpleNamespace(total_bytes=1000)
        with (
            patch.object(views, "visible_corpora_for", return_value=query),
            patch.object(views, "_with_latest_tasks", return_value=query),
            patch.object(views, "upload_limits_for", return_value=limits),
            patch.object(views, "uploaded_bytes_for", return_value=250),
            patch.object(views, "can_upload_personal_corpus", return_value=True),
            patch.object(
                views, "render", return_value=HttpResponse("mine")
            ) as render_mock,
        ):
            views.my_corpora.__wrapped__(self.request())
        self.assertEqual(render_mock.call_args.args[2]["used_bytes"], 250)

    def test_create_enforces_permission_and_redirects_after_save(self) -> None:
        with patch.object(views, "can_create_personal_corpus", return_value=False):
            response = views.corpus_create.__wrapped__(self.request())
        self.assertEqual(response.status_code, 403)

        form = Mock()
        form.is_valid.return_value = True
        form.save.return_value = self.corpus
        with (
            patch.object(views, "can_create_personal_corpus", return_value=True),
            patch.object(views, "PersonalCorpusForm", return_value=form),
            patch.object(views, "redirect", return_value=HttpResponse("created")),
        ):
            response = views.corpus_create.__wrapped__(self.request("post"))
        self.assertEqual(response.content, b"created")

        form.save.side_effect = PermissionDenied
        with (
            patch.object(views, "can_create_personal_corpus", return_value=True),
            patch.object(views, "PersonalCorpusForm", return_value=form),
        ):
            response = views.corpus_create.__wrapped__(self.request("post"))
        self.assertEqual(response.status_code, 403)

    def test_upload_handles_success_validation_and_processing_failures(self) -> None:
        with patch.object(views, "can_upload_personal_corpus", return_value=False):
            response = views.corpus_upload.__wrapped__(self.request())
        self.assertEqual(response.status_code, 403)

        task = SimpleNamespace(pk="task")
        files = Mock()
        files.count.return_value = 1
        files.all.return_value = [SimpleNamespace(size_bytes=12)]
        self.corpus.files = files
        form = Mock()
        form.is_valid.return_value = True
        form.save.return_value = (self.corpus, task)
        with (
            patch.object(views, "can_upload_personal_corpus", return_value=True),
            patch.object(views, "CorpusUploadForm", return_value=form),
            patch.object(views, "record_audit_event") as audit,
            patch.object(views, "dispatch_processing_task") as dispatch,
            patch.object(views.messages, "success"),
            patch.object(views, "redirect", return_value=HttpResponse("uploaded")),
        ):
            response = views.corpus_upload.__wrapped__(self.request("post"))
        self.assertEqual(response.content, b"uploaded")
        audit.assert_called_once()
        dispatch.assert_called_once_with(task)

        form.save.side_effect = ValidationError("bad upload")
        with (
            patch.object(views, "can_upload_personal_corpus", return_value=True),
            patch.object(views, "CorpusUploadForm", return_value=form),
            patch.object(
                views,
                "upload_limits_for",
                return_value=SimpleNamespace(max_file_bytes=1, total_bytes=2),
            ),
            patch.object(views, "uploaded_bytes_for", return_value=0),
            patch.object(views, "render", return_value=HttpResponse("error")),
        ):
            response = views.corpus_upload.__wrapped__(self.request("post"))
        self.assertEqual(response.content, b"error")

        form.save.side_effect = ProcessingError("queue failed")
        with (
            patch.object(views, "can_upload_personal_corpus", return_value=True),
            patch.object(views, "CorpusUploadForm", return_value=form),
            patch.object(views.messages, "error"),
            patch.object(
                views,
                "upload_limits_for",
                return_value=SimpleNamespace(max_file_bytes=1, total_bytes=2),
            ),
            patch.object(views, "uploaded_bytes_for", return_value=0),
            patch.object(views, "render", return_value=HttpResponse("error")),
        ):
            response = views.corpus_upload.__wrapped__(self.request("post"))
        self.assertEqual(response.content, b"error")

    def test_documentation_previews_parallel_corpus_and_repairs_index(self) -> None:
        tasks = Mock()
        tasks.order_by.return_value.first.return_value = None
        audits = Mock()
        audits.order_by.return_value.first.return_value = None
        self.corpus.processing_tasks = tasks
        self.corpus.parallel_audits = audits
        queryset = Mock()
        queryset.select_related.return_value = queryset
        engine = Mock()
        engine.preview.return_value = ("preview",)
        with (
            patch.object(
                views, "catalog_corpora_with_access_for", return_value=queryset
            ),
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "ParallelSearchEngine", return_value=engine),
            patch.object(
                views, "render", return_value=HttpResponse("docs")
            ) as render_mock,
        ):
            views.corpus_documentation.__wrapped__(self.request(), self.corpus.pk)
        self.assertEqual(
            render_mock.call_args.args[2]["alignment_preview"], ("preview",)
        )
        engine.preview.assert_called_once_with(alignment_unit="paragraph")

        engine.preview.side_effect = views.ParallelIndexUnavailable("missing")
        notice = IndexRepairNotice(state="pending", message="repairing")
        with (
            patch.object(
                views, "catalog_corpora_with_access_for", return_value=queryset
            ),
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "ParallelSearchEngine", return_value=engine),
            patch.object(views, "ensure_corpus_index_ready", return_value=notice),
            patch.object(
                views, "render", return_value=HttpResponse("docs")
            ) as render_mock,
        ):
            views.corpus_documentation.__wrapped__(self.request(), self.corpus.pk)
        self.assertEqual(
            render_mock.call_args.args[2]["alignment_preview_error"], "repairing"
        )

    def test_documentation_redirects_when_catalog_entry_is_denied(self) -> None:
        self.corpus.has_access = False
        queryset = Mock()
        queryset.select_related.return_value = queryset
        with (
            patch.object(
                views, "catalog_corpora_with_access_for", return_value=queryset
            ),
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views.messages, "warning"),
            patch.object(views, "redirect", return_value=HttpResponse("denied")),
        ):
            response = views.corpus_documentation.__wrapped__(
                self.request(), self.corpus.pk
            )
        self.assertEqual(response.content, b"denied")

    @override_settings(DATA_ROOT=".")
    def test_audit_download_rejects_missing_and_unsafe_files(self) -> None:
        audits = Mock()
        audits.filter.return_value.first.return_value = None
        self.corpus.parallel_audits = audits
        with (
            patch.object(views, "visible_corpora_for", return_value="queryset"),
            patch.object(views, "get_object_or_404", return_value=self.corpus),
        ):
            with self.assertRaises(Http404):
                views.parallel_audit_anomalies.__wrapped__(
                    self.request(), self.corpus.pk
                )

        audits.filter.return_value.first.return_value = SimpleNamespace(
            anomalies_path=str(Path(tempfile.gettempdir()) / "outside.jsonl")
        )
        with (
            patch.object(views, "visible_corpora_for", return_value="queryset"),
            patch.object(views, "get_object_or_404", return_value=self.corpus),
        ):
            with self.assertRaises(Http404):
                views.parallel_audit_anomalies.__wrapped__(
                    self.request(), self.corpus.pk
                )

    def test_status_serializes_optional_task_and_audit(self) -> None:
        task = SimpleNamespace(
            pk="task",
            status="running",
            progress=50,
            error_message="",
            get_status_display=lambda: "运行中",
        )
        audit = SimpleNamespace(
            pk="audit",
            status="success",
            error_message="",
            get_status_display=lambda: "成功",
        )
        self.corpus.get_status_display = lambda: "可用"
        self.corpus.processing_tasks = Mock()
        self.corpus.processing_tasks.order_by.return_value.first.return_value = task
        self.corpus.parallel_audits = Mock()
        self.corpus.parallel_audits.order_by.return_value.first.return_value = audit
        with (
            patch.object(views, "visible_corpora_for", return_value="queryset"),
            patch.object(views, "get_object_or_404", return_value=self.corpus),
        ):
            response = views.corpus_status.__wrapped__(self.request(), self.corpus.pk)
        payload = json.loads(response.content)
        self.assertEqual(payload["task"]["progress"], 50)
        self.assertEqual(payload["audit"]["status"], "success")

    def test_retry_and_delete_cover_success_and_error_paths(self) -> None:
        task = SimpleNamespace(pk="task")
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "retry_user_corpus", return_value=task),
            patch.object(views, "dispatch_processing_task"),
            patch.object(views, "record_audit_event") as audit,
            patch.object(views.messages, "success"),
            patch.object(views, "redirect", return_value=HttpResponse("retry")),
        ):
            response = views.corpus_retry.__wrapped__(
                self.request("post"), self.corpus.pk
            )
        self.assertEqual(response.content, b"retry")
        audit.assert_called_once()

        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(
                views, "retry_user_corpus", side_effect=PermissionDenied("denied")
            ),
        ):
            response = views.corpus_retry.__wrapped__(
                self.request("post"), self.corpus.pk
            )
        self.assertEqual(response.status_code, 403)

        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "delete_user_corpus"),
            patch.object(views, "record_audit_event") as audit,
            patch.object(views.messages, "success"),
            patch.object(views, "redirect", return_value=HttpResponse("deleted")),
        ):
            response = views.corpus_delete.__wrapped__(
                self.request("post"), self.corpus.pk
            )
        self.assertEqual(response.content, b"deleted")
        audit.assert_called_once()

        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(
                views, "delete_user_corpus", side_effect=ValidationError("busy")
            ),
            patch.object(views.messages, "error"),
            patch.object(views, "redirect", return_value=HttpResponse("busy")),
        ):
            response = views.corpus_delete.__wrapped__(
                self.request("post"), self.corpus.pk
            )
        self.assertEqual(response.content, b"busy")
