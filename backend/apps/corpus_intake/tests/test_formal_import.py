from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase

from apps.corpora.models import (
    CorpusAccessLevel,
    CorpusDocumentation,
    CorpusFile,
    CorpusSourceType,
)
from apps.corpus_intake.formal_import import register_formal_teacher_corpora
from apps.corpus_intake.scanner import ManifestRecord, ScanResult


class FormalTeacherCorpusRegistrationTests(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_registers_processable_groups_and_preserves_pair_ids(self) -> None:
        records = [
            self._record("pair-zh.txt", "zh", "paired_raw_zh_en", "pair-0001"),
            self._record("pair-en.txt", "en", "paired_raw_zh_en", "pair-0001"),
            self._record("mono-zh.txt", "zh", "raw_zh"),
            self._record("empty.txt", "unknown", "unknown", status="quarantined"),
        ]

        result = register_formal_teacher_corpora(
            scan_result=ScanResult(self.root, records, {}),
            access_level=CorpusAccessLevel.ADVANCED,
        )

        self.assertEqual(result.registered_file_count, 3)
        self.assertEqual(len(result.quarantined_records), 1)
        self.assertEqual(len(result.corpora), 2)
        self.assertTrue(
            all(corpus.source_type == CorpusSourceType.TEACHER for corpus in result.corpora)
        )
        paired_files = CorpusFile.objects.exclude(pair_id="")
        self.assertEqual(paired_files.count(), 2)
        self.assertEqual(set(paired_files.values_list("pair_id", flat=True)), {"pair-0001"})
        self.assertEqual(
            sorted(
                CorpusDocumentation.objects.get(corpus=corpus).file_count
                for corpus in result.corpora
            ),
            [1, 2],
        )

    def test_rejects_incomplete_pair_group(self) -> None:
        records = [
            self._record("pair-zh.txt", "zh", "paired_raw_zh_en", "pair-0001"),
        ]

        with self.assertRaisesRegex(ValueError, "恰好包含一个中文文件和一个英文文件"):
            register_formal_teacher_corpora(
                scan_result=ScanResult(self.root, records, {})
            )

    def test_reregistration_updates_metadata_without_duplicate_corpora(self) -> None:
        first_record = self._record("first.txt", "zh", "raw_zh")
        stale_record = self._record("stale.txt", "zh", "raw_zh")
        first = register_formal_teacher_corpora(
            scan_result=ScanResult(self.root, [first_record, stale_record], {})
        )

        replacement_record = self._record("replacement.txt", "zh", "raw_zh")
        second = register_formal_teacher_corpora(
            scan_result=ScanResult(
                self.root,
                [first_record, replacement_record],
                {},
            ),
            name_prefix="正式语料",
        )

        self.assertEqual(first.corpora[0].pk, second.corpora[0].pk)
        corpus = second.corpora[0]
        self.assertEqual(corpus.name, "正式语料·中文原文语料")
        self.assertEqual(corpus.files.count(), 2)
        self.assertSetEqual(
            set(corpus.files.values_list("original_filename", flat=True)),
            {"first.txt", "replacement.txt"},
        )

    def _record(
        self,
        filename: str,
        language: str,
        detected_type: str,
        pair_id: str = "",
        *,
        status: str = "pending_review",
    ) -> ManifestRecord:
        path = self.root / filename
        path.write_text("sample", encoding="utf-8")
        return ManifestRecord(
            file_id=filename,
            original_path=filename,
            filename=filename,
            size_bytes=path.stat().st_size,
            encoding="utf-8",
            detected_language=language,
            detected_type=detected_type,
            confidence=1.0,
            probable_pair_id=pair_id,
            stage_or_period="",
            author="",
            title_guess=filename,
            date_guess="",
            notes="",
            status=status,
        )
