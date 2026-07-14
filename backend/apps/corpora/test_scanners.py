from __future__ import annotations

import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole

from .models import Corpus, CorpusLanguage
from .scanners import ScanResult, scan_uploaded_file
from .services import UploadedCorpusData, create_uploaded_corpus


class AcceptingTestScanner:
    scanned_paths: list[Path] = []

    def scan(self, path: Path) -> ScanResult:
        self.scanned_paths.append(path)
        return ScanResult(scanner="test", detail="clean")


class RejectingTestScanner:
    def scan(self, path: Path) -> ScanResult:
        raise ValidationError("测试扫描器拒绝文件。")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class UploadScannerTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.data_root = Path(self.temp_dir)
        self.enterContext(override_settings(DATA_ROOT=self.data_root))
        self.user = get_user_model().objects.create_user(
            username="scanner-owner",
            email="scanner-owner@example.invalid",
            password="StrongPass!2026",
        )
        UserProfile.objects.create(
            user=self.user,
            full_name="扫描测试用户",
            organization="测试单位",
            email=self.user.email,
            role=UserRole.JUNIOR,
            requested_role=UserRole.JUNIOR,
            use_purpose="上传扫描测试",
            application_reason="验证恶意文件扫描接口",
            status=ApplicationStatus.APPROVED,
        )
        AcceptingTestScanner.scanned_paths.clear()

    def test_accepted_upload_is_scanned_before_randomized_file_is_persisted(self):
        with override_settings(
            UPLOAD_SCANNER_BACKEND="apps.corpora.test_scanners.AcceptingTestScanner"
        ):
            corpus, _ = create_uploaded_corpus(
                user=self.user,
                data=UploadedCorpusData(
                    name="扫描通过语料",
                    language=CorpusLanguage.ZH,
                ),
                uploaded_file=SimpleUploadedFile(
                    "source.txt",
                    "安全文本语料。".encode("utf-8"),
                    content_type="text/plain",
                ),
            )

        stored_path = Path(corpus.files.get().stored_path)
        self.assertTrue(stored_path.is_file())
        self.assertNotEqual(stored_path.name, "source.txt")
        self.assertEqual(len(AcceptingTestScanner.scanned_paths), 1)
        self.assertTrue(AcceptingTestScanner.scanned_paths[0].name.endswith(".uploading"))

    def test_rejected_upload_leaves_no_database_record_or_file(self):
        with override_settings(
            UPLOAD_SCANNER_BACKEND="apps.corpora.test_scanners.RejectingTestScanner"
        ):
            with self.assertRaisesMessage(ValidationError, "测试扫描器拒绝"):
                create_uploaded_corpus(
                    user=self.user,
                    data=UploadedCorpusData(
                        name="扫描拒绝语料",
                        language=CorpusLanguage.EN,
                    ),
                    uploaded_file=SimpleUploadedFile(
                        "source.txt",
                        b"This upload must be rejected.",
                        content_type="text/plain",
                    ),
                )

        self.assertFalse(Corpus.objects.exists())
        upload_root = self.data_root / "user_uploads"
        self.assertFalse(upload_root.exists() and any(upload_root.rglob("*")))

    def test_invalid_scanner_backend_fails_closed(self):
        path = self.data_root / "sample.txt"
        path.write_text("content", encoding="utf-8")

        with override_settings(UPLOAD_SCANNER_BACKEND="missing.Scanner"):
            with self.assertRaises(ImproperlyConfigured):
                scan_uploaded_file(path)
