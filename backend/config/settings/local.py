from .base import *  # noqa: F401,F403


DEBUG = env_bool("DJANGO_DEBUG", True)  # noqa: F405
FIXED_TEST_ACCOUNT_ENABLED = env_bool("FIXED_TEST_ACCOUNT_ENABLED", True)  # noqa: F405

# The Vite development server proxies API requests while the browser keeps
# the frontend origin (127.0.0.1:5173). Trust both local hostnames so Django's
# CSRF origin check accepts the development login form.
CSRF_TRUSTED_ORIGINS = [
    *CSRF_TRUSTED_ORIGINS,  # noqa: F405
    "http://127.0.0.1:5173",
    "http://localhost:5173",
]
