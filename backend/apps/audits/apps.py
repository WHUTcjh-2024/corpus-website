from django.apps import AppConfig


class AuditsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audits"
    verbose_name = "平行语料审计"
