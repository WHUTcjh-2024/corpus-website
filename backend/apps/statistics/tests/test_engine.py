from __future__ import annotations

import json
import math
import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase

from apps.statistics.engine import StatisticsEngine
from apps.search.kwic import KwicSearchEngine


class StatisticsEngineParityTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_root = Path(self.temp_dir.name)
        self.corpus_id = "00000000-0000-0000-0000-000000000001"
        index_dir = self.data_root / "indexes" / self.corpus_id
        processed_dir = self.data_root / "processed" / self.corpus_id
        index_dir.mkdir(parents=True)
        processed_dir.mkdir(parents=True)
        self.index_path = index_dir / "kwic_index.sqlite"
        self._build_index()
        documents = (
            {"id": "d1", "filename": "one.txt"},
            {"id": "d2", "filename": "two.txt"},
            {"id": "d3", "filename": "three.txt"},
        )
        (processed_dir / "documents.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in documents),
            encoding="utf-8",
        )
        (processed_dir / "paragraphs.jsonl").write_text("", encoding="utf-8")
        (processed_dir / "sentences.jsonl").write_text(
            "".join(
                json.dumps({"id": f"s-d{index}", "ordinal": 1, "paragraph_id": ""}) + "\n"
                for index in range(1, 4)
            ),
            encoding="utf-8",
        )
        self.engine = StatisticsEngine(data_root=self.data_root, corpus_id=self.corpus_id)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _build_index(self) -> None:
        connection = sqlite3.connect(self.index_path)
        connection.executescript(
            """
            CREATE TABLE tokens (
                global_position INTEGER PRIMARY KEY,
                stream_position INTEGER NOT NULL,
                token_id TEXT NOT NULL UNIQUE,
                normalized TEXT NOT NULL,
                surface TEXT NOT NULL,
                lemma TEXT NOT NULL,
                pos TEXT NOT NULL,
                language TEXT NOT NULL,
                document_id TEXT NOT NULL,
                sentence_id TEXT NOT NULL,
                sentence_position INTEGER NOT NULL,
                document_start INTEGER NOT NULL,
                document_end INTEGER NOT NULL,
                is_punctuation INTEGER NOT NULL
            );
            CREATE TABLE ngrams (
                language TEXT NOT NULL,
                n INTEGER NOT NULL,
                normalized TEXT NOT NULL,
                display TEXT NOT NULL,
                frequency INTEGER NOT NULL,
                document_range INTEGER NOT NULL,
                contains_punctuation INTEGER NOT NULL,
                PRIMARY KEY (language, n, normalized)
            );
            CREATE TABLE documents (
                document_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                language TEXT NOT NULL
            );
            CREATE TABLE document_streams (
                document_id TEXT NOT NULL,
                language TEXT NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY (document_id, language)
            );
            """
        )
        position = 0
        for document_id, words in (
            ("d1", ("in", "terms", "of", "policy")),
            ("d2", ("in", "terms", "of", "law")),
            ("d3", ("in", "support", "of", "policy")),
        ):
            connection.execute(
                "INSERT INTO documents VALUES (?, ?, ?)",
                (document_id, f"{document_id}.txt", "en"),
            )
            connection.execute(
                "INSERT INTO document_streams VALUES (?, ?, ?)",
                (document_id, "en", " ".join(words)),
            )
            sentence_id = f"s-{document_id}"
            character_position = 0
            for sentence_position, word in enumerate(words, start=1):
                position += 1
                word_start = character_position
                word_end = word_start + len(word)
                connection.execute(
                    """
                    INSERT INTO tokens VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        position,
                        sentence_position,
                        f"t-{position}",
                        word,
                        word,
                        "term" if word == "terms" else word,
                        "",
                        "en",
                        document_id,
                        sentence_id,
                        sentence_position,
                        word_start,
                        word_end,
                        0,
                    ),
                )
                character_position = word_end + 1
        connection.commit()
        connection.close()

    def test_clusters_match_antconc_position_frequency_and_range_semantics(self) -> None:
        page = self.engine.clusters(
            "in",
            language="en",
            cluster_size=3,
            query_position="left",
            min_frequency=2,
        )
        self.assertEqual(page.total_types, 1)
        row = page.rows[0]
        self.assertEqual(row.cluster, "in terms of")
        self.assertEqual(row.frequency, 2)
        self.assertEqual(row.document_range, 2)
        self.assertEqual(row.transition_probability, 1.0)

    def test_cluster_query_can_be_anchored_on_right(self) -> None:
        page = self.engine.clusters(
            "of",
            language="en",
            cluster_size=3,
            query_position="right",
            min_frequency=1,
        )
        self.assertEqual(
            [(row.cluster, row.frequency) for row in page.rows],
            [("in terms of", 2), ("in support of", 1)],
        )

    def test_open_slot_ngrams_report_variant_ratio_and_entropy(self) -> None:
        page = self.engine.ngrams(
            language="en",
            n=3,
            open_slot=2,
            min_frequency=3,
        )
        row = next(row for row in page.rows if row.ngram == "in <*> of")
        self.assertEqual(row.frequency, 3)
        self.assertEqual(row.document_range, 3)
        self.assertEqual(row.slot_type_count, 2)
        self.assertAlmostEqual(row.slot_type_token_ratio, 2 / 3)
        expected_entropy = -(2 / 3 * math.log2(2 / 3) + 1 / 3 * math.log2(1 / 3))
        self.assertAlmostEqual(row.slot_entropy, expected_entropy)

    def test_open_slot_is_validated_against_ngram_size(self) -> None:
        with self.assertRaises(ValueError):
            self.engine.ngrams(language="en", n=2, open_slot=3)

    def test_word_list_supports_headwords_allowlist_and_inverted_end_sort(self) -> None:
        page = self.engine.word_list(
            language="en",
            display_type="headword",
            allowlist=("terms",),
            sort_by="end",
            invert_order=True,
        )
        self.assertEqual(page.total_types, 1)
        self.assertEqual(page.rows[0].term, "term")
        self.assertEqual(page.rows[0].frequency, 2)
        self.assertEqual(page.rows[0].document_range, 2)

    def test_collocates_expose_antconc_likelihood_and_effect_size_measures(self) -> None:
        page = self.engine.collocates(
            "terms",
            language="en",
            left_span=1,
            right_span=1,
            min_frequency=1,
            sort_by="log_likelihood",
        )
        row = next(row for row in page.rows if row.term == "in")
        self.assertEqual((row.frequency, row.left_frequency, row.right_frequency), (2, 2, 0))
        self.assertGreater(row.log_likelihood, 0)
        self.assertGreater(row.chi_square, 0)
        self.assertGreater(row.mi2, row.mutual_information)
        self.assertGreater(row.mi3, row.mi2)
        self.assertGreaterEqual(row.p_value, 0)
        self.assertLessEqual(row.p_value, 1)

    def test_plot_supports_overlay_normalized_frequency_and_dispersion_sort(self) -> None:
        result = self.engine.concordance_plot(
            "terms",
            language="en",
            overlay_query="policy",
            sort_by="normalized_frequency",
            invert_order=True,
            show_zero_hits=True,
            bin_count=20,
        )
        self.assertEqual((result.total, result.overlay_total), (2, 2))
        self.assertEqual(len(result.documents), 3)
        self.assertTrue(all(len(document.cells) == 20 for document in result.documents))
        self.assertTrue(all(document.token_count == 4 for document in result.documents))
        self.assertEqual(result.documents[0].normalized_frequency, 250_000)
        self.assertTrue(
            any(cell.overlay_count for document in result.documents for cell in document.cells)
        )

    def test_kwic_random_result_set_is_seeded_and_reports_available_hits(self) -> None:
        kwic = KwicSearchEngine(data_root=self.data_root, corpus_id=self.corpus_id)
        first = kwic.search("in", language="en", sample_size=2, sample_seed=42)
        second = kwic.search("in", language="en", sample_size=2, sample_seed=42)
        self.assertEqual(first.total, 2)
        self.assertEqual(first.available_total, 3)
        self.assertEqual(first.sample_size, 2)
        self.assertEqual(
            [hit.row_id for hit in first.hits],
            [hit.row_id for hit in second.hits],
        )

    def test_kwic_pattern_frequency_counts_equal_sort_patterns(self) -> None:
        page = KwicSearchEngine(
            data_root=self.data_root,
            corpus_id=self.corpus_id,
        ).search(
            "of",
            language="en",
            sort_keys=("R1",),
            sort_order="frequency",
            page_size=1,
        )
        policy_rows = [hit for hit in page.hits if hit.r1 == "policy"]
        self.assertEqual(len(policy_rows), 1)
        self.assertTrue(all(hit.kpf_count == 2 for hit in policy_rows))

    def test_kwic_advanced_query_list_unions_and_deduplicates_matches(self) -> None:
        page = KwicSearchEngine(
            data_root=self.data_root,
            corpus_id=self.corpus_id,
        ).search_advanced("terms", query_list=("support", "terms"), language="en")
        self.assertEqual(page.total, 3)
        self.assertEqual(page.advanced_queries, ("terms", "support"))

    def test_kwic_context_query_supports_window_logic_and_exclusion(self) -> None:
        engine = KwicSearchEngine(data_root=self.data_root, corpus_id=self.corpus_id)
        included = engine.search_advanced(
            "in",
            context_queries=("terms",),
            context_from=1,
            context_to=1,
            language="en",
        )
        excluded = engine.search_advanced(
            "in",
            context_queries=("terms",),
            context_from=1,
            context_to=1,
            exclude_context=True,
            language="en",
        )
        self.assertEqual(included.total, 2)
        self.assertEqual(excluded.total, 1)

    def test_full_regex_random_result_set_is_seeded(self) -> None:
        engine = KwicSearchEngine(data_root=self.data_root, corpus_id=self.corpus_id)
        first = engine.search(
            r"\bin\b",
            language="en",
            full_regex=True,
            sample_size=2,
            sample_seed=11,
        )
        second = engine.search(
            r"\bin\b",
            language="en",
            full_regex=True,
            sample_size=2,
            sample_seed=11,
        )
        self.assertEqual((first.total, first.available_total), (2, 3))
        self.assertEqual(
            [hit.row_id for hit in first.hits],
            [hit.row_id for hit in second.hits],
        )
