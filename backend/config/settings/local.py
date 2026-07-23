from .base import *  # noqa: F401,F403


DEBUG = env_bool("DJANGO_DEBUG", True)  # noqa: F405
FIXED_TEST_ACCOUNT_ENABLED = env_bool("FIXED_TEST_ACCOUNT_ENABLED", True)  # noqa: F405
