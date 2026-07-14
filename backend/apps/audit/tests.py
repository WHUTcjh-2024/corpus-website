from __future__ import annotations

import tempfile
from pathlib import Path

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
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

from .models import AuditEvent, AuditEventType


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AuditAndWatermarkTests(TestCase):
    password = "StrongPass!2026"
    fixtures_root = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "processing"

    def setUp(self) -> None:
        self.temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.data_root = Path(self.temp_dir)
        self.enterContext(override_settings(DATA_ROOT=self.data_root))
        self.user = get_user_model().objects.create_user(
            username="advanced-reader",
            email="advanced-reader@example.invalid",
            password=self.password,
        )
        UserProfile.objects.create(
            user=self.user,
            full_name="高级读者",
            organization="测试单位",
            email=self.user.email,
            role=UserRole.ADVANCED,
            requested_role=UserRole.ADVANCED,
            use_purpose="审计测试",
            application_reason="验证教师语料水印和检索审计",
            status=ApplicationStatus.APPROVED,
        )

    def create_teacher_corpus(self) -> Corpus:
        corpus = Corpus.objects.create(
            name="Teacher English",
            source_type=CorpusSourceType.TEACHER,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            access_level=CorpusAccessLevel.ADVANCED,
            status=CorpusStatus.CREATED,
        )
        source = self.fixtures_root / "raw_en.txt"
        CorpusFile.objects.create(
            corpus=corpus,
            original_filename=source.name,
            stored_path=str(source),
            detected_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            size_bytes=source.stat().st_size,
            encoding="utf-8",
        )
        process_task(create_processing_task(corpus=corpus).pk)
        corpus.refresh_from_db()
        return corpus

    def test_successful_and_failed_login_are_audited_without_passwords(self):
        success = self.client.post(
            reverse("accounts:login"),
            {"username": self.user.username, "password": self.password},
            REMOTE_ADDR="127.0.0.1",
            HTTP_USER_AGENT="Stage11-Test-Agent",
        )

        self.assertEqual(success.status_code, 302)
        success_event = AuditEvent.objects.get(event_type=AuditEventType.LOGIN_SUCCESS)
        self.assertEqual(success_event.actor, self.user)
        self.assertEqual(success_event.method, "POST")
        self.assertEqual(success_event.ip_address, "127.0.0.1")
        self.assertEqual(success_event.user_agent, "Stage11-Test-Agent")

        self.client.logout()
        failed = self.client.post(
            reverse("accounts:login"),
            {"username": self.user.username, "password": "wrong-secret"},
        )

        self.assertEqual(failed.status_code, 200)
        failed_event = AuditEvent.objects.get(event_type=AuditEventType.LOGIN_FAILED)
        self.assertIsNone(failed_event.actor)
        self.assertEqual(failed_event.metadata, {"username": self.user.username})
        self.assertNotIn("password", str(failed_event.metadata).lower())
        self.assertNotIn("wrong-secret", str(failed_event.metadata))

    def test_teacher_pages_have_dynamic_user_watermark_and_search_audit(self):
        corpus = self.create_teacher_corpus()
        self.client.force_login(self.user)
        AuditEvent.objects.all().delete()

        response = self.client.get(
            reverse("search:kwic", kwargs={"corpus_id": corpus.pk}),
            {"q": "development", "language": "en"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="teacher-watermark-layer"')
        self.assertContains(response, "advanced-reader")
        self.assertContains(response, "data-watermark-trace=")
        event = AuditEvent.objects.get(event_type=AuditEventType.KWIC_SEARCH)
        self.assertEqual(event.actor, self.user)
        self.assertEqual(event.corpus, corpus)
        self.assertEqual(event.metadata["result_count"], 1)
        self.assertEqual(event.metadata["parameters"]["q"], "development")

    def test_demo_page_does_not_receive_teacher_watermark(self):
        demo = Corpus.objects.create(
            name="Demo Corpus",
            source_type=CorpusSourceType.DEMO,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            access_level=CorpusAccessLevel.DEMO,
            status=CorpusStatus.READY,
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("corpora:documentation", kwargs={"corpus_id": demo.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="teacher-watermark-layer"')

    def test_django_admin_log_entry_is_mirrored_to_audit_event(self):
        admin = get_user_model().objects.create_superuser(
            username="audit-admin",
            email="audit-admin@example.invalid",
            password=self.password,
        )

        log_entry = LogEntry.objects.create(
            user=admin,
            content_type=None,
            object_id="42",
            object_repr="Quota request 42",
            action_flag=CHANGE,
            change_message="approved",
        )

        event = AuditEvent.objects.get(event_type=AuditEventType.ADMIN_ACTION)
        self.assertEqual(event.actor, admin)
        self.assertEqual(event.metadata["object_id"], log_entry.object_id)
        self.assertEqual(event.metadata["change_message"], "approved")
