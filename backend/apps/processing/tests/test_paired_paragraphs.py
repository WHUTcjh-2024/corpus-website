from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from apps.processing.contracts import SourceFile
from apps.processing.importers.paired_paragraphs import PairedParagraphImporter


class PairedParagraphImporterTests(SimpleTestCase):
    def test_mismatched_paragraph_counts_are_preserved_without_false_certainty(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            zh_path = root / "zh.txt"
            en_path = root / "en.txt"
            zh_path.write_text("第一段。\n\n第二段。\n\n第三段。", encoding="utf-8")
            en_path.write_text("First paragraph.\n\nSecond and third paragraphs.", encoding="utf-8")
            sources = [
                SourceFile("zh", zh_path.name, zh_path, "paired_raw_zh_en", "zh"),
                SourceFile("en", en_path.name, en_path, "paired_raw_zh_en", "en"),
            ]

            result = next(PairedParagraphImporter().iter_import(sources))

        self.assertTrue(result.parallel_pairs)
        self.assertEqual(
            sum(bool(pair.zh_text) for pair in result.parallel_pairs),
            len(result.parallel_pairs),
        )
        self.assertTrue(all(pair.confidence < 1.0 for pair in result.parallel_pairs))
        self.assertTrue(any("段落数不一致" in warning for warning in result.warnings))
