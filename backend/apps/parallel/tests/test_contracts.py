from django.test import SimpleTestCase

from apps.parallel.contracts import ParallelQuery, ParallelSearchResult
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
