from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.corpora.models import CorpusAccessLevel
from apps.processing.services import create_processing_task, process_task

from ...formal_import import register_formal_teacher_corpora
from ...manifest import write_manifest
from ...scanner import scan_inbox


class Command(BaseCommand):
    help = "扫描、登记并可同步加工老师交付的完整正式语料。"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source-root", required=True, type=Path)
        parser.add_argument(
            "--access-level",
            choices=(
                CorpusAccessLevel.JUNIOR,
                CorpusAccessLevel.MIDDLE,
                CorpusAccessLevel.ADVANCED,
            ),
            default=CorpusAccessLevel.ADVANCED,
        )
        parser.add_argument(
            "--name-prefix",
            default="马克思主义中国化经典文献",
        )
        parser.add_argument(
            "--process",
            action="store_true",
            help="登记后在当前进程同步构建全部检索索引。",
        )

    def handle(self, *args, **options) -> None:
        source_root = options["source_root"].resolve()
        if not source_root.is_dir():
            raise CommandError(f"正式语料目录不存在：{source_root}")

        scan_result = scan_inbox(source_root)
        manifest_dir = settings.DATA_ROOT / "manifests" / "formal_teacher_corpus"
        csv_path, json_path = write_manifest(scan_result, manifest_dir)
        try:
            registration = register_formal_teacher_corpora(
                scan_result=scan_result,
                access_level=options["access_level"],
                name_prefix=options["name_prefix"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"Manifest CSV: {csv_path}")
        self.stdout.write(f"Manifest JSON: {json_path}")
        self.stdout.write(f"Registered files: {registration.registered_file_count}")
        self.stdout.write(f"Quarantined files: {len(registration.quarantined_records)}")
        for record in registration.quarantined_records:
            self.stdout.write(f"Quarantined: {record.original_path} ({record.notes})")

        for corpus in registration.corpora:
            self.stdout.write(
                f"Registered corpus: {corpus.name} ({corpus.pk}); files={corpus.files.count()}"
            )
            if options["process"]:
                task = create_processing_task(corpus=corpus)
                report = process_task(task.pk)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Processed corpus: {corpus.name}; counts={report['counts']}"
                    )
                )
        self.stdout.write(self.style.SUCCESS("正式教师语料登记完成。"))
