"""Isolated local regression tests: no external database, storage or task services."""
import os
import base64

os.environ.update(
    SECRET_KEY="local-regression-tests-only",
    DATABASE_URL="sqlite:///:memory:",
    USE_AZURE_STORAGE="False",
    USE_S3_STORAGE="False",
    FIELD_ENCRYPTION_KEY=base64.urlsafe_b64encode(b"0" * 32).decode(),
)

from .settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
DEBUG = False
LOGGING = {"version": 1, "disable_existing_loggers": False, "handlers": {"null": {"class": "logging.NullHandler"}}, "loggers": {"django.db.backends.schema": {"handlers": ["null"], "propagate": False}}}
# Historic deployment migrations use PostgreSQL-only SQL. Build current models
# directly for SQLite regression tests; deployment still runs all migrations.
MIGRATION_MODULES = {app: None for app in ["core", "cases", "licensing", "gateways", "knowledge_base", "links", "manual", "app_portal", "esign", "projects", "shared_docs", "announcements"]}
