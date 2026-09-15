from __future__ import annotations

import tempfile
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management.base import CommandError, OutputWrapper
from django.test import SimpleTestCase, TestCase

from apps.corpora.management.commands.register_teacher_samples import (
    Command,
    SampleCorpus,
    SampleFile,
    _find_unique_file,
    _register_sample,
)
from apps.corpora.models import (
    CorpusAccessLevel,
    CorpusLanguage,
    CorpusSourceType,
    CorpusType,
)


class TeacherSampleCommandTests(SimpleTestCase):
    def command(self) -> Command:
        command = Command()
        command.stdout = OutputWrapper(StringIO())
        return command

    def test_rejects_missing_root_and_demo_access_level(self) -> None:
        with self.assertRaisesMessage(CommandError, "does not exist"):
            self.command().handle(
                source_root=Path("missing-teacher-corpus"),
                source_type=CorpusSourceType.DEMO,
                access_level=None,
                process=False,
            )

        with tempfile.TemporaryDirectory() as source_dir:
            with self.assertRaisesMessage(CommandError, "--access-level"):
                self.command().handle(
                    source_root=Path(source_dir),
                    source_type=CorpusSourceType.DEMO,
                    access_level=CorpusAccessLevel.JUNIOR,
                    process=False,
                )

    def test_registers_and_processes_all_declared_samples(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir:
            corpus = SimpleNamespace(pk="corpus", name="样本")
            task = SimpleNamespace(pk="task")
            module = "apps.corpora.management.commands.register_teacher_samples"
            command = self.command()
            with (
                patch(
                    f"{module}._find_unique_file",
                    return_value=Path(source_dir) / "sample.txt",
                ),
                patch(f"{module}._register_sample", return_value=corpus) as register,
                patch(f"{module}.create_processing_task", return_value=task),
                patch(
                    f"{module}.process_task", return_value={"counts": {"tokens": 10}}
                ),
            ):
                command.handle(
                    source_root=Path(source_dir),
                    source_type=CorpusSourceType.TEACHER,
                    access_level=None,
                    process=True,
                )

        self.assertEqual(register.call_count, 4)
        self.assertIn("Teacher samples ready: 4", command.stdout._out.getvalue())
        self.assertIn("Processed", command.stdout._out.getvalue())

    def test_find_unique_file_requires_exactly_one_match(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir:
            root = Path(source_dir)
            with self.assertRaisesMessage(CommandError, "found 0"):
                _find_unique_file(root, "sample.txt")
            nested = root / "nested"
            nested.mkdir()
            expected = nested / "sample.txt"
            expected.write_text("content", encoding="utf-8")
            self.assertEqual(_find_unique_file(root, "sample.txt"), expected.resolve())
            (root / "sample.txt").write_text("duplicate", encoding="utf-8")
            with self.assertRaisesMessage(CommandError, "found 2"):
                _find_unique_file(root, "sample.txt")


class TeacherSampleRegistrationTests(TestCase):
    def test_registration_is_idempotent_and_removes_stale_files(self) -> None:
        sample = SampleCorpus(
            name="教师测试样本",
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            files=(SampleFile("sample.txt", CorpusLanguage.EN),),
        )
        with tempfile.TemporaryDirectory() as source_dir:
            source = Path(source_dir) / "sample.txt"
            source.write_text("Farmers organize.", encoding="utf-8")
            classification = SimpleNamespace(encoding="utf-8")
            with patch(
                "apps.corpora.management.commands.register_teacher_samples.classify_path",
                return_value=classification,
            ):
                corpus = _register_sample(
                    sample,
                    [source],
                    source_type=CorpusSourceType.TEACHER,
                    access_level=CorpusAccessLevel.ADVANCED,
                )
                corpus.files.create(
                    original_filename="stale.txt",
                    stored_path=str(Path(source_dir) / "stale.txt"),
                    detected_type=CorpusType.RAW_EN,
                    language=CorpusLanguage.EN,
                )
                updated = _register_sample(
                    sample,
                    [source],
                    source_type=CorpusSourceType.TEACHER,
                    access_level=CorpusAccessLevel.ADVANCED,
                )

        self.assertEqual(corpus.pk, updated.pk)
        self.assertEqual(updated.files.count(), 1)
        self.assertEqual(updated.documentation.file_count, 1)
