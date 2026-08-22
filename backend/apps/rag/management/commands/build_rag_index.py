from django.core.management.base import BaseCommand, CommandError

from apps.corpora.models import Corpus
from apps.processing.models import ProcessingTask, ProcessingTaskStatus
from apps.rag.services import build_rag_index, queue_rag_index


class Command(BaseCommand):
    help = "Build a versioned dense-vector RAG index for a processed corpus."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--corpus-id", required=True)

    def handle(self, *args, **options) -> None:
        try:
            corpus = Corpus.objects.get(pk=options["corpus_id"])
        except (Corpus.DoesNotExist, ValueError) as exc:
            raise CommandError("--corpus-id does not identify a corpus.") from exc
        task = (
            ProcessingTask.objects.filter(corpus=corpus, status=ProcessingTaskStatus.SUCCESS)
            .order_by("-finished_at", "-created_at")
            .first()
        )
        if task is None:
            raise CommandError("The corpus must have a completed processing task.")
        # This command is intentionally synchronous.  The normal processing
        # path uses schedule=True and the durable Outbox event; doing both here
        # would let a worker race the operator's direct invocation.
        index = queue_rag_index(corpus=corpus, processing_task=task, schedule=False)
        result = build_rag_index(str(index.pk))
        self.stdout.write(self.style.SUCCESS(str(result)))
