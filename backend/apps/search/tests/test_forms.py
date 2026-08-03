from django.test import SimpleTestCase

from apps.search.forms import KwicSearchForm


class KwicSearchFormTests(SimpleTestCase):
    def test_search_query_list_can_replace_main_query(self) -> None:
        form = KwicSearchForm(
            {
                "q": "",
                "query_mode": "simple",
                "language": "en",
                "query_list": "terms\nsupport\nterms",
            },
            available_languages=("en",),
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["query_list"], ("terms", "support"))

    def test_context_window_must_be_ordered(self) -> None:
        form = KwicSearchForm(
            {
                "q": "in",
                "query_mode": "simple",
                "language": "en",
                "context_queries": "terms",
                "context_from": "2",
                "context_to": "-2",
            },
            available_languages=("en",),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("context_to", form.errors)

    def test_advanced_lists_reject_incompatible_query_modes(self) -> None:
        form = KwicSearchForm(
            {
                "q": r"\bin\b",
                "query_mode": "full_regex",
                "language": "en",
                "query_list": "terms",
            },
            available_languages=("en",),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)
