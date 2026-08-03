import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from apps.parallel.engine import ParallelQuery, ParallelSearchEngine
from apps.processing.artifacts import ArtifactWriter
from apps.processing.contracts import SourceFile
from apps.processing.importers.aligned_tsv import AlignedTsvImporter
from apps.search.kwic import KwicSearchEngine
from apps.search.query_engine import ComplexQueryEngine


FIXTURES = Path(__file__).parent / "fixtures"


class GoldenPipelineTests(SimpleTestCase):
    def test_import_index_and_both_search_engines_match_golden_contract(self) -> None:
        expected = json.loads(
            (FIXTURES / "golden_expected.json").read_text(encoding="utf-8")
        )
        source_path = FIXTURES / "golden_aligned.tsv"
        source = SourceFile(
            "golden",
            source_path.name,
            source_path,
            "aligned_tsv",
            "zh_en",
        )
        result = next(AlignedTsvImporter().iter_import((source,)))

        self.assertEqual(
            [(pair.zh_text, pair.en_text) for pair in result.parallel_pairs],
            [tuple(pair) for pair in expected["pairs"]],
        )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            writer = ArtifactWriter(
                data_root=root,
                corpus_id="golden-corpus",
                task_id="golden-task",
            )
            writer.open()
            writer.add_result(result)
            report = writer.finalize(
                corpus_meta={},
                source_files=[],
                importer_name="aligned_tsv",
            )
            kwic = KwicSearchEngine(
                data_root=root,
                corpus_id="golden-corpus",
            ).search("future", language="en")
            open_slot = ComplexQueryEngine(
                data_root=root,
                corpus_id="golden-corpus",
            ).search('[word="A"] [] [word="future"]', language="en")
            parallel_engine = ParallelSearchEngine(
                data_root=root,
                corpus_id="golden-corpus",
            )
            parallel = parallel_engine.search(
                ParallelQuery(
                    q="future",
                    search_side="en",
                    alignment_unit="sentence",
                    filename_contains="golden",
                    min_confidence=0.9,
                )
            )
            exported = tuple(
                parallel_engine.iter_export_rows(
                    ParallelQuery(
                        q="future",
                        search_side="en",
                        alignment_unit="sentence",
                    )
                )
            )

        for key in (
            "document_count",
            "paragraph_count",
            "sentence_count",
            "parallel_pair_count",
        ):
            self.assertEqual(report["counts"][key], expected[key])
        self.assertEqual(kwic.total, 1)
        self.assertEqual(open_slot.total, 1)
        self.assertEqual(parallel.total, 1)
        self.assertEqual(parallel.hits[0].zh_text, "共同未来！")
        self.assertEqual(len(exported), 1)
