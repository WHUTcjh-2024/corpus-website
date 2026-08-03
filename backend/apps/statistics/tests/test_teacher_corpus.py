from __future__ import annotations

import time
from pathlib import Path
from unittest import TestCase, skipUnless

from apps.parallel.engine import ParallelQuery, ParallelSearchEngine
from apps.search.kwic import KwicSearchEngine
from apps.search.query_engine import ComplexQueryEngine
from apps.statistics.engine import StatisticsEngine


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DATA_ROOT = PROJECT_ROOT / "data"
TEACHER_CORPUS_ID = "71d92f26-c5e5-485f-ac83-3ebccb6a9acc"
TEACHER_INDEX = DATA_ROOT / "indexes" / TEACHER_CORPUS_ID / "kwic_index.sqlite"


@skipUnless(TEACHER_INDEX.is_file(), "teacher corpus regression index is not installed")
class TeacherCorpusAntConcRegressionTests(TestCase):
    """Golden checks against the provided tagged bilingual teacher corpus."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.started_at = time.perf_counter()
        cls.statistics = StatisticsEngine(data_root=DATA_ROOT, corpus_id=TEACHER_CORPUS_ID)
        cls.kwic = KwicSearchEngine(data_root=DATA_ROOT, corpus_id=TEACHER_CORPUS_ID)

    @classmethod
    def tearDownClass(cls) -> None:
        elapsed = time.perf_counter() - cls.started_at
        if elapsed >= 30:
            raise AssertionError(f"teacher corpus parity suite took {elapsed:.2f}s; expected <30s")
        super().tearDownClass()

    def test_word_lists_match_teacher_corpus_golden_counts(self) -> None:
        zh = self.statistics.word_list(language="zh", page_size=20)
        en = self.statistics.word_list(language="en", page_size=20)
        self.assertEqual((zh.total_tokens, zh.total_types), (9908, 2323))
        self.assertEqual((zh.rows[0].term, zh.rows[0].frequency), ("的", 570))
        self.assertEqual((en.total_tokens, en.total_types), (13601, 2542))
        self.assertEqual((en.rows[0].term.casefold(), en.rows[0].frequency), ("the", 1398))

    def test_kwic_words_full_regex_random_set_and_kpf(self) -> None:
        words = self.kwic.search("农民", language="zh", sort_keys=("R1",))
        full_regex = self.kwic.search(r"\bpeasant\b", language="en", full_regex=True)
        sample = self.kwic.search(
            "农民",
            language="zh",
            sample_size=25,
            sample_seed=20260803,
        )
        self.assertEqual(words.total, 259)
        self.assertEqual(full_regex.total, 158)
        self.assertEqual((sample.total, sample.available_total), (25, 259))
        self.assertEqual(sample.hits[0].row_id, self.kwic.search(
            "农民", language="zh", sample_size=25, sample_seed=20260803
        ).hits[0].row_id)
        self.assertTrue(all(hit.kpf_count >= 1 for hit in words.hits))
        file_view = self.kwic.file_view(
            document_id=words.hits[0].document_id,
            language="zh",
            row_id=words.hits[0].row_id,
            query="农民",
        )
        self.assertEqual(file_view.hit_count, 259)
        self.assertGreater(file_view.selected_hit, 0)
        self.assertEqual((file_view.token_count, file_view.type_count), (12061, 2340))

    def test_cluster_and_open_slot_ngram_match_golden_results(self) -> None:
        clusters = self.statistics.clusters(
            "农民",
            language="zh",
            cluster_size=2,
            query_position="left",
            min_frequency=2,
            page_size=20,
        )
        open_slots = self.statistics.ngrams(
            language="en",
            n=3,
            open_slot=2,
            min_frequency=10,
            page_size=20,
        )
        self.assertEqual(clusters.total_types, 28)
        self.assertEqual(
            (clusters.rows[0].cluster, clusters.rows[0].frequency),
            ("农民协会", 49),
        )
        self.assertEqual(open_slots.total_types, 51)
        self.assertEqual(
            (
                open_slots.rows[0].ngram.casefold(),
                open_slots.rows[0].frequency,
                open_slots.rows[0].slot_type_count,
            ),
            ("the <*> of", 207, 136),
        )

    def test_collocate_plot_wordcloud_and_cqp_match_golden_results(self) -> None:
        collocates = self.statistics.collocates(
            "农民",
            language="zh",
            left_span=2,
            right_span=2,
            min_frequency=2,
            page_size=20,
        )
        plot = self.statistics.concordance_plot("农民", language="zh")
        cloud = self.statistics.wordcloud(language="zh", min_frequency=2, max_words=25)
        cqp = ComplexQueryEngine(
            data_root=DATA_ROOT,
            corpus_id=TEACHER_CORPUS_ID,
        ).search('[word="the"] [] [word="of"]', language="en")
        self.assertEqual(collocates.node_frequency, 259)
        self.assertEqual((collocates.rows[0].term, collocates.rows[0].frequency), ("协会", 50))
        self.assertGreater(collocates.rows[0].log_likelihood, 0)
        self.assertEqual(plot.total, 259)
        self.assertEqual(sum(row.hit_count for row in plot.documents), 259)
        self.assertEqual((len(cloud.terms), cloud.terms[0].term), (25, "的"))
        self.assertEqual(cqp.total, 207)

    def test_parallel_search_matches_teacher_alignment_contract(self) -> None:
        parallel = ParallelSearchEngine(
            data_root=DATA_ROOT,
            corpus_id=TEACHER_CORPUS_ID,
        ).search(
            ParallelQuery(
                q="农民",
                search_side="zh",
                alignment_unit="sentence",
            )
        )
        self.assertEqual(parallel.total, 259)
        self.assertIn("peasant", parallel.hits[0].en_text.casefold())
