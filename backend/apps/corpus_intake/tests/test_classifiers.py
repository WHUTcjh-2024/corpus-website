from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from apps.corpus_intake.classifiers import classify_text
from apps.corpus_intake.scanner import scan_inbox


class ClassifierDataQualityTests(SimpleTestCase):
    def test_pos_tagged_english_is_not_misclassified_as_raw(self) -> None:
        result = classify_text(
            "China_NN1 develops_VVZ through_II reform_NN1 today_RT ._PUN"
        )

        self.assertEqual(result.detected_type, "tagged_en")
        self.assertEqual(result.detected_language, "en")

    def test_structured_text_is_not_misclassified_as_raw(self) -> None:
        result = classify_text(
            "<head>Title</head><p n='1'><s n='1'>A short sentence.</s></p>"
        )

        self.assertEqual(result.detected_type, "xml_like")
        self.assertIn("xml_like_tags", result.notes)

    def test_empty_source_is_quarantined(self) -> None:
        result = classify_text("\n\t")

        self.assertEqual(result.detected_type, "unknown")
        self.assertEqual(result.confidence, 0.1)
        self.assertIn("empty_file", result.notes)

    def test_scanner_quarantines_empty_files(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "empty.txt"
            path.write_text("", encoding="utf-8")
            scan = scan_inbox(Path(directory))

        self.assertEqual(scan.records[0].status, "quarantined")
