from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.parallel.engine import ParallelQuery, ParallelSearchEngine
from apps.processing.models import ProcessingTask, ProcessingTaskStatus
from apps.processing.services import process_task

from .models import (
    Corpus,
    CorpusAccessLevel,
    CorpusFile,
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

    def test_test_user_can_upload_small_private_txt_corpus(self):
        test_user = self.create_user("test_uploader", UserRole.TEST)
        self.client.force_login(test_user)
        temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        with override_settings(DATA_ROOT=Path(temp_dir)), patch(
            "apps.corpora.views.dispatch_processing_task"
        ) as dispatch:
            response = self.client.post(
                reverse("corpora:upload"),
                {
                    "name": "测试账号私有语料",
                    "language": CorpusLanguage.ZH,
                    "description": "人工测试上传",
                    "source_file": SimpleUploadedFile(
                        "sample.txt",
                        "语料平台用于人工测试。".encode("utf-8"),
                        content_type="text/plain",
                    ),
                },
            )

            corpus = Corpus.objects.get(name="测试账号私有语料")
            corpus_file = CorpusFile.objects.get(corpus=corpus)
            self.assertRedirects(
                response,
                reverse("corpora:documentation", kwargs={"corpus_id": corpus.pk}),
                fetch_redirect_response=False,
            )
            self.assertEqual(corpus.owner, test_user)
            self.assertEqual(corpus.access_level, CorpusAccessLevel.PRIVATE)
            self.assertEqual(corpus.status, CorpusStatus.PENDING_PROCESSING)
            self.assertTrue(Path(corpus_file.stored_path).is_file())
            self.assertIn(corpus, visible_corpora_for(test_user))
            dispatch.assert_called_once()

    def test_user_can_upload_and_process_human_aligned_bilingual_pair(self):
        user = self.create_user("parallel_owner", UserRole.JUNIOR)
        self.client.force_login(user)
        temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        data_root = Path(temp_dir)
        with override_settings(DATA_ROOT=data_root), patch(
            "apps.corpora.views.dispatch_processing_task"
        ) as dispatch:
            response = self.client.post(
                reverse("corpora:upload"),
                {
                    "name": "人工段落对齐语料",
                    "upload_mode": "paired_raw",
                    "zh_file": SimpleUploadedFile(
                        "zh.txt",
                        "第一段人民生活。\n\n第二段共同发展。".encode("utf-8"),
                    ),
                    "en_file": SimpleUploadedFile(
                        "en.txt",
                        b"The first paragraph concerns people's lives.\n\nThe second concerns development.",
                    ),
                },
            )

            corpus = Corpus.objects.get(name="人工段落对齐语料")
            task = corpus.processing_tasks.get()
            self.assertRedirects(
                response,
                reverse("corpora:documentation", kwargs={"corpus_id": corpus.pk}),
                fetch_redirect_response=False,
            )
            self.assertEqual(corpus.corpus_type, CorpusType.PAIRED_RAW_ZH_EN)
            self.assertEqual(corpus.language, CorpusLanguage.ZH_EN)
            self.assertEqual(
                set(corpus.files.values_list("language", flat=True)),
                {CorpusLanguage.ZH, CorpusLanguage.EN},
            )
            dispatch.assert_called_once_with(task)

            process_task(task.pk)
            corpus.refresh_from_db()
            self.assertEqual(corpus.status, CorpusStatus.READY)
            result = ParallelSearchEngine(
                data_root=data_root,
                corpus_id=str(corpus.pk),
            ).search(ParallelQuery(q="人民", alignment_unit="paragraph"))
            self.assertEqual(result.total, 1)
            self.assertIn("people", result.hits[0].en_text)

    def test_user_can_upload_numbered_tagged_bilingual_pair(self):
        user = self.create_user("tagged_owner", UserRole.JUNIOR)
        self.client.force_login(user)
        fixture_root = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "processing"
        temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        with override_settings(DATA_ROOT=Path(temp_dir)), patch(
            "apps.corpora.views.dispatch_processing_task"
        ):
            response = self.client.post(
                reverse("corpora:upload"),
                {
                    "name": "编号标注双语",
                    "upload_mode": "paired_tagged",
                    "zh_file": SimpleUploadedFile(
                        "tagged-zh.txt",
                        (fixture_root / "paired_tagged_zh.txt").read_bytes(),
                    ),
                    "en_file": SimpleUploadedFile(
                        "tagged-en.txt",
                        (fixture_root / "paired_tagged_en.txt").read_bytes(),
                    ),
                },
            )

        corpus = Corpus.objects.get(name="编号标注双语")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(corpus.corpus_type, CorpusType.PAIRED_TAGGED_ZH_EN)
        self.assertEqual(corpus.documentation.file_count, 2)

    def test_upload_rejects_binary_content_disguised_as_txt(self):
        user = self.create_user("binary_owner", UserRole.JUNIOR)
        self.client.force_login(user)
        temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        with override_settings(DATA_ROOT=Path(temp_dir)):
            response = self.client.post(
                reverse("corpora:upload"),
                {
                    "name": "伪装二进制",
                    "language": CorpusLanguage.EN,
                    "source_file": SimpleUploadedFile("fake.txt", b"\x01" * 100),
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "不像纯文本")
        self.assertFalse(Corpus.objects.filter(name="伪装二进制").exists())

    def test_failed_upload_can_be_retried_and_status_is_json(self):
        user = self.create_user("retry_owner", UserRole.JUNIOR)
        corpus = self.create_corpus(
            "Retry corpus",
            source_type=CorpusSourceType.USER,
            owner=user,
            status=CorpusStatus.FAILED,
        )
        temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        source = Path(temp_dir) / "source.txt"
        source.write_text("retry text", encoding="utf-8")
        CorpusFile.objects.create(
            corpus=corpus,
            original_filename="source.txt",
            stored_path=str(source),
            detected_type=CorpusType.RAW_ZH,
            language=CorpusLanguage.ZH,
            size_bytes=source.stat().st_size,
        )
        ProcessingTask.objects.create(
            corpus=corpus,
            requested_by=user,
            status=ProcessingTaskStatus.FAILED,
            error_message="previous failure",
        )
        self.client.force_login(user)

        with patch("apps.corpora.views.dispatch_processing_task") as dispatch:
            response = self.client.post(
                reverse("corpora:retry", kwargs={"corpus_id": corpus.pk})
            )

        self.assertEqual(response.status_code, 302)
        task = corpus.processing_tasks.order_by("-created_at").first()
        self.assertEqual(task.status, ProcessingTaskStatus.PENDING)
        dispatch.assert_called_once_with(task)
        status_response = self.client.get(
            reverse("corpora:status", kwargs={"corpus_id": corpus.pk})
        )
        self.assertEqual(status_response.json()["task"]["progress"], 0)

    def test_owner_can_delete_finished_user_corpus_and_runtime_files(self):
        user = self.create_user("delete_owner", UserRole.JUNIOR)
        corpus = self.create_corpus(
            "Delete corpus",
            source_type=CorpusSourceType.USER,
            owner=user,
        )
        temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        data_root = Path(temp_dir)
        targets = [
            data_root / "user_uploads" / str(user.pk) / str(corpus.pk),
            data_root / "processed" / str(corpus.pk),
            data_root / "indexes" / str(corpus.pk),
            data_root / "exports" / str(corpus.pk),
        ]
        for target in targets:
            target.mkdir(parents=True)
            (target / "artifact.txt").write_text("data", encoding="utf-8")
        self.client.force_login(user)

        with override_settings(DATA_ROOT=data_root), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("corpora:delete", kwargs={"corpus_id": corpus.pk})
            )

        self.assertRedirects(response, reverse("corpora:mine"), fetch_redirect_response=False)
        self.assertFalse(Corpus.objects.filter(pk=corpus.pk).exists())
        self.assertTrue(all(not target.exists() for target in targets))

    def test_other_user_cannot_retry_delete_or_read_status(self):
        owner = self.create_user("lifecycle_owner", UserRole.JUNIOR)
        intruder = self.create_user("lifecycle_intruder", UserRole.JUNIOR)
        corpus = self.create_corpus(
            "Protected lifecycle",
            source_type=CorpusSourceType.USER,
            owner=owner,
            status=CorpusStatus.FAILED,
        )
        self.client.force_login(intruder)

        status = self.client.get(reverse("corpora:status", kwargs={"corpus_id": corpus.pk}))
        retry = self.client.post(reverse("corpora:retry", kwargs={"corpus_id": corpus.pk}))
        delete = self.client.post(reverse("corpora:delete", kwargs={"corpus_id": corpus.pk}))

        self.assertEqual(status.status_code, 404)
        self.assertEqual(retry.status_code, 403)
        self.assertEqual(delete.status_code, 403)
        self.assertTrue(Corpus.objects.filter(pk=corpus.pk).exists())

    @override_settings(TEST_UPLOAD_MAX_FILE_BYTES=8, TEST_UPLOAD_TOTAL_BYTES=16)
    def test_test_user_upload_rejects_file_over_sandbox_limit(self):
        test_user = self.create_user("limited_test", UserRole.TEST)
        self.client.force_login(test_user)

        response = self.client.post(
            reverse("corpora:upload"),
            {
                "name": "Too large",
                "language": CorpusLanguage.EN,
                "source_file": SimpleUploadedFile("large.txt", b"123456789"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"].errors.as_data()["source_file"][0].code,
            "file_too_large",
        )
        self.assertFalse(Corpus.objects.filter(name="Too large").exists())

    def test_test_user_cannot_see_other_users_uploaded_corpus(self):
        test_user = self.create_user("test_reader", UserRole.TEST)
        owner = self.create_user("upload_owner", UserRole.JUNIOR)
        private = self.create_corpus(
            "Other upload",
            source_type=CorpusSourceType.USER,
            owner=owner,
        )

        self.assertNotIn(private, visible_corpora_for(test_user))

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
