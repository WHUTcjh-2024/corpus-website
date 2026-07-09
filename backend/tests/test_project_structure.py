from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class ProjectStructureTests(SimpleTestCase):
    def test_expected_app_directories_exist(self):
        apps_root = Path(settings.BASE_DIR) / "apps"
        expected = {
            "accounts",
            "audit",
            "corpora",
            "corpus_intake",
            "exports",
            "feedback",
            "health",
            "parallel",
            "processing",
            "search",
            "statistics",
        }

        existing = {path.name for path in apps_root.iterdir() if path.is_dir()}

        self.assertTrue(expected.issubset(existing))

    def test_runtime_data_root_is_outside_backend(self):
        backend_root = Path(settings.BASE_DIR).resolve()
        data_root = Path(settings.DATA_ROOT).resolve()

        self.assertFalse(data_root.is_relative_to(backend_root))
