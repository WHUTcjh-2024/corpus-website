from __future__ import annotations

import sqlite3
from contextlib import closing

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.corpora.models import (
    Corpus,
    CorpusLanguage,
    CorpusStatus,
    CorpusType,
)
from apps.parallel.engine import ParallelSearchEngine
from apps.search.kwic import KwicSearchEngine
from apps.statistics.engine import StatisticsEngine
from apps.processing.index_health import inspect_corpus_index


PARALLEL_TYPES = {
    CorpusType.ALIGNED_TSV,
    CorpusType.PAIRED_RAW_ZH_EN,
    CorpusType.PAIRED_TAGGED_ZH_EN,
}


class Command(BaseCommand):
    help = "对已加工语料执行索引完整性、KWIC、统计和平行检索端到端验收。"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--corpus-id",
            action="append",
            dest="corpus_ids",
            help="只验收指定语料；可重复传入。",
        )

    def handle(self, *args, **options) -> None:
        corpora = (
            Corpus.objects.filter(status=CorpusStatus.READY)
            .select_related("documentation")
            .order_by("name", "pk")
        )
        if options["corpus_ids"]:
            corpora = corpora.filter(pk__in=options["corpus_ids"])

        checked = 0
        failures: list[str] = []
        for corpus in corpora:
            checked += 1
            try:
                result = self._validate_corpus(corpus)
            except Exception as exc:
                failures.append(f"{corpus.pk} {corpus.name}: {exc}")
                self.stderr.write(self.style.ERROR(f"FAIL  {failures[-1]}"))
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"PASS  {corpus.pk}  {corpus.name}  "
                        f"tokens={result['tokens']} kwic={result['kwic']} "
                        f"types={result['types']} pairs={result['pairs']}"
                    )
                )

        if failures:
            raise CommandError(
                f"validated={checked}, passed={checked - len(failures)}, "
                f"failed={len(failures)}"
            )
        self.stdout.write(
            self.style.SUCCESS(f"validated={checked}, passed={checked}, failed=0")
        )

    def _validate_corpus(self, corpus: Corpus) -> dict[str, int]:
        health = inspect_corpus_index(str(corpus.pk))
        if not health.is_ready:
            raise RuntimeError(f"health={health.state.value}: {health.detail}")

        index_path = (
            settings.DATA_ROOT
            / "indexes"
            / str(corpus.pk)
            / "kwic_index.sqlite"
        )
        with closing(
            sqlite3.connect(f"{index_path.as_uri()}?mode=ro", uri=True)
        ) as connection:
            token_count = int(
                connection.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
            )
            pair_count = int(
                connection.execute("SELECT COUNT(*) FROM parallel_pairs").fetchone()[0]
            )
            sample = connection.execute(
                """
                SELECT language,
                       MIN(surface) AS surface,
                       normalized,
                       COUNT(*) AS frequency,
                       MIN(global_position) AS first_position
                FROM tokens
                WHERE is_punctuation = 0
                GROUP BY language, normalized
                ORDER BY frequency DESC, first_position
                LIMIT 1
                """
            ).fetchone()

        if token_count != corpus.documentation.token_count:
            raise RuntimeError(
                "token count mismatch: "
                f"sqlite={token_count}, documentation={corpus.documentation.token_count}"
            )
        if token_count and sample is None:
            raise RuntimeError("no searchable token sample")

        kwic_total = 0
        type_total = 0
        if sample is not None:
            language, surface, _normalized, expected_frequency, _first_position = sample
            kwic = KwicSearchEngine(
                data_root=settings.DATA_ROOT,
                corpus_id=str(corpus.pk),
            ).search(
                str(surface),
                language=str(language),
                page_size=20,
            )
            kwic_total = kwic.total
            if kwic_total != int(expected_frequency):
                raise RuntimeError(
                    "KWIC frequency mismatch: "
                    f"query={surface!r}, expected={expected_frequency}, actual={kwic_total}"
                )
            frequencies = StatisticsEngine(
                data_root=settings.DATA_ROOT,
                corpus_id=str(corpus.pk),
            ).word_list(
                language=str(language),
                min_frequency=1,
                min_range=1,
                page_size=20,
            )
            type_total = frequencies.total_types
            if type_total < 1:
                raise RuntimeError("word list returned no types")

        if corpus.corpus_type in PARALLEL_TYPES:
            if pair_count < 1:
                raise RuntimeError("parallel corpus has no aligned pairs")
            alignment_unit = (
                "paragraph"
                if corpus.corpus_type == CorpusType.PAIRED_RAW_ZH_EN
                else "sentence"
            )
            preview = ParallelSearchEngine(
                data_root=settings.DATA_ROOT,
                corpus_id=str(corpus.pk),
            ).preview(alignment_unit=alignment_unit, limit=1)
            if not preview:
                raise RuntimeError("parallel preview returned no aligned pair")
        elif pair_count:
            raise RuntimeError("monolingual corpus unexpectedly contains parallel pairs")

        if corpus.language == CorpusLanguage.UNKNOWN:
            raise RuntimeError("corpus language is unresolved")

        return {
            "tokens": token_count,
            "kwic": kwic_total,
            "types": type_total,
            "pairs": pair_count,
        }
