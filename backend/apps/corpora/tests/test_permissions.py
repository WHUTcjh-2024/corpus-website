from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole
from apps.accounts.permissions import AccessScope
from apps.corpora.models import (
    Corpus,
    CorpusAccessLevel,
    CorpusLanguage,
    CorpusSourceType,
    CorpusStatus,
    CorpusType,
)
from apps.corpora.services import (
    can_create_personal_corpus,
    can_upload_personal_corpus,
    catalog_corpora_with_access_for,
)


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


class CorpusCatalogAccessTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="catalog-admin",
            email="catalog-admin@example.test",
            password="admin-password",
        )
        self.member = user_model.objects.create_user(
            username="catalog-member",
            email="catalog-member@example.test",
            password="member-password",
        )
        UserProfile.objects.create(
            user=self.member,
            full_name="Catalog Member",
            organization="测试单位",
            email="catalog-member@example.test",
            role=UserRole.JUNIOR,
            requested_role=UserRole.JUNIOR,
            use_purpose="验证语料目录",
            application_reason="验证锁定语料展示与授权后访问。",
            status=ApplicationStatus.APPROVED,
        )
        self.corpus = Corpus.objects.create(
            name="仅指定用户可访问的教师语料",
            source_type=CorpusSourceType.TEACHER,
            corpus_type=CorpusType.RAW_ZH,
            language=CorpusLanguage.ZH,
            access_level=CorpusAccessLevel.PRIVATE,
            status=CorpusStatus.READY,
        )

    def test_locked_platform_corpus_is_discoverable_but_not_accessible(self):
        catalog = catalog_corpora_with_access_for(self.member)
        record = catalog.get(pk=self.corpus.pk)
        self.assertFalse(record.has_access)

        self.client.force_login(self.member)
        response = self.client.get(reverse("corpora:list"))
        self.assertContains(response, self.corpus.name)
        self.assertContains(response, "corpus-row--locked")
        self.assertContains(response, "您暂无权限访问该语料")

        response = self.client.get(
            reverse("corpora:documentation", kwargs={"corpus_id": self.corpus.pk}),
            follow=True,
        )
        self.assertRedirects(response, reverse("corpora:list"))
        self.assertContains(response, f"您暂无权限访问“{self.corpus.name}”")

    def test_direct_grant_switches_the_catalog_record_to_accessible(self):
        self.corpus.access_grants.create(user=self.member, granted_by=self.admin)

        record = catalog_corpora_with_access_for(self.member).get(pk=self.corpus.pk)
        self.assertTrue(record.has_access)

        self.client.force_login(self.member)
        response = self.client.get(reverse("corpora:list"))
        self.assertContains(
            response,
            reverse("corpora:documentation", kwargs={"corpus_id": self.corpus.pk}),
        )
