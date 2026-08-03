from django.test import SimpleTestCase

from apps.processing.exceptions import ProcessingError
from apps.processing.services import _validate_source_classification


class SourceClassificationValidationTests(SimpleTestCase):
    def test_tagged_file_cannot_enter_raw_importer(self) -> None:
        with self.assertRaisesRegex(ProcessingError, "文件格式与语料类型不一致"):
            _validate_source_classification(
                corpus_type="raw_en",
                filename="tagged-pos.txt",
                declared_language="en",
                actual_type="tagged_en",
                actual_language="en",
            )

    def test_raw_pair_accepts_one_raw_file_per_language(self) -> None:
        for language in ("zh", "en"):
            _validate_source_classification(
                corpus_type="paired_raw_zh_en",
                filename=f"sample-{language}.txt",
                declared_language=language,
                actual_type=f"raw_{language}",
                actual_language=language,
            )

    def test_declared_language_must_match_content(self) -> None:
        with self.assertRaisesRegex(ProcessingError, "文件语言与登记信息不一致"):
            _validate_source_classification(
                corpus_type="raw_zh",
                filename="wrong-language.txt",
                declared_language="zh",
                actual_type="raw_zh",
                actual_language="en",
            )
