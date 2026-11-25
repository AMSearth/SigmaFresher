"""Test suite settings."""

from .base import *  # noqa: F403
from .base import BASE_DIR, env

DEBUG = False

DATABASES = {
    "default": env.db(
        "TEST_DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'test_db.sqlite3'}",
    )
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Disable rate limits during unit tests unless explicitly tested
ACCOUNT_RATE_LIMITS = {}
