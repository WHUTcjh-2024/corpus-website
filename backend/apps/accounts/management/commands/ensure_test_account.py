from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import UserRole
from apps.accounts.services import ensure_seed_account


class Command(BaseCommand):
    help = "确保本地固定测试账号 test/test 处于启用和已审核状态。"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="忽略 FIXED_TEST_ACCOUNT_ENABLED；仅用于明确的测试环境初始化。",
        )

    def handle(self, *args, **options) -> None:
        if not settings.FIXED_TEST_ACCOUNT_ENABLED and not options["force"]:
            raise CommandError(
                "固定测试账号未启用。仅可在本地设置 FIXED_TEST_ACCOUNT_ENABLED=true。"
            )
        if not settings.DEBUG and not options["force"]:
            raise CommandError("拒绝在非 DEBUG 环境创建固定弱口令测试账号。")

        user, created = ensure_seed_account(
            username="test",
            email="test@example.invalid",
            password="test",
            role=UserRole.TEST,
            full_name="测试用户",
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"固定测试账号已{'创建' if created else '校正'}："
                f"{user.username}/test（已审核，demo only）。"
            )
        )
