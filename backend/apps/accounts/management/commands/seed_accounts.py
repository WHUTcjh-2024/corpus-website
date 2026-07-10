from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import UserRole
from apps.accounts.services import ensure_seed_account


class Command(BaseCommand):
    help = "创建或更新阶段 2 验收使用的 test_user 和 admin 账号。"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--test-password",
            default=os.getenv("SEED_TEST_USER_PASSWORD"),
            help="test_user 密码，也可使用 SEED_TEST_USER_PASSWORD。",
        )
        parser.add_argument(
            "--admin-password",
            default=os.getenv("SEED_ADMIN_PASSWORD"),
            help="admin 密码，也可使用 SEED_ADMIN_PASSWORD。",
        )
        parser.add_argument("--test-email", default="test_user@example.invalid")
        parser.add_argument("--admin-email", default="admin@example.invalid")

    def handle(self, *args, **options) -> None:
        if not options["test_password"] or not options["admin_password"]:
            raise CommandError(
                "必须通过命令参数或环境变量提供 test_user 和 admin 密码。"
            )

        test_user, test_created = ensure_seed_account(
            username="test_user",
            email=options["test_email"],
            password=options["test_password"],
            role=UserRole.TEST,
            full_name="测试用户",
        )
        admin_user, admin_created = ensure_seed_account(
            username="admin",
            email=options["admin_email"],
            password=options["admin_password"],
            role=UserRole.ADMIN,
            full_name="系统管理员",
            is_admin=True,
        )

        self.stdout.write(
            self.style.SUCCESS(
                "seed_accounts 完成："
                f"test_user={'created' if test_created else 'updated'}, "
                f"admin={'created' if admin_created else 'updated'}。"
            )
        )
        self.stdout.write(
            f"账号：{test_user.username}（demo only），{admin_user.username}（admin）"
        )
