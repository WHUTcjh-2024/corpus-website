from django.test import SimpleTestCase

from apps.parallel.contracts import (
    HighlightFragment,
    ParallelHit,
    ParallelQuery,
    ParallelSearchResult,
)
from apps.parallel.engine import ParallelQuery as PublicParallelQuery


class ParallelContractTests(SimpleTestCase):
    def test_engine_module_keeps_contract_imports_compatible(self) -> None:
        self.assertIs(PublicParallelQuery, ParallelQuery)

    def test_query_deduplicates_highlights_and_sort_positions(self) -> None:
        query = ParallelQuery(
            q="发展",
            zh_contains="发展",
            sort_1="L1",
            sort_2="L1",
            sort_3="R1",
        )

        query.validate()
        self.assertEqual(query.zh_highlights, ("发展",))
        self.assertEqual(query.sort_positions, ("L1", "L1", "R1"))

    def test_result_navigation(self) -> None:
        result = ParallelSearchResult(
            query=ParallelQuery(q="development"),
            hits=(),
            total=11,
            raw_total=11,
            page=2,
            page_size=10,
            num_pages=2,
        )

        self.assertTrue(result.has_previous)
        self.assertFalse(result.has_next)

    def test_query_rejects_empty_conditions(self) -> None:
        with self.assertRaisesRegex(ValueError, "至少填写"):
            ParallelQuery().validate()

    def test_browse_mode_allows_empty_search_and_rejects_unknown_mode(self) -> None:
        ParallelQuery(mode="browse").validate()
        with self.assertRaisesRegex(ValueError, "mode"):
            ParallelQuery(mode="unknown", q="term").validate()

    def test_hit_exposes_visual_track_and_evidence_based_quality(self) -> None:
        fragments = (HighlightFragment("text", False),)
        verified = ParallelHit(
            global_position=7,
            pair_id="pair-7",
            pair_ordinal=7,
            zh_text="中文",
            en_text="English",
            zh_fragments=fragments,
            en_fragments=fragments,
            alignment_unit="sentence",
            method="provided_structure_id",
            confidence=1.0,
        )
        candidate = ParallelHit(
            global_position=8,
            pair_id="pair-8",
            pair_ordinal=8,
            zh_text="中文",
            en_text="",
            zh_fragments=fragments,
            en_fragments=fragments,
            alignment_unit="paragraph",
            method="automatic_length_dp_1_0",
            confidence=0.0,
        )

        self.assertEqual(verified.palette_class, "parallel-track--1")
        self.assertEqual(verified.quality_label, "编号核验")
        self.assertEqual(candidate.quality_label, "单边缺口")
