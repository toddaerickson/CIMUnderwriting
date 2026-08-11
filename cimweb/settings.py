"""Django settings for the CIM Analyst web front end.

Mirrors the managertools pattern: env-driven via django-environ,
SQLite by default, DATABASE_URL override for prod Postgres.
"""
import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    ALLOWED_EMAILS=(list, []),
)
environ.Env.read_env(BASE_DIR / ".env")

INSECURE_DEFAULT_SECRET_KEY = "dev-only-insecure-key"
SECRET_KEY = env("DJANGO_SECRET_KEY", default=INSECURE_DEFAULT_SECRET_KEY)
DEBUG = env("DEBUG")
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
    # HSTS: tell browsers to only ever reach this host over HTTPS. Prod-only
    # (inside `not DEBUG`) so a plain-HTTP dev host is never pinned. Render
    # terminates TLS at the edge and forwards the X-Forwarded-Proto header
    # SECURE_PROXY_SSL_HEADER reads, so the HSTS header is emitted correctly.
    SECURE_HSTS_SECONDS = 31_536_000            # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# These two are already the Django 5.1 defaults; set explicitly so a security
# review sees the intent rather than silence. Safe in every environment.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

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
