"""Test settings: in-memory SQLite, no manifest static storage."""
from .settings import *  # noqa: F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
STORAGES = {
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# settings.py sets SECURE_SSL_REDIRECT = True whenever DEBUG is falsy (the
# default with no .env override). Django's test client talks plain HTTP, so
# without this the redirect middleware 301s every request before it reaches
# the view, and no view-level assertion (status code, body, adapter
# behavior) is reachable. Same override managertools' settings_test.py makes
# via IS_PROD.
SECURE_SSL_REDIRECT = False
