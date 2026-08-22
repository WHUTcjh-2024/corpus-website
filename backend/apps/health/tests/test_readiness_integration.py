import os
from unittest import skipUnless
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.audits.queue import AuditQueueUnavailable


class ReadinessIntegrationTests(TestCase):
    @override_settings(CORPUS_AUDITOR_QUEUE_ENABLED=False)
    @patch("apps.health.views._redis_ready", return_value=True)
    def test_readiness_reports_required_dependency_contract(self, _redis_ready):
        response = self.client.get("/readyz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["checks"],
            {
                "database": True,
                "redis": True,
                "data_root": True,
                "agent_model": True,
                "rag_vector_store": True,
                "auditor_queue": True,
            },
        )

    @override_settings(CORPUS_AUDITOR_QUEUE_ENABLED=True)
    @patch("apps.health.views._redis_ready", return_value=True)
    @patch("apps.health.views.AuditQueue.ping")
    def test_readiness_checks_auditor_redis_stream_dependency(self, ping, _redis_ready):
        response = self.client.get("/readyz")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["checks"]["auditor_queue"])
        ping.assert_called_once()

    @override_settings(
        CORPUS_AUDITOR_QUEUE_ENABLED=False,
        RAG_INDEXING_ENABLED=True,
        RAG_MILVUS_URI="http://milvus.test:19530",
    )
    @patch("apps.health.views._redis_ready", return_value=True)
    @patch("apps.health.views.MilvusVectorStore.ping")
    def test_readiness_checks_milvus_when_rag_indexing_is_enabled(self, ping, _redis_ready):
        response = self.client.get("/readyz")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["checks"]["rag_vector_store"])
        ping.assert_called_once()

    @override_settings(CORPUS_AUDITOR_QUEUE_ENABLED=True)
    @patch("apps.health.views._redis_ready", return_value=True)
    @patch("apps.health.views.AuditQueue.ping", side_effect=AuditQueueUnavailable("offline"))
    def test_readiness_rejects_work_when_auditor_queue_is_unavailable(
        self, _ping, _redis_ready
    ):
        response = self.client.get("/readyz")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["checks"]["auditor_queue"])

    @skipUnless(
        os.getenv("REQUIRE_REDIS_INTEGRATION") == "true",
        "Redis integration checks run only where Redis is provisioned.",
    )
    def test_readiness_checks_postgres_redis_and_data_root(self):
        response = self.client.get("/readyz")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["checks"]["database"])
        self.assertTrue(response.json()["checks"]["redis"])
        self.assertTrue(response.json()["checks"]["data_root"])
