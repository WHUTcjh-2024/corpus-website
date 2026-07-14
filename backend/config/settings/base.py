from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse


BASE_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BASE_DIR.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def database_from_env() -> dict[str, object]:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        parsed = urlparse(database_url)
        if parsed.scheme not in {"postgres", "postgresql"}:
            raise ValueError("DATABASE_URL must use postgres:// or postgresql://")
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "localhost",
            "PORT": str(parsed.port or 5432),
        }
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "corpus_platform"),
        "USER": os.getenv("POSTGRES_USER", "corpus_platform"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "corpus_platform"),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-local-dev-key")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.health",
    "apps.accounts",
    "apps.corpora",
    "apps.corpus_intake",
    "apps.processing",
    "apps.search",
    "apps.parallel",
    "apps.statistics",
    "apps.exports",
    "apps.audit",
    "apps.feedback",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.audit.context_processors.teacher_watermark",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {"default": database_from_env()}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:dashboard"
LOGOUT_REDIRECT_URL = "home"

DATA_ROOT = Path(os.getenv("DATA_ROOT", PROJECT_ROOT / "data")).resolve()
PLATFORM_STAGE = os.getenv("PLATFORM_STAGE", "stage-11")
DATA_SUBDIRS = [
    "inbox",
    "demo",
    "dev_sample",
    "teacher_private",
    "user_uploads",
    "processed",
    "indexes",
    "exports",
    "manifests",
]

# User uploads are deliberately bounded at both file and account level. Test
# accounts use a smaller sandbox quota while approved users retain the 30 MB
# project default.
USER_UPLOAD_MAX_FILE_BYTES = int(os.getenv("USER_UPLOAD_MAX_FILE_BYTES", 30 * 1024 * 1024))
USER_UPLOAD_TOTAL_BYTES = int(os.getenv("USER_UPLOAD_TOTAL_BYTES", 30 * 1024 * 1024))
TEST_UPLOAD_MAX_FILE_BYTES = int(os.getenv("TEST_UPLOAD_MAX_FILE_BYTES", 2 * 1024 * 1024))
TEST_UPLOAD_TOTAL_BYTES = int(os.getenv("TEST_UPLOAD_TOTAL_BYTES", 5 * 1024 * 1024))
UPLOAD_SCANNER_BACKEND = os.getenv(
    "UPLOAD_SCANNER_BACKEND",
    "apps.corpora.scanners.DisabledUploadScanner",
)
CLAMAV_HOST = os.getenv("CLAMAV_HOST", "127.0.0.1")
CLAMAV_PORT = int(os.getenv("CLAMAV_PORT", "3310"))
CLAMAV_TIMEOUT_SECONDS = float(os.getenv("CLAMAV_TIMEOUT_SECONDS", "15"))

# Export jobs are asynchronous and deliberately bounded to protect the worker,
# source corpora, and users from accidental bulk disclosure.
EXPORT_TTL_SECONDS = int(os.getenv("EXPORT_TTL_SECONDS", 24 * 60 * 60))
EXPORT_MAX_ROWS = int(os.getenv("EXPORT_MAX_ROWS", 100_000))
EXPORT_MAX_DOWNLOADS = int(os.getenv("EXPORT_MAX_DOWNLOADS", 5))
EXPORT_MAX_JOBS_PER_HOUR = int(os.getenv("EXPORT_MAX_JOBS_PER_HOUR", 10))

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.getenv("DJANGO_LOG_LEVEL", "INFO"),
    },
}
