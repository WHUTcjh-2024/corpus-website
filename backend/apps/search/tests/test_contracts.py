from django.test import SimpleTestCase

from apps.search.contracts import KwicMatch, KwicPage
from apps.search.kwic import KwicMatch as PublicKwicMatch
from apps.search.kwic import KwicPage as PublicKwicPage


class KwicContractTests(SimpleTestCase):
    def test_engine_module_keeps_contract_imports_compatible(self) -> None:
        self.assertIs(PublicKwicMatch, KwicMatch)
        self.assertIs(PublicKwicPage, KwicPage)

    def test_page_navigation_uses_total_and_page_size(self) -> None:
        page = KwicPage(
            query="term",
            hits=(),
            total=21,
            page=2,
            page_size=10,
            context_size=5,
            sort_by="C",
            sort_keys=("C",),
            sort_order="asc",
            pos="",
        )

        self.assertEqual(page.num_pages, 3)
        self.assertTrue(page.has_previous)
        self.assertTrue(page.has_next)

    def test_match_prefers_explicit_keyword_length(self) -> None:
        match = KwicMatch(
            global_position=1,
            stream_position=1,
            sentence_id="s1",
            document_id="d1",
            sentence_position=0,
            language="en",
            keyword_surfaces=("New", "York"),
            keyword_token_length=1,
        )

        self.assertEqual(match.token_length, 1)
