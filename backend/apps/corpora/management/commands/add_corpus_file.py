from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.corpora.models import Corpus, CorpusLanguage, CorpusType
from apps.corpora.services import CorpusFileData, register_corpus_file


class Command(BaseCommand):
    help = "为已登记语料库添加只读 txt/tsv 源文件元数据。"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--corpus-id", required=True)
        parser.add_argument("--path", required=True, type=Path)
        parser.add_argument("--detected-type", required=True, choices=CorpusType.values)
        parser.add_argument("--language", required=True, choices=CorpusLanguage.values)
        parser.add_argument("--encoding", default="")

    def handle(self, *args, **options) -> None:
        try:
            corpus = Corpus.objects.get(pk=options["corpus_id"])
            corpus_file, created = register_corpus_file(
                corpus=corpus,
                data=CorpusFileData(
                    path=options["path"],
                    detected_type=options["detected_type"],
                    language=options["language"],
                    encoding=options["encoding"],
                ),
            )
        except Corpus.DoesNotExist as exc:
            raise CommandError("Corpus does not exist.") from exc
        except (FileNotFoundError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        action = "created" if created else "updated"
        self.stdout.write(
            self.style.SUCCESS(f"CorpusFile {action}: {corpus_file.pk} {corpus_file.stored_path}")
        )
