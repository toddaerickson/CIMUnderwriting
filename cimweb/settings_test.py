"""Test settings: in-memory SQLite, no manifest static storage."""
import os

# MUST precede the star-import: settings.py refuses to boot on the
# insecure default key whenever DEBUG is falsy, and DEBUG defaults to
# False here exactly as it does in prod. That guard is the point — but a
# test run is not a deploy, so supply a real (throwaway) key rather than
# weaken the check. Same shape as the SECURE_SSL_REDIRECT override below:
# the production default is correct and the test env opts out explicitly.
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-key-never-deployed")

from .settings import *  # noqa: E402,F401,F403

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

# Extraction runs inline: a daemon thread opens its own connection to the
# shared in-memory test DB and its writes commit outside the test
# transaction (same reasoning as managertools' COACHING_ENABLED note —
# spawned-thread writes can leak past rollback into unrelated tests).
EXTRACT_USE_THREAD = False

# Same reasoning as EXTRACT_USE_THREAD: analysis runs inline in tests.
ANALYSIS_USE_THREAD = False

# The admin gate defaults to DEBUG, and settings.py reads the developer's
# .env — without this pin the suite's URLconf would depend on which
# machine it runs on. Pinned OFF; the enabled state is exercised by
# flipping the setting and reloading cimweb.urls (tests/test_admin_gate.py).
ADMIN_ENABLED = False
