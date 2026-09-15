from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from apps.processing.contracts import SourceFile
from apps.processing.importers.paired_paragraphs import PairedParagraphImporter
from apps.processing.importers.paired_tagged_structure import (
    PairedTaggedStructureImporter,
)


class BatchedPairImporterTests(SimpleTestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_raw_importer_processes_multiple_manifest_pairs(self) -> None:
        sources = []
        for number in (1, 2):
            sources.extend(
                (
                    self._source(number, "zh", f"中文段落{number}。", "raw_zh"),
                    self._source(number, "en", f"English paragraph {number}.", "raw_en"),
                )
            )

        results = list(PairedParagraphImporter().iter_import(sources))

        self.assertEqual(len(results), 2)
        self.assertEqual(sum(len(result.documents) for result in results), 4)
        self.assertTrue(all(result.parallel_pairs for result in results))

    def test_tagged_importer_processes_multiple_manifest_pairs(self) -> None:
        sources = []
        for number in (1, 2):
            sources.extend(
                (
                    self._source(
                        number,
                        "zh",
                        '<p n="1"><s n="1">农民/n 运动/n</s></p>',
                        "tagged_zh",
                    ),
                    self._source(
                        number,
                        "en",
                        '<p n="1"><s n="1">peasant_NN movement_NN</s></p>',
                        "tagged_en",
                    ),
                )
            )

        results = list(PairedTaggedStructureImporter().iter_import(sources))

        self.assertEqual(len(results), 2)
        self.assertEqual(sum(len(result.documents) for result in results), 4)
        self.assertTrue(all(result.parallel_pairs for result in results))

    def _source(
        self,
        number: int,
        language: str,
        content: str,
        actual_type: str,
    ) -> SourceFile:
        filename = f"{number}-{language}.txt"
        path = self.root / filename
        path.write_text(content, encoding="utf-8")
        return SourceFile(
            id=filename,
            filename=filename,
            path=path,
            detected_type=actual_type,
            language=language,
            encoding="utf-8",
            size_bytes=path.stat().st_size,
            actual_type=actual_type,
            pair_id=f"pair-{number:04d}",
        )
