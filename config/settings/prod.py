"""Production settings with strict transport and cookie security."""

from .base import *  # noqa: F403,F401
from .base import env

DEBUG = False

SECRET_KEY = env("DJANGO_SECRET_KEY")

SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# PgBouncer transaction pooling compatibility
DATABASES["default"]["CONN_MAX_AGE"] = 0  # noqa: F405
