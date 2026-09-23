import gzip
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from apps.processing.artifacts import ArtifactWriter
from apps.processing.contracts import SourceFile
from apps.processing.importers.raw_mono import import_raw_source
from apps.search.kwic import KwicSearchEngine


class ArtifactWriterTests(SimpleTestCase):
    def test_type_count_and_frequency_are_language_scoped(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            zh_path = root / "zh.txt"
            en_path = root / "en.txt"
            zh_path.write_text("AI", encoding="utf-8")
            en_path.write_text("AI", encoding="utf-8")
            writer = ArtifactWriter(data_root=root, corpus_id="corpus", task_id="task")
            writer.open()
            writer.add_result(
                import_raw_source(SourceFile("zh", "zh.txt", zh_path, "raw_zh", "zh"))
            )
            writer.add_result(
                import_raw_source(SourceFile("en", "en.txt", en_path, "raw_en", "en"))
            )
            report = writer.finalize(
                corpus_meta={},
                source_files=[],
                importer_name="raw_mono_txt",
            )
            frequency = json.loads(
                (root / "indexes" / "corpus" / "word_frequency.json").read_text(
                    encoding="utf-8"
                )
            )
            hit = KwicSearchEngine(data_root=root, corpus_id="corpus").search(
                "AI", language="en"
            ).hits[0]
            file_view = KwicSearchEngine(
                data_root=root, corpus_id="corpus"
            ).file_view(
                document_id=hit.document_id,
                language=hit.language,
                row_id=hit.row_id,
            )
            token_archive = root / "processed" / "corpus" / "tokens.jsonl.gz"
            with gzip.open(token_archive, "rt", encoding="utf-8") as archive:
                archived_tokens = [json.loads(line) for line in archive]
            with closing(sqlite3.connect(root / "indexes" / "corpus" / "kwic_index.sqlite")) as connection:
                token_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(tokens)")
                }
                ngram_schema = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'ngrams'"
                ).fetchone()[0]

        self.assertEqual(report["counts"]["type_count"], 2)
        self.assertEqual(
            {(item["language"], item["token"]) for item in frequency["items"]},
            {("zh", "ai"), ("en", "ai")},
        )
        self.assertEqual(file_view.keyword, "AI")
        self.assertEqual(file_view.filename, "en.txt")
        self.assertEqual(len(archived_tokens), 2)
        self.assertNotIn("token_id", token_columns)
        self.assertIn("WITHOUT ROWID", ngram_schema)
