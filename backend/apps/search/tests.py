from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
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

from .kwic import KwicSearchEngine


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class KwicSearchTests(TestCase):
    password = "StrongPass!2026"
    fixtures_root = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "search"
    expected_path = Path(__file__).resolve().parents[2] / "tests" / "expected" / "search" / "kwic.json"

    def setUp(self) -> None:
        self.temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.data_root = Path(self.temp_dir)
        self.enterContext(override_settings(DATA_ROOT=self.data_root))
        self.expected = json.loads(self.expected_path.read_text(encoding="utf-8"))
        self.reader = self.create_user("reader", UserRole.JUNIOR)
        self.zh_corpus = self.create_ready_corpus("KWIC 中文", "kwic_zh.txt", CorpusLanguage.ZH)
        self.en_corpus = self.create_ready_corpus("KWIC English", "kwic_en.txt", CorpusLanguage.EN)

    def create_user(self, username: str, role: str):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.invalid",
            password=self.password,
        )
        UserProfile.objects.create(
            user=user,
            full_name=f"{username} 姓名",
            organization="测试单位",
            email=f"{username}@example.invalid",
            role=role,
            requested_role=role,
            use_purpose="阶段 5-6 测试",
            application_reason="验证 KWIC 与 L/R 排序",
            status=ApplicationStatus.APPROVED,
        )
        return user

    def create_ready_corpus(self, name: str, filename: str, language: str) -> Corpus:
        corpus = Corpus.objects.create(
            name=name,
            source_type=CorpusSourceType.DEMO,
            corpus_type=CorpusType.RAW_ZH if language == CorpusLanguage.ZH else CorpusType.RAW_EN,
            language=language,
            access_level=CorpusAccessLevel.DEMO,
            status=CorpusStatus.CREATED,
        )
        path = self.fixtures_root / filename
        CorpusFile.objects.create(
            corpus=corpus,
            original_filename=filename,
            stored_path=str(path),
            detected_type=corpus.corpus_type,
            language=language,
            size_bytes=path.stat().st_size,
            encoding="utf-8",
        )
        task = create_processing_task(corpus=corpus)
        process_task(task.pk)
        corpus.refresh_from_db()
        return corpus

    @staticmethod
    def compact_hits(result) -> list[dict]:
        fields = {
            "left",
            "keyword",
            "right",
            "source_filename",
            "paragraph_ordinal",
            "sentence_ordinal",
        }
        return [
            {key: value for key, value in asdict(hit).items() if key in fields}
            for hit in result.hits
        ]

    def test_chinese_phrase_matches_golden_answer(self):
        expected = self.expected["zh_phrase"]

        result = KwicSearchEngine(
            data_root=self.data_root,
            corpus_id=str(self.zh_corpus.pk),
        ).search(expected["query"], context_size=2)

        self.assertEqual(result.total, expected["total"])
        self.assertEqual(self.compact_hits(result), expected["hits"])

    def test_english_search_is_case_insensitive_and_matches_golden_answer(self):
        expected = self.expected["en_word"]

        result = KwicSearchEngine(
            data_root=self.data_root,
            corpus_id=str(self.en_corpus.pk),
        ).search("DEVELOPMENT", context_size=2)

        self.assertEqual(result.total, expected["total"])
        self.assertEqual(self.compact_hits(result), expected["hits"])

    def test_english_phrase_search(self):
        result = KwicSearchEngine(
            data_root=self.data_root,
            corpus_id=str(self.en_corpus.pk),
        ).search("high quality", context_size=2)

        self.assertEqual(result.total, 1)
        self.assertEqual(result.hits[0].keyword, "High quality")
        self.assertEqual(result.hits[0].right, "development matters")
        self.assertEqual((result.hits[0].r1, result.hits[0].r2), ("development", "matters"))

    def test_pagination_preserves_total_and_limits_page_size(self):
        engine = KwicSearchEngine(data_root=self.data_root, corpus_id=str(self.en_corpus.pk))

        first = engine.search("development", page=1, page_size=1)
        second = engine.search("development", page=2, page_size=1)

        self.assertEqual(first.total, 3)
        self.assertEqual(first.num_pages, 3)
        self.assertEqual(len(first.hits), 1)
        self.assertEqual(first.hits[0].sentence_ordinal, 1)
        self.assertEqual(second.hits[0].sentence_ordinal, 2)

    def test_all_lr_sort_modes_match_golden_order(self):
        expected = self.expected["en_sort"]
        engine = KwicSearchEngine(data_root=self.data_root, corpus_id=str(self.en_corpus.pk))

        for sort_by, sentence_order in expected["orders"].items():
            with self.subTest(sort_by=sort_by):
                result = engine.search(expected["query"], context_size=3, sort_by=sort_by)
                self.assertEqual(result.total, 3)
                self.assertEqual(
                    [hit.sentence_ordinal for hit in result.hits],
                    sentence_order,
                )

    def test_lr_fields_are_generated_independently_from_display_window(self):
        result = KwicSearchEngine(
            data_root=self.data_root,
            corpus_id=str(self.en_corpus.pk),
        ).search("focus", context_size=0)

        hit = result.hits[0]
        self.assertEqual((hit.left, hit.right), ("", ""))
        self.assertEqual((hit.l3, hit.l2, hit.l1), ("Alpha", "beta", "gamma"))
        self.assertEqual((hit.r1, hit.r2, hit.r3), ("Zebra", "yak", "xray"))

    def test_sort_is_applied_before_pagination_and_preserves_total(self):
        engine = KwicSearchEngine(data_root=self.data_root, corpus_id=str(self.en_corpus.pk))

        first = engine.search("focus", sort_by="R1", page=1, page_size=1)
        second = engine.search("focus", sort_by="R1", page=2, page_size=1)

        self.assertEqual(first.total, second.total, 3)
        self.assertEqual(first.hits[0].sentence_ordinal, 5)
        self.assertEqual(second.hits[0].sentence_ordinal, 6)

    def test_chinese_lr_sort_is_stable_and_missing_values_are_last(self):
        result = KwicSearchEngine(
            data_root=self.data_root,
            corpus_id=str(self.zh_corpus.pk),
        ).search("数字经济", sort_by="R1", context_size=3)

        self.assertEqual(result.total, 2)
        self.assertEqual([hit.sentence_ordinal for hit in result.hits], [1, 2])
        self.assertEqual(result.hits[1].r1, "")

    def test_invalid_sort_field_is_rejected_by_engine(self):
        engine = KwicSearchEngine(data_root=self.data_root, corpus_id=str(self.en_corpus.pk))

        with self.assertRaisesMessage(ValueError, "sort_by must be one of"):
            engine.search("focus", sort_by="DROP TABLE tokens")

    def test_no_result_returns_empty_page(self):
        result = KwicSearchEngine(
            data_root=self.data_root,
            corpus_id=str(self.zh_corpus.pk),
        ).search("不存在")

        self.assertEqual(result.total, 0)
        self.assertEqual(result.hits, ())
        self.assertEqual(result.num_pages, 1)

    def test_kwic_page_uses_defaults_and_highlights_keyword(self):
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse("search:kwic", kwargs={"corpus_id": self.en_corpus.pk}),
            {"q": "development"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "共 <strong>3</strong> 条命中", html=True)
        self.assertContains(response, "<mark class=\"px-1\">Development</mark>", html=True)
        self.assertContains(response, "kwic_en.txt")
        self.assertEqual(response.context["result"].context_size, 5)
        self.assertEqual(response.context["result"].page_size, 50)

    def test_kwic_page_integrates_sort_control_tokens_and_pagination(self):
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse("search:kwic", kwargs={"corpus_id": self.en_corpus.pk}),
            {"q": "focus", "sort_by": "R1", "context": 3, "page_size": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["result"].sort_by, "R1")
        self.assertEqual(response.context["result"].hits[0].sentence_ordinal, 5)
        self.assertContains(response, "R1 apple")
        self.assertContains(response, "sort_by=R1")

    def test_invalid_sort_field_is_rejected_by_form(self):
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse("search:kwic", kwargs={"corpus_id": self.en_corpus.pk}),
            {"q": "focus", "sort_by": "INVALID"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["result"])
        self.assertEqual(response.context["form"].errors.as_data()["sort_by"][0].code, "invalid_choice")

    def test_non_authorized_corpus_returns_403(self):
        owner = self.create_user("owner", UserRole.JUNIOR)
        private = Corpus.objects.create(
            name="Private Corpus",
            source_type=CorpusSourceType.USER,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            owner=owner,
            access_level=CorpusAccessLevel.PRIVATE,
            status=CorpusStatus.READY,
        )
        self.client.force_login(self.reader)

        response = self.client.get(reverse("search:kwic", kwargs={"corpus_id": private.pk}))

        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_is_redirected_to_login(self):
        url = reverse("search:kwic", kwargs={"corpus_id": self.en_corpus.pk})

        response = self.client.get(url)

        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={url}",
            fetch_redirect_response=False,
        )

    def test_page_size_over_100_is_rejected(self):
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse("search:kwic", kwargs={"corpus_id": self.en_corpus.pk}),
            {"q": "development", "page_size": 101},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["result"])
        self.assertEqual(response.context["form"].errors.as_data()["page_size"][0].code, "max_value")

    def test_teacher_corpus_has_no_export_endpoint(self):
        self.client.force_login(self.reader)

        response = self.client.get(f"/search/{self.en_corpus.pk}/export/")

        self.assertEqual(response.status_code, 404)
