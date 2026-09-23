from __future__ import annotations

import uuid
import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.processing.storage_compaction import publish_compact_artifacts


class Command(BaseCommand):
    help = "Publish a verified compact pilot, retaining recoverable old artifacts."

    def add_arguments(self, parser) -> None:
        parser.add_argument("corpus_id")
        parser.add_argument("--pilot-root", required=True, type=Path)

    def handle(self, *args, **options) -> None:
        try:
            corpus_id = str(uuid.UUID(options["corpus_id"]))
        except ValueError as exc:
            raise CommandError("corpus_id must be a UUID") from exc
        try:
            backup = publish_compact_artifacts(
                data_root=Path(settings.DATA_ROOT).resolve(),
                pilot_root=options["pilot_root"].resolve(),
                corpus_id=corpus_id,
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"published={corpus_id} old_index_backup={backup} "
            "old_tokens=tokens.jsonl (retained)"
        )
