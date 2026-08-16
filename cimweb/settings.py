"""Django settings for the CIM Analyst web front end.

Mirrors the managertools pattern: env-driven via django-environ,
SQLite by default, DATABASE_URL override for prod Postgres.
"""
import os
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    ALLOWED_EMAILS=(list, []),
)
environ.Env.read_env(BASE_DIR / ".env")

INSECURE_DEFAULT_SECRET_KEY = "dev-only-insecure-key"


def check_secret_key(secret_key: str, debug: bool) -> None:
    """Refuse to boot production on the dev key.

    The default exists so a fresh clone runs without a .env; with
    DEBUG=False it is a live session-forgery hole, and Django's own
    `check --deploy` only WARNS. A missing DJANGO_SECRET_KEY in prod is
    a deploy misconfiguration, so it fails loudly at import rather than
    serving signed cookies anyone can mint. Split out as a function so
    the guard is testable without re-importing the settings module."""
    if not debug and secret_key == INSECURE_DEFAULT_SECRET_KEY:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is unset and DEBUG is False — refusing to "
            "start production on the insecure development key. Set "
            "DJANGO_SECRET_KEY in the environment (see DEPLOY.md)."
        )


SECRET_KEY = env("DJANGO_SECRET_KEY", default=INSECURE_DEFAULT_SECRET_KEY)
DEBUG = env("DEBUG")
check_secret_key(SECRET_KEY, DEBUG)
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# /admin/ is mounted only when this is on (see cimweb/urls.py). Off means
# the path is never mapped — a 404, not a redirect — so an unauthenticated
# probe of a production host learns nothing. Defaults to DEBUG: on for a
# dev box whose .env sets DEBUG=true, off in production unless flipped
# deliberately (DEPLOY.md runbook step 7 sets ADMIN_ENABLED=1 for the
# verification pass and removes it after). Operator decision 2026-08-10.
ADMIN_ENABLED = env.bool("ADMIN_ENABLED", default=DEBUG)

# Comma-separated allowlist of login emails (closed system).
ALLOWED_EMAILS = [e.strip().lower() for e in env("ALLOWED_EMAILS")]

# Where deal folders live — same env var the Streamlit app and CLI use.
CIM_DEALS_DIR = os.environ.get("CIM_DEALS_DIR", str(BASE_DIR / "deals"))

# Keep the Census API key out of the logs. urllib3 logs the full query string
# at DEBUG on every successful request, and the key travels as a query
# parameter. The CLI gets this through `log_config.setup_logging`; the web app
# never calls it, so the pin is applied here instead — one definition in
# log_config, two entry points, so the two interfaces cannot drift.
#
# Deliberately NOT a `LOGGING` dict: Django's default already suits this app,
# and restating it to change one logger's level would be a large surface added
# for a small reason. A level pin is the whole requirement.
from log_config import pin_third_party_loggers  # noqa: E402

pin_third_party_loggers()

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # `intcomma`, for the deal table and the detail sub-header. Those are
    # the two places a raw figure reaches the page without passing through
    # `webapp.results`, which formats everything else it emits — so they
    # were the two places printing 48762 next to $58,051,289.
    "django.contrib.humanize",
    # webapp precedes allauth so its templates/allauth/... overrides
    # (chrome for login/logout/signup-closed pages) win over allauth's
    # own bundled templates — app_directories.Loader resolves by
    # INSTALLED_APPS order.
    "webapp",
    "allauth",
    "allauth.account",
    "django_htmx",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "cimweb.urls"
WSGI_APPLICATION = "cimweb.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],  # app-dirs only; root templates/ belongs to the memo writer
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DATABASES["default"]["CONN_MAX_AGE"] = 60

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# allauth: email login, closed signup (see webapp/auth_adapter.py)
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_ADAPTER = "webapp.auth_adapter.ClosedSignupAdapter"
LOGIN_REDIRECT_URL = "/deals/"
LOGIN_URL = "/accounts/login/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/accounts/login/"

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TIME_ZONE = "America/Chicago"
USE_TZ = True

CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS", default=[], cast=list)
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # SECURE_SSL_REDIRECT upgrades a plaintext request only AFTER it has
    # been sent; HSTS stops the browser from sending it at all. Env-tunable
    # so a cautious cutover can start at 3600 and raise once verified —
    # a too-long value is not retractable from a browser that cached it.
    SECURE_HSTS_SECONDS = env("SECURE_HSTS_SECONDS", default=31536000, cast=int)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    # Preload stays OFF: submission to the browser preload list is a
    # one-way door that needs a deliberate act, not a default.
    SECURE_HSTS_PRELOAD = False

# CI runs `check --deploy --fail-level WARNING` (test.yml), so any NEW
# deploy warning fails the build. Exactly one check is silenced:
# security.W021 flags SECURE_HSTS_PRELOAD not being True, and preload is
# off ON PURPOSE — see the one-way-door comment above. Nothing else may
# join this list without the same kind of recorded reason.
#
# W009 (weak SECRET_KEY) deliberately stays LIVE, but be precise about
# what that buys, because the obvious assumption is wrong: `check
# --deploy` runs ONLY in CI, against CI's own throwaway key. Nothing in
# the deploy path runs it, so W009 never vets the production key. It is
# kept live so that a future weak key cannot slip in unnoticed, and CI
# satisfies it with a 50+ character throwaway rather than silencing it
# for every environment at once.
#
# One consequence worth knowing before it surprises someone: running
# `manage.py check --deploy` ON THE RENDER HOST reports W009. Render's
# `generateValue` mints a base64-encoded 256-bit key — 44 characters —
# and W009's threshold is a flat 50-character length heuristic, which
# that key fails while carrying far more entropy than the heuristic is
# proxying for. It is a false alarm, not a weak key. Do not "fix" it by
# silencing W009 here; that would also silence the real case.
SILENCED_SYSTEM_CHECKS = ["security.W021"]

# Deliberately NOT set here — Django already supplies them, and restating
# them invites drift if the defaults change:
#   SECURE_CONTENT_TYPE_NOSNIFF  defaults True since Django 3.0
#   X_FRAME_OPTIONS              defaults "DENY" since Django 3.0, applied
#                                by XFrameOptionsMiddleware (in MIDDLEWARE)
# An audit reporting these as "missing" has read the settings file, not the
# response headers. Check `curl -I` before adding anything back.
