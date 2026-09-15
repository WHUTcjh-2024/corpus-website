import os

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import validate_email

from .base import *  # noqa: F401,F403


DEBUG = False
LOGIN_SECURITY_FAIL_CLOSED = env_bool("LOGIN_SECURITY_FAIL_CLOSED", True)  # noqa: F405
DATABASES["default"]["CONN_MAX_AGE"] = int(  # noqa: F405
    os.getenv("DB_CONN_MAX_AGE_SECONDS", "60")
)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = env_bool(  # noqa: F405
    "DB_CONN_HEALTH_CHECKS", True
)

if SECRET_KEY == "unsafe-local-dev-key":  # noqa: F405
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set in production.")

if not ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set in production.")

if DATABASES["default"]["CONN_MAX_AGE"] < 1:  # noqa: F405
    raise ImproperlyConfigured("DB_CONN_MAX_AGE_SECONDS must be positive in production.")

if not DATABASES["default"]["CONN_HEALTH_CHECKS"]:  # noqa: F405
    raise ImproperlyConfigured("DB_CONN_HEALTH_CHECKS must be true in production.")

if DATABASE_SLOW_QUERY_MS <= 0:  # noqa: F405
    raise ImproperlyConfigured("DATABASE_SLOW_QUERY_MS must be positive in production.")

if UPLOAD_SCANNER_BACKEND == "apps.corpora.scanners.DisabledUploadScanner":  # noqa: F405
    raise ImproperlyConfigured(
        "UPLOAD_SCANNER_BACKEND must use an active malware scanner in production."
    )

if not METRICS_BEARER_TOKEN:  # noqa: F405
    raise ImproperlyConfigured("METRICS_BEARER_TOKEN must be set in production.")

if not FEEDBACK_SUPPORT_NAME:  # noqa: F405
    raise ImproperlyConfigured("FEEDBACK_SUPPORT_NAME must be set in production.")
try:
    validate_email(FEEDBACK_SUPPORT_EMAIL)  # noqa: F405
except ValidationError as exc:
    raise ImproperlyConfigured(
        "FEEDBACK_SUPPORT_EMAIL must be a valid address in production."
    ) from exc
if FEEDBACK_SUPPORT_EMAIL.lower().endswith(".invalid"):  # noqa: F405
    raise ImproperlyConfigured(
        "FEEDBACK_SUPPORT_EMAIL must not use the reserved .invalid domain in production."
    )

if not CORPUS_AUDITOR_QUEUE_ENABLED:  # noqa: F405
    raise ImproperlyConfigured("CORPUS_AUDITOR_QUEUE_ENABLED must be true in production.")

if not (  # noqa: F405
    CORPUS_AUDITOR_QUEUE_URL  # noqa: F405
    and CORPUS_AUDITOR_COMMAND_STREAM  # noqa: F405
    and CORPUS_AUDITOR_RESULT_STREAM  # noqa: F405
):
    raise ImproperlyConfigured(
        "CORPUS_AUDITOR_QUEUE_URL, CORPUS_AUDITOR_COMMAND_STREAM, and CORPUS_AUDITOR_RESULT_STREAM are required in production."
    )

if AGENT_MODEL_ENABLED and (  # noqa: F405
    not AGENT_MODEL_BASE_URL  # noqa: F405
    or not AGENT_MODEL_API_KEY  # noqa: F405
    or not AGENT_MODEL_NAME  # noqa: F405
):
    raise ImproperlyConfigured(
        "AGENT_MODEL_BASE_URL, AGENT_MODEL_API_KEY, and AGENT_MODEL_NAME are required when AGENT_MODEL_ENABLED is true."
    )

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)  # noqa: F405
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
