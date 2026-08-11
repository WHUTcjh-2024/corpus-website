import os
from unittest import skipUnless

from django.test import TestCase


class ReadinessIntegrationTests(TestCase):
    @skipUnless(
        os.getenv("REQUIRE_REDIS_INTEGRATION") == "true",
        "Redis integration checks run only where Redis is provisioned.",
    )
    def test_readiness_checks_postgres_redis_and_data_root(self):
        response = self.client.get("/readyz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ready",
                "checks": {"database": True, "redis": True, "data_root": True},
            },
        )
