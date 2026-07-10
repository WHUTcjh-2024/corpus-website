from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse


class HealthEndpointTests(SimpleTestCase):
    def test_home_page_loads(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "在线语料库平台")
        self.assertContains(response, "Stage 4")

    def test_healthz_returns_liveness_payload(self):
        response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["stage"], "stage-0")


class Stage0SettingsTests(SimpleTestCase):
    def test_database_backend_is_postgresql(self):
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], "django.db.backends.postgresql")

    def test_celery_uses_redis(self):
        self.assertTrue(settings.CELERY_BROKER_URL.startswith("redis://"))
        self.assertTrue(settings.CELERY_RESULT_BACKEND.startswith("redis://"))

    def test_data_root_declares_required_subdirectories(self):
        self.assertIn("teacher_private", settings.DATA_SUBDIRS)
        self.assertIn("processed", settings.DATA_SUBDIRS)
        self.assertIn("indexes", settings.DATA_SUBDIRS)
