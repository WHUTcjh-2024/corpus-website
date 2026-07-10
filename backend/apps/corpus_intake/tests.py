import csv
import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase

from apps.corpus_intake.classifiers import classify_text, decode_text
from apps.corpus_intake.manifest import MANIFEST_FIELDS, write_manifest
from apps.corpus_intake.scanner import scan_inbox


FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "corpus_intake"
EXPECTED = Path(__file__).resolve().parents[2] / "tests" / "expected" / "corpus_intake" / "summary.json"


class CorpusClassifierTests(SimpleTestCase):
    def test_classifies_core_content_types(self):
        cases = {
            "共同富裕是社会主义的本质要求\n人民群众物质生活和精神生活都富裕。": "raw_zh",
            "Promote high-quality development through innovation and coordination.": "raw_en",
            "zh\ten\n中国\tChina\n人民\tpeople": "aligned_tsv",
            "发展/vn 数字/n 经济/n 创新/vn 协调/vn 绿色/a 开放/vn 共享/vn": "tagged_zh",
            "Development_NN1 is_VBZ high-quality_JJ and_CC green_JJ ._.": "tagged_en",
            "<head>Title</head>\n<p><s n=\"1\">Sentence.</s></p>": "xml_like",
            "12345\n!!!": "unknown",
        }

        for text, expected_type in cases.items():
            with self.subTest(expected_type=expected_type):
                result = classify_text(text)
                self.assertEqual(result.detected_type, expected_type)

    def test_decode_prefers_readable_text_for_ascii_gb18030(self):
        text, encoding = decode_text("PROBLEMS OF WAR AND STRATEGY".encode("gb18030"))

        self.assertEqual(text, "PROBLEMS OF WAR AND STRATEGY")
        self.assertIn(encoding, {"utf-8-sig", "gb18030"})


class CorpusScannerTests(SimpleTestCase):
    def test_scans_fixture_directory_and_detects_expected_types(self):
        scan_result = scan_inbox(FIXTURES)
        expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
        type_counts = scan_result.summary["type_counts"]

        self.assertEqual(scan_result.summary["total_files"], expected["total_files"])
        self.assertEqual(scan_result.summary["unknown_file_count"], expected["unknown_file_count"])
        self.assertEqual(scan_result.summary["probable_pair_count"], expected["probable_pair_count"])
        for detected_type in expected["required_types"]:
            self.assertIn(detected_type, type_counts)

    def test_pair_detection_sets_pair_id_without_modifying_files(self):
        before = {path.name: path.stat().st_mtime_ns for path in FIXTURES.glob("*")}

        scan_result = scan_inbox(FIXTURES)

        paired_records = [record for record in scan_result.records if record.detected_type == "paired_raw_zh_en"]
        after = {path.name: path.stat().st_mtime_ns for path in FIXTURES.glob("*")}
        self.assertEqual(len(paired_records), 2)
        self.assertEqual({record.probable_pair_id for record in paired_records}, {"pair-0001"})
        self.assertEqual(before, after)


class ManifestWriterTests(SimpleTestCase):
    def test_writes_csv_and_json_manifest(self):
        scan_result = scan_inbox(FIXTURES)
        output_dir = self._tmp_path()

        csv_path, json_path = write_manifest(scan_result, output_dir)

        self.assertTrue(csv_path.exists())
        self.assertTrue(json_path.exists())
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, MANIFEST_FIELDS)
            rows = list(reader)
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(len(rows), scan_result.summary["total_files"])
        self.assertEqual(payload["summary"]["probable_pair_count"], 1)

    def test_management_command_writes_manifest(self):
        output_dir = self._tmp_path()

        call_command("scan_corpus_inbox", "--inbox", str(FIXTURES), "--output-dir", str(output_dir))

        self.assertTrue((output_dir / "corpus_manifest.csv").exists())
        self.assertTrue((output_dir / "corpus_manifest.json").exists())

    def _tmp_path(self) -> Path:
        return Path(self.enterContext(tempfile.TemporaryDirectory(prefix="corpus-intake-test-")))
