from __future__ import annotations

import tempfile
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management.base import CommandError, OutputWrapper
from django.test import SimpleTestCase, override_settings

from apps.corpus_intake.management.commands.import_formal_teacher_corpus import Command


class FormalImportCommandTests(SimpleTestCase):
    def command(self) -> Command:
        command = Command()
        command.stdout = OutputWrapper(StringIO())
        return command

    def test_rejects_missing_source_directory(self) -> None:
        with self.assertRaisesMessage(CommandError, "不存在"):
            self.command().handle(
                source_root=Path("missing-formal-corpus"),
                access_level="advanced",
                name_prefix="正式语料",
                process=False,
            )

    def test_scans_registers_reports_quarantine_and_processes(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as data_dir:
            corpus = SimpleNamespace(
                pk="corpus",
                name="正式语料",
                files=Mock(),
            )
            corpus.files.count.return_value = 2
            scan_result = SimpleNamespace(records=())
            quarantine = SimpleNamespace(original_path="empty.txt", notes="empty")
            registration = SimpleNamespace(
                registered_file_count=2,
                quarantined_records=(quarantine,),
                corpora=(corpus,),
            )
            task = SimpleNamespace(pk="task")
            command = self.command()
            module = "apps.corpus_intake.management.commands.import_formal_teacher_corpus"
            with (
                override_settings(DATA_ROOT=Path(data_dir)),
                patch(f"{module}.scan_inbox", return_value=scan_result) as scan,
                patch(
                    f"{module}.write_manifest",
                    return_value=(Path("manifest.csv"), Path("manifest.json")),
                ) as write,
                patch(
                    f"{module}.register_formal_teacher_corpora",
                    return_value=registration,
                ) as register,
                patch(f"{module}.create_processing_task", return_value=task),
                patch(f"{module}.process_task", return_value={"counts": {"tokens": 10}}),
            ):
                command.handle(
                    source_root=Path(source_dir),
                    access_level="advanced",
                    name_prefix="正式语料",
                    process=True,
                )

        scan.assert_called_once()
        write.assert_called_once()
        register.assert_called_once()
        output = command.stdout._out.getvalue()
        self.assertIn("Quarantined: empty.txt", output)
        self.assertIn("Processed corpus", output)
        self.assertIn("登记完成", output)

    def test_translates_registration_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as source_dir:
            module = "apps.corpus_intake.management.commands.import_formal_teacher_corpus"
            with (
                patch(f"{module}.scan_inbox", return_value=SimpleNamespace()),
                patch(
                    f"{module}.write_manifest",
                    return_value=(Path("manifest.csv"), Path("manifest.json")),
                ),
                patch(
                    f"{module}.register_formal_teacher_corpora",
                    side_effect=ValueError("invalid manifest"),
                ),
            ):
                with self.assertRaisesMessage(CommandError, "invalid manifest"):
                    self.command().handle(
                        source_root=Path(source_dir),
                        access_level="advanced",
                        name_prefix="正式语料",
                        process=False,
                    )
