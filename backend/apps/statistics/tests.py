from __future__ import annotations

import json
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

from .engine import StatisticsEngine


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class StatisticsModuleTests(TestCase):
    fixtures_root = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "processing"
    statistics_fixtures_root = (
        Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "statistics"
    )
    keyword_expected_path = (
        Path(__file__).resolve().parents[2] / "tests" / "expected" / "statistics" / "keyword.json"
    )
    password = "StrongPass!2026"

    def setUp(self) -> None:
        self.temp_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.data_root = Path(self.temp_dir)
        self.enterContext(override_settings(DATA_ROOT=self.data_root))
        self.reader = self.create_user("statistics_reader")
        self.corpus = self.create_ready_tagged_corpus()
        self.engine = StatisticsEngine(
            data_root=self.data_root,
            corpus_id=str(self.corpus.pk),
        )
        self.keyword_target = self.create_ready_raw_en_corpus(
            name="Keyword Target",
            filename="keyword_target_en.txt",
        )
        self.keyword_reference = self.create_ready_raw_en_corpus(
            name="Keyword Reference",
            filename="keyword_reference_en.txt",
        )
        self.keyword_expected = json.loads(
            self.keyword_expected_path.read_text(encoding="utf-8")
        )

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
            use_purpose="阶段 9 测试",
            application_reason="验证 AntConc 统计工具",
            status=ApplicationStatus.APPROVED,
        )
        return user

    def create_ready_tagged_corpus(self) -> Corpus:
        corpus = Corpus.objects.create(
            name="Statistics Tagged Pair",
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

    def create_ready_raw_en_corpus(self, *, name: str, filename: str) -> Corpus:
        corpus = Corpus.objects.create(
            name=name,
            source_type=CorpusSourceType.DEMO,
            corpus_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            access_level=CorpusAccessLevel.DEMO,
            status=CorpusStatus.CREATED,
        )
        path = self.statistics_fixtures_root / filename
        CorpusFile.objects.create(
            corpus=corpus,
            original_filename=filename,
            stored_path=str(path),
            detected_type=CorpusType.RAW_EN,
            language=CorpusLanguage.EN,
            size_bytes=path.stat().st_size,
            encoding="utf-8",
        )
        process_task(create_processing_task(corpus=corpus).pk)
        corpus.refresh_from_db()
        return corpus

    def test_word_list_reports_rank_frequency_normalization_and_pos_filter(self):
        result = self.engine.word_list(language="zh", pos="n", page_size=20)

        economy = next(row for row in result.rows if row.term == "经济")
        self.assertEqual(economy.frequency, 2)
        self.assertGreater(economy.per_million, 0)
        self.assertTrue(all(row.rank >= 1 for row in result.rows))
        self.assertEqual(result.pos, "n")

        index_path = self.data_root / "indexes" / str(self.corpus.pk) / "kwic_index.sqlite"
        with closing(sqlite3.connect(index_path)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertIn("word_totals", tables)
        self.assertIn("word_frequencies", tables)

    def test_ngram_list_uses_materialized_sentence_bounded_index(self):
        result = self.engine.ngrams(
            language="zh",
            n=2,
            min_frequency=1,
            filter_text="数字",
            page_size=20,
        )

        self.assertEqual(result.total_types, 1)
        self.assertEqual(result.rows[0].ngram, "数字经济")
        self.assertEqual(result.rows[0].frequency, 1)

    def test_collocates_support_independent_spans_pos_and_association_scores(self):
        result = self.engine.collocates(
            "经济",
            language="zh",
            left_span=1,
            right_span=1,
            min_frequency=1,
            sort_by="log_dice",
            page_size=20,
        )

        self.assertEqual(result.node_frequency, 2)
        self.assertEqual(
            {row.term for row in result.rows},
            {"数字", "发展", "件"},
        )
        self.assertTrue(all(row.corpus_frequency >= row.frequency for row in result.rows))
        self.assertTrue(all(row.log_dice > 0 for row in result.rows))

        noun_only = self.engine.collocates(
            "经济",
            language="zh",
            left_span=1,
            right_span=1,
            min_frequency=1,
            pos="n",
            page_size=20,
        )
        self.assertEqual({row.term for row in noun_only.rows}, {"数字"})

    def test_concordance_plot_aggregates_hits_into_document_bins(self):
        result = self.engine.concordance_plot("经济", language="zh")

        self.assertEqual(result.total, 2)
        self.assertEqual(len(result.documents), 1)
        self.assertEqual(result.documents[0].hit_count, 2)
        self.assertEqual(len(result.documents[0].cells), 100)
        self.assertEqual(sum(cell.count for cell in result.documents[0].cells), 2)

    def test_keyword_list_matches_golden_log_likelihood_and_effect_size(self):
        result = StatisticsEngine(
            data_root=self.data_root,
            corpus_id=str(self.keyword_target.pk),
        ).keywords(
            reference=StatisticsEngine(
                data_root=self.data_root,
                corpus_id=str(self.keyword_reference.pk),
            ),
            reference_name=self.keyword_reference.name,
            language="en",
            min_frequency=1,
            include_negative=True,
            page_size=20,
        )

        self.assertEqual(result.target_tokens, self.keyword_expected["target_tokens"])
        self.assertEqual(result.reference_tokens, self.keyword_expected["reference_tokens"])
        rows = {row.term: row for row in result.rows}
        positive = self.keyword_expected["positive"]
        apple = rows[positive["term"]]
        self.assertEqual(apple.target_frequency, positive["target_frequency"])
        self.assertEqual(apple.reference_frequency, positive["reference_frequency"])
        self.assertEqual(apple.target_range, positive["target_range"])
        self.assertEqual(apple.reference_range, positive["reference_range"])
        self.assertAlmostEqual(apple.log_likelihood, positive["log_likelihood"], places=12)
        self.assertAlmostEqual(apple.chi_square, positive["chi_square"], places=12)
        self.assertAlmostEqual(apple.log_ratio, positive["log_ratio"], places=12)
        self.assertEqual(apple.direction, "positive")

        negative = self.keyword_expected["negative"]
        banana = rows[negative["term"]]
        self.assertEqual(banana.target_frequency, negative["target_frequency"])
        self.assertEqual(banana.reference_frequency, negative["reference_frequency"])
        self.assertAlmostEqual(banana.log_ratio, negative["log_ratio"], places=12)
        self.assertEqual(banana.direction, "negative")

        positive_only = StatisticsEngine(
            data_root=self.data_root,
            corpus_id=str(self.keyword_target.pk),
        ).keywords(
            reference=StatisticsEngine(
                data_root=self.data_root,
                corpus_id=str(self.keyword_reference.pk),
            ),
            reference_name=self.keyword_reference.name,
            language="en",
            min_frequency=1,
            include_negative=False,
            page_size=20,
        )
        self.assertEqual([row.term for row in positive_only.rows], ["apple"])

    def test_wordcloud_uses_materialized_frequency_stopwords_and_log_scaling(self):
        engine = StatisticsEngine(
            data_root=self.data_root,
            corpus_id=str(self.keyword_target.pk),
        )
        result = engine.wordcloud(
            language="en",
            min_frequency=1,
            max_words=25,
        )

        terms = {term.term: term for term in result.terms}
        self.assertEqual(terms["apple"].frequency, 3)
        self.assertEqual(terms["banana"].frequency, 1)
        self.assertEqual(terms["apple"].font_size, 72.0)
        self.assertEqual(terms["banana"].font_size, 18.0)
        self.assertEqual(result.theme, "ocean")
        self.assertEqual((result.canvas_width, result.canvas_height), (1000, 560))
        self.assertEqual(terms["apple"].color, "#0f4c81")
        for term in result.terms:
            self.assertGreater(term.x, 0)
            self.assertLess(term.x, result.canvas_width)
            self.assertGreater(term.y, 0)
            self.assertLess(term.y, result.canvas_height)

        filtered = engine.wordcloud(
            language="en",
            min_frequency=1,
            max_words=25,
            stopwords=("apple",),
        )
        self.assertEqual([term.term for term in filtered.terms], ["banana"])
        self.assertEqual(filtered.excluded_stopwords, 1)

    def test_statistics_pages_render_and_preserve_language_controls(self):
        self.client.force_login(self.reader)
        word_response = self.client.get(
            reverse("statistics:word_list", kwargs={"corpus_id": self.corpus.pk}),
            {"language": "zh", "pos": "n"},
        )
        ngram_response = self.client.get(
            reverse("statistics:ngrams", kwargs={"corpus_id": self.corpus.pk}),
            {"language": "zh", "n": 2, "min_frequency": 1, "filter": "数字"},
        )
        collocate_response = self.client.get(
            reverse("statistics:collocates", kwargs={"corpus_id": self.corpus.pk}),
            {
                "language": "zh",
                "q": "经济",
                "left_span": 1,
                "right_span": 1,
                "min_frequency": 1,
            },
        )
        plot_response = self.client.get(
            reverse(
                "statistics:concordance_plot",
                kwargs={"corpus_id": self.corpus.pk},
            ),
            {"language": "zh", "q": "经济"},
        )
        keyword_response = self.client.get(
            reverse(
                "statistics:keywords",
                kwargs={"corpus_id": self.keyword_target.pk},
            ),
            {
                "language": "en",
                "reference_corpus": self.keyword_reference.pk,
                "min_frequency": 1,
                "min_range": 1,
                "include_negative": "on",
            },
        )
        wordcloud_response = self.client.get(
            reverse(
                "statistics:wordcloud",
                kwargs={"corpus_id": self.keyword_target.pk},
            ),
            {"language": "en", "min_frequency": 1, "max_words": 25},
        )

        for response in (
            word_response,
            ngram_response,
            collocate_response,
            plot_response,
            keyword_response,
            wordcloud_response,
        ):
            self.assertEqual(response.status_code, 200)
        self.assertContains(word_response, "Per million")
        self.assertContains(ngram_response, "数字经济")
        self.assertContains(collocate_response, "LogDice")
        self.assertContains(plot_response, "100 个位置槽")
        self.assertContains(keyword_response, "Log Ratio")
        self.assertContains(keyword_response, "apple")
        self.assertContains(wordcloud_response, "按词频加权的词云")
        self.assertContains(wordcloud_response, 'class="wordcloud-svg"')
        self.assertContains(wordcloud_response, 'font-size="72.0"')
        self.assertContains(wordcloud_response, "防重叠螺旋布局")

    def test_private_reference_corpus_cannot_be_selected_or_disclosed(self):
        owner = self.create_user("private_reference_owner")
        Corpus.objects.filter(pk=self.keyword_reference.pk).update(
            source_type=CorpusSourceType.USER,
            owner=owner,
            access_level=CorpusAccessLevel.PRIVATE,
        )
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse(
                "statistics:keywords",
                kwargs={"corpus_id": self.keyword_target.pk},
            ),
            {
                "language": "en",
                "reference_corpus": self.keyword_reference.pk,
                "min_frequency": 1,
                "min_range": 1,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "Select a valid choice", status_code=409)
        self.assertNotContains(response, self.keyword_reference.name, status_code=409)

    def test_keyword_choices_exclude_incompatible_tokenization(self):
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse(
                "statistics:keywords",
                kwargs={"corpus_id": self.keyword_target.pk},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.keyword_reference.name)
        self.assertNotContains(response, self.corpus.name)

    def test_keyword_rejects_a_language_with_incompatible_tokenization(self):
        partial_reference = Corpus.objects.create(
            name="Partially Compatible Reference",
            source_type=CorpusSourceType.DEMO,
            corpus_type=CorpusType.PAIRED_TAGGED_ZH_EN,
            language=CorpusLanguage.ZH_EN,
            access_level=CorpusAccessLevel.DEMO,
            status=CorpusStatus.READY,
        )
        partial_reference.documentation.segmentation_tool = (
            "zh:source-provided-pos-v1;en:regex-baseline-v1"
        )
        partial_reference.documentation.save(update_fields=["segmentation_tool"])
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse(
                "statistics:keywords",
                kwargs={"corpus_id": self.corpus.pk},
            ),
            {
                "language": "en",
                "reference_corpus": partial_reference.pk,
                "min_frequency": 1,
                "min_range": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, partial_reference.name)
        self.assertContains(response, "参照语料不包含所选语言")

    def test_private_corpus_statistics_return_403_to_non_owner(self):
        owner = self.create_user("statistics_owner")
        Corpus.objects.filter(pk=self.corpus.pk).update(
            source_type=CorpusSourceType.USER,
            owner=owner,
            access_level=CorpusAccessLevel.PRIVATE,
        )
        self.client.force_login(self.reader)

        response = self.client.get(
            reverse("statistics:word_list", kwargs={"corpus_id": self.corpus.pk})
        )

        self.assertEqual(response.status_code, 403)
