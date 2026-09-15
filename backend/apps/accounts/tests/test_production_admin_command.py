from __future__ import annotations

import os
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.accounts.models import UserProfile, UserRole


@override_settings(DEBUG=False, FIXED_TEST_ACCOUNT_ENABLED=False)
class ProductionAdminCommandTests(TestCase):
    def password_file(self, password: str = "Unique-Production-Password-937!"):
        temporary = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)
        temporary.write(password + "\n")
        temporary.close()
        os.chmod(temporary.name, 0o600)
        self.addCleanup(Path(temporary.name).unlink, missing_ok=True)
        return temporary.name

    def options(self, **overrides):
        values = {
            "username": "formal-admin",
            "email": "formal-admin@example.com",
            "full_name": "正式管理员",
            "organization": "翻译跨学科研究中心",
            "password_file": self.password_file(),
        }
        values.update(overrides)
        return values

    def test_provisions_idempotent_formal_admin_without_resetting_password(self) -> None:
        options = self.options()
        call_command("provision_production_admin", **options)
        user = get_user_model().objects.get(username="formal-admin")
        profile = UserProfile.objects.get(user=user)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("Unique-Production-Password-937!"))
        self.assertEqual(profile.role, UserRole.ADMIN)

        user.set_password("Manually-Rotated-Password-482!")
        user.save(update_fields=["password"])
        call_command("provision_production_admin", **options)
        user.refresh_from_db()
        self.assertTrue(user.check_password("Manually-Rotated-Password-482!"))

    def test_rejects_test_identity_and_refuses_to_take_over_existing_user(self) -> None:
        with self.assertRaisesMessage(CommandError, "测试账号名"):
            call_command(
                "provision_production_admin", **self.options(username="test_user")
            )

        get_user_model().objects.create_user("formal-admin")
        with self.assertRaisesMessage(CommandError, "拒绝接管"):
            call_command("provision_production_admin", **self.options())

    def test_test_account_commands_cannot_be_forced_in_production(self) -> None:
        with self.assertRaisesMessage(CommandError, "非 DEBUG"):
            call_command("ensure_test_account", force=True)
        with self.assertRaisesMessage(CommandError, "仅供 DEBUG"):
            call_command(
                "seed_accounts",
                test_password="Strong-Test-Password!",
                admin_password="Strong-Admin-Password!",
            )
