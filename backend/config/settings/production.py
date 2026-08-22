import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403


DEBUG = False

if SECRET_KEY == "unsafe-local-dev-key":  # noqa: F405
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set in production.")

if not ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set in production.")

if UPLOAD_SCANNER_BACKEND == "apps.corpora.scanners.DisabledUploadScanner":  # noqa: F405
    raise ImproperlyConfigured(
        "UPLOAD_SCANNER_BACKEND must use an active malware scanner in production."
    )

if not METRICS_BEARER_TOKEN:  # noqa: F405
    raise ImproperlyConfigured("METRICS_BEARER_TOKEN must be set in production.")

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

if RAG_INDEXING_ENABLED and (  # noqa: F405
    not RAG_EMBEDDING_BASE_URL  # noqa: F405
    or not RAG_EMBEDDING_API_KEY  # noqa: F405
    or not RAG_EMBEDDING_MODEL  # noqa: F405
    or not RAG_MILVUS_URI  # noqa: F405
):
    raise ImproperlyConfigured(
        "RAG_EMBEDDING_BASE_URL, RAG_EMBEDDING_API_KEY, RAG_EMBEDDING_MODEL, and RAG_MILVUS_URI are required when RAG_INDEXING_ENABLED is true."
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
