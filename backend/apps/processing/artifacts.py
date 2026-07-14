from __future__ import annotations

import json
import os
import shutil
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, TextIO

from .contracts import (
    ImportResult,
    ParallelPairRecord,
    SCHEMA_VERSION,
    TokenRecord,
    record_dict,
)


PROCESSED_JSONL_FILES = {
    "documents": "documents.jsonl",
    "paragraphs": "paragraphs.jsonl",
    "sentences": "sentences.jsonl",
    "tokens": "tokens.jsonl",
    "parallel_pairs": "parallel_pairs.jsonl",
}
DEFERRED_INDEX_FILES: tuple[str, ...] = ()


class ArtifactWriter:
    def __init__(self, *, data_root: Path, corpus_id: str, task_id: str) -> None:
        self.data_root = data_root.resolve()
        self.corpus_id = corpus_id
        self.task_id = task_id
        self.processed_staging = self.data_root / "processed" / ".staging" / task_id
        self.index_staging = self.data_root / "indexes" / ".staging" / task_id
        self.processed_output = self.data_root / "processed" / corpus_id
        self.index_output = self.data_root / "indexes" / corpus_id
        self._handles: dict[str, TextIO] = {}
        self._sqlite: sqlite3.Connection | None = None
        self._frequency: Counter[str] = Counter()
        self._global_position = 0
        self._parallel_position = 0
        self.counts = {
            "file_count": 0,
            "document_count": 0,
            "paragraph_count": 0,
            "sentence_count": 0,
            "token_count": 0,
            "type_count": 0,
            "parallel_pair_count": 0,
        }
        self.warnings: list[str] = []

    def open(self) -> None:
        self.abort()
        self.processed_staging.mkdir(parents=True, exist_ok=False)
        self.index_staging.mkdir(parents=True, exist_ok=False)
        for key, filename in PROCESSED_JSONL_FILES.items():
            self._handles[key] = (self.processed_staging / filename).open(
                "w", encoding="utf-8", newline="\n"
            )
        self._sqlite = sqlite3.connect(self.index_staging / "kwic_index.sqlite")
        self._sqlite.execute(
            """
            CREATE TABLE tokens (
                global_position INTEGER PRIMARY KEY,
                token_id TEXT NOT NULL UNIQUE,
                normalized TEXT NOT NULL,
                surface TEXT NOT NULL,
                lemma TEXT NOT NULL,
                pos TEXT NOT NULL,
                language TEXT NOT NULL,
                document_id TEXT NOT NULL,
                sentence_id TEXT NOT NULL,
                sentence_position INTEGER NOT NULL,
                is_punctuation INTEGER NOT NULL
            )
            """
        )
        self._sqlite.execute(
            """
            CREATE TABLE ngrams (
                language TEXT NOT NULL,
                n INTEGER NOT NULL,
                normalized TEXT NOT NULL,
                display TEXT NOT NULL,
                frequency INTEGER NOT NULL,
                contains_punctuation INTEGER NOT NULL,
                PRIMARY KEY (language, n, normalized)
            )
            """
        )
        self._sqlite.execute(
            """
            CREATE TABLE parallel_pairs (
                global_position INTEGER PRIMARY KEY,
                pair_id TEXT NOT NULL UNIQUE,
                pair_ordinal INTEGER NOT NULL,
                zh_unit_id TEXT NOT NULL,
                en_unit_id TEXT NOT NULL,
                zh_text TEXT NOT NULL,
                en_text TEXT NOT NULL,
                zh_normalized TEXT NOT NULL,
                en_normalized TEXT NOT NULL,
                alignment_unit TEXT NOT NULL,
                method TEXT NOT NULL,
                confidence REAL NOT NULL
            )
            """
        )

    def add_result(self, result: ImportResult) -> None:
        self.counts["file_count"] += len(result.source_file_ids)
        self.counts["document_count"] += len(result.documents)
        self.counts["paragraph_count"] += len(result.paragraphs)
        self.counts["sentence_count"] += len(result.sentences)
        self.counts["token_count"] += len(result.tokens)
        self.counts["parallel_pair_count"] += len(result.parallel_pairs)
        self.warnings.extend(result.warnings)

        for key in ("documents", "paragraphs", "sentences"):
            for record in getattr(result, key):
                self._write_jsonl(key, record_dict(record))
        for pair in result.parallel_pairs:
            self._write_parallel_pair(pair)
        for token in result.tokens:
            self._write_token(token)
        self._write_ngrams(result.tokens)

    def finalize(
        self,
        *,
        corpus_meta: dict[str, Any],
        source_files: list[dict[str, Any]],
        importer_name: str,
    ) -> dict[str, Any]:
        self.counts["type_count"] = len(self._frequency)
        self._close_streams()
        self._finalize_sqlite()

        documentation = {
            "schema_version": SCHEMA_VERSION,
            **self.counts,
            "segmentation_tool": "regex-baseline-v1",
            "importer": importer_name,
        }
        report = {
            "schema_version": SCHEMA_VERSION,
            "status": "success",
            "task_id": self.task_id,
            "corpus_id": self.corpus_id,
            "importer": importer_name,
            "source_files": source_files,
            "counts": self.counts,
            "warnings": self.warnings,
        }
        self._write_json(self.processed_staging / "meta.json", {"schema_version": SCHEMA_VERSION, **corpus_meta})
        self._write_json(self.processed_staging / "documentation.json", documentation)
        self._write_json(self.processed_staging / "processing_report.json", report)
        self._write_index_artifacts()
        self._publish()
        return report

    def abort(self) -> None:
        self._close_streams()
        if self._sqlite is not None:
            self._sqlite.close()
            self._sqlite = None
        for path in (self.processed_staging, self.index_staging):
            if path.exists():
                shutil.rmtree(path)

    def _write_token(self, token: TokenRecord) -> None:
        self._write_jsonl("tokens", record_dict(token))
        self._global_position += 1
        self._frequency[token.normalized] += 1
        if self._sqlite is None:
            raise RuntimeError("ArtifactWriter is not open.")
        self._sqlite.execute(
            """
            INSERT INTO tokens (
                global_position, token_id, normalized, surface, lemma, pos,
                language, document_id, sentence_id, sentence_position, is_punctuation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._global_position,
                token.id,
                token.normalized,
                token.text,
                token.lemma,
                token.pos,
                token.language,
                token.document_id,
                token.sentence_id,
                token.ordinal,
                int(_is_punctuation(token.text)),
            ),
        )

    def _write_parallel_pair(self, pair: ParallelPairRecord) -> None:
        self._write_jsonl("parallel_pairs", record_dict(pair))
        self._parallel_position += 1
        if self._sqlite is None:
            raise RuntimeError("ArtifactWriter is not open.")
        self._sqlite.execute(
            """
            INSERT INTO parallel_pairs (
                global_position, pair_id, pair_ordinal, zh_unit_id,
                en_unit_id, zh_text, en_text, zh_normalized,
                en_normalized, alignment_unit, method, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._parallel_position,
                pair.id,
                pair.ordinal,
                pair.zh_unit_id,
                pair.en_unit_id,
                pair.zh_text,
                pair.en_text,
                pair.zh_text.casefold(),
                pair.en_text.casefold(),
                pair.alignment_unit,
                pair.method,
                pair.confidence,
            ),
        )

    def _write_ngrams(self, tokens: list[TokenRecord]) -> None:
        if self._sqlite is None:
            raise RuntimeError("ArtifactWriter is not open.")
        by_sentence: dict[str, list[TokenRecord]] = defaultdict(list)
        for token in tokens:
            by_sentence[token.sentence_id].append(token)
        counts: Counter[tuple[str, int, str, str, int]] = Counter()
        for sentence_tokens in by_sentence.values():
            ordered = sorted(sentence_tokens, key=lambda token: token.ordinal)
            if not ordered:
                continue
            language = ordered[0].language
            separator = "" if language == "zh" else " "
            for n in range(2, 6):
                for start in range(0, len(ordered) - n + 1):
                    window = ordered[start : start + n]
                    normalized = "\x1f".join(token.normalized for token in window)
                    display = separator.join(token.text for token in window)
                    contains_punctuation = int(
                        any(_is_punctuation(token.text) for token in window)
                    )
                    counts[(language, n, normalized, display, contains_punctuation)] += 1
        self._sqlite.executemany(
            """
            INSERT INTO ngrams (
                language, n, normalized, display, contains_punctuation, frequency
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(language, n, normalized)
            DO UPDATE SET frequency = frequency + excluded.frequency
            """,
            [(*key, frequency) for key, frequency in counts.items()],
        )

    def _write_jsonl(self, key: str, payload: dict[str, Any]) -> None:
        handle = self._handles.get(key)
        if handle is None:
            raise RuntimeError("ArtifactWriter is not open.")
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")

    def _close_streams(self) -> None:
        for handle in self._handles.values():
            if not handle.closed:
                handle.close()
        self._handles.clear()

    def _finalize_sqlite(self) -> None:
        if self._sqlite is None:
            raise RuntimeError("ArtifactWriter is not open.")
        self._sqlite.executescript(
            """
            CREATE TABLE word_totals AS
            SELECT language,
                   normalized,
                   MIN(surface) AS display,
                   COUNT(*) AS frequency,
                   COUNT(DISTINCT document_id) AS document_range,
                   MIN(is_punctuation) AS is_punctuation
            FROM tokens
            GROUP BY language, normalized;

            CREATE TABLE word_frequencies AS
            SELECT language,
                   normalized,
                   MIN(surface) AS display,
                   pos,
                   COUNT(*) AS frequency,
                   COUNT(DISTINCT document_id) AS document_range,
                   MIN(is_punctuation) AS is_punctuation
            FROM tokens
            GROUP BY language, normalized, pos;

            CREATE INDEX idx_tokens_normalized_position
                ON tokens(normalized, global_position);
            CREATE INDEX idx_tokens_language_normalized
                ON tokens(language, normalized);
            CREATE INDEX idx_tokens_language_pos
                ON tokens(language, pos);
            CREATE INDEX idx_tokens_document_position
                ON tokens(document_id, global_position);
            CREATE INDEX idx_tokens_sentence_position
                ON tokens(sentence_id, sentence_position);
            CREATE INDEX idx_parallel_pairs_unit_position
                ON parallel_pairs(alignment_unit, global_position);
            CREATE INDEX idx_ngrams_language_n_frequency
                ON ngrams(language, n, frequency DESC, normalized);
            CREATE UNIQUE INDEX idx_word_totals_language_normalized
                ON word_totals(language, normalized);
            CREATE INDEX idx_word_totals_language_frequency
                ON word_totals(language, frequency DESC, normalized);
            CREATE UNIQUE INDEX idx_word_frequencies_language_normalized_pos
                ON word_frequencies(language, normalized, pos);
            CREATE INDEX idx_word_frequencies_language_pos_frequency
                ON word_frequencies(language, pos, frequency DESC, normalized);
            """
        )
        self._sqlite.commit()
        self._sqlite.close()
        self._sqlite = None

    def _write_index_artifacts(self) -> None:
        frequency = [
            {"token": token, "frequency": count}
            for token, count in sorted(self._frequency.items(), key=lambda item: (-item[1], item[0]))
        ]
        self._write_json(
            self.index_staging / "token_position_index",
            {
                "schema_version": SCHEMA_VERSION,
                "storage": "kwic_index.sqlite",
                "index_name": "idx_tokens_normalized_position",
            },
        )
        self._write_json(
            self.index_staging / "word_frequency.json",
            {
                "schema_version": SCHEMA_VERSION,
                "status": "ready",
                "storage": "kwic_index.sqlite",
                "tables": ["word_totals", "word_frequencies"],
                "items": frequency,
            },
        )
        self._write_json(
            self.index_staging / "ngram_frequency.json",
            {
                "schema_version": SCHEMA_VERSION,
                "status": "ready",
                "storage": "kwic_index.sqlite",
                "table": "ngrams",
                "n_values": [2, 3, 4, 5],
            },
        )
        self._write_json(
            self.index_staging / "collocate_cache.json",
            {
                "schema_version": SCHEMA_VERSION,
                "status": "dynamic",
                "storage": "kwic_index.sqlite",
            },
        )
        self._write_json(
            self.index_staging / "concordance_plot.json",
            {
                "schema_version": SCHEMA_VERSION,
                "status": "dynamic",
                "storage": "kwic_index.sqlite",
                "bins_per_document": 100,
            },
        )
        self._write_json(
            self.index_staging / "wordcloud_terms.json",
            {
                "schema_version": SCHEMA_VERSION,
                "status": "ready",
                "storage": "kwic_index.sqlite",
                "table": "word_totals",
            },
        )
        for filename in DEFERRED_INDEX_FILES:
            self._write_json(
                self.index_staging / filename,
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "deferred",
                    "items": [],
                },
            )

    def _publish(self) -> None:
        pairs = (
            (self.processed_staging, self.processed_output),
            (self.index_staging, self.index_output),
        )
        backups: list[tuple[Path, Path]] = []
        published: list[Path] = []
        try:
            for _, target_dir in pairs:
                target_dir.parent.mkdir(parents=True, exist_ok=True)
                backup = target_dir.parent / f".backup-{target_dir.name}-{self.task_id}"
                if backup.exists():
                    shutil.rmtree(backup)
                if target_dir.exists():
                    os.replace(target_dir, backup)
                    backups.append((backup, target_dir))
            for source_dir, target_dir in pairs:
                os.replace(source_dir, target_dir)
                published.append(target_dir)
        except Exception:
            for target_dir in reversed(published):
                if target_dir.exists():
                    shutil.rmtree(target_dir)
            for backup, target_dir in reversed(backups):
                if backup.exists():
                    os.replace(backup, target_dir)
            raise
        else:
            for backup, _ in backups:
                if backup.exists():
                    shutil.rmtree(backup)

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _is_punctuation(value: str) -> bool:
    return bool(value) and all(
        unicodedata.category(character).startswith(("P", "S")) for character in value
    )
