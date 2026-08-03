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
        self._frequency: Counter[tuple[str, str]] = Counter()
        self._global_position = 0
        self._stream_positions: defaultdict[tuple[str, str], int] = defaultdict(int)
        self._token_document_offsets: dict[str, tuple[int, int]] = {}
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
            CREATE TABLE documents (
                document_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                language TEXT NOT NULL
            )
            """
        )
        self._sqlite.execute(
            """
            CREATE TABLE document_streams (
                document_id TEXT NOT NULL,
                language TEXT NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY (document_id, language)
            )
            """
        )
        self._sqlite.execute(
            """
            CREATE TABLE tokens (
                global_position INTEGER PRIMARY KEY,
                stream_position INTEGER NOT NULL,
                token_id TEXT NOT NULL UNIQUE,
                normalized TEXT NOT NULL,
                surface TEXT NOT NULL,
                lemma TEXT NOT NULL,
                pos TEXT NOT NULL,
                language TEXT NOT NULL,
                document_id TEXT NOT NULL,
                sentence_id TEXT NOT NULL,
                sentence_position INTEGER NOT NULL,
                document_start INTEGER NOT NULL,
                document_end INTEGER NOT NULL,
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
                document_range INTEGER NOT NULL DEFAULT 0,
                contains_punctuation INTEGER NOT NULL,
                PRIMARY KEY (language, n, normalized)
            )
            """
        )
        self._sqlite.execute(
            """
            CREATE TABLE ngram_documents (
                language TEXT NOT NULL,
                n INTEGER NOT NULL,
                normalized TEXT NOT NULL,
                document_id TEXT NOT NULL,
                PRIMARY KEY (language, n, normalized, document_id)
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
                zh_document_id TEXT NOT NULL DEFAULT '',
                en_document_id TEXT NOT NULL DEFAULT '',
                zh_filename TEXT NOT NULL DEFAULT '',
                en_filename TEXT NOT NULL DEFAULT '',
                zh_token_spans TEXT NOT NULL DEFAULT '[]',
                en_token_spans TEXT NOT NULL DEFAULT '[]',
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

        self._write_document_streams(result)

        for key in ("documents", "paragraphs", "sentences"):
            for record in getattr(result, key):
                self._write_jsonl(key, record_dict(record))
        if self._sqlite is None:
            raise RuntimeError("ArtifactWriter is not open.")
        self._sqlite.executemany(
            """
            INSERT INTO documents (document_id, filename, language)
            VALUES (?, ?, ?)
            """,
            [
                (document.id, document.filename, document.language)
                for document in result.documents
            ],
        )
        document_filenames = {
            document.id: document.filename for document in result.documents
        }
        unit_documents = {
            unit.id: unit.document_id
            for unit in (*result.paragraphs, *result.sentences)
        }
        tokens_by_sentence: defaultdict[str, list[TokenRecord]] = defaultdict(list)
        for token in result.tokens:
            tokens_by_sentence[token.sentence_id].append(token)
        sentence_ids_by_paragraph: defaultdict[str, list[str]] = defaultdict(list)
        for sentence in result.sentences:
            sentence_ids_by_paragraph[sentence.paragraph_id].append(sentence.id)
        unit_tokens: dict[str, list[TokenRecord]] = {
            sentence.id: sorted(
                tokens_by_sentence[sentence.id],
                key=lambda token: token.ordinal,
            )
            for sentence in result.sentences
        }
        for paragraph in result.paragraphs:
            unit_tokens[paragraph.id] = [
                token
                for sentence_id in sentence_ids_by_paragraph[paragraph.id]
                for token in sorted(
                    tokens_by_sentence[sentence_id],
                    key=lambda item: item.ordinal,
                )
            ]
        for pair in result.parallel_pairs:
            zh_document_id = unit_documents.get(pair.zh_unit_id, "")
            en_document_id = unit_documents.get(pair.en_unit_id, "")
            self._write_parallel_pair(
                pair,
                zh_document_id=zh_document_id,
                en_document_id=en_document_id,
                zh_filename=document_filenames.get(zh_document_id, ""),
                en_filename=document_filenames.get(en_document_id, ""),
                zh_token_spans=_serialize_token_spans(
                    pair.zh_text,
                    unit_tokens.get(pair.zh_unit_id, []),
                ),
                en_token_spans=_serialize_token_spans(
                    pair.en_text,
                    unit_tokens.get(pair.en_unit_id, []),
                ),
            )
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
            "segmentation_tool": _segmentation_tool(importer_name),
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
        stream_key = (token.document_id, token.language)
        self._stream_positions[stream_key] += 1
        self._frequency[(token.language, token.normalized)] += 1
        if self._sqlite is None:
            raise RuntimeError("ArtifactWriter is not open.")
        self._sqlite.execute(
            """
            INSERT INTO tokens (
                global_position, stream_position, token_id, normalized, surface, lemma, pos,
                language, document_id, sentence_id, sentence_position,
                document_start, document_end, is_punctuation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._global_position,
                self._stream_positions[stream_key],
                token.id,
                token.normalized,
                token.text,
                token.lemma,
                token.pos,
                token.language,
                token.document_id,
                token.sentence_id,
                token.ordinal,
                *self._token_document_offsets.get(token.id, (0, 0)),
                int(_is_punctuation(token.text)),
            ),
        )

    def _write_document_streams(self, result: ImportResult) -> None:
        if self._sqlite is None:
            raise RuntimeError("ArtifactWriter is not open.")
        sentences: defaultdict[tuple[str, str], list[Any]] = defaultdict(list)
        tokens: defaultdict[str, list[TokenRecord]] = defaultdict(list)
        for sentence in result.sentences:
            sentences[(sentence.document_id, sentence.language)].append(sentence)
        for token in result.tokens:
            tokens[token.sentence_id].append(token)

        for (document_id, language), stream_sentences in sentences.items():
            parts: list[str] = []
            cursor = 0
            for sentence in sorted(stream_sentences, key=lambda item: item.ordinal):
                if parts:
                    parts.append("\n")
                    cursor += 1
                sentence_start = cursor
                parts.append(sentence.text)
                cursor += len(sentence.text)
                for token in tokens.get(sentence.id, ()):
                    self._token_document_offsets[token.id] = (
                        sentence_start + token.start,
                        sentence_start + token.end,
                    )
            self._sqlite.execute(
                """
                INSERT INTO document_streams (document_id, language, text)
                VALUES (?, ?, ?)
                """,
                (document_id, language, "".join(parts)),
            )

    def _write_parallel_pair(
        self,
        pair: ParallelPairRecord,
        *,
        zh_document_id: str,
        en_document_id: str,
        zh_filename: str,
        en_filename: str,
        zh_token_spans: str,
        en_token_spans: str,
    ) -> None:
        self._write_jsonl("parallel_pairs", record_dict(pair))
        self._parallel_position += 1
        if self._sqlite is None:
            raise RuntimeError("ArtifactWriter is not open.")
        self._sqlite.execute(
            """
            INSERT INTO parallel_pairs (
                global_position, pair_id, pair_ordinal, zh_unit_id,
                en_unit_id, zh_text, en_text, zh_normalized,
                en_normalized, zh_document_id, en_document_id,
                zh_filename, en_filename, zh_token_spans, en_token_spans,
                alignment_unit, method, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                zh_document_id,
                en_document_id,
                zh_filename,
                en_filename,
                zh_token_spans,
                en_token_spans,
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
        documents: set[tuple[str, int, str, str]] = set()
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
                    documents.add((language, n, normalized, window[0].document_id))
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
        self._sqlite.executemany(
            """
            INSERT OR IGNORE INTO ngram_documents (
                language, n, normalized, document_id
            ) VALUES (?, ?, ?, ?)
            """,
            documents,
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

            UPDATE ngrams
            SET document_range = (
                SELECT COUNT(*)
                FROM ngram_documents
                WHERE ngram_documents.language = ngrams.language
                  AND ngram_documents.n = ngrams.n
                  AND ngram_documents.normalized = ngrams.normalized
            );

            DROP TABLE ngram_documents;

            CREATE INDEX idx_tokens_normalized_position
                ON tokens(normalized, global_position);
            CREATE INDEX idx_tokens_language_normalized
                ON tokens(language, normalized);
            CREATE INDEX idx_tokens_language_pos
                ON tokens(language, pos);
            CREATE INDEX idx_tokens_document_position
                ON tokens(document_id, global_position);
            CREATE UNIQUE INDEX idx_tokens_document_language_stream
                ON tokens(document_id, language, stream_position);
            CREATE INDEX idx_tokens_document_language_chars
                ON tokens(document_id, language, document_start, document_end);
            CREATE INDEX idx_documents_filename
                ON documents(filename COLLATE NOCASE, document_id);
            CREATE INDEX idx_tokens_sentence_position
                ON tokens(sentence_id, sentence_position);
            CREATE INDEX idx_parallel_pairs_unit_position
                ON parallel_pairs(alignment_unit, global_position);
            CREATE INDEX idx_ngrams_language_n_frequency
                ON ngrams(language, n, frequency DESC, normalized);
            CREATE INDEX idx_ngrams_language_n_range
                ON ngrams(language, n, document_range DESC, normalized);
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
            {"language": language, "token": token, "frequency": count}
            for (language, token), count in sorted(
                self._frequency.items(),
                key=lambda item: (-item[1], item[0][0], item[0][1]),
            )
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


def _serialize_token_spans(text: str, tokens: list[TokenRecord]) -> str:
    spans: list[tuple[str, int, int]] = []
    position = 0
    for token in tokens:
        start = text.find(token.text, position)
        if start < 0:
            continue
        end = start + len(token.text)
        spans.append((token.text, start, end))
        position = end
    return json.dumps(spans, ensure_ascii=False, separators=(",", ":"))


def _is_punctuation(value: str) -> bool:
    return bool(value) and all(
        unicodedata.category(character).startswith(("P", "S")) for character in value
    )


def _segmentation_tool(importer_name: str) -> str:
    if "tagged" in importer_name:
        return "source-provided-pos-v1"
    return "zh:jieba-0.42.1;en:unicode-regex-v1"
