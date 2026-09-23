from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.corpora.models import CorpusStatus
from apps.processing import index_health
from apps.processing.contracts import SCHEMA_VERSION
from apps.processing.exceptions import ProcessingAlreadyQueued, ProcessingError
from apps.processing.models import ProcessingTaskStatus


class IndexInspectionTests(SimpleTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.corpus_id = "corpus"
        index_health._inspect_corpus_index_snapshot.cache_clear()

    def tearDown(self) -> None:
        index_health._inspect_corpus_index_snapshot.cache_clear()
        self.temp_dir.cleanup()

    def processed_root(self) -> Path:
        path = self.root / "processed" / self.corpus_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def index_path(self) -> Path:
        path = self.root / "indexes" / self.corpus_id / "kwic_index.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_processed_files(self, *, metadata=None) -> None:
        root = self.processed_root()
        for filename in index_health.REQUIRED_PROCESSED_FILES:
            content = ""
            if filename == "meta.json":
                content = json.dumps(metadata or {"schema_version": SCHEMA_VERSION})
            (root / filename).write_text(content, encoding="utf-8")
        (root / "tokens.jsonl").write_text("", encoding="utf-8")

    def create_schema(
        self, *, omit_table: str = "", omit_column: tuple[str, str] = ("", "")
    ):
        connection = sqlite3.connect(self.index_path())
        for table, columns in index_health.REQUIRED_TABLE_COLUMNS.items():
            if table == omit_table:
                continue
            definitions = [
                f'"{column}" TEXT'
                for column in sorted(columns)
                if (table, column) != omit_column
            ]
            connection.execute(f'CREATE TABLE "{table}" ({", ".join(definitions)})')
        connection.commit()
        connection.close()

    def inspect(self):
        index_health._inspect_corpus_index_snapshot.cache_clear()
        return index_health.inspect_corpus_index(self.corpus_id, data_root=self.root)

    def test_reports_missing_files_corrupt_metadata_and_outdated_schema(self) -> None:
        missing = self.inspect()
        self.assertEqual(missing.state, index_health.IndexHealthState.MISSING)
        self.assertIn("kwic_index.sqlite", missing.detail)

        self.write_processed_files()
        self.index_path().write_text("not sqlite", encoding="utf-8")
        (self.processed_root() / "meta.json").write_text("{bad", encoding="utf-8")
        corrupt = self.inspect()
        self.assertEqual(corrupt.state, index_health.IndexHealthState.CORRUPT)
        self.assertIn("JSONDecodeError", corrupt.detail)

        self.write_processed_files(metadata={"schema_version": "1.0"})
        outdated = self.inspect()
        self.assertEqual(outdated.state, index_health.IndexHealthState.OUTDATED)
        self.assertEqual(outdated.schema_version, "1.0")

    def test_reports_missing_tables_and_columns(self) -> None:
        self.write_processed_files()
        self.create_schema(omit_table="tokens")
        missing_table = self.inspect()
        self.assertEqual(missing_table.state, index_health.IndexHealthState.CORRUPT)
        self.assertIn("missing_tables=tokens", missing_table.detail)

        self.index_path().unlink()
        self.create_schema(omit_column=("tokens", "surface"))
        missing_column = self.inspect()
        self.assertEqual(missing_column.state, index_health.IndexHealthState.CORRUPT)
        self.assertIn("missing_columns=tokens:surface", missing_column.detail)

    def test_accepts_complete_current_schema(self) -> None:
        self.write_processed_files()
        self.create_schema()
        (self.processed_root() / "tokens.jsonl").unlink()
        (self.processed_root() / "tokens.jsonl.gz").write_bytes(b"archive")
        health = self.inspect()
        self.assertTrue(health.is_ready)
        self.assertEqual(health.reader_label, "索引可用")

        (self.processed_root() / "tokens.jsonl.gz").unlink()
        (self.processed_root() / "tokens.jsonl").write_text("", encoding="utf-8")
        self.assertTrue(self.inspect().is_ready)

    def test_path_fingerprint_handles_missing_and_existing_files(self) -> None:
        missing = self.root / "missing"
        self.assertEqual(index_health._path_fingerprint(missing)[1:], (False, 0, 0))
        existing = self.root / "file.txt"
        existing.write_text("value", encoding="utf-8")
        fingerprint = index_health._path_fingerprint(existing)
        self.assertTrue(fingerprint[1])
        self.assertEqual(fingerprint[2], 5)


class EnsureIndexReadyTests(SimpleTestCase):
    def corpus(self, status=CorpusStatus.READY):
        return SimpleNamespace(
            pk="corpus",
            status=status,
            stage="ready",
            refresh_from_db=Mock(),
        )

    def task(self, status=ProcessingTaskStatus.PENDING):
        return SimpleNamespace(
            pk="task",
            status=status,
            progress=30,
            refresh_from_db=Mock(),
        )

    def test_returns_active_notice_or_skips_unready_and_healthy_corpus(self) -> None:
        active = self.task()
        with patch.object(index_health, "_active_task", return_value=active):
            notice = index_health.ensure_corpus_index_ready(self.corpus())
        self.assertTrue(notice.is_active)
        self.assertEqual(notice.task_id, "task")

        with patch.object(index_health, "_active_task", return_value=None):
            self.assertIsNone(
                index_health.ensure_corpus_index_ready(
                    self.corpus(CorpusStatus.PROCESSING)
                )
            )

        health = index_health.IndexHealth(index_health.IndexHealthState.READY)
        with (
            patch.object(index_health, "_active_task", return_value=None),
            patch.object(index_health, "inspect_corpus_index", return_value=health),
        ):
            self.assertIsNone(index_health.ensure_corpus_index_ready(self.corpus()))

    def test_creates_repairs_and_handles_success_failure_and_queue_race(self) -> None:
        corrupt = index_health.IndexHealth(
            index_health.IndexHealthState.CORRUPT,
            detail="bad",
            schema_version=SCHEMA_VERSION,
        )
        pending = self.task()
        with (
            patch.object(index_health, "_active_task", return_value=None),
            patch.object(index_health, "inspect_corpus_index", return_value=corrupt),
            patch.object(index_health, "create_processing_task", return_value=pending),
            patch.object(index_health, "dispatch_processing_task") as dispatch,
        ):
            notice = index_health.ensure_corpus_index_ready(self.corpus())
        dispatch.assert_called_once_with(pending)
        self.assertTrue(notice.is_active)
        self.assertIn("原子替换", notice.message)

        failed = self.task(ProcessingTaskStatus.FAILED)
        with (
            patch.object(index_health, "_active_task", return_value=None),
            patch.object(index_health, "inspect_corpus_index", return_value=corrupt),
            patch.object(index_health, "create_processing_task", return_value=failed),
            patch.object(index_health, "dispatch_processing_task"),
        ):
            notice = index_health.ensure_corpus_index_ready(self.corpus())
        self.assertEqual(notice.state, ProcessingTaskStatus.FAILED)
        self.assertEqual(notice.retry_after_seconds, 0)

        active = self.task(ProcessingTaskStatus.RUNNING)
        with (
            patch.object(index_health, "_active_task", side_effect=[None, active]),
            patch.object(index_health, "inspect_corpus_index", return_value=corrupt),
            patch.object(
                index_health,
                "create_processing_task",
                side_effect=ProcessingAlreadyQueued,
            ),
        ):
            notice = index_health.ensure_corpus_index_ready(self.corpus())
        self.assertEqual(notice.state, ProcessingTaskStatus.RUNNING)

        with (
            patch.object(index_health, "_active_task", return_value=None),
            patch.object(index_health, "inspect_corpus_index", return_value=corrupt),
            patch.object(
                index_health, "create_processing_task", side_effect=ProcessingError
            ),
        ):
            notice = index_health.ensure_corpus_index_ready(self.corpus())
        self.assertEqual(notice.state, ProcessingTaskStatus.FAILED)

    def test_force_repair_and_synchronous_success_recheck(self) -> None:
        ready = index_health.IndexHealth(
            index_health.IndexHealthState.READY,
            schema_version=SCHEMA_VERSION,
        )
        success = self.task(ProcessingTaskStatus.SUCCESS)
        with (
            patch.object(index_health, "_active_task", return_value=None),
            patch.object(
                index_health, "inspect_corpus_index", side_effect=[ready, ready]
            ),
            patch.object(index_health, "create_processing_task", return_value=success),
            patch.object(index_health, "dispatch_processing_task"),
        ):
            notice = index_health.ensure_corpus_index_ready(self.corpus(), force=True)
        self.assertIsNone(notice)
