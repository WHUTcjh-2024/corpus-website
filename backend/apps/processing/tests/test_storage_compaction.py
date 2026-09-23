from __future__ import annotations

import gzip
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from apps.parallel.contracts import ParallelQuery
from apps.parallel.engine import ParallelSearchEngine
from apps.processing.artifacts import ArtifactWriter
from apps.processing.contracts import SourceFile
from apps.processing.importers.aligned_tsv import AlignedTsvImporter
from apps.processing.storage_compaction import compact_index, compress_token_archive
from apps.processing.storage_compaction import publish_compact_artifacts
from apps.search.kwic import KwicSearchEngine


FIXTURE = Path(__file__).parent / "fixtures" / "golden_aligned.tsv"


class StorageCompactionTests(SimpleTestCase):
    def test_compact_copy_preserves_kwic_and_parallel_results(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result = next(
                AlignedTsvImporter().iter_import(
                    (SourceFile("golden", FIXTURE.name, FIXTURE, "aligned_tsv", "zh_en"),)
                )
            )
            writer = ArtifactWriter(data_root=root, corpus_id="golden", task_id="task")
            writer.open()
            writer.add_result(result)
            writer.finalize(corpus_meta={}, source_files=[], importer_name="aligned_tsv")
            source = root / "indexes" / "golden" / "kwic_index.sqlite"
            destination = root / "compact.sqlite"
            counts = compact_index(source, destination)
            original_kwic = KwicSearchEngine(data_root=root, corpus_id="golden")
            original_parallel = ParallelSearchEngine(data_root=root, corpus_id="golden")
            compact_kwic = KwicSearchEngine(data_root=root, corpus_id="golden")
            compact_kwic.index_path = destination
            compact_parallel = ParallelSearchEngine(data_root=root, corpus_id="golden")
            compact_parallel.index_path = destination
            query = ParallelQuery(q="future", search_side="en", alignment_unit="sentence")
            self.assertEqual(
                original_kwic.search("future", language="en"),
                compact_kwic.search("future", language="en"),
            )
            self.assertEqual(original_parallel.search(query), compact_parallel.search(query))
            with closing(sqlite3.connect(destination)) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(tokens)")}
                self.assertNotIn("token_id", columns)
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone(), ("ok",))
            self.assertEqual(counts["tokens"], len(result.tokens))

    def test_token_archive_round_trips_and_never_overwrites(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tokens.jsonl"
            source.write_bytes((b'{"token":"future"}\n' * 1000))
            target = root / "tokens.jsonl.gz"
            size = compress_token_archive(source, target)
            self.assertLess(size, source.stat().st_size)
            with gzip.open(target, "rb") as archive:
                self.assertEqual(archive.read(), source.read_bytes())
            with self.assertRaises(FileExistsError):
                compress_token_archive(source, target)

    def test_publish_preserves_old_artifacts_until_live_verification(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result = next(
                AlignedTsvImporter().iter_import(
                    (SourceFile("golden", FIXTURE.name, FIXTURE, "aligned_tsv", "zh_en"),)
                )
            )
            writer = ArtifactWriter(data_root=root, corpus_id="golden", task_id="task")
            writer.open()
            writer.add_result(result)
            writer.finalize(corpus_meta={}, source_files=[], importer_name="aligned_tsv")
            source_index = root / "indexes" / "golden" / "kwic_index.sqlite"
            published_archive = root / "processed" / "golden" / "tokens.jsonl.gz"
            old_tokens = root / "processed" / "golden" / "tokens.jsonl"
            with gzip.open(published_archive, "rb") as archive:
                old_tokens.write_bytes(archive.read())
            published_archive.unlink()
            pilot = root / "pilot"
            compact_index(source_index, pilot / "indexes" / "golden" / "kwic_index.sqlite")
            compress_token_archive(
                old_tokens, pilot / "processed" / "golden" / "tokens.jsonl.gz"
            )
            backup = publish_compact_artifacts(
                data_root=root, pilot_root=pilot, corpus_id="golden"
            )
            self.assertTrue(backup.is_file())
            self.assertTrue(old_tokens.is_file())
            self.assertTrue(published_archive.is_file())
            self.assertEqual(
                KwicSearchEngine(data_root=root, corpus_id="golden").search(
                    "future", language="en"
                ).total,
                1,
            )

    def test_publish_rolls_back_when_pilot_index_is_corrupt(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result = next(
                AlignedTsvImporter().iter_import(
                    (SourceFile("golden", FIXTURE.name, FIXTURE, "aligned_tsv", "zh_en"),)
                )
            )
            writer = ArtifactWriter(data_root=root, corpus_id="golden", task_id="task")
            writer.open()
            writer.add_result(result)
            writer.finalize(corpus_meta={}, source_files=[], importer_name="aligned_tsv")
            source_index = root / "indexes" / "golden" / "kwic_index.sqlite"
            original_size = source_index.stat().st_size
            published_archive = root / "processed" / "golden" / "tokens.jsonl.gz"
            old_tokens = root / "processed" / "golden" / "tokens.jsonl"
            with gzip.open(published_archive, "rb") as archive:
                old_tokens.write_bytes(archive.read())
            published_archive.unlink()
            pilot = root / "pilot"
            pilot_index = pilot / "indexes" / "golden" / "kwic_index.sqlite"
            compact_index(source_index, pilot_index)
            compress_token_archive(
                old_tokens, pilot / "processed" / "golden" / "tokens.jsonl.gz"
            )
            pilot_index.write_bytes(b"invalid sqlite")
            with self.assertRaises(ValueError):
                publish_compact_artifacts(
                    data_root=root, pilot_root=pilot, corpus_id="golden"
                )
            self.assertEqual(source_index.stat().st_size, original_size)
            self.assertFalse(published_archive.exists())
            self.assertFalse(list(source_index.parent.glob("*.precompact-*")))
            self.assertEqual(
                KwicSearchEngine(data_root=root, corpus_id="golden").search(
                    "future", language="en"
                ).total,
                1,
            )
