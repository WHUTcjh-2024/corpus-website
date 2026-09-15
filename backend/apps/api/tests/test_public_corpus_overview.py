from __future__ import annotations

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusDocumentation,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from apps.processing.models import ProcessingTask, ProcessingTaskStatus

from ..views import PUBLIC_CORPUS_OVERVIEW_CACHE_KEY


class PublicCorpusOverviewTests(TestCase):
    def setUp(self) -> None:
        cache.delete(PUBLIC_CORPUS_OVERVIEW_CACHE_KEY)
        for index in range(4):
            corpus = Corpus.objects.create(
                name=f"Public corpus {index}",
                source_type=CorpusSourceType.DEMO,
                corpus_type=CorpusType.ALIGNED_TSV,
                language=CorpusLanguage.ZH_EN,
                access_level=CorpusAccessLevel.DEMO,
                status=CorpusStatus.READY,
                description="Public sample",
            )
            CorpusDocumentation.objects.update_or_create(
                corpus=corpus,
                defaults={
                    "file_count": 1,
                    "document_count": index + 1,
                    "paragraph_count": 10,
                    "sentence_count": 100,
                    "token_count": 1_000,
                    "type_count": 100,
                },
            )
            ProcessingTask.objects.create(
                corpus=corpus,
                status=ProcessingTaskStatus.SUCCESS,
                progress=100,
            )

    def tearDown(self) -> None:
        cache.delete(PUBLIC_CORPUS_OVERVIEW_CACHE_KEY)

    def test_uncached_overview_has_one_query_and_public_projection(self):
        with self.assertNumQueries(1):
            response = self.client.get("/api/public-corpora/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metrics"]["corpus_count"], 4)
        self.assertEqual(payload["metrics"]["document_count"], 10)
        self.assertEqual(payload["metrics"]["sentence_count"], 400)
        self.assertEqual(payload["metrics"]["token_count"], 4_000)
        self.assertEqual(
            set(payload["corpora"][0]),
            {
                "id",
                "name",
                "corpus_type",
                "corpus_type_label",
                "language",
                "language_label",
                "access_level_label",
                "status_label",
                "description",
                "documentation",
            },
        )

    def test_second_request_is_served_from_cache_without_database_queries(self):
        first_response = self.client.get("/api/public-corpora/")

        with self.assertNumQueries(0):
            second_response = self.client.get("/api/public-corpora/")

        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.json(), first_response.json())

    def test_cache_outage_falls_back_to_database(self):
        with (
            patch("apps.api.views.cache.get", side_effect=ConnectionError("cache unavailable")),
            patch("apps.api.views.cache.set", side_effect=ConnectionError("cache unavailable")),
            self.assertLogs("apps.api.views", level="WARNING") as captured,
            self.assertNumQueries(1),
        ):
            response = self.client.get("/api/public-corpora/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["metrics"]["corpus_count"], 4)
        self.assertEqual(len(captured.records), 2)
