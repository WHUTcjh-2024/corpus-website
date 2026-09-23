"""Build smaller, query-compatible copies of existing corpus artifacts.

All work happens in caller-supplied destination paths. The published corpus is
never modified by these functions.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path


TABLES = (
    "documents",
    "document_streams",
    "tokens",
    "ngrams",
    "parallel_pairs",
    "word_totals",
    "word_frequencies",
)
TOKEN_COLUMNS = (
    "global_position", "stream_position", "normalized", "surface", "lemma",
    "pos", "language", "document_id", "sentence_id", "sentence_position",
    "document_start", "document_end", "is_punctuation",
)
UNUSED_INDEXES = {"idx_tokens_document_language_chars"}


def compact_index(source: Path, destination: Path) -> dict[str, int]:
    """Copy a v2.2 index with compact token and n-gram tables.

    The source is attached read-only. A failed build removes only the incomplete
    destination, so retrying cannot damage a live index.
    """
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(destination, uri=True)) as connection:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("ATTACH DATABASE ? AS old", (f"{source.as_uri()}?mode=ro",))
            table_sql = {
                name: sql
                for name, sql in connection.execute(
                    "SELECT name, sql FROM old.sqlite_master WHERE type='table'"
                )
                if name in TABLES
            }
            if set(table_sql) != set(TABLES):
                raise ValueError("Index does not contain the expected v2.2 tables")
            token_sql = table_sql["tokens"]
            if "token_id TEXT NOT NULL UNIQUE," in token_sql:
                token_sql = token_sql.replace("token_id TEXT NOT NULL UNIQUE,", "")
            elif "token_id" in token_sql:
                raise ValueError("Unsupported token_id schema")
            table_sql["tokens"] = token_sql
            if "WITHOUT ROWID" not in table_sql["ngrams"].upper():
                table_sql["ngrams"] += " WITHOUT ROWID"

            counts: dict[str, int] = {}
            for table in TABLES:
                connection.execute(table_sql[table])
                if table == "tokens":
                    columns = ", ".join(TOKEN_COLUMNS)
                    connection.execute(
                        f"INSERT INTO main.tokens ({columns}) "
                        f"SELECT {columns} FROM old.tokens ORDER BY global_position"
                    )
                elif table == "ngrams":
                    connection.execute(
                        "INSERT INTO main.ngrams "
                        "SELECT * FROM old.ngrams ORDER BY language, n, normalized"
                    )
                else:
                    connection.execute(f"INSERT INTO main.{table} SELECT * FROM old.{table}")
                old_count = connection.execute(
                    f"SELECT COUNT(*) FROM old.{table}"
                ).fetchone()[0]
                new_count = connection.execute(
                    f"SELECT COUNT(*) FROM main.{table}"
                ).fetchone()[0]
                if old_count != new_count:
                    raise ValueError(f"{table}: source/destination row count differs")
                counts[table] = int(new_count)

            indexes = connection.execute(
                "SELECT name, sql FROM old.sqlite_master "
                "WHERE type='index' AND sql IS NOT NULL ORDER BY name"
            ).fetchall()
            for name, sql in indexes:
                if name not in UNUSED_INDEXES:
                    connection.execute(sql)
            connection.commit()
            if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise ValueError("Compacted index failed SQLite quick_check")
        return counts
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def compress_token_archive(source: Path, destination: Path) -> int:
    """Create and round-trip-verify a gzip archive of the legacy token JSONL."""
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_hash = hashlib.sha256()
    try:
        with source.open("rb") as raw, gzip.open(destination, "wb", compresslevel=6) as archive:
            while chunk := raw.read(1024 * 1024):
                archive.write(chunk)
                source_hash.update(chunk)
        archive_hash = hashlib.sha256()
        with gzip.open(destination, "rb") as archive:
            while chunk := archive.read(1024 * 1024):
                archive_hash.update(chunk)
        if archive_hash.digest() != source_hash.digest():
            raise ValueError("Token archive round-trip checksum differs")
        return destination.stat().st_size
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def publish_compact_artifacts(
    *, data_root: Path, pilot_root: Path, corpus_id: str
) -> Path:
    """Atomically publish a verified pilot, retaining a hard-linked index backup.

    The old uncompressed token file also remains until the caller completes live
    verification. The returned backup path allows an immediate rollback.
    """
    live_index = data_root / "indexes" / corpus_id / "kwic_index.sqlite"
    live_tokens = data_root / "processed" / corpus_id / "tokens.jsonl"
    live_archive = live_tokens.with_suffix(".jsonl.gz")
    pilot_index = pilot_root / "indexes" / corpus_id / "kwic_index.sqlite"
    pilot_archive = pilot_root / "processed" / corpus_id / "tokens.jsonl.gz"
    if not all(path.is_file() for path in (live_index, live_tokens, pilot_index, pilot_archive)):
        raise FileNotFoundError("Published or pilot artifacts are missing")
    if live_archive.exists():
        raise FileExistsError(live_archive)
    if live_index.stat().st_mtime_ns > pilot_index.stat().st_mtime_ns:
        raise ValueError("Published index changed after the pilot was built")
    if live_tokens.stat().st_mtime_ns > pilot_archive.stat().st_mtime_ns:
        raise ValueError("Published tokens changed after the pilot was built")
    backup = live_index.with_name(f"kwic_index.sqlite.precompact-{uuid.uuid4().hex}")
    index_published = False
    archive_published = False
    os.link(live_index, backup)
    try:
        os.replace(pilot_index, live_index)
        index_published = True
        os.replace(pilot_archive, live_archive)
        archive_published = True
        from .index_health import inspect_corpus_index

        health = inspect_corpus_index(corpus_id, data_root=data_root)
        if not health.is_ready:
            raise ValueError(f"Published index is not ready: {health.state} {health.detail}")
    except BaseException:
        if archive_published:
            os.replace(live_archive, pilot_archive)
        if index_published:
            os.replace(live_index, pilot_index)
            os.replace(backup, live_index)
        else:
            backup.unlink(missing_ok=True)
        raise
    return backup
