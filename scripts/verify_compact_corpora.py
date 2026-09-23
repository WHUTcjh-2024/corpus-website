"""Compare published and compact pilot indexes with real query engines."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

import django  # noqa: E402

django.setup()

from apps.parallel.contracts import ParallelQuery  # noqa: E402
from apps.parallel.engine import ParallelSearchEngine  # noqa: E402
from apps.search.kwic import KwicSearchEngine  # noqa: E402
from apps.statistics.engine import StatisticsEngine  # noqa: E402


def compare(label, source_call, compact_call) -> None:
    start = time.perf_counter()
    source_result = source_call()
    source_seconds = time.perf_counter() - start
    start = time.perf_counter()
    compact_result = compact_call()
    compact_seconds = time.perf_counter() - start
    if source_result != compact_result:
        raise AssertionError(f"{label}: query results differ")
    print(
        f"{label}: equal; original={source_seconds:.3f}s "
        f"compact={compact_seconds:.3f}s",
        flush=True,
    )


def is_search_term(language: str, term: str) -> bool:
    if language == "en":
        return bool(re.fullmatch(r"[a-z]{3,}", term))
    return any("\u4e00" <= character <= "\u9fff" for character in term)


def verify(data_root: Path, pilot_root: Path, corpus_id: str) -> None:
    source_path = data_root / "indexes" / corpus_id / "kwic_index.sqlite"
    compact_path = pilot_root / "indexes" / corpus_id / "kwic_index.sqlite"
    source_kwic = KwicSearchEngine(data_root=data_root, corpus_id=corpus_id)
    compact_kwic = KwicSearchEngine(data_root=data_root, corpus_id=corpus_id)
    compact_kwic.index_path = compact_path
    source_stats = StatisticsEngine(data_root=data_root, corpus_id=corpus_id)
    compact_stats = StatisticsEngine(data_root=data_root, corpus_id=corpus_id)
    compact_stats.index_path = compact_path
    source_parallel = ParallelSearchEngine(data_root=data_root, corpus_id=corpus_id)
    compact_parallel = ParallelSearchEngine(data_root=data_root, corpus_id=corpus_id)
    compact_parallel.index_path = compact_path

    with closing(sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)) as db:
        terms = []
        for language in ("en", "zh"):
            candidates = db.execute(
                "SELECT normalized FROM word_totals "
                "WHERE language = ? AND is_punctuation = 0 "
                "AND frequency BETWEEN 10 AND 100 "
                "ORDER BY frequency DESC LIMIT 100",
                (language,),
            ).fetchall()
            terms.extend(
                (language, term)
                for term in [
                    term for (term,) in candidates if is_search_term(language, term)
                ][:2]
            )
        ngram_language = db.execute(
            "SELECT language FROM ngrams ORDER BY language LIMIT 1"
        ).fetchone()[0]
        pair_count = db.execute("SELECT COUNT(*) FROM parallel_pairs").fetchone()[0]
        pair_text = db.execute(
            "SELECT en_text FROM parallel_pairs WHERE en_text != '' LIMIT 1"
        ).fetchone()
    for language, term in terms:
        compare(
            f"{corpus_id} KWIC {language}:{term}",
            lambda language=language, term=term: source_kwic.search(term, language=language),
            lambda language=language, term=term: compact_kwic.search(term, language=language),
        )
    compare(
        f"{corpus_id} word list",
        lambda: source_stats.word_list(language=ngram_language, page_size=20),
        lambda: compact_stats.word_list(language=ngram_language, page_size=20),
    )
    compare(
        f"{corpus_id} 2-gram",
        lambda: source_stats.ngrams(language=ngram_language, n=2, page_size=20),
        lambda: compact_stats.ngrams(language=ngram_language, n=2, page_size=20),
    )
    compare(
        f"{corpus_id} 5-gram range",
        lambda: source_stats.ngrams(
            language=ngram_language, n=5, sort_by="range", min_frequency=1, page_size=20
        ),
        lambda: compact_stats.ngrams(
            language=ngram_language, n=5, sort_by="range", min_frequency=1, page_size=20
        ),
    )
    if pair_count:
        query = ParallelQuery(mode="browse", alignment_unit="sentence")
        compare(
            f"{corpus_id} parallel browse",
            lambda: source_parallel.search(query, page_size=10),
            lambda: compact_parallel.search(query, page_size=10),
        )
        words = re.findall(r"[A-Za-z]{4,}", pair_text[0] if pair_text else "")
        if words:
            text_query = ParallelQuery(
                q=words[0], search_side="en", alignment_unit="sentence"
            )
            compare(
                f"{corpus_id} parallel search {words[0]}",
                lambda: source_parallel.search(text_query, page_size=10),
                lambda: compact_parallel.search(text_query, page_size=10),
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus_ids", nargs="+")
    parser.add_argument("--data-root", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--pilot-root", type=Path, required=True)
    args = parser.parse_args()
    for corpus_id in args.corpus_ids:
        verify(args.data_root.resolve(), args.pilot_root.resolve(), corpus_id)


if __name__ == "__main__":
    main()
