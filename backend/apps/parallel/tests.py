from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusFile,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from apps.processing.services import create_processing_task, process_task

from .engine import ParallelQuery, ParallelSearchEngine


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ParallelSearchTests(TestCase):
    fixtures_root = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "processing"
    password = "StrongPass!2026"

    def setUp(self) -> None:
        self.temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.data_root = Path(self.temp_dir)
        self.enterContext(override_settings(DATA_ROOT=self.data_root))
        self.reader = self.create_user("reader", UserRole.JUNIOR)
        self.corpus = self.create_ready_parallel_corpus()
        self.engine = ParallelSearchEngine(
            data_root=self.data_root,
            corpus_id=str(self.corpus.pk),
        )

    def create_user(self, username: str, role: str):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.invalid",
            password=self.password,
        )
        UserProfile.objects.create(
            user=user,
            full_name=username,
            organization="测试单位",
            email=f"{username}@example.invalid",
            role=role,
            requested_role=role if role != UserRole.TEST else "",
            use_purpose="阶段 7 测试",
            application_reason="验证平行检索",
            status=ApplicationStatus.APPROVED,
        )
        return user

    def create_ready_parallel_corpus(self) -> Corpus:
        corpus = Corpus.objects.create(
            name="Aligned Demo",
            source_type=CorpusSourceType.DEMO,
            corpus_type=CorpusType.ALIGNED_TSV,
            language=CorpusLanguage.ZH_EN,
            access_level=CorpusAccessLevel.DEMO,
            status=CorpusStatus.CREATED,
        )
        path = self.fixtures_root / "aligned.tsv"
        CorpusFile.objects.create(
            corpus=corpus,
            original_filename=path.name,
            stored_path=str(path),
            detected_type=CorpusType.ALIGNED_TSV,
            language=CorpusLanguage.ZH_EN,
            size_bytes=path.stat().st_size,
            encoding="utf-8",
        )
        process_task(create_processing_task(corpus=corpus).pk)
        corpus.refresh_from_db()
        return corpus

    def create_ready_paired_paragraph_corpus(self) -> Corpus:
        corpus = Corpus.objects.create(
            name="Provided Paragraph Pair",
            source_type=CorpusSourceType.DEMO,
            corpus_type=CorpusType.PAIRED_RAW_ZH_EN,
            language=CorpusLanguage.ZH_EN,
            access_level=CorpusAccessLevel.DEMO,
            status=CorpusStatus.CREATED,
        )
        for filename, language in (
            ("paired_zh.txt", CorpusLanguage.ZH),
            ("paired_en.txt", CorpusLanguage.EN),
        ):
            path = self.fixtures_root / filename
            CorpusFile.objects.create(
                corpus=corpus,
                original_filename=path.name,
                stored_path=str(path),
                detected_type=CorpusType.PAIRED_RAW_ZH_EN,
                language=language,
                size_bytes=path.stat().st_size,
                encoding="utf-8",
            )
        process_task(create_processing_task(corpus=corpus).pk)
        corpus.refresh_from_db()
        return corpus

    def create_ready_paired_tagged_corpus(self) -> Corpus:
        corpus = Corpus.objects.create(
            name="Provided Tagged Structure Pair",
            source_type=CorpusSourceType.DEMO,
            corpus_type=CorpusType.PAIRED_TAGGED_ZH_EN,
            language=CorpusLanguage.ZH_EN,
            access_level=CorpusAccessLevel.DEMO,
            status=CorpusStatus.CREATED,
        )
        for filename, language in (
            ("paired_tagged_zh.txt", CorpusLanguage.ZH),
            ("paired_tagged_en.txt", CorpusLanguage.EN),
        ):
            path = self.fixtures_root / filename
            CorpusFile.objects.create(
                corpus=corpus,
                original_filename=path.name,
                stored_path=str(path),
                detected_type=CorpusType.PAIRED_TAGGED_ZH_EN,
                language=language,
                size_bytes=path.stat().st_size,
                encoding="utf-8",
            )
        process_task(create_processing_task(corpus=corpus).pk)
        corpus.refresh_from_db()
        return corpus

    def insert_auto_highlight_pairs(self) -> None:
        index_path = self.data_root / "indexes" / str(self.corpus.pk) / "kwic_index.sqlite"
        matched = (
            ("农民组织起来。", "Peasants organized."),
            ("农民参加运动。", "The peasant joined the movement."),
            ("农民需要土地。", "Peasants need land."),
            ("农民协会成立。", "The peasant association formed."),
            ("我们帮助农民。", "We support the peasants."),
            ("农民正在行动。", "A peasant is acting."),
        )
        background = (
            ("工人组织起来。", "Workers organized."),
            ("学生参加活动。", "Students joined the activity."),
            ("会议已经开始。", "The meeting started."),
            ("学校需要教师。", "The school needs teachers."),
            ("代表发表报告。", "The delegate presented a report."),
            ("协会已经成立。", "The association was formed."),
            ("我们支持改革。", "We support the reform."),
            ("群众正在行动。", "The people are acting."),
        )
        with closing(sqlite3.connect(index_path)) as connection:
            position = connection.execute(
                "SELECT COALESCE(MAX(global_position), 0) FROM parallel_pairs"
            ).fetchone()[0]
            for ordinal, (zh_text, en_text) in enumerate((*matched, *background), start=1):
                position += 1
                connection.execute(
                    """
                    INSERT INTO parallel_pairs (
                        global_position, pair_id, pair_ordinal, zh_unit_id, en_unit_id,
                        zh_text, en_text, zh_normalized, en_normalized,
                        alignment_unit, method, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        position,
                        f"auto-{ordinal}",
                        100 + ordinal,
                        f"zh-auto-{ordinal}",
                        f"en-auto-{ordinal}",
                        zh_text,
                        en_text,
                        zh_text.casefold(),
                        en_text.casefold(),
                        "sentence",
                        "provided",
                        1.0,
                    ),
                )
            connection.commit()

    def test_chinese_search_returns_english_translation(self):
        result = self.engine.search(ParallelQuery(q="数字经济", search_side="zh"))

        self.assertEqual(result.total, 1)
        self.assertEqual(result.hits[0].en_text, "The digital economy is growing rapidly.")
        self.assertTrue(any(fragment.matched for fragment in result.hits[0].zh_fragments))

    def test_english_search_is_case_insensitive_and_returns_chinese(self):
        result = self.engine.search(ParallelQuery(q="ARTIFICIAL INTELLIGENCE", search_side="en"))

        self.assertEqual(result.total, 1)
        self.assertEqual(result.hits[0].zh_text, "人工智能改变生活。")

    def test_repeated_pair_evidence_auto_highlights_target_side_translation(self):
        self.insert_auto_highlight_pairs()

        zh_to_en = self.engine.search(ParallelQuery(q="农民", search_side="zh"))
        en_to_zh = self.engine.search(ParallelQuery(q="peasant", search_side="en"))

        self.assertEqual(zh_to_en.total, 6)
        self.assertEqual(set(zh_to_en.auto_target_highlights), {"peasant", "peasants"})
        self.assertTrue(
            all(any(fragment.matched for fragment in hit.en_fragments) for hit in zh_to_en.hits)
        )
        self.assertEqual(en_to_zh.total, 6)
        self.assertEqual(en_to_zh.auto_target_highlights, ("农民",))
        self.assertTrue(
            all(any(fragment.matched for fragment in hit.zh_fragments) for hit in en_to_zh.hits)
        )

    def test_page_labels_auto_translation_highlight_as_corpus_inference(self):
        self.insert_auto_highlight_pairs()
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse("parallel:search", kwargs={"corpus_id": self.corpus.pk}),
            {"q": "农民", "search_side": "zh"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "自动译词高亮（语料内共现推断）")
        self.assertContains(response, "<mark>Peasants</mark>", html=True)

    def test_bilingual_and_not_conditions_are_combined(self):
        included = self.engine.search(
            ParallelQuery(
                q="经济",
                search_side="zh",
                en_contains="growing",
                en_not_contains="declining",
            )
        )
        excluded = self.engine.search(
            ParallelQuery(q="经济", search_side="zh", en_not_contains="digital")
        )

        self.assertEqual(included.total, 1)
        self.assertEqual(excluded.total, 0)

    def test_pair_order_and_pagination_are_stable(self):
        first = self.engine.search(ParallelQuery(q="。", search_side="zh"), page=1, page_size=1)
        second = self.engine.search(ParallelQuery(q="。", search_side="zh"), page=2, page_size=1)

        self.assertEqual(first.total, second.total, 2)
        self.assertEqual(first.hits[0].pair_ordinal, 1)
        self.assertEqual(second.hits[0].pair_ordinal, 2)

    def test_paragraph_alignment_unit_is_supported_by_index(self):
        index_path = self.data_root / "indexes" / str(self.corpus.pk) / "kwic_index.sqlite"
        with closing(sqlite3.connect(index_path)) as connection:
            connection.execute(
                "UPDATE parallel_pairs SET alignment_unit = 'paragraph' WHERE global_position = 2"
            )
            connection.commit()
        result = self.engine.search(
            ParallelQuery(q="人工智能", search_side="zh", alignment_unit="paragraph")
        )

        self.assertEqual(result.total, 1)
        self.assertEqual(result.hits[0].alignment_unit, "paragraph")

    def test_alignment_preview_is_bounded_and_keeps_pair_order(self):
        preview = self.engine.preview(alignment_unit="sentence", limit=1)

        self.assertEqual(len(preview), 1)
        self.assertEqual(preview[0].pair_ordinal, 1)
        self.assertEqual(preview[0].en_text, "The digital economy is growing rapidly.")

    def test_page_renders_parallel_columns_and_safe_highlight(self):
        self.client.force_login(self.reader)
        response = self.client.get(
            reverse("parallel:search", kwargs={"corpus_id": self.corpus.pk}),
            {"q": "digital", "search_side": "en"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "中文原文")
        self.assertContains(response, "English translation")
        self.assertContains(response, "<mark>digital</mark>", html=True)

    def test_documentation_page_previews_authoritative_alignment(self):
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse("corpora:documentation", kwargs={"corpus_id": self.corpus.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "人工对齐预览")
        self.assertContains(response, "The digital economy is growing rapidly.")

    def test_paired_files_default_to_provided_paragraph_alignment(self):
        corpus = self.create_ready_paired_paragraph_corpus()
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse("parallel:search", kwargs={"corpus_id": corpus.pk}),
            {"q": "人工智能", "search_side": "zh"},
        )

        self.assertEqual(response.status_code, 200)
        result = response.context["result"]
        self.assertEqual(result.total, 1)
        self.assertEqual(result.query.alignment_unit, "paragraph")
        self.assertEqual(result.hits[0].method, "provided_paragraph_order")
        self.assertEqual(result.hits[0].confidence, 1.0)
        self.assertContains(response, "It changes lives.")
        self.assertContains(response, "人工段落顺序")
        self.assertEqual(
            list(response.context["form"].fields["alignment_unit"].choices),
            [("paragraph", "段落")],
        )

    def test_tagged_pair_uses_explicit_sentence_ids_and_offers_both_units(self):
        corpus = self.create_ready_paired_tagged_corpus()
        self.client.force_login(self.reader)

        sentence_response = self.client.get(
            reverse("parallel:search", kwargs={"corpus_id": corpus.pk}),
            {"q": "人工智能", "search_side": "zh"},
        )
        paragraph_response = self.client.get(
            reverse("parallel:search", kwargs={"corpus_id": corpus.pk}),
            {"q": "人工智能", "search_side": "zh", "alignment_unit": "paragraph"},
        )

        self.assertEqual(sentence_response.status_code, 200)
        sentence_result = sentence_response.context["result"]
        self.assertEqual(sentence_result.total, 1)
        self.assertEqual(sentence_result.query.alignment_unit, "sentence")
        self.assertEqual(sentence_result.hits[0].en_text, "Artificial intelligence changes lives.")
        self.assertEqual(sentence_result.hits[0].method, "provided_structure_id")
        self.assertContains(sentence_response, "人工结构编号")
        self.assertNotContains(sentence_response, "_NN1")
        self.assertNotIn("/n", sentence_result.hits[0].zh_text)
        self.assertNotIn("_NN", sentence_result.hits[0].en_text)
        self.assertEqual(
            list(sentence_response.context["form"].fields["alignment_unit"].choices),
            [("sentence", "句子"), ("paragraph", "段落")],
        )

        self.assertEqual(paragraph_response.status_code, 200)
        paragraph_result = paragraph_response.context["result"]
        self.assertEqual(paragraph_result.total, 1)
        self.assertEqual(paragraph_result.query.alignment_unit, "paragraph")
        self.assertIn("The digital economy develops.", paragraph_result.hits[0].en_text)

    def test_unauthorized_private_corpus_returns_403(self):
        owner = self.create_user("owner", UserRole.JUNIOR)
        Corpus.objects.filter(pk=self.corpus.pk).update(
            source_type=CorpusSourceType.USER,
            owner=owner,
            access_level=CorpusAccessLevel.PRIVATE,
        )
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse("parallel:search", kwargs={"corpus_id": self.corpus.pk})
        )

        self.assertEqual(response.status_code, 403)

    def test_teacher_or_demo_corpus_export_is_forbidden(self):
        self.client.force_login(self.reader)
        response = self.client.get(
            reverse("parallel:export", kwargs={"corpus_id": self.corpus.pk}),
            {"q": "经济", "search_side": "zh"},
        )

        self.assertEqual(response.status_code, 403)

    def test_owner_can_stream_own_parallel_search_as_tsv(self):
        Corpus.objects.filter(pk=self.corpus.pk).update(
            source_type=CorpusSourceType.USER,
            owner=self.reader,
            access_level=CorpusAccessLevel.PRIVATE,
        )
        self.client.force_login(self.reader)
        response = self.client.get(
            reverse("parallel:export", kwargs={"corpus_id": self.corpus.pk}),
            {"q": "经济", "search_side": "zh"},
        )

        self.assertEqual(response.status_code, 200)
        payload = b"".join(response.streaming_content).decode("utf-8")
        self.assertIn("数字经济快速发展。", payload)
        self.assertIn("The digital economy is growing rapidly.", payload)
