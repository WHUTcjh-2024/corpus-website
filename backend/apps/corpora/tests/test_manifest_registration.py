from django.test import SimpleTestCase

from apps.corpora.services import (
    _manifest_corpus_records,
    _manifest_registration_id,
    _validate_manifest_records,
)


class ManifestPairRegistrationTests(SimpleTestCase):
    def test_selecting_one_paired_record_registers_both_languages(self) -> None:
        records = [
            {
                "file_id": "zh-id",
                "detected_type": "paired_raw_zh_en",
                "detected_language": "zh",
                "probable_pair_id": "pair-1",
            },
            {
                "file_id": "en-id",
                "detected_type": "paired_raw_zh_en",
                "detected_language": "en",
                "probable_pair_id": "pair-1",
            },
        ]

        selected = _manifest_corpus_records(records[0], records)

        self.assertEqual({item["file_id"] for item in selected}, {"zh-id", "en-id"})
        self.assertLessEqual(len(_manifest_registration_id(selected)), 64)

    def test_incomplete_pair_is_rejected(self) -> None:
        record = {
            "file_id": "zh-id",
            "detected_type": "paired_raw_zh_en",
            "detected_language": "zh",
            "probable_pair_id": "pair-1",
        }

        with self.assertRaisesRegex(ValueError, "exactly one zh and one en"):
            _manifest_corpus_records(record, [record])

    def test_quarantined_record_cannot_be_registered(self) -> None:
        record = {
            "file_id": "empty-id",
            "detected_type": "unknown",
            "detected_language": "unknown",
            "status": "quarantined",
            "filename": "empty.txt",
        }

        with self.assertRaisesRegex(ValueError, "不能登记已隔离"):
            _validate_manifest_records([record])
