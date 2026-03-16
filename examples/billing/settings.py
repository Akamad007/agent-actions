"""
Minimal Django settings for the billing example.

Run the dev server:
    pip install django-agent-actions
    python manage.py migrate
    python manage.py runserver

Then try:
    curl http://127.0.0.1:8000/agent-actions/actions/
    curl -X POST http://127.0.0.1:8000/agent-actions/actions/get_invoice/execute/ \
         -H "Content-Type: application/json" \
         -H "X-Actor-Id: alice" \
         -H "X-Roles: finance" \
         -d '{"inputs": {"invoice_id": "INV-001"}}'
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "replace-me-in-production"

DEBUG = True

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django_agent_actions",
    "examples.billing",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "billing.db",
    }
}

ROOT_URLCONF = "examples.billing.urls"

USE_TZ = True

# ---------------------------------------------------------------------------
# agent-actions config (all keys optional)
# ---------------------------------------------------------------------------
from django_agent_actions.policies import RiskBasedPolicy  # noqa: E402

AGENT_ACTIONS = {
    # High-risk actions require human approval; low/medium are auto-allowed.
    "DEFAULT_POLICY": RiskBasedPolicy(),
}
