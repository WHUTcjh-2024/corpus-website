from django.core.management.base import BaseCommand, CommandError

from apps.corpora.models import Corpus
from apps.processing.exceptions import ProcessingError
from apps.processing.services import (
    create_processing_task,
    dispatch_processing_task,
    process_task,
)


class Command(BaseCommand):
    help = "创建语料加工任务；默认发送到 Celery，--sync 用于本地验收。"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--corpus-id", required=True)
        parser.add_argument(
            "--sync",
            action="store_true",
            help="在当前进程同步执行，仅用于开发和验收。",
        )

    def handle(self, *args, **options) -> None:
        try:
            corpus = Corpus.objects.get(pk=options["corpus_id"])
            task = create_processing_task(corpus=corpus)
            if options["sync"]:
                report = process_task(task.pk)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Processing success: task={task.pk}, counts={report['counts']}"
                    )
                )
            else:
                async_result = dispatch_processing_task(task)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Processing queued: task={task.pk}, celery={async_result.id}"
                    )
                )
        except Corpus.DoesNotExist as exc:
            raise CommandError("Corpus does not exist.") from exc
        except ProcessingError as exc:
            raise CommandError(str(exc)) from exc
