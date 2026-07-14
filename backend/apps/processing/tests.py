from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from io import StringIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusFile,
    CorpusFileStatus,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from apps.search.kwic import KwicSearchEngine

from .exceptions import ProcessingAlreadyQueued, ProcessingError
from .models import ProcessingTask, ProcessingTaskStatus
from .services import create_processing_task, process_task
from .tasks import process_corpus_task
from .text import token_matches


class ProcessingPipelineTests(TestCase):
    fixtures_root = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "processing"
    expected_path = Path(__file__).resolve().parents[2] / "tests" / "expected" / "processing" / "counts.json"

    def setUp(self) -> None:
        self.temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.data_root = Path(self.temp_dir)
        self.enterContext(override_settings(DATA_ROOT=self.data_root))
        self.expected = json.loads(self.expected_path.read_text(encoding="utf-8"))

    def test_raw_chinese_tokenization_uses_words_instead_of_single_characters(self):
        terms = [match.group(0) for match in token_matches("中国社会各阶级的分析", "zh")]

        self.assertEqual(terms, ["中国", "社会", "各", "阶级", "的", "分析"])
        self.assertEqual(
            [(match.start(), match.end()) for match in token_matches("数字经济", "zh")],
            [(0, 2), (2, 4)],
        )

    def create_corpus(
        self,
        *,
        corpus_type: str,
        language: str,
        files: list[tuple[str, str, str]],
        source_type: str = CorpusSourceType.DEMO,
        owner=None,
    ) -> Corpus:
        corpus = Corpus.objects.create(
            name=f"Processing {corpus_type}",
            source_type=source_type,
            corpus_type=corpus_type,
            language=language,
            owner=owner,
            access_level=(
                CorpusAccessLevel.PRIVATE
                if source_type == CorpusSourceType.USER
                else CorpusAccessLevel.DEMO
            ),
            status=CorpusStatus.CREATED,
        )
        for filename, detected_type, file_language in files:
            path = self.fixtures_root / filename
            CorpusFile.objects.create(
                corpus=corpus,
                original_filename=filename,
                stored_path=str(path),
                detected_type=detected_type,
                language=file_language,
                size_bytes=path.stat().st_size,
                encoding="utf-8",
            )
        return corpus

    def run_pipeline(self, corpus: Corpus):
        task = create_processing_task(corpus=corpus)
        report = process_task(task.pk)
        task.refresh_from_db()
        corpus.refresh_from_db()
        return task, report

    def read_jsonl(self, corpus: Corpus, filename: str) -> list[dict]:
        path = self.data_root / "processed" / str(corpus.pk) / filename
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def test_raw_zh_sentence_count_and_standard_artifacts(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.RAW_ZH,
            language=CorpusLanguage.ZH,
            files=[("raw_zh.txt", CorpusType.RAW_ZH, CorpusLanguage.ZH)],
        )

        task, report = self.run_pipeline(corpus)

        expected = self.expected["raw_zh"]
        self.assertEqual(report["counts"]["document_count"], expected["document_count"])
        self.assertEqual(report["counts"]["paragraph_count"], expected["paragraph_count"])
        self.assertEqual(report["counts"]["sentence_count"], expected["sentence_count"])
        self.assertEqual(task.status, ProcessingTaskStatus.SUCCESS)
        self.assertEqual(task.progress, 100)
        self.assertEqual(corpus.status, CorpusStatus.READY)
        self.assertEqual(corpus.stage, "processed")

        processed = self.data_root / "processed" / str(corpus.pk)
        indexes = self.data_root / "indexes" / str(corpus.pk)
        self.assertEqual(
            {path.name for path in processed.iterdir()},
            {
                "meta.json",
                "documents.jsonl",
                "paragraphs.jsonl",
                "sentences.jsonl",
                "tokens.jsonl",
                "parallel_pairs.jsonl",
                "documentation.json",
                "processing_report.json",
            },
        )
        self.assertEqual(
            {path.name for path in indexes.iterdir()},
            {
                "kwic_index.sqlite",
                "token_position_index",
                "word_frequency.json",
                "ngram_frequency.json",
                "collocate_cache.json",
                "concordance_plot.json",
                "wordcloud_terms.json",
            },
        )
        with closing(sqlite3.connect(indexes / "kwic_index.sqlite")) as connection:
            token_count = connection.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
        self.assertEqual(token_count, report["counts"]["token_count"])

    def test_raw_en_token_count(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            files=[("raw_en.txt", CorpusType.RAW_EN, CorpusLanguage.EN)],
        )

        _, report = self.run_pipeline(corpus)

        expected = self.expected["raw_en"]
        self.assertEqual(report["counts"]["sentence_count"], expected["sentence_count"])
        self.assertEqual(report["counts"]["token_count"], expected["token_count"])
        self.assertEqual(
            [token["normalized"] for token in self.read_jsonl(corpus, "tokens.jsonl")],
            ["development", "matters", "quality", "improves"],
        )

    def test_aligned_tsv_pair_count(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.ALIGNED_TSV,
            language=CorpusLanguage.ZH_EN,
            files=[("aligned.tsv", CorpusType.ALIGNED_TSV, CorpusLanguage.ZH_EN)],
        )

        _, report = self.run_pipeline(corpus)

        expected = self.expected["aligned_tsv"]
        self.assertEqual(report["counts"]["sentence_count"], expected["sentence_count"])
        self.assertEqual(
            report["counts"]["parallel_pair_count"], expected["parallel_pair_count"]
        )
        pairs = self.read_jsonl(corpus, "parallel_pairs.jsonl")
        self.assertEqual([pair["ordinal"] for pair in pairs], [1, 2])
        self.assertEqual(pairs[0]["method"], "provided")

    def test_tagged_zh_parses_token_and_pos(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.TAGGED_ZH,
            language=CorpusLanguage.ZH,
            files=[("tagged_zh.txt", CorpusType.TAGGED_ZH, CorpusLanguage.ZH)],
        )

        _, report = self.run_pipeline(corpus)

        self.assertEqual(report["counts"]["token_count"], self.expected["tagged_zh"]["token_count"])
        tokens = self.read_jsonl(corpus, "tokens.jsonl")
        self.assertEqual(
            [(token["text"], token["pos"]) for token in tokens],
            [("发展", "vn"), ("经济", "n"), ("改变", "v"), ("生活", "n")],
        )

    def test_tagged_en_parses_token_and_pos(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.TAGGED_EN,
            language=CorpusLanguage.EN,
            files=[("tagged_en.txt", CorpusType.TAGGED_EN, CorpusLanguage.EN)],
        )

        _, report = self.run_pipeline(corpus)

        self.assertEqual(report["counts"]["token_count"], self.expected["tagged_en"]["token_count"])
        tokens = self.read_jsonl(corpus, "tokens.jsonl")
        self.assertEqual(
            [(token["text"], token["pos"]) for token in tokens],
            [("Development", "NN1"), ("improves", "VVZ"), ("quality", "NN1")],
        )

    def test_xml_like_importer_preserves_structured_sentences(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.XML_LIKE,
            language=CorpusLanguage.ZH,
            files=[("xml_like.txt", CorpusType.XML_LIKE, CorpusLanguage.ZH)],
        )

        _, report = self.run_pipeline(corpus)

        self.assertEqual(report["counts"]["sentence_count"], self.expected["xml_like"]["sentence_count"])
        documents = self.read_jsonl(corpus, "documents.jsonl")
        self.assertEqual(documents[0]["title"], "测试文档")

    def test_paired_raw_files_preserve_provided_paragraph_alignment(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.PAIRED_RAW_ZH_EN,
            language=CorpusLanguage.ZH_EN,
            files=[
                ("paired_zh.txt", CorpusType.PAIRED_RAW_ZH_EN, CorpusLanguage.ZH),
                ("paired_en.txt", CorpusType.PAIRED_RAW_ZH_EN, CorpusLanguage.EN),
            ],
        )

        _, report = self.run_pipeline(corpus)

        expected = self.expected["paired_raw_zh_en"]
        self.assertEqual(report["counts"]["document_count"], expected["document_count"])
        self.assertEqual(
            report["counts"]["parallel_pair_count"], expected["parallel_pair_count"]
        )
        pairs = self.read_jsonl(corpus, "parallel_pairs.jsonl")
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["alignment_unit"], "paragraph")
        self.assertEqual(pairs[0]["method"], "provided_paragraph_order")
        self.assertEqual(pairs[0]["confidence"], 1.0)
        self.assertEqual(report["counts"]["sentence_count"], 5)
        self.assertEqual(pairs[0]["zh_text"], "数字经济发展。人工智能进步并改变生活。")
        self.assertEqual(
            pairs[0]["en_text"],
            "The digital economy develops. Artificial intelligence advances. It changes lives.",
        )

    def test_paired_raw_files_reject_mismatched_paragraph_counts(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.PAIRED_RAW_ZH_EN,
            language=CorpusLanguage.ZH_EN,
            files=[
                ("paired_zh.txt", CorpusType.PAIRED_RAW_ZH_EN, CorpusLanguage.ZH),
                ("raw_zh.txt", CorpusType.PAIRED_RAW_ZH_EN, CorpusLanguage.EN),
            ],
        )
        task = create_processing_task(corpus=corpus)

        with self.assertRaisesMessage(
            ProcessingError,
            "Provided paragraph alignment is invalid",
        ):
            process_task(task.pk)

        task.refresh_from_db()
        self.assertEqual(task.status, ProcessingTaskStatus.FAILED)

    def test_paired_tagged_structure_preserves_ids_pos_and_clean_surface_text(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.PAIRED_TAGGED_ZH_EN,
            language=CorpusLanguage.ZH_EN,
            files=[
                (
                    "paired_tagged_zh.txt",
                    CorpusType.PAIRED_TAGGED_ZH_EN,
                    CorpusLanguage.ZH,
                ),
                (
                    "paired_tagged_en.txt",
                    CorpusType.PAIRED_TAGGED_ZH_EN,
                    CorpusLanguage.EN,
                ),
            ],
        )

        _, report = self.run_pipeline(corpus)

        expected = self.expected["paired_tagged_zh_en"]
        for key, value in expected.items():
            self.assertEqual(report["counts"][key], value)
        pairs = self.read_jsonl(corpus, "parallel_pairs.jsonl")
        self.assertEqual(
            [pair["alignment_unit"] for pair in pairs],
            ["sentence", "sentence", "sentence", "paragraph", "paragraph"],
        )
        self.assertTrue(all(pair["method"] == "provided_structure_id" for pair in pairs))
        self.assertTrue(all(pair["confidence"] == 1.0 for pair in pairs))
        self.assertEqual(pairs[0]["zh_text"], "数字经济发展。")
        self.assertEqual(pairs[0]["en_text"], "The digital economy develops.")
        self.assertEqual(pairs[2]["zh_text"], "第三件经济")

        tokens = self.read_jsonl(corpus, "tokens.jsonl")
        self.assertIn(("数字", "n"), [(token["text"], token["pos"]) for token in tokens])
        self.assertIn(("digital", "JJ"), [(token["text"], token["pos"]) for token in tokens])
        for record in [*pairs, *self.read_jsonl(corpus, "sentences.jsonl")]:
            serialized = json.dumps(record, ensure_ascii=False)
            self.assertNotIn("<s", serialized)
            self.assertNotIn("/n", serialized)
            self.assertNotIn("_NN", serialized)

        kwic = KwicSearchEngine(
            data_root=self.data_root,
            corpus_id=str(corpus.pk),
        ).search("发展", pos="v")
        self.assertEqual(kwic.total, 1)
        self.assertEqual(kwic.hits[0].keyword, "发展")
        self.assertEqual(
            KwicSearchEngine(
                data_root=self.data_root,
                corpus_id=str(corpus.pk),
            ).search("发展", pos="n").total,
            0,
        )
        corpus.documentation.refresh_from_db()
        self.assertEqual(
            corpus.documentation.segmentation_tool,
            "zh:source-provided-pos-v1;en:source-provided-pos-v1",
        )

    def test_paired_tagged_structure_rejects_cross_language_id_mismatch(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.PAIRED_TAGGED_ZH_EN,
            language=CorpusLanguage.ZH_EN,
            files=[
                (
                    "paired_tagged_zh.txt",
                    CorpusType.PAIRED_TAGGED_ZH_EN,
                    CorpusLanguage.ZH,
                ),
                (
                    "paired_tagged_en_mismatch.txt",
                    CorpusType.PAIRED_TAGGED_ZH_EN,
                    CorpusLanguage.EN,
                ),
            ],
        )
        task = create_processing_task(corpus=corpus)

        with self.assertRaisesMessage(
            ProcessingError,
            "Tagged sentence n identifiers differ",
        ):
            process_task(task.pk)

        task.refresh_from_db()
        self.assertEqual(task.status, ProcessingTaskStatus.FAILED)

    def test_processing_failure_persists_error_message(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.ALIGNED_TSV,
            language=CorpusLanguage.ZH_EN,
            files=[("invalid_aligned.tsv", CorpusType.ALIGNED_TSV, CorpusLanguage.ZH_EN)],
        )
        task = create_processing_task(corpus=corpus)

        with self.assertRaises(ProcessingError):
            process_task(task.pk)

        task.refresh_from_db()
        corpus.refresh_from_db()
        corpus_file = corpus.files.get()
        self.assertEqual(task.status, ProcessingTaskStatus.FAILED)
        self.assertIn("Invalid aligned TSV row", task.error_message)
        self.assertEqual(corpus.status, CorpusStatus.FAILED)
        self.assertEqual(corpus_file.status, CorpusFileStatus.FAILED)
        self.assertIn("Invalid aligned TSV row", corpus_file.error_message)

    def test_source_file_is_not_modified(self):
        source = self.fixtures_root / "raw_zh.txt"
        before = (source.read_bytes(), source.stat().st_mtime_ns)
        corpus = self.create_corpus(
            corpus_type=CorpusType.RAW_ZH,
            language=CorpusLanguage.ZH,
            files=[("raw_zh.txt", CorpusType.RAW_ZH, CorpusLanguage.ZH)],
        )

        self.run_pipeline(corpus)

        after = (source.read_bytes(), source.stat().st_mtime_ns)
        self.assertEqual(after, before)
        corpus_file = corpus.files.get()
        self.assertEqual(len(corpus_file.checksum_sha256), 64)

    def test_documentation_metadata_is_updated_from_artifacts(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            files=[("raw_en.txt", CorpusType.RAW_EN, CorpusLanguage.EN)],
        )

        _, report = self.run_pipeline(corpus)

        corpus.documentation.refresh_from_db()
        self.assertEqual(corpus.documentation.token_count, report["counts"]["token_count"])
        self.assertEqual(corpus.documentation.type_count, report["counts"]["type_count"])
        self.assertEqual(corpus.documentation.segmentation_tool, "en:regex-baseline-v1")

    def test_duplicate_active_task_is_rejected(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            files=[("raw_en.txt", CorpusType.RAW_EN, CorpusLanguage.EN)],
        )
        create_processing_task(corpus=corpus)

        with self.assertRaises(ProcessingAlreadyQueued):
            create_processing_task(corpus=corpus)

        self.assertEqual(ProcessingTask.objects.filter(corpus=corpus).count(), 1)

    def test_user_can_have_only_one_active_processing_task(self):
        user = get_user_model().objects.create_user(username="queued-owner")
        first = self.create_corpus(
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            files=[("raw_en.txt", CorpusType.RAW_EN, CorpusLanguage.EN)],
        )
        second = self.create_corpus(
            corpus_type=CorpusType.RAW_ZH,
            language=CorpusLanguage.ZH,
            files=[("raw_zh.txt", CorpusType.RAW_ZH, CorpusLanguage.ZH)],
        )
        create_processing_task(corpus=first, requested_by=user)

        with self.assertRaisesMessage(ProcessingAlreadyQueued, "当前账号已有"):
            create_processing_task(corpus=second, requested_by=user)

    def test_database_constraint_rejects_two_active_tasks(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            files=[("raw_en.txt", CorpusType.RAW_EN, CorpusLanguage.EN)],
        )
        ProcessingTask.objects.create(corpus=corpus)

        with self.assertRaises(IntegrityError), transaction.atomic():
            ProcessingTask.objects.create(corpus=corpus)

    def test_celery_task_entrypoint_executes_pipeline(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            files=[("raw_en.txt", CorpusType.RAW_EN, CorpusLanguage.EN)],
        )
        task = create_processing_task(corpus=corpus)

        report = process_corpus_task.apply(args=[str(task.pk)]).get()

        task.refresh_from_db()
        self.assertEqual(task.status, ProcessingTaskStatus.SUCCESS)
        self.assertEqual(report["counts"]["token_count"], 4)

    def test_sync_management_command_processes_corpus(self):
        corpus = self.create_corpus(
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            files=[("raw_en.txt", CorpusType.RAW_EN, CorpusLanguage.EN)],
        )
        output = StringIO()

        call_command("process_corpus", corpus_id=str(corpus.pk), sync=True, stdout=output)

        self.assertIn("Processing success", output.getvalue())
        self.assertTrue(
            ProcessingTask.objects.filter(corpus=corpus, status=ProcessingTaskStatus.SUCCESS).exists()
        )

    def test_user_corpus_cannot_read_source_outside_user_upload_root(self):
        user = get_user_model().objects.create_user(username="owner")
        corpus = self.create_corpus(
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            files=[("raw_en.txt", CorpusType.RAW_EN, CorpusLanguage.EN)],
            source_type=CorpusSourceType.USER,
            owner=user,
        )
        task = create_processing_task(corpus=corpus)

        with self.assertRaisesMessage(ProcessingError, "DATA_ROOT/user_uploads"):
            process_task(task.pk)

        task.refresh_from_db()
        self.assertEqual(task.status, ProcessingTaskStatus.FAILED)
