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

# settings.py also enables HSTS whenever DEBUG is falsy. The test client talks
# plain HTTP, so HSTS headers are never emitted anyway, but we clear it here for
# parity with the SSL-redirect override above — the test env asserts no
# production-only transport rule leaks into a view-level assertion.
SECURE_HSTS_SECONDS = 0

# Extraction runs inline: a daemon thread opens its own connection to the
# shared in-memory test DB and its writes commit outside the test
# transaction (same reasoning as managertools' COACHING_ENABLED note —
# spawned-thread writes can leak past rollback into unrelated tests).
EXTRACT_USE_THREAD = False

# Same reasoning as EXTRACT_USE_THREAD: analysis runs inline in tests.
ANALYSIS_USE_THREAD = False
