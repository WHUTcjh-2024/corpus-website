from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.admin_portal.models import Announcement, AnnouncementAudience
from apps.admin_portal.services import active_announcements_for
from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from apps.corpora.services import (
    ManagedUploadedCorpusData,
    create_managed_uploaded_corpus,
    visible_corpora_for,
)
from apps.feedback.models import FeedbackStatus, FeedbackTicket


class ManagementWorkflowTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="admin", email="admin@example.test", password="admin-password"
        )
        self.member = self._approved_user("member", "member@example.test")
        self.other_member = self._approved_user("other", "other@example.test")

    def _approved_user(self, username: str, email: str):
        user = get_user_model().objects.create_user(username=username, email=email, password="member-password")
        UserProfile.objects.create(
            user=user,
            full_name=username.title(),
            organization="测试单位",
            email=email,
            role=UserRole.JUNIOR,
            requested_role=UserRole.JUNIOR,
            use_purpose="自动化测试",
            application_reason="验证管理授权工作流",
            status=ApplicationStatus.APPROVED,
        )
        user.refresh_from_db()
        return user

    def test_direct_corpus_grant_is_visible_only_to_selected_user(self):
        corpus = Corpus.objects.create(
            name="指定用户语料",
            source_type=CorpusSourceType.TEACHER,
            corpus_type=CorpusType.RAW_ZH,
            language=CorpusLanguage.ZH,
            access_level=CorpusAccessLevel.PRIVATE,
            status=CorpusStatus.READY,
        )
        corpus.access_grants.create(user=self.member, granted_by=self.admin)

        self.assertQuerySetEqual(visible_corpora_for(self.member), [corpus], ordered=False)
        self.assertFalse(visible_corpora_for(self.other_member).filter(pk=corpus.pk).exists())

    def test_managed_upload_uses_normal_processing_task_and_direct_grant(self):
        with TemporaryDirectory() as temporary_dir:
            with override_settings(
                DATA_ROOT=Path(temporary_dir),
                USER_UPLOAD_MAX_FILE_BYTES=1024 * 1024,
            ):
                corpus, task = create_managed_uploaded_corpus(
                    actor=self.admin,
                    data=ManagedUploadedCorpusData(
                        name="管理员上传语料",
                        corpus_type=CorpusType.RAW_ZH,
                        language=CorpusLanguage.ZH,
                        source_type=CorpusSourceType.TEACHER,
                        access_level=CorpusAccessLevel.PRIVATE,
                        description="用于验证授权可见性。",
                    ),
                    files=((SimpleUploadedFile("sample.txt", "管理员上传文本".encode()), CorpusLanguage.ZH),),
                    recipients=[self.member],
                )

                self.assertEqual(task.corpus_id, corpus.pk)
                self.assertTrue(corpus.files.get().stored_path.startswith(temporary_dir))
                self.assertTrue(visible_corpora_for(self.member).filter(pk=corpus.pk).exists())
                self.assertFalse(visible_corpora_for(self.other_member).filter(pk=corpus.pk).exists())

    def test_selected_announcement_appears_only_for_recipient_dashboard(self):
        announcement = Announcement.objects.create(
            title="定向维护通知",
            body="请在本周完成验证。",
            audience=AnnouncementAudience.SELECTED,
            is_published=True,
            starts_at=timezone.now(),
            created_by=self.admin,
            published_by=self.admin,
            published_at=timezone.now(),
        )
        announcement.recipients.add(self.member)

        self.assertTrue(active_announcements_for(self.member).filter(pk=announcement.pk).exists())
        self.assertFalse(active_announcements_for(self.other_member).filter(pk=announcement.pk).exists())

        self.client.force_login(self.member)
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertContains(response, announcement.title)

    def test_management_workspace_requires_staff(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("admin_portal:dashboard"))
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin_portal:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "管理工作台")

    def test_admin_upload_view_creates_processing_task_and_visibility_grant(self):
        with TemporaryDirectory() as temporary_dir:
            with override_settings(
                DATA_ROOT=Path(temporary_dir),
                USER_UPLOAD_MAX_FILE_BYTES=1024 * 1024,
            ):
                self.client.force_login(self.admin)
                with patch("apps.admin_portal.views.dispatch_processing_task") as dispatch:
                    response = self.client.post(
                        reverse("admin_portal:corpus_upload"),
                        data={
                            "name": "管理端上传测试",
                            "upload_mode": "monolingual",
                            "language": CorpusLanguage.ZH,
                            "publish_scope": "selected",
                            "recipients": [str(self.member.pk)],
                            "description": "管理端工作流验证",
                            "source_file": SimpleUploadedFile("managed.txt", "可见性验证".encode()),
                        },
                    )

                corpus = Corpus.objects.get(name="管理端上传测试")
                self.assertRedirects(
                    response,
                    reverse("admin_portal:corpus_visibility", kwargs={"corpus_id": corpus.pk}),
                    fetch_redirect_response=False,
                )
                dispatch.assert_called_once()
                self.assertTrue(visible_corpora_for(self.member).filter(pk=corpus.pk).exists())

    def test_review_and_feedback_workflows_persist_operator_decisions(self):
        pending_user = get_user_model().objects.create_user(
            username="pending", email="pending@example.test", password="pending-password"
        )
        pending_profile = UserProfile.objects.create(
            user=pending_user,
            full_name="Pending User",
            organization="测试单位",
            email="pending@example.test",
            role=UserRole.JUNIOR,
            requested_role=UserRole.MIDDLE,
            use_purpose="审核测试",
            application_reason="验证审核工作流",
            status=ApplicationStatus.PENDING,
        )
        ticket = FeedbackTicket.objects.create(
            user=self.member,
            title="管理端处理测试",
            description="请验证状态与处理备注保存。",
        )

        self.client.force_login(self.admin)
        review_response = self.client.post(
            reverse("admin_portal:user_review", kwargs={"profile_id": pending_profile.pk}),
            data={"role": UserRole.MIDDLE, "status": ApplicationStatus.APPROVED},
        )
        feedback_response = self.client.post(
            reverse("admin_portal:feedback_detail", kwargs={"ticket_id": ticket.pk}),
            data={"status": FeedbackStatus.RESOLVED, "admin_note": "已完成验证并处理。"},
        )

        self.assertRedirects(review_response, reverse("admin_portal:user_list"), fetch_redirect_response=False)
        self.assertRedirects(feedback_response, reverse("admin_portal:feedback_list"), fetch_redirect_response=False)
        pending_profile.refresh_from_db()
        ticket.refresh_from_db()
        self.assertEqual(pending_profile.status, ApplicationStatus.APPROVED)
        self.assertEqual(pending_profile.role, UserRole.MIDDLE)
        self.assertEqual(ticket.status, FeedbackStatus.RESOLVED)
        self.assertEqual(ticket.admin_note, "已完成验证并处理。")
        self.assertIsNotNone(ticket.resolved_at)

    def test_announcement_create_view_reaches_selected_user(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("admin_portal:announcement_create"),
            data={
                "title": "发布流程验证",
                "body": "该公告只应出现在指定用户工作台。",
                "audience": AnnouncementAudience.SELECTED,
                "recipients": [str(self.member.pk)],
                "starts_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "ends_at": "",
                "is_published": "on",
            },
        )

        announcement = Announcement.objects.get(title="发布流程验证")
        self.assertRedirects(
            response,
            reverse("admin_portal:announcement_list"),
            fetch_redirect_response=False,
        )
        self.assertTrue(active_announcements_for(self.member).filter(pk=announcement.pk).exists())
        self.assertFalse(active_announcements_for(self.other_member).filter(pk=announcement.pk).exists())
