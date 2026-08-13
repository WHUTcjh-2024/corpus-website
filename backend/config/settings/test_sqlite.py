"""Fast, isolated settings for deterministic unit tests and migration checks."""

from .base import *  # noqa: F401,F403


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "agent-harness-tests",
    }
}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False

# The production platform intentionally relies on PostgreSQL's sequence-backed
# deterministic processing order.  SQLite cannot parse that DDL, so unit tests
# use the normal migration graph with only this PostgreSQL-only migration
# replaced by a no-op state transition.
MIGRATION_MODULES = {"processing": "apps.processing.migrations_sqlite"}
