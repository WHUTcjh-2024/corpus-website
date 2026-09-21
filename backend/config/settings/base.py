from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from kombu import Queue


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
    conn_max_age = int(os.getenv("DB_CONN_MAX_AGE_SECONDS", "0"))
    if conn_max_age < 0:
        raise ValueError("DB_CONN_MAX_AGE_SECONDS must be zero or greater.")
    connection_options = {
        "CONN_MAX_AGE": conn_max_age,
        "CONN_HEALTH_CHECKS": env_bool("DB_CONN_HEALTH_CHECKS", conn_max_age > 0),
    }
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
            **connection_options,
        }
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "corpus_platform"),
        "USER": os.getenv("POSTGRES_USER", "corpus_platform"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "corpus_platform"),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        **connection_options,
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
    "rest_framework",
    "apps.api",
    "apps.health",
    "apps.accounts",
    "apps.corpora",
    "apps.corpus_intake",
    "apps.processing",
    "apps.audits",
    "apps.search",
    "apps.parallel",
    "apps.statistics",
    "apps.exports",
    "apps.outbox",
    "apps.audit",
    "apps.feedback",
    "apps.admin_portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "config.middleware.SlowQueryLoggingMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "config.middleware.AdminLoginProtectionMiddleware",
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
                "apps.health.context_processors.public_site_metadata",
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

LANGUAGE_CODE = "zh-hans"
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

FEEDBACK_SUPPORT_NAME = os.getenv("FEEDBACK_SUPPORT_NAME", "陈俊宏").strip()
FEEDBACK_SUPPORT_EMAIL = os.getenv(
    "FEEDBACK_SUPPORT_EMAIL", "570372819@qq.com"
).strip()
ICP_LICENSE_NUMBER = os.getenv("ICP_LICENSE_NUMBER", "").strip()

LOGIN_RATE_LIMIT_ATTEMPTS = int(os.getenv("LOGIN_RATE_LIMIT_ATTEMPTS", "10"))
LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(
    os.getenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60")
)
LOGIN_PAIR_FAILURE_LIMIT = int(os.getenv("LOGIN_PAIR_FAILURE_LIMIT", "5"))
LOGIN_USERNAME_FAILURE_LIMIT = int(os.getenv("LOGIN_USERNAME_FAILURE_LIMIT", "10"))
LOGIN_IP_FAILURE_LIMIT = int(os.getenv("LOGIN_IP_FAILURE_LIMIT", "30"))
LOGIN_FAILURE_WINDOW_SECONDS = int(os.getenv("LOGIN_FAILURE_WINDOW_SECONDS", "900"))
LOGIN_LOCKOUT_SECONDS = int(os.getenv("LOGIN_LOCKOUT_SECONDS", "900"))
LOGIN_SECURITY_FAIL_CLOSED = env_bool("LOGIN_SECURITY_FAIL_CLOSED", False)
LOGIN_TRUSTED_PROXY_CIDRS = env_list(
    "LOGIN_TRUSTED_PROXY_CIDRS",
    "127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
)
if min(
    LOGIN_RATE_LIMIT_ATTEMPTS,
    LOGIN_RATE_LIMIT_WINDOW_SECONDS,
    LOGIN_PAIR_FAILURE_LIMIT,
    LOGIN_USERNAME_FAILURE_LIMIT,
    LOGIN_IP_FAILURE_LIMIT,
    LOGIN_FAILURE_WINDOW_SECONDS,
    LOGIN_LOCKOUT_SECONDS,
) < 1:
    raise ValueError("Login rate limits and lockout settings must be positive.")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

DATA_ROOT = Path(os.getenv("DATA_ROOT", PROJECT_ROOT / "data")).resolve()
PLATFORM_STAGE = os.getenv("PLATFORM_STAGE", "stage-11")
FIXED_TEST_ACCOUNT_ENABLED = env_bool("FIXED_TEST_ACCOUNT_ENABLED", False)
DATA_SUBDIRS = [
    "inbox",
    "demo",
    "dev_sample",
    "teacher_private",
    "user_uploads",
    "managed_uploads",
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

# Saved query data is intentionally bounded per account. This supports the
# contract's search-history requirement without allowing unbounded JSON/query
# payload growth.
SAVED_SEARCH_MAX_ITEMS = int(os.getenv("SAVED_SEARCH_MAX_ITEMS", 100))
SAVED_SEARCH_MAX_QUERY_BYTES = int(os.getenv("SAVED_SEARCH_MAX_QUERY_BYTES", 4 * 1024))
SAVED_SEARCH_TOTAL_BYTES = int(os.getenv("SAVED_SEARCH_TOTAL_BYTES", 64 * 1024))
if min(SAVED_SEARCH_MAX_ITEMS, SAVED_SEARCH_MAX_QUERY_BYTES, SAVED_SEARCH_TOTAL_BYTES) < 1:
    raise ValueError("Saved search limits must be positive.")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}
PUBLIC_CORPUS_OVERVIEW_CACHE_SECONDS = int(
    os.getenv("PUBLIC_CORPUS_OVERVIEW_CACHE_SECONDS", "60")
)
DATABASE_SLOW_QUERY_MS = float(os.getenv("DATABASE_SLOW_QUERY_MS", "500"))
if PUBLIC_CORPUS_OVERVIEW_CACHE_SECONDS < 1:
    raise ValueError("PUBLIC_CORPUS_OVERVIEW_CACHE_SECONDS must be positive.")
