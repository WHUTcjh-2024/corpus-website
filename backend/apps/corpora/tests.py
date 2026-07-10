from __future__ import annotations

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole

from .models import (
    Corpus,
    CorpusAccessLevel,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from .services import visible_corpora_for


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class CorporaModuleTests(TestCase):
    password = "StrongPass!2026"

    def create_user(self, username: str, role: str):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.invalid",
            password=self.password,
        )
        UserProfile.objects.create(
            user=user,
            full_name=f"{username} 姓名",
            organization="测试单位",
            email=f"{username}@example.invalid",
            role=role,
            requested_role=(role if role in {UserRole.JUNIOR, UserRole.MIDDLE, UserRole.ADVANCED} else ""),
            use_purpose="阶段 3 测试",
            application_reason="验证语料库可见性",
            status=ApplicationStatus.APPROVED,
        )
        return user

    def create_corpus(
        self,
        name: str,
        *,
        source_type: str = CorpusSourceType.TEACHER,
        access_level: str = CorpusAccessLevel.JUNIOR,
        owner=None,
        status: str = CorpusStatus.READY,
    ) -> Corpus:
        return Corpus.objects.create(
            name=name,
            source_type=source_type,
            corpus_type=CorpusType.RAW_ZH,
            language=CorpusLanguage.ZH,
            owner=owner,
            access_level=(
                CorpusAccessLevel.PRIVATE
                if source_type == CorpusSourceType.USER
                else access_level
            ),
            status=status,
            stage="registered",
        )

    def test_test_user_can_only_see_demo_corpora(self):
        test_user = self.create_user("test_user", UserRole.TEST)
        demo = self.create_corpus(
            "Demo Corpus",
            source_type=CorpusSourceType.DEMO,
            access_level=CorpusAccessLevel.DEMO,
        )
        self.create_corpus("Teacher Corpus", access_level=CorpusAccessLevel.DEMO)

        visible = list(visible_corpora_for(test_user))

        self.assertEqual(visible, [demo])

    def test_junior_cannot_see_advanced_teacher_corpus(self):
        junior = self.create_user("junior", UserRole.JUNIOR)
        junior_corpus = self.create_corpus(
            "Junior Corpus",
            access_level=CorpusAccessLevel.JUNIOR,
        )
        self.create_corpus(
            "Advanced Corpus",
            access_level=CorpusAccessLevel.ADVANCED,
        )

        visible = list(visible_corpora_for(junior))

        self.assertEqual(visible, [junior_corpus])

    def test_middle_and_advanced_roles_receive_hierarchical_access(self):
        middle = self.create_user("middle", UserRole.MIDDLE)
        advanced = self.create_user("advanced", UserRole.ADVANCED)
        junior_corpus = self.create_corpus("A Junior", access_level=CorpusAccessLevel.JUNIOR)
        middle_corpus = self.create_corpus("B Middle", access_level=CorpusAccessLevel.MIDDLE)
        advanced_corpus = self.create_corpus("C Advanced", access_level=CorpusAccessLevel.ADVANCED)

        self.assertEqual(
            list(visible_corpora_for(middle)),
            [junior_corpus, middle_corpus],
        )
        self.assertEqual(
            list(visible_corpora_for(advanced)),
            [junior_corpus, middle_corpus, advanced_corpus],
        )

    def test_user_cannot_see_or_open_another_users_private_corpus(self):
        user_a = self.create_user("user_a", UserRole.JUNIOR)
        user_b = self.create_user("user_b", UserRole.JUNIOR)
        corpus_b = self.create_corpus(
            "User B Private",
            source_type=CorpusSourceType.USER,
            owner=user_b,
        )
        self.client.force_login(user_a)

        self.assertNotIn(corpus_b, visible_corpora_for(user_a))
        response = self.client.get(
            reverse("corpora:documentation", kwargs={"corpus_id": corpus_b.pk})
        )
        self.assertEqual(response.status_code, 404)

    def test_admin_can_see_all_corpora_including_disabled(self):
        admin = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.invalid",
            password=self.password,
        )
        active = self.create_corpus("Active Corpus")
        disabled = self.create_corpus("Disabled Corpus", status=CorpusStatus.DISABLED)

        self.assertEqual(list(visible_corpora_for(admin)), [active, disabled])

    def test_approved_user_can_register_personal_metadata_without_upload(self):
        user = self.create_user("owner", UserRole.JUNIOR)
        self.client.force_login(user)

        response = self.client.post(
            reverse("corpora:create"),
            {
                "name": "My Corpus",
                "corpus_type": CorpusType.RAW_EN,
                "language": CorpusLanguage.EN,
                "description": "仅登记元数据",
            },
        )

        corpus = Corpus.objects.get(name="My Corpus")
        self.assertRedirects(
            response,
            reverse("corpora:documentation", kwargs={"corpus_id": corpus.pk}),
            fetch_redirect_response=False,
        )
        self.assertEqual(corpus.owner, user)
        self.assertEqual(corpus.source_type, CorpusSourceType.USER)
        self.assertEqual(corpus.access_level, CorpusAccessLevel.PRIVATE)
        self.assertEqual(corpus.status, CorpusStatus.CREATED)
        self.assertEqual(corpus.stage, "registered")
        self.assertEqual(corpus.documentation.token_count, 0)

    def test_test_user_cannot_register_personal_corpus(self):
        test_user = self.create_user("test_user", UserRole.TEST)
        self.client.force_login(test_user)

        response = self.client.get(reverse("corpora:create"))

        self.assertEqual(response.status_code, 403)

    def test_documentation_page_displays_metadata_and_statistics_placeholders(self):
        user = self.create_user("reader", UserRole.JUNIOR)
        corpus = self.create_corpus("Documented Corpus")
        corpus.documentation.file_count = 3
        corpus.documentation.save(update_fields=["file_count", "updated_at"])
        self.client.force_login(user)

        response = self.client.get(
            reverse("corpora:documentation", kwargs={"corpus_id": corpus.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Corpus Documentation")
        self.assertContains(response, "Documented Corpus")
        self.assertContains(response, "Token")
        self.assertContains(response, "3")

    def test_manifest_command_registers_metadata_and_is_idempotent(self):
        manifest_path = (
            Path(__file__).resolve().parents[2]
            / "tests"
            / "fixtures"
            / "corpora"
            / "manifest_sample.json"
        )
        options = {
            "manifest": manifest_path,
            "file_id": "fixture-001",
            "source_type": CorpusSourceType.DEMO,
            "access_level": CorpusAccessLevel.DEMO,
        }

        call_command("register_manifest_corpus", **options)
        call_command("register_manifest_corpus", **options)

        corpus = Corpus.objects.get(manifest_file_id="fixture-001")
        self.assertEqual(Corpus.objects.filter(manifest_file_id="fixture-001").count(), 1)
        self.assertEqual(corpus.name, "测试语料")
        self.assertEqual(corpus.source_type, CorpusSourceType.DEMO)
        self.assertEqual(corpus.corpus_type, CorpusType.RAW_ZH)
        self.assertEqual(corpus.language, CorpusLanguage.ZH)
        self.assertEqual(corpus.manifest_relative_path, "demo/test.txt")
        self.assertEqual(corpus.manifest_size_bytes, 128)
        self.assertEqual(corpus.documentation.file_count, 1)

    def test_status_choices_match_stage_three_contract(self):
        self.assertEqual(
            set(CorpusStatus.values),
            {
                "created",
                "pending_processing",
                "processing",
                "ready",
                "failed",
                "disabled",
            },
        )
