from django.test import SimpleTestCase

from apps.statistics.contracts import (
    WORDCLOUD_HEIGHT,
    WORDCLOUD_WIDTH,
    FrequencyPage,
    WordcloudResult,
)
from apps.statistics.engine import FrequencyPage as PublicFrequencyPage


class StatisticsContractTests(SimpleTestCase):
    def test_engine_module_keeps_contract_imports_compatible(self) -> None:
        self.assertIs(PublicFrequencyPage, FrequencyPage)

    def test_frequency_page_navigation(self) -> None:
        page = FrequencyPage(
            rows=(),
            total_tokens=100,
            total_types=20,
            page=2,
            page_size=10,
            num_pages=2,
            language="en",
            filter_text="",
            pos="",
            min_frequency=1,
            min_range=1,
            sort_by="frequency",
            include_punctuation=False,
        )

        self.assertTrue(page.has_previous)
        self.assertFalse(page.has_next)

    def test_wordcloud_uses_shared_canvas_defaults(self) -> None:
        result = WordcloudResult(
            terms=(),
            language="zh",
            min_frequency=2,
            max_words=100,
            excluded_stopwords=0,
            source_types=0,
            theme="ocean",
        )

        self.assertEqual(result.canvas_width, WORDCLOUD_WIDTH)
        self.assertEqual(result.canvas_height, WORDCLOUD_HEIGHT)
