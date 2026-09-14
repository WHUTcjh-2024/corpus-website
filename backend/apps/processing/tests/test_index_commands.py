from __future__ import annotations

import sqlite3
import tempfile
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management.base import CommandError, OutputWrapper
from django.test import SimpleTestCase, override_settings

from apps.corpora.models import CorpusLanguage, CorpusType
from apps.processing.exceptions import ProcessingAlreadyQueued, ProcessingError
from apps.processing.management.commands.repair_corpus_indexes import (
    Command as RepairCommand,
)
from apps.processing.management.commands.validate_corpus_indexes import (
    Command as ValidateCommand,
)
from apps.processing.models import ProcessingTaskStatus


class FakeCorpusQuerySet:
    def __init__(self, corpora):
        self.corpora = list(corpora)

    def filter(self, **_kwargs):
        return self

    def select_related(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def __iter__(self):
        return iter(self.corpora)


def command_with_streams(command_class):
    command = command_class()
    command.stdout = OutputWrapper(StringIO())
    command.stderr = OutputWrapper(StringIO())
    return command


class ValidateCorpusIndexesCommandTests(SimpleTestCase):
    def corpus(self, *, corpus_type=CorpusType.RAW_EN, language=CorpusLanguage.EN):
        return SimpleNamespace(
            pk="00000000-0000-0000-0000-000000000001",
            name="验收语料",
            corpus_type=corpus_type,
            language=language,
            documentation=SimpleNamespace(token_count=3),
        )

    def test_handle_reports_success_and_aggregates_failures(self) -> None:
        good = self.corpus()
        bad = self.corpus()
        bad.pk = "00000000-0000-0000-0000-000000000002"
        bad.name = "损坏语料"
        command = command_with_streams(ValidateCommand)
        queryset = FakeCorpusQuerySet([good, bad])
        with (
            patch(
                "apps.processing.management.commands.validate_corpus_indexes.Corpus.objects.filter",
                return_value=queryset,
            ),
            patch.object(
                command,
                "_validate_corpus",
                side_effect=[
                    {"tokens": 3, "kwic": 2, "types": 1, "pairs": 0},
                    RuntimeError("bad"),
                ],
            ),
        ):
            with self.assertRaisesMessage(CommandError, "validated=2"):
                command.handle(corpus_ids=[str(good.pk)])
        self.assertIn("PASS", command.stdout._out.getvalue())
        self.assertIn("FAIL", command.stderr._out.getvalue())

    def test_validate_corpus_checks_counts_queries_and_parallel_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            corpus = self.corpus(
                corpus_type=CorpusType.PAIRED_TAGGED_ZH_EN,
                language=CorpusLanguage.ZH_EN,
            )
            index_dir = root / "indexes" / str(corpus.pk)
            index_dir.mkdir(parents=True)
            connection = sqlite3.connect(index_dir / "kwic_index.sqlite")
            connection.executescript(
                """
                CREATE TABLE tokens (id INTEGER);
                INSERT INTO tokens VALUES (1), (2), (3);
                CREATE TABLE parallel_pairs (id INTEGER);
                INSERT INTO parallel_pairs VALUES (1);
                CREATE TABLE word_totals (
                    language TEXT,
                    display TEXT,
                    normalized TEXT,
                    frequency INTEGER,
                    is_punctuation INTEGER
                );
                INSERT INTO word_totals VALUES ('en', 'farmer', 'farmer', 2, 0);
                """
            )
            connection.close()
            health = SimpleNamespace(
                is_ready=True, state=SimpleNamespace(value="ready"), detail=""
            )
            kwic = Mock()
            kwic.search.return_value = SimpleNamespace(total=2)
            statistics = Mock()
            statistics.word_list.return_value = SimpleNamespace(total_types=1)
            parallel = Mock()
            parallel.preview.return_value = ("pair",)
            command = ValidateCommand()
            with (
                override_settings(DATA_ROOT=root),
                patch(
                    "apps.processing.management.commands.validate_corpus_indexes.inspect_corpus_index",
                    return_value=health,
                ),
                patch(
                    "apps.processing.management.commands.validate_corpus_indexes.KwicSearchEngine",
                    return_value=kwic,
                ),
                patch(
                    "apps.processing.management.commands.validate_corpus_indexes.StatisticsEngine",
                    return_value=statistics,
                ),
                patch(
                    "apps.processing.management.commands.validate_corpus_indexes.ParallelSearchEngine",
                    return_value=parallel,
                ),
            ):
                result = command._validate_corpus(corpus)

            self.assertEqual(result, {"tokens": 3, "kwic": 2, "types": 1, "pairs": 1})
            parallel.preview.assert_called_once_with(alignment_unit="sentence", limit=1)

    def test_validate_corpus_rejects_bad_health_and_language(self) -> None:
        corpus = self.corpus(language=CorpusLanguage.UNKNOWN)
        bad_health = SimpleNamespace(
            is_ready=False,
            state=SimpleNamespace(value="corrupt"),
            detail="checksum mismatch",
        )
        command = ValidateCommand()
        with patch(
            "apps.processing.management.commands.validate_corpus_indexes.inspect_corpus_index",
            return_value=bad_health,
        ):
            with self.assertRaisesMessage(RuntimeError, "health=corrupt"):
                command._validate_corpus(corpus)


class RepairCorpusIndexesCommandTests(SimpleTestCase):
    def corpus(self, name: str):
        tasks = Mock()
        tasks.filter.return_value.first.return_value = SimpleNamespace(pk="existing")
        return SimpleNamespace(pk=name, name=name, processing_tasks=tasks)

    def run_command(self, corpora, **options):
        command = command_with_streams(RepairCommand)
        queryset = FakeCorpusQuerySet(corpora)
        defaults = {"corpus_ids": None, "sync": False, "dry_run": False}
        defaults.update(options)
        corpus_module = "apps.processing.management.commands.repair_corpus_indexes"
        return command, queryset, defaults, corpus_module

    def test_handle_skips_healthy_and_queues_unhealthy_indexes(self) -> None:
        healthy = self.corpus("healthy")
        broken = self.corpus("broken")
        command, queryset, options, module = self.run_command([healthy, broken])
        states = [
            SimpleNamespace(
                is_ready=True, state=SimpleNamespace(value="ready"), detail=""
            ),
            SimpleNamespace(
                is_ready=False, state=SimpleNamespace(value="missing"), detail="absent"
            ),
        ]
        task = SimpleNamespace(pk="task")
        with (
            patch(f"{module}.Corpus.objects.filter", return_value=queryset),
            patch(f"{module}.inspect_corpus_index", side_effect=states),
            patch(f"{module}.create_processing_task", return_value=task),
            patch(
                f"{module}.dispatch_processing_task",
                return_value=SimpleNamespace(id="celery"),
            ),
        ):
            command.handle(**options)
        output = command.stdout._out.getvalue()
        self.assertIn("healthy=1", output)
        self.assertIn("queued=1", output)

    def test_handle_covers_dry_run_sync_and_existing_queue(self) -> None:
        corpus = self.corpus("broken")
        unhealthy = SimpleNamespace(
            is_ready=False,
            state=SimpleNamespace(value="missing"),
            detail="absent",
        )
        ready = SimpleNamespace(
            is_ready=True, state=SimpleNamespace(value="ready"), detail=""
        )

        command, queryset, options, module = self.run_command([corpus], dry_run=True)
        with (
            patch(f"{module}.Corpus.objects.filter", return_value=queryset),
            patch(f"{module}.inspect_corpus_index", return_value=unhealthy),
            patch(f"{module}.create_processing_task") as create,
        ):
            command.handle(**options)
        create.assert_not_called()

        task = SimpleNamespace(pk="task")
        command, queryset, options, module = self.run_command([corpus], sync=True)
        with (
            patch(f"{module}.Corpus.objects.filter", return_value=queryset),
            patch(f"{module}.inspect_corpus_index", side_effect=[unhealthy, ready]),
            patch(f"{module}.create_processing_task", return_value=task),
            patch(f"{module}.process_task") as process,
        ):
            command.handle(**options)
        process.assert_called_once_with(task.pk)
        self.assertIn("repaired=1", command.stdout._out.getvalue())

        command, queryset, options, module = self.run_command([corpus])
        with (
            patch(f"{module}.Corpus.objects.filter", return_value=queryset),
            patch(f"{module}.inspect_corpus_index", return_value=unhealthy),
            patch(
                f"{module}.create_processing_task", side_effect=ProcessingAlreadyQueued
            ),
        ):
            command.handle(**options)
        self.assertIn("already queued", command.stdout._out.getvalue())

    def test_handle_raises_summary_when_repair_fails(self) -> None:
        corpus = self.corpus("broken")
        unhealthy = SimpleNamespace(
            is_ready=False,
            state=SimpleNamespace(value="corrupt"),
            detail="bad",
        )
        command, queryset, options, module = self.run_command([corpus])
        with (
            patch(f"{module}.Corpus.objects.filter", return_value=queryset),
            patch(f"{module}.inspect_corpus_index", return_value=unhealthy),
            patch(
                f"{module}.create_processing_task",
                side_effect=ProcessingError("failed"),
            ),
        ):
            with self.assertRaisesMessage(CommandError, "failed=1"):
                command.handle(**options)
        self.assertIn("failed", command.stderr._out.getvalue())

    def test_active_queue_status_contract_stays_explicit(self) -> None:
        self.assertEqual(
            [ProcessingTaskStatus.PENDING, ProcessingTaskStatus.RUNNING],
            ["pending", "running"],
        )
