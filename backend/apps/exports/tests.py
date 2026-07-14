from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.audit.models import AuditEvent, AuditEventType
from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusFile,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from apps.processing.services import create_processing_task, process_task

from .models import ExportJob, ExportJobStatus, ExportKind
from .services import (
    acquire_download,
    create_export_job,
    expire_exports,
    process_export_job,
)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    EXPORT_TTL_SECONDS=3600,
    EXPORT_MAX_ROWS=100,
    EXPORT_MAX_DOWNLOADS=1,
    EXPORT_MAX_JOBS_PER_HOUR=5,
)
class ControlledExportTests(TestCase):
    password = "StrongPass!2026"
    fixtures_root = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "processing"

    def setUp(self) -> None:
        self.temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.data_root = Path(self.temp_dir)
        self.enterContext(override_settings(DATA_ROOT=self.data_root))
        self.owner = self.create_user("export-owner")
        self.other_user = self.create_user("other-export-user")
        self.corpus = self.create_ready_user_corpus(
            owner=self.owner,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            files=(("raw_en.txt", CorpusLanguage.EN),),
        )

    def create_user(self, username: str):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.invalid",
            password=self.password,
        )
        UserProfile.objects.create(
            user=user,
            full_name=username,
            organization="测试单位",
            email=user.email,
            role=UserRole.JUNIOR,
            requested_role=UserRole.JUNIOR,
            use_purpose="导出测试",
            application_reason="验证受控导出",
            status=ApplicationStatus.APPROVED,
        )
        return user

    def create_ready_user_corpus(
        self,
        *,
        owner,
        corpus_type: str,
        language: str,
        files: tuple[tuple[str, str], ...],
    ) -> Corpus:
        corpus = Corpus.objects.create(
            name=f"Private {corpus_type}",
            source_type=CorpusSourceType.USER,
            corpus_type=corpus_type,
            language=language,
            owner=owner,
            access_level=CorpusAccessLevel.PRIVATE,
            status=CorpusStatus.CREATED,
        )
        upload_dir = self.data_root / "user_uploads" / str(owner.pk) / str(corpus.pk)
        upload_dir.mkdir(parents=True)
        for filename, file_language in files:
            source = self.fixtures_root / filename
            stored_path = upload_dir / filename
            shutil.copy2(source, stored_path)
            CorpusFile.objects.create(
                corpus=corpus,
                original_filename=filename,
                stored_path=str(stored_path),
                detected_type=corpus_type,
                language=file_language,
                size_bytes=stored_path.stat().st_size,
                encoding="utf-8",
            )
        process_task(create_processing_task(corpus=corpus, requested_by=owner).pk)
        corpus.refresh_from_db()
        return corpus

    def create_kwic_job(self) -> ExportJob:
        return create_export_job(
            user=self.owner,
            corpus=self.corpus,
            kind=ExportKind.KWIC,
            parameters=QueryDict(
                "q=development&query_mode=simple&language=en&context=5&sort_by=&pos="
            ),
        )

    def test_kwic_export_is_generated_under_private_root_and_audited(self):
        job = self.create_kwic_job()

        report = process_export_job(job.pk)
        job.refresh_from_db()

        output_path = Path(job.output_path)
        export_root = (self.data_root / "exports").resolve()
        self.assertEqual(job.status, ExportJobStatus.SUCCESS)
        self.assertEqual(job.row_count, 1)
        self.assertTrue(output_path.resolve().is_relative_to(export_root))
        self.assertEqual(report["row_count"], 1)
        content = output_path.read_text(encoding="utf-8-sig")
        self.assertIn("命中词", content)
        self.assertIn("development", content.lower())
        self.assertTrue(
            AuditEvent.objects.filter(
                event_type=AuditEventType.EXPORT_COMPLETED,
                corpus=self.corpus,
                metadata__job_id=str(job.pk),
            ).exists()
        )

    def test_download_is_owner_only_counted_and_bounded(self):
        job = self.create_kwic_job()
        process_export_job(job.pk)
        self.client.force_login(self.other_user)

        forbidden = self.client.get(reverse("exports:download", kwargs={"job_id": job.pk}))

        self.assertEqual(forbidden.status_code, 403)
        job.refresh_from_db()
        self.assertEqual(job.download_count, 0)

        self.client.force_login(self.owner)
        downloaded = self.client.get(reverse("exports:download", kwargs={"job_id": job.pk}))
        payload = b"".join(downloaded.streaming_content)
        self.assertEqual(downloaded.status_code, 200)
        self.assertIn(b"development", payload.lower())
        job.refresh_from_db()
        self.assertEqual(job.download_count, 1)
        self.assertEqual(
            AuditEvent.objects.filter(
                event_type=AuditEventType.EXPORT_DOWNLOADED,
                metadata__job_id=str(job.pk),
            ).count(),
            1,
        )

        limited = self.client.get(reverse("exports:download", kwargs={"job_id": job.pk}))
        self.assertEqual(limited.status_code, 403)

    def test_teacher_corpus_cannot_create_any_bulk_export(self):
        teacher = Corpus.objects.create(
            name="Teacher Restricted",
            source_type=CorpusSourceType.TEACHER,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            access_level=CorpusAccessLevel.JUNIOR,
            status=CorpusStatus.READY,
        )

        with self.assertRaises(PermissionDenied):
            create_export_job(
                user=self.owner,
                corpus=teacher,
                kind=ExportKind.KWIC,
                parameters={"q": "development", "language": "en"},
            )

    def test_one_active_job_and_hourly_rate_limit_are_enforced(self):
        first = self.create_kwic_job()
        with self.assertRaisesMessage(ValidationError, "已有等待或执行中"):
            self.create_kwic_job()

        first.status = ExportJobStatus.FAILED
        first.save(update_fields=["status", "updated_at"])
        with override_settings(EXPORT_MAX_JOBS_PER_HOUR=1):
            with self.assertRaisesMessage(ValidationError, "过于频繁"):
                self.create_kwic_job()

    def test_expired_job_is_revoked_and_file_removed(self):
        job = self.create_kwic_job()
        process_export_job(job.pk)
        job.refresh_from_db()
        output_path = Path(job.output_path)
        ExportJob.objects.filter(pk=job.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        self.assertEqual(expire_exports(user=self.owner), 1)

        job.refresh_from_db()
        self.assertEqual(job.status, ExportJobStatus.EXPIRED)
        self.assertEqual(job.output_path, "")
        self.assertFalse(output_path.exists())

    def test_download_rejects_output_path_outside_export_root(self):
        outside = self.data_root / "outside.tsv"
        outside.write_text("secret", encoding="utf-8")
        job = ExportJob.objects.create(
            requested_by=self.owner,
            corpus=self.corpus,
            kind=ExportKind.KWIC,
            query={"q": "development"},
            status=ExportJobStatus.SUCCESS,
            progress=100,
            output_path=str(outside),
            expires_at=timezone.now() + timedelta(hours=1),
        )

        with self.assertRaisesMessage(ValidationError, "路径无效"):
            acquire_download(job_id=job.pk, user=self.owner)

    def test_parallel_export_uses_authoritative_alignment(self):
        parallel_corpus = self.create_ready_user_corpus(
            owner=self.owner,
            corpus_type=CorpusType.ALIGNED_TSV,
            language=CorpusLanguage.ZH_EN,
            files=(("aligned.tsv", CorpusLanguage.ZH_EN),),
        )
        job = create_export_job(
            user=self.owner,
            corpus=parallel_corpus,
            kind=ExportKind.PARALLEL,
            parameters=QueryDict("q=数字经济&search_side=zh&alignment_unit=sentence"),
        )

        process_export_job(job.pk)
        job.refresh_from_db()

        self.assertEqual(job.status, ExportJobStatus.SUCCESS)
        self.assertEqual(job.row_count, 1)
        content = Path(job.output_path).read_text(encoding="utf-8-sig")
        self.assertIn("数字经济快速发展", content)
        self.assertIn("The digital economy is growing rapidly", content)