if DATABASE_SLOW_QUERY_MS < 0:
    raise ValueError("DATABASE_SLOW_QUERY_MS must be zero or greater.")

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_QUEUES = (
    Queue("default"),
    Queue("processing"),
    Queue("exports"),
    Queue("audit_commands"),
)
CELERY_TASK_ROUTES = {
    "processing.process_corpus": {"queue": "processing"},
    "audits.publish_parallel_audit_command": {"queue": "audit_commands"},
    "exports.build_export": {"queue": "exports"},
}
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": int(os.getenv("CELERY_VISIBILITY_TIMEOUT_SECONDS", 60 * 60)),
}

# The outbox publisher runs independently from Celery so a broker outage
# cannot lose a task that has already committed to PostgreSQL.
OUTBOX_LEASE_SECONDS = int(os.getenv("OUTBOX_LEASE_SECONDS", "30"))
OUTBOX_RETRY_INITIAL_SECONDS = int(os.getenv("OUTBOX_RETRY_INITIAL_SECONDS", "5"))
OUTBOX_RETRY_MAX_SECONDS = int(os.getenv("OUTBOX_RETRY_MAX_SECONDS", "300"))
OUTBOX_MAX_ATTEMPTS = int(os.getenv("OUTBOX_MAX_ATTEMPTS", "12"))
OUTBOX_PUBLISHED_RETENTION_DAYS = int(
    os.getenv("OUTBOX_PUBLISHED_RETENTION_DAYS", "7")
)

# The Go auditor is a separate data-plane service. Django owns authorization,
# durable orchestration and callback persistence; the service receives only
# data-root-relative references and writes its reports to the shared volume.
CORPUS_AUDITOR_QUEUE_ENABLED = env_bool("CORPUS_AUDITOR_QUEUE_ENABLED", False)
CORPUS_AUDITOR_QUEUE_URL = os.getenv("CORPUS_AUDITOR_QUEUE_URL", REDIS_URL)
CORPUS_AUDITOR_COMMAND_STREAM = os.getenv("CORPUS_AUDITOR_COMMAND_STREAM", "corpus:audit:commands:v1")
CORPUS_AUDITOR_COMMAND_GROUP = os.getenv("CORPUS_AUDITOR_COMMAND_GROUP", "corpus-auditor-v1")
CORPUS_AUDITOR_COMMAND_CONSUMER = os.getenv("CORPUS_AUDITOR_COMMAND_CONSUMER", "")
CORPUS_AUDITOR_RESULT_STREAM = os.getenv("CORPUS_AUDITOR_RESULT_STREAM", "corpus:audit:results:v1")
CORPUS_AUDITOR_RESULT_GROUP = os.getenv("CORPUS_AUDITOR_RESULT_GROUP", "django-audit-projector-v1")
CORPUS_AUDITOR_RESULT_BATCH_SIZE = int(os.getenv("CORPUS_AUDITOR_RESULT_BATCH_SIZE", "100"))
CORPUS_AUDITOR_RESULT_BLOCK_MS = int(os.getenv("CORPUS_AUDITOR_RESULT_BLOCK_MS", "1000"))
CORPUS_AUDITOR_RESULT_CLAIM_IDLE_MS = int(os.getenv("CORPUS_AUDITOR_RESULT_CLAIM_IDLE_MS", "30000"))
CORPUS_AUDITOR_QUEUE_SOCKET_TIMEOUT_SECONDS = float(
    os.getenv("CORPUS_AUDITOR_QUEUE_SOCKET_TIMEOUT_SECONDS", "5")
)
CORPUS_AUDITOR_STREAM_MAXLEN = int(os.getenv("CORPUS_AUDITOR_STREAM_MAXLEN", "100000"))
CORPUS_AUDITOR_MESSAGE_MAX_BYTES = int(os.getenv("CORPUS_AUDITOR_MESSAGE_MAX_BYTES", "1048576"))
# This local executable is an explicit development/test fallback only.
CORPUS_AUDITOR_COMMAND = os.getenv(
    "CORPUS_AUDITOR_COMMAND",
    "go -C ./go/corpus-auditor run ./cmd/corpus-auditor",
)
PARALLEL_AUDIT_TIMEOUT_SECONDS = int(os.getenv("PARALLEL_AUDIT_TIMEOUT_SECONDS", "300"))
PARALLEL_AUDIT_LOW_CONFIDENCE = float(os.getenv("PARALLEL_AUDIT_LOW_CONFIDENCE", "0.6"))
PARALLEL_AUDIT_MIN_LENGTH_RATIO = float(os.getenv("PARALLEL_AUDIT_MIN_LENGTH_RATIO", "0.12"))
PARALLEL_AUDIT_MAX_LENGTH_RATIO = float(os.getenv("PARALLEL_AUDIT_MAX_LENGTH_RATIO", "1.8"))
PARALLEL_AUDIT_MAX_ANOMALIES = int(os.getenv("PARALLEL_AUDIT_MAX_ANOMALIES", "1000"))

if (
    CORPUS_AUDITOR_RESULT_BATCH_SIZE < 1
    or CORPUS_AUDITOR_RESULT_BLOCK_MS < 0
    or CORPUS_AUDITOR_RESULT_CLAIM_IDLE_MS < 1000
    or CORPUS_AUDITOR_QUEUE_SOCKET_TIMEOUT_SECONDS <= 0
    or CORPUS_AUDITOR_STREAM_MAXLEN < 100
    or not 1 <= CORPUS_AUDITOR_MESSAGE_MAX_BYTES <= 1_048_576
):
    raise ValueError("Corpus auditor queue settings must use safe positive bounds.")
METRICS_BEARER_TOKEN = os.getenv("METRICS_BEARER_TOKEN", "")

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
