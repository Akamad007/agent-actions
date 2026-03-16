"""Minimal Django settings for the test suite."""

import tempfile
import os

SECRET_KEY = "test-secret-key-not-for-production"

# Use a temp file so concurrent tests can use WAL mode (not supported in-memory).
_tmp_db = os.path.join(tempfile.gettempdir(), "django_agent_actions_test.db")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": _tmp_db,
        "OPTIONS": {
            "timeout": 20,
        },
        "TEST": {
            "NAME": _tmp_db,
        },
    }
}

INSTALLED_APPS = [
    "django_agent_actions",
]

USE_TZ = True
