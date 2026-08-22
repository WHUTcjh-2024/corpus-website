import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.rag.evaluation import evaluate_retrieval_cases, load_evaluation_cases
from apps.rag.providers import OpenAICompatibleEmbeddingProvider
from apps.rag.retrieval import HybridRagIndex


class Command(BaseCommand):
    help = "Evaluate a corpus Hybrid RAG index with fixed citation relevance cases."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--corpus-id", required=True)
        parser.add_argument("--cases", required=True, help="Path to a JSON evaluation case array.")
        parser.add_argument("--top-k", type=int, default=5)
        parser.add_argument("--min-recall", type=float, default=0.0)
        parser.add_argument("--min-mrr", type=float, default=0.0)

    def handle(self, *args, **options) -> None:
        if not 0 <= options["min_recall"] <= 1 or not 0 <= options["min_mrr"] <= 1:
            raise CommandError("--min-recall and --min-mrr must be between 0 and 1.")
        cases = load_evaluation_cases(Path(options["cases"]))
        index = HybridRagIndex(data_root=settings.DATA_ROOT, corpus_id=str(options["corpus_id"]))
        provider = OpenAICompatibleEmbeddingProvider.from_settings()
        report = evaluate_retrieval_cases(
            cases=cases,
            top_k=options["top_k"],
            retrieve=lambda query, language, top_k: index.search(
                query=query,
                language=language,
                max_results=top_k,
                provider=provider,
            ),
        )
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
        summary = report["summary"]
        if summary["recall_at_k"] < options["min_recall"] or summary["mrr"] < options["min_mrr"]:
            raise CommandError("RAG retrieval quality gate did not meet the configured threshold.")
