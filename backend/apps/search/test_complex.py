from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
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

from .filters import MatchOperator, QueryAttribute
from .forms import KwicSearchForm
from .query_engine import ComplexQueryEngine
from .query_parser import QuerySyntaxError, parse_query


class ComplexQueryParserTests(SimpleTestCase):
    def test_parser_builds_exact_phrase_wildcard_function_and_attribute_filters(self):
        cases = (
            ("development", QueryAttribute.WORD, MatchOperator.EXACT, 1),
            ('"high quality"', QueryAttribute.WORD, MatchOperator.EXACT, 2),
            ("develop*", QueryAttribute.WORD, MatchOperator.WILDCARD, 1),
            ("starts_with(develop)", QueryAttribute.WORD, MatchOperator.STARTS_WITH, 1),
            ("ends_with(ment)", QueryAttribute.WORD, MatchOperator.ENDS_WITH, 1),
            ("contains(velo)", QueryAttribute.WORD, MatchOperator.CONTAINS, 1),
            ('[pos="NN1"]', QueryAttribute.POS, MatchOperator.EXACT, 1),
            ('[lemma="develop"]', QueryAttribute.LEMMA, MatchOperator.EXACT, 1),
        )

        for query, attribute, operator, count in cases:
            with self.subTest(query=query):
                plan = parse_query(query, language="en")
                self.assertEqual(len(plan.filters), count)
                self.assertEqual(plan.filters[0].attribute, attribute)
                self.assertEqual(plan.filters[0].operator, operator)

    def test_chinese_bare_phrase_is_segmented_into_word_filters(self):
        plan = parse_query("数字经济", language="zh")

        self.assertEqual([item.value for item in plan.filters], ["数字", "经济"])

    def test_invalid_expressions_return_specific_errors(self):
        cases = (
            ('"unclosed', "双引号没有闭合"),
            ('[word="test"', "方括号没有闭合"),
            ("contains(test", "函数括号没有闭合"),
            ("unknown(test)", "函数格式应为"),
            ('[unknown="test"]', "属性条件格式应为"),
            ("***", "通配符必须至少包含"),
            ("contains(*)", "不允许的符号"),
            ("development;drop", "不允许的符号"),
            ('[word="test"];drop', "前缺少空格"),
        )

        for query, message in cases:
            with self.subTest(query=query):
                with self.assertRaisesMessage(QuerySyntaxError, message):
                    parse_query(query, language="en")

    def test_query_rejects_more_than_twenty_token_conditions(self):
        with self.assertRaisesMessage(QuerySyntaxError, "最多包含 20 个 Token"):
            parse_query(" ".join(["word"] * 21), language="en")

    def test_simple_mode_auto_detects_language_and_rejects_an_explicit_mismatch(self):
        automatic = KwicSearchForm(
            {"q": "development", "query_mode": "simple"},
            available_languages=("zh", "en"),
        )
        mismatch = KwicSearchForm(
            {"q": "development", "query_mode": "simple", "language": "zh"},
            available_languages=("zh", "en"),
        )

        self.assertTrue(automatic.is_valid())
        self.assertEqual(automatic.cleaned_data["language"], "en")
        self.assertFalse(mismatch.is_valid())
        self.assertIn("检索词语言与所选语言不一致", mismatch.non_field_errors()[0])


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ComplexQueryEngineTests(TestCase):
    password = "StrongPass!2026"
    search_fixtures = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "search"
    processing_fixtures = (
        Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "processing"
    )
    expected_path = (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "expected"
        / "search"
        / "complex_query.json"
    )

    def setUp(self) -> None:
        self.temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.data_root = Path(self.temp_dir)
        self.enterContext(override_settings(DATA_ROOT=self.data_root))
        self.reader = self.create_user("complex_reader")
        self.en_corpus = self.create_raw_en_corpus()
        self.expected = json.loads(self.expected_path.read_text(encoding="utf-8"))

    def create_user(self, username: str):
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
            role=UserRole.JUNIOR,
            requested_role=UserRole.JUNIOR,
            use_purpose="阶段 10 复杂查询测试",
            application_reason="验证安全 CQP 查询子集",
            status=ApplicationStatus.APPROVED,
        )
        return user

    def create_raw_en_corpus(self) -> Corpus:
        corpus = Corpus.objects.create(
            name="Complex Query English",
            source_type=CorpusSourceType.DEMO,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            access_level=CorpusAccessLevel.DEMO,
            status=CorpusStatus.CREATED,
        )
        path = self.search_fixtures / "kwic_en.txt"
        CorpusFile.objects.create(
            corpus=corpus,
            original_filename=path.name,
            stored_path=str(path),
            detected_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            size_bytes=path.stat().st_size,
            encoding="utf-8",
        )
        process_task(create_processing_task(corpus=corpus).pk)
        corpus.refresh_from_db()
        return corpus

    def create_tagged_pair(self) -> Corpus:
        corpus = Corpus.objects.create(
            name="Complex Query Tagged Pair",
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
            path = self.processing_fixtures / filename
            CorpusFile.objects.create(
                corpus=corpus,
                original_filename=filename,
                stored_path=str(path),
                detected_type=CorpusType.PAIRED_TAGGED_ZH_EN,
                language=language,
                size_bytes=path.stat().st_size,
                encoding="utf-8",
            )
        process_task(create_processing_task(corpus=corpus).pk)
        corpus.refresh_from_db()
        return corpus

    def engine(self, corpus: Corpus | None = None) -> ComplexQueryEngine:
        return ComplexQueryEngine(
            data_root=self.data_root,
            corpus_id=str((corpus or self.en_corpus).pk),
        )

    def test_all_required_query_types_match_golden_answers(self):
        for expected in self.expected["raw_en"]:
            with self.subTest(kind=expected["kind"]):
                result = self.engine().search(expected["query"], language="en")
                self.assertEqual(result.total, expected["total"])
                self.assertEqual(
                    [hit.sentence_ordinal for hit in result.hits],
                    expected["sentence_ordinals"],
                )

    def test_pos_and_lemma_attributes_query_index_annotations(self):
        tagged = self.create_tagged_pair()
        expected = self.expected["tagged_en"]

        tagged_result = self.engine(tagged).search(expected["query"], language="en")
        self.assertEqual(tagged_result.total, expected["total"])
        self.assertEqual(tagged_result.hits[0].keyword, expected["keyword"])

        index_path = self.data_root / "indexes" / str(self.en_corpus.pk) / "kwic_index.sqlite"
        with closing(sqlite3.connect(index_path)) as connection:
            connection.execute(
                "UPDATE tokens SET lemma = ? WHERE normalized = ?",
                ("develop", "development"),
            )
            connection.commit()
        lemma_result = self.engine().search('[lemma="develop"]', language="en")
        self.assertEqual(lemma_result.total, 3)

    def test_complex_query_preserves_sort_and_pagination(self):
        first = self.engine().search(
            "?ocus",
            language="en",
            sort_by="R1",
            page=1,
            page_size=1,
        )
        second = self.engine().search(
            "?ocus",
            language="en",
            sort_by="R1",
            page=2,
            page_size=1,
        )

        self.assertEqual(first.total, second.total, 3)
        self.assertEqual(first.hits[0].sentence_ordinal, 5)
        self.assertEqual(second.hits[0].sentence_ordinal, 6)

    def test_parameterized_filter_does_not_execute_injected_sql(self):
        result = self.engine().search('[word="x\' OR 1=1 --"]', language="en")

        self.assertEqual(result.total, 0)

    def test_page_renders_errors_and_preserves_complex_pagination_parameters(self):
        self.client.force_login(self.reader)
        url = reverse("search:kwic", kwargs={"corpus_id": self.en_corpus.pk})

        response = self.client.get(
            url,
            {
                "q": "develop*",
                "query_mode": "cqp",
                "language": "en",
                "page_size": 1,
            },
        )
        invalid = self.client.get(
            url,
            {"q": '"unclosed', "query_mode": "cqp", "language": "en"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["result"].total, 3)
        self.assertContains(response, "CQP 子集语法示例")
        self.assertContains(response, "query_mode=cqp")
        self.assertContains(response, "language=en")
        self.assertEqual(invalid.status_code, 200)
        self.assertIsNone(invalid.context["result"])
        self.assertContains(invalid, "双引号没有闭合")

    def test_complex_query_keeps_corpus_permission_boundary(self):
        owner = self.create_user("complex_owner")
        private = Corpus.objects.create(
            name="Private Complex Corpus",
            source_type=CorpusSourceType.USER,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            owner=owner,
            access_level=CorpusAccessLevel.PRIVATE,
            status=CorpusStatus.READY,
        )
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse("search:kwic", kwargs={"corpus_id": private.pk}),
            {"q": "develop*", "query_mode": "cqp", "language": "en"},
        )

        self.assertEqual(response.status_code, 403)
