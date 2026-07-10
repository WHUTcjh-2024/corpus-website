from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.corpus_intake.manifest import write_manifest
from apps.corpus_intake.scanner import scan_inbox


class Command(BaseCommand):
    help = "Scan DATA_ROOT/inbox and generate corpus manifest CSV/JSON files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--inbox",
            type=Path,
            default=None,
            help="Directory to scan. Defaults to DATA_ROOT/inbox.",
        )
        parser.add_argument(
            "--output-dir",
            type=Path,
            default=None,
            help="Directory for corpus_manifest.csv/json. Defaults to DATA_ROOT/manifests.",
        )

    def handle(self, *args, **options):
        inbox = (options["inbox"] or (settings.DATA_ROOT / "inbox")).resolve()
        output_dir = (options["output_dir"] or (settings.DATA_ROOT / "manifests")).resolve()

        try:
            scan_result = scan_inbox(inbox)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise CommandError(str(exc)) from exc

        csv_path, json_path = write_manifest(scan_result, output_dir)
        summary = scan_result.summary

        self.stdout.write(self.style.SUCCESS("Corpus inbox scan complete."))
        self.stdout.write(f"Inbox: {inbox}")
        self.stdout.write(f"CSV: {csv_path}")
        self.stdout.write(f"JSON: {json_path}")
        self.stdout.write(f"Total files: {summary['total_files']}")
        self.stdout.write(f"Total size bytes: {summary['total_size_bytes']}")
        self.stdout.write(f"Probable pairs: {summary['probable_pair_count']}")
        self.stdout.write(f"Unknown files: {summary['unknown_file_count']}")
        self.stdout.write(f"Type counts: {summary['type_counts']}")
