from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.corpora.models import Corpus, CorpusStatus
from apps.processing.exceptions import ProcessingAlreadyQueued, ProcessingError
from apps.processing.index_health import inspect_corpus_index
from apps.processing.models import ProcessingTaskStatus
from apps.processing.services import (
    create_processing_task,
    dispatch_processing_task,
    process_task,
)


class Command(BaseCommand):
    help = "检查语料索引并批量修复缺失、旧版或损坏的索引。"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--corpus-id",
            action="append",
            dest="corpus_ids",
            help="只检查指定语料；可重复传入。",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="在当前进程同步重建，适合部署升级和本地验收。",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="仅输出健康状态，不创建修复任务。",
        )

    def handle(self, *args, **options) -> None:
        corpora = Corpus.objects.filter(status=CorpusStatus.READY).order_by("name", "pk")
        if options["corpus_ids"]:
            corpora = corpora.filter(pk__in=options["corpus_ids"])

        checked = repaired = queued = skipped = failed = 0
        for corpus in corpora:
            checked += 1
            health = inspect_corpus_index(str(corpus.pk))
            self.stdout.write(
                f"{corpus.pk}  {health.state.value:<8}  {corpus.name}"
                + (f"  ({health.detail})" if health.detail else "")
            )
            if health.is_ready:
                skipped += 1
                continue
            if options["dry_run"]:
                continue
            try:
                task = create_processing_task(corpus=corpus)
                if options["sync"]:
                    process_task(task.pk)
                    verified = inspect_corpus_index(str(corpus.pk))
                    if not verified.is_ready:
                        raise ProcessingError(
                            f"重建后索引仍未通过健康检查：{verified.detail}"
                        )
                    repaired += 1
                    self.stdout.write(
                        self.style.SUCCESS(f"  repaired task={task.pk}")
                    )
                else:
                    async_result = dispatch_processing_task(task)
                    queued += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  queued task={task.pk} celery={async_result.id}"
                        )
                    )
            except ProcessingAlreadyQueued:
                active = corpus.processing_tasks.filter(
                    status__in=[
                        ProcessingTaskStatus.PENDING,
                        ProcessingTaskStatus.RUNNING,
                    ]
                ).first()
                queued += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  already queued task={active.pk if active else 'unknown'}"
                    )
                )
            except ProcessingError as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"  failed: {exc}"))

        summary = (
            f"checked={checked}, healthy={skipped}, repaired={repaired}, "
            f"queued={queued}, failed={failed}"
        )
        if failed:
            raise CommandError(summary)
        self.stdout.write(self.style.SUCCESS(summary))
