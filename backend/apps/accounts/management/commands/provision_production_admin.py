from __future__ import annotations

import os
import stat
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction

from apps.accounts.models import ApplicationStatus, UserProfile, UserRole


PROVISIONING_MARKER = "由 provision_production_admin 安全配置创建"
RESERVED_TEST_USERNAMES = {"test", "test_user"}


class Command(BaseCommand):
    help = "从受限密码文件创建正式生产管理员；重复执行不会覆盖已修改的密码。"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--username", default=os.getenv("PRODUCTION_ADMIN_USERNAME", "")
        )
        parser.add_argument("--email", default=os.getenv("PRODUCTION_ADMIN_EMAIL", ""))
        parser.add_argument(
            "--full-name", default=os.getenv("PRODUCTION_ADMIN_FULL_NAME", "")
        )
        parser.add_argument(
            "--organization",
            default=os.getenv(
                "PRODUCTION_ADMIN_ORGANIZATION",
                "武汉理工大学外国语学院翻译跨学科研究中心",
            ),
        )
        parser.add_argument(
            "--password-file",
            default=os.getenv("PRODUCTION_ADMIN_PASSWORD_FILE", ""),
        )

    def handle(self, *args, **options) -> None:
        if settings.DEBUG:
            raise CommandError("正式管理员配置命令只能在非 DEBUG 环境运行。")

        username = str(options["username"]).strip()
        email = str(options["email"]).strip().lower()
        full_name = str(options["full_name"]).strip()
        organization = str(options["organization"]).strip()
        if not all((username, email, full_name, organization)):
            raise CommandError("正式管理员用户名、姓名、单位和邮箱均不能为空。")
        if username.casefold() in RESERVED_TEST_USERNAMES:
            raise CommandError("正式管理员不能使用固定测试账号名。")
        if email.endswith(".invalid"):
            raise CommandError("正式管理员不能使用测试邮箱域名。")
        try:
            validate_email(email)
        except ValidationError as exc:
            raise CommandError("正式管理员邮箱格式无效。") from exc

        password_path = self._validated_password_path(options["password_file"])
        password = password_path.read_text(encoding="utf-8").rstrip("\r\n")
        user_model = get_user_model()
        try:
            user_model._meta.get_field(user_model.USERNAME_FIELD).run_validators(username)
        except ValidationError as exc:
            raise CommandError("正式管理员用户名格式无效。") from exc
        password_user = user_model(username=username, email=email)
        try:
            validate_password(password, user=password_user)
        except ValidationError as exc:
            raise CommandError("正式管理员密码不符合安全策略：" + "；".join(exc.messages)) from exc

        with transaction.atomic():
            existing = (
                user_model.objects.select_for_update()
                .filter(username__iexact=username)
                .first()
            )
            if existing is None:
                user = user_model.objects.create_superuser(
                    username=username,
                    email=email,
                    password=password,
                )
                created = True
            else:
                user = existing
                created = False
                profile = UserProfile.objects.filter(user=user).first()
                if profile is None or profile.application_reason != PROVISIONING_MARKER:
                    raise CommandError(
                        "同名账号不是本命令创建的正式管理员；拒绝接管或转换现有账号。"
                    )
                user.email = email
                user.is_active = True
                user.is_staff = True
                user.is_superuser = True
                user.save(
                    update_fields=["email", "is_active", "is_staff", "is_superuser"]
                )

            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    "full_name": full_name,
                    "organization": organization,
                    "email": email,
                    "role": UserRole.ADMIN,
                    "requested_role": "",
                    "use_purpose": "平台生产运维",
                    "application_reason": PROVISIONING_MARKER,
                    "status": ApplicationStatus.APPROVED,
                },
            )

        action = "已创建" if created else "已验证并同步资料"
        self.stdout.write(self.style.SUCCESS(f"正式管理员 {username} {action}。"))

    @staticmethod
    def _validated_password_path(value: object) -> Path:
        if not value:
            raise CommandError("PRODUCTION_ADMIN_PASSWORD_FILE 未配置。")
        path = Path(str(value))
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise CommandError("管理员密码文件必须是存在的绝对普通文件，且不能是符号链接。")
        if path.stat().st_size == 0 or path.stat().st_size > 1024:
            raise CommandError("管理员密码文件必须为 1 至 1024 字节。")
        if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise CommandError("管理员密码文件权限必须为 600 或更严格。")
        return path
