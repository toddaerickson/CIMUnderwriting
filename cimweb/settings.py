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

# Comma-separated allowlist of login emails (closed system).
ALLOWED_EMAILS = [e.strip().lower() for e in env("ALLOWED_EMAILS")]

# Where deal folders live — same env var the Streamlit app and CLI use.
CIM_DEALS_DIR = os.environ.get("CIM_DEALS_DIR", str(BASE_DIR / "deals"))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
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

# Deliberately NOT set here — Django already supplies them, and restating
# them invites drift if the defaults change:
#   SECURE_CONTENT_TYPE_NOSNIFF  defaults True since Django 3.0
#   X_FRAME_OPTIONS              defaults "DENY" since Django 3.0, applied
#                                by XFrameOptionsMiddleware (in MIDDLEWARE)
# An audit reporting these as "missing" has read the settings file, not the
# response headers. Check `curl -I` before adding anything back.

# Password strength for the allauth set/change-password flows. bootstrap_operator
# seeds accounts via set_password(), which bypasses these validators, so the
# operator-seed path is unaffected.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Web-app logging. The CLI keeps its own file audit trail via
# log_config.setup_logging(); the web process logs to stdout instead (12-factor
# — Render captures stdout, and a FileHandler would fight the ephemeral disk).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    # The console handler lives on root; the app loggers below only set a level
    # and propagate up to it. No per-logger handler (avoids double-emit) and no
    # propagate=False (which would hide records from pytest's caplog, since it
    # captures via propagation to the root logger).
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"level": "INFO"},
        "cim_analyst": {"level": "INFO"},
    },
}
