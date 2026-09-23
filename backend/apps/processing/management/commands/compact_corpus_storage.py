from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.processing.storage_compaction import compact_index, compress_token_archive


class Command(BaseCommand):
    help = "Build an isolated, verified compact copy of one existing corpus."

    def add_arguments(self, parser) -> None:
        parser.add_argument("corpus_id")
        parser.add_argument("--output-root", required=True, type=Path)

    def handle(self, *args, **options) -> None:
        try:
            corpus_id = str(uuid.UUID(options["corpus_id"]))
        except ValueError as exc:
            raise CommandError("corpus_id must be a UUID") from exc
        data_root = Path(settings.DATA_ROOT).resolve()
        output_root = options["output_root"].resolve()
        source_index = data_root / "indexes" / corpus_id / "kwic_index.sqlite"
        source_tokens = data_root / "processed" / corpus_id / "tokens.jsonl"
        target_index = output_root / "indexes" / corpus_id / "kwic_index.sqlite"
        target_tokens = output_root / "processed" / corpus_id / "tokens.jsonl.gz"
        if not source_index.is_file() or not source_tokens.is_file():
            raise CommandError("The corpus needs a legacy SQLite index and tokens.jsonl")
        if target_index.exists() or target_tokens.exists():
            raise CommandError("The output paths already exist")
        if output_root == data_root or data_root in output_root.parents and (
            output_root.parts[len(data_root.parts)] in {"indexes", "processed"}
        ):
            raise CommandError("Output root must not overlap published artifacts")
        try:
            counts = compact_index(source_index, target_index)
            compressed_size = compress_token_archive(source_tokens, target_tokens)
        except (OSError, sqlite3.Error, ValueError) as exc:
            target_index.unlink(missing_ok=True)
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"corpus={corpus_id} tokens={counts['tokens']} "
            f"ngrams={counts['ngrams']} "
            f"index_bytes={target_index.stat().st_size} "
            f"archive_bytes={compressed_size} output={output_root}"
        )
