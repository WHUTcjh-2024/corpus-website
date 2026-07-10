from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.corpora.models import CorpusAccessLevel, CorpusSourceType
from apps.corpora.services import register_manifest_corpus


class Command(BaseCommand):
    help = "从阶段 1 JSON manifest 中选择一条记录，登记为教师或 demo 语料库。"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--manifest", required=True, type=Path)
        parser.add_argument("--file-id", required=True)
        parser.add_argument(
            "--source-type",
            required=True,
            choices=[CorpusSourceType.TEACHER, CorpusSourceType.DEMO],
        )
        parser.add_argument(
            "--access-level",
            required=True,
            choices=[
                CorpusAccessLevel.DEMO,
                CorpusAccessLevel.JUNIOR,
                CorpusAccessLevel.MIDDLE,
                CorpusAccessLevel.ADVANCED,
            ],
        )
        parser.add_argument("--name")

    def handle(self, *args, **options) -> None:
        try:
            corpus, created = register_manifest_corpus(
                manifest_path=options["manifest"],
                file_id=options["file_id"],
                source_type=options["source_type"],
                access_level=options["access_level"],
                name=options["name"],
            )
        except (FileNotFoundError, LookupError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        action = "created" if created else "updated"
        self.stdout.write(
            self.style.SUCCESS(f"Corpus {action}: {corpus.name} ({corpus.pk})")
        )
