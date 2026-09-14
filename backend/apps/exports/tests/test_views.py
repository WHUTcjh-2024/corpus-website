from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from apps.exports import views
from apps.exports.models import ExportJob, ExportKind
from apps.exports.services import ExportError


class ExportViewTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = SimpleNamespace(pk=1, is_authenticated=True)
        self.corpus = SimpleNamespace(pk="00000000-0000-0000-0000-000000000001")

    def request(self, method: str = "get", data=None):
        request = getattr(self.factory, method)("/exports/", data or {})
        request.user = self.user
        return request

    def test_list_expires_old_jobs_and_renders_current_jobs(self) -> None:
        jobs = Mock()
        jobs.select_related.return_value = jobs
        jobs.__getitem__ = Mock(return_value=["job"])
        with (
            patch.object(views, "expire_exports") as expire,
            patch.object(views.ExportJob.objects, "filter", return_value=jobs),
            patch.object(
                views, "render", return_value=HttpResponse("list")
            ) as render_mock,
        ):
            response = views.export_list.__wrapped__(self.request())
        self.assertEqual(response.content, b"list")
        expire.assert_called_once_with(user=self.user)
        self.assertEqual(render_mock.call_args.args[2]["jobs"], ["job"])

    def test_status_serializes_job(self) -> None:
        job = SimpleNamespace(
            pk="job",
            kind=ExportKind.KWIC,
            status="success",
            progress=100,
            row_count=5,
            download_count=1,
            expires_at=datetime(2026, 9, 15, tzinfo=UTC),
            error_message="",
            get_status_display=lambda: "成功",
        )
        with (
            patch.object(views, "expire_exports"),
            patch.object(views, "get_object_or_404", return_value=job),
        ):
            response = views.export_status.__wrapped__(self.request(), "job")
        self.assertEqual(json.loads(response.content)["row_count"], 5)

    def test_download_handles_not_found_permission_validation_and_success(self) -> None:
        for error, status in (
            (ExportJob.DoesNotExist(), 404),
            (PermissionDenied("denied"), 403),
            (ValidationError("expired"), 409),
        ):
            with (
                self.subTest(status=status),
                patch.object(
                    views,
                    "acquire_download",
                    side_effect=error,
                ),
            ):
                response = views.export_download.__wrapped__(self.request(), "job")
                self.assertEqual(response.status_code, status)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.tsv"
            path.write_text("term\tcount\n", encoding="utf-8")
            job = SimpleNamespace(pk="job", kind=ExportKind.KWIC)
            with patch.object(views, "acquire_download", return_value=(job, path)):
                response = views.export_download.__wrapped__(self.request(), "job")
                self.assertEqual(response.status_code, 200)
                response.close()

    def test_create_export_covers_length_permission_error_and_success(self) -> None:
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views.messages, "error"),
            patch.object(
                views, "_return_to_search", return_value=HttpResponse("too-long")
            ),
        ):
            response = views._create_export(
                self.request("post", {"query_string": "x" * 5001}),
                self.corpus.pk,
                ExportKind.KWIC,
            )
        self.assertEqual(response.content, b"too-long")

        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(
                views, "create_export_job", side_effect=PermissionDenied("denied")
            ),
        ):
            response = views._create_export(
                self.request("post", {"query_string": "q=farmer"}),
                self.corpus.pk,
                ExportKind.KWIC,
            )
        self.assertEqual(response.status_code, 403)

        for error in (ValidationError("bad"), ExportError("failed")):
            with (
                patch.object(views, "get_object_or_404", return_value=self.corpus),
                patch.object(views, "create_export_job", side_effect=error),
                patch.object(views.messages, "error"),
                patch.object(
                    views, "_return_to_search", return_value=HttpResponse("back")
                ),
            ):
                response = views._create_export(
                    self.request("post", {"query_string": "q=farmer"}),
                    self.corpus.pk,
                    ExportKind.KWIC,
                )
                self.assertEqual(response.content, b"back")

        job = SimpleNamespace(pk="job")
        with (
            patch.object(views, "get_object_or_404", return_value=self.corpus),
            patch.object(views, "create_export_job", return_value=job),
            patch.object(views, "dispatch_export_job") as dispatch,
            patch.object(views.messages, "success"),
            patch.object(views, "redirect", return_value=HttpResponse("queued")),
        ):
            response = views._create_export(
                self.request("post", {"query_string": "q=farmer"}),
                self.corpus.pk,
                ExportKind.PARALLEL,
            )
        self.assertEqual(response.content, b"queued")
        dispatch.assert_called_once_with(job)

    def test_return_to_search_uses_kind_specific_route(self) -> None:
        with patch.object(
            views, "redirect", side_effect=lambda route, **_kwargs: route
        ):
            self.assertEqual(
                views._return_to_search(ExportKind.PARALLEL, self.corpus),
                "parallel:search",
            )
            self.assertEqual(
                views._return_to_search(ExportKind.KWIC, self.corpus),
                "search:kwic",
            )
