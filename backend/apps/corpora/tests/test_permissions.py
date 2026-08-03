from unittest.mock import patch

from django.test import SimpleTestCase

from apps.accounts.permissions import AccessScope
from apps.corpora.services import can_create_personal_corpus, can_upload_personal_corpus


class CorpusPermissionPolicyTests(SimpleTestCase):
    @patch("apps.corpora.services.workspace_access_scope")
    def test_creation_requires_standard_or_admin_scope(self, scope) -> None:
        user = object()
        for value, expected in (
            (AccessScope.NONE, False),
            (AccessScope.DEMO_ONLY, False),
            (AccessScope.STANDARD, True),
            (AccessScope.ADMIN, True),
        ):
            scope.return_value = value
            self.assertEqual(can_create_personal_corpus(user), expected)

    @patch("apps.corpora.services.workspace_access_scope")
    def test_demo_scope_may_upload_only_private_sandbox_corpora(self, scope) -> None:
        user = object()
        for value, expected in (
            (AccessScope.NONE, False),
            (AccessScope.DEMO_ONLY, True),
            (AccessScope.STANDARD, True),
            (AccessScope.ADMIN, True),
        ):
            scope.return_value = value
            self.assertEqual(can_upload_personal_corpus(user), expected)
