from django.test import SimpleTestCase

from apps.processing.text import token_matches


class TokenizationTests(SimpleTestCase):
    def test_english_tokenizer_supports_unicode_hyphens_and_punctuation(self) -> None:
        tokens = [
            match.group(0)
            for match in token_matches(
                "Café—state-of-the-art, China's 3.14%！",
                "en",
            )
        ]

        self.assertEqual(
            tokens,
            ["Café", "—", "state-of-the-art", ",", "China's", "3.14", "%", "！"],
        )

    def test_chinese_tokenizer_preserves_punctuation(self) -> None:
        tokens = [match.group(0) for match in token_matches("中国，发展！", "zh")]

        self.assertEqual(tokens, ["中国", "，", "发展", "！"])
