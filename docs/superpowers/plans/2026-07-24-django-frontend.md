# CIM Analyst Django Front End — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Streamlit GUI with a Django + HTMX + Tailwind front end following the managertools pattern, keeping the entire analysis pipeline (`analysis/`, `model/`, `output/`, `extract/`, `gui/engine.py`) untouched.

**Architecture:** Django project lives at the repo root (`manage.py` + `cimweb/` settings package + `webapp/` app) so the analysis modules import with zero path games. Views call `gui.engine.extract_pdf_data()` / `run_analysis()` exactly as Streamlit does today. Deals become database rows (SQLite dev, Neon Postgres prod at cutover); uploaded PDFs and generated outputs stay on the filesystem tree that `CIM_DEALS_DIR` already governs. Server-rendered pages, HTMX for partial updates and progress polling, precompiled Tailwind, no npm.

**Tech Stack:** Django 5.1.15, django-allauth 65.16.1 (closed signup), django-htmx 1.27.0, pytailwindcss (Tailwind 3.4.17), whitenoise, gunicorn, pytest-django. Pins copied verbatim from `/home/terickson/managertools/manager-tool-django/requirements.txt`.

## Global Constraints

- Boring/stable stack only; every new dependency must state why no stdlib alternative works (this plan's justification: Django/allauth/htmx/whitenoise/gunicorn are the exact stack already operated in managertools — one ops pattern across both apps).
- The analysis pipeline is read-only for this project: no edits to `analysis/`, `model/`, `output/`, `extract/`, `config.py`, `run.py` except the one-line engine relocation in Phase 5.
- Django app package is named `webapp` (NOT `deals` — a `deals/` data directory already exists at repo root). Settings package is `cimweb`. Django templates live in `webapp/templates/` (root `templates/` is occupied by the memo template).
- Auth: closed signup (allauth `is_open_for_signup → False`), email login, `@login_required` on every view except `/health/`. The auth PR is HIGH-RISK tier per operator rules: full canonical review cycle, never downgraded.
- Single source of truth: the settings editor (Phase 5) stores only deltas from `config.py`, never copies of it. The comps browser reads the existing SQLite comp DB; comp data is never duplicated into Django models.
- Every form/UI PR requires both a layout/compaction pass and an adversarial density pass (small diffs: one agent may run both sequentially; new pages: two independent agents).
- All money/measure fields mirror the float types of the source `deal_meta.json` / `CIMData` — this is display metadata, not accounting; no Decimal conversion.
- Existing pytest suite (57 tests) must stay green untouched; pytest-django must not break non-Django tests.
- CI: the existing `.github/workflows/test.yml` pytest job must run the Django tests too (same job, same command).
- Ship dark: nothing in this plan changes the deployed Streamlit app until Phase 5 cutover.

---

## Roadmap (one PR per phase; Phases 3–5 get their own detailed plan docs when reached)

| Phase | PR contents | Risk tier | Detailed tasks |
|---|---|---|---|
| 1 | Django scaffold, settings, closed-signup auth, base template + sidebar, `/health/` with git SHA, Tailwind build, CI extension | **High (auth)** | Tasks 1–5 below (this doc) |
| 2 | `Deal` model + migration, `import_deals` command over existing `deals/*/deal_meta.json`, Deal Pipeline list page with filters | Standard | Tasks 6–9 below (this doc) |
| 3 | Upload → dupe check → background extract (thread + DB status + HTMX poll) → extraction report → assumptions editor (collapsible sections, per-deal `assumption_overrides` JSONField, unit-mix row editor) | Standard, UI passes required | Own plan doc |
| 4 | `AnalysisRun` model (status/progress/result_json/output paths), threaded run + HTMX poll, results pages (Summary gates + replacement cost, Returns + sensitivity, Financials, Risks), download endpoints (.docx/.xlsx/.xlsm), Deal detail becomes real | Standard, UI passes required | docs/superpowers/plans/2026-07-27-phase4-analysis-runs-results.md |
| 5 | Comps browser (read-only over `data/cim_comps.db`), settings editor (delta-only `ConfigOverride` — operator directive 2026-07-25: overrides scoped optionally by asset_type and effective-dated, so thresholds can tighten/loosen over time and per property class while past analyses keep the thresholds they ran under), `render.yaml` + Neon + persistent disk, prod deploy + deal import, retire `gui/` + Streamlit + Railway service, move `gui/engine.py` → `engine.py`, docs/CLAUDE.md updates | **High (deploy/cutover + prod migrate ordering)** | Own plan doc |

**Phase 5 pre-cutover checklist (from Phase 2 final review, 2026-07-26 — Postgres-only failure modes invisible on SQLite; resolve before the Neon migrate):**

- `Deal.Meta.ordering` — NULL `analysis_date` sorts first on Postgres, last on SQLite; switch to `F("analysis_date").desc(nulls_last=True)` so undated deals don't jump to the top at cutover.
- Length overflow fails hard on Postgres (SQLite silently accepts): the live tree already has a 114/120-char `deal_id`, and `state` max_length=2 is guaranteed only by the parser regex — validate or widen before importing into Neon. (`import_deals` now catches `DataError` so an overflow skips the folder rather than aborting, but skipped ≠ imported.)
- Add a `build_deal_meta` ↔ `import_deals` round-trip drift test when `webapp/services.py` absorbs the deal_manager helpers (no-drift CI guard for the meta→model key mapping).

**Analysis-layer backlog (operator, 2026-07-25 — separate from this front-end plan, do not fold into Phases 3–5):** quantify special/bonus depreciation in family office return expectations — cost-segregation allocation assumption, bonus depreciation % for the tax year, tax-shield value at assumed LP bracket, presented alongside pre-tax returns. Belongs in `analysis/`/`model/` as its own PR after the front-end port, or earlier if prioritized.

### What gets retired from `gui/` (Phase 5), and what replaces it

| Current file | Fate | Successor |
|---|---|---|
| `gui/app.py`, `gui/pages/*` (upload_analyze, deal_tracker, comp_database, settings, batch stubs) | Delete | `webapp/views/` pages (Phases 2–5) |
| `gui/engine.py` | **Keep — moves to repo root `engine.py`** | Same code; Django import updates from `gui.engine` to `engine` |
| `gui/components/assumptions_editor.py`, `cim_data_editor.py` | Delete | Assumptions editor page (Phase 3) |
| `gui/components/scenario_table.py`, `metrics_row.py`, `gate_display.py`, `file_downloads.py` | Delete | Results templates (Phase 4) |
| `gui/components/config_editors.py` | Delete | Settings editor (Phase 5) |
| `gui/config_manager.py` (session-state config patching) | Delete | `ConfigOverride` delta model (Phase 5) |
| `gui/deal_manager.py` | Partial keep | Folder/meta helpers absorbed into `webapp/services.py`; `deal_meta.json` still written for back-compat with the CLI |
| `gui/session.py` | Delete | Django ORM state (nothing to replace) |
| `streamlit==1.55.0` in requirements, Streamlit Dockerfile CMD, Railway service | Removed at cutover | gunicorn on Render (`render.yaml`, copied shape from managertools) |

`run.py` (terminal CLI) survives untouched.

---

# Phase 1 tasks (this PR — high-risk tier: auth)

## File Structure

- Modify: `requirements.txt` — append Django block
- Create: `manage.py`
- Create: `cimweb/__init__.py`, `cimweb/settings.py`, `cimweb/urls.py`, `cimweb/wsgi.py`
- Create: `webapp/__init__.py`, `webapp/apps.py`, `webapp/auth_adapter.py`, `webapp/views.py`, `webapp/urls.py`
- Create: `webapp/templates/base.html`, `webapp/templates/webapp/home.html`
- Create: `tailwind.config.js`, `static/src/input.css`, `static/css/tw.css` (generated, committed)
- Modify: `pytest.ini` — add `DJANGO_SETTINGS_MODULE`
- Modify: `.github/workflows/test.yml` — install pytest-django, run Django checks
- Modify: `.gitignore` — add `db.sqlite3`, `staticfiles/`
- Test: `tests/test_web_auth.py`

File-count guardrail justification: 14 new files is the irreducible Django scaffold (4 settings-package files, 5 app files, 2 templates, 2 Tailwind inputs, 1 test file); no existing file could host them.

### Task 1: Dependencies + Django scaffold

**Files:**
- Modify: `requirements.txt`
- Create: `manage.py`, `cimweb/__init__.py`, `cimweb/settings.py`, `cimweb/urls.py`, `cimweb/wsgi.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: importable `cimweb.settings` with `DJANGO_SETTINGS_MODULE=cimweb.settings`; `env("ALLOWED_EMAILS")` comma-list gate used by Task 3; `CIM_DEALS_DIR` setting used by Phase 2.

- [ ] **Step 1: Append the Django block to `requirements.txt`**

Copy the version pins for these packages **verbatim** from `/home/terickson/managertools/manager-tool-django/requirements.txt` (they are the proven-in-prod set; do not invent versions): `Django`, `django-allauth`, `django-environ`, `django-htmx`, `gunicorn`, `whitenoise`, `psycopg`, `psycopg-binary`, `pytailwindcss`, `pytest`, `pytest-django`, plus allauth's transitive pins that file carries (`oauthlib`, `PyJWT`, `requests-oauthlib`) if present there. Append under a comment line:

```text
# ── Django front end (pins mirrored from managertools) ──
Django==<pin from managertools>
django-allauth==<pin>
django-environ==<pin>
django-htmx==<pin>
gunicorn==<pin>
whitenoise==<pin>
psycopg==<pin>
psycopg-binary==<pin>
pytailwindcss==<pin>
pytest==<pin>
pytest-django==<pin>
```

(The `<pin>` markers are filled at execution time by reading that file — they are the one thing this plan cannot hardcode without risking drift from the file of record.)

- [ ] **Step 2: Install**

Run: `.venv/bin/python -m pip install -r requirements.txt`
Expected: clean install, no resolver conflicts (Streamlit 1.55 coexists with Django).

- [ ] **Step 3: Create `manage.py`**

```python
#!/usr/bin/env python
"""Django management entry point for the CIM Analyst web front end."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cimweb.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create `cimweb/__init__.py`** (empty), **`cimweb/wsgi.py`**:

```python
import os

from django.core.exceptions import ImproperlyConfigured
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cimweb.settings")
application = get_wsgi_application()

# Fail closed at the prod entrypoint: gunicorn imports this module, so a
# deploy without a real secret refuses to boot. Tests and manage.py never
# import wsgi, so dev/CI are unaffected.
from django.conf import settings  # noqa: E402

if not settings.DEBUG and settings.SECRET_KEY == "dev-only-insecure-key":
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DEBUG=False")
```

- [ ] **Step 5: Create `cimweb/settings.py`**

```python
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

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-insecure-key")
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
    "allauth",
    "allauth.account",
    "django_htmx",
    "webapp",
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
```

- [ ] **Step 6: Create `cimweb/urls.py`**

```python
from django.contrib import admin
from django.urls import include, path

from webapp import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("health/", views.health, name="health"),
    path("", include("webapp.urls")),
]
```

- [ ] **Step 7: Add to `.gitignore`**: `db.sqlite3`, `staticfiles/`, `.env`

- [ ] **Step 8: Sanity check**

Run: `.venv/bin/python manage.py check 2>&1 | tail -3`
Expected: fails with `ModuleNotFoundError: No module named 'webapp'` (app comes next task). That failure IS the pass signal for this step.

- [ ] **Step 9: Commit**

```bash
git add requirements.txt manage.py cimweb/ .gitignore
git commit -m "feat(web): Django scaffold — settings package, wsgi, env-driven config"
```

### Task 2: The `webapp` app — health endpoint + closed-signup adapter

**Files:**
- Create: `webapp/__init__.py` (empty), `webapp/apps.py`, `webapp/auth_adapter.py`, `webapp/views.py`, `webapp/urls.py`
- Test: `tests/test_web_auth.py`
- Modify: `pytest.ini`

**Interfaces:**
- Consumes: `settings.ALLOWED_EMAILS` (Task 1).
- Produces: `webapp.views.health(request) -> JsonResponse` with keys `status`, `db`, `git_sha`; `ClosedSignupAdapter` blocking all signup; `webapp/urls.py` with a `home` route redirecting to `deal-list` (stub until Phase 2 — for now renders `home.html`).

- [ ] **Step 1: Create `cimweb/settings_test.py`** (managertools pattern — plain static storage so `{% static %}` works without collectstatic, in-memory DB):

```python
"""Test settings: in-memory SQLite, no manifest static storage."""
from .settings import *  # noqa: F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
STORAGES = {
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
SECURE_SSL_REDIRECT = False  # test client speaks http; prod redirect stays in settings.py
```

Then update `pytest.ini` so pytest-django activates without disturbing the existing 57 tests:

```ini
[pytest]
testpaths = tests
pythonpath = .
DJANGO_SETTINGS_MODULE = cimweb.settings_test
```

- [ ] **Step 2: Write the failing tests** — `tests/test_web_auth.py`:

```python
"""Web front-end auth + health tests (pytest-django)."""
import pytest


@pytest.mark.django_db
def test_health_is_public_and_reports_sha(client):
    resp = client.get("/health/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert "git_sha" in body


@pytest.mark.django_db
def test_home_requires_login(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.url.startswith("/accounts/login/")


@pytest.mark.django_db
def test_signup_is_closed(client):
    resp = client.get("/accounts/signup/")
    # allauth renders the "signup closed" template when the adapter
    # refuses signup; ensure no form that could create an account
    assert resp.status_code == 200
    assert b"password1" not in resp.content


@pytest.mark.django_db
def test_health_reports_db_failure(client, monkeypatch):
    """The 503 contract: a booted-but-dead DB must not report healthy."""
    from django.db import connection

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(connection, "cursor", boom)
    resp = client.get("/health/")
    assert resp.status_code == 503
    assert resp.json()["db"] is False
```

- [ ] **Step 3: Run tests to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_auth.py -v 2>&1 | tail -5`
Expected: errors (webapp module missing).

- [ ] **Step 4: Create the app files**

`webapp/apps.py`:

```python
from django.apps import AppConfig


class WebappConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "webapp"
```

`webapp/auth_adapter.py` (same shape as managertools `core/auth_adapter.ClosedSignupAdapter`):

```python
from allauth.account.adapter import DefaultAccountAdapter


class ClosedSignupAdapter(DefaultAccountAdapter):
    """Single-operator system: no self-serve signup, ever.

    This adapter only closes signup. ALLOWED_EMAILS is enforced at
    account creation time — `manage.py bootstrap_operator` is the sole
    path that creates accounts, and it reads that allowlist. If a social
    provider is ever enabled, add explicit login-time enforcement here;
    do not assume this class already does it.
    """

    def is_open_for_signup(self, request):
        return False
```

`webapp/views.py`:

```python
import logging
import os

from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render

logger = logging.getLogger("cim_analyst.web")


def health(request):
    """Public health + version endpoint (same contract as managertools).

    Reports git SHA so /verify-deploy gets a definitive match answer,
    and proves the DB is reachable — a booted-but-dead process must
    not report healthy.
    """
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        db_ok = False
        logger.exception("health check: database unreachable")
    sha = (
        os.environ.get("RENDER_GIT_COMMIT")
        or os.environ.get("RAILWAY_GIT_COMMIT_SHA")
        or "unknown"
    )
    return JsonResponse(
        {"status": "ok" if db_ok else "degraded", "db": db_ok, "git_sha": sha[:12]},
        status=200 if db_ok else 503,
    )


@login_required
def home(request):
    return render(request, "webapp/home.html")
```

`webapp/urls.py`:

```python
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
]
```

- [ ] **Step 5: Create throwaway `webapp/templates/webapp/home.html`** (replaced by base.html extension in Task 4):

```html
<!doctype html><title>CIM Analyst</title><p>CIM Analyst web — scaffold OK.</p>
```

- [ ] **Step 6: Migrate the auth tables and run the tests**

Run: `.venv/bin/python manage.py migrate 2>&1 | tail -2 && .venv/bin/python -m pytest tests/test_web_auth.py -v 2>&1 | tail -6`
Expected: 3 passed.

- [ ] **Step 7: Run the FULL suite** (regression gate for the existing 57)

Run: `.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3`
Expected: 60 passed.

- [ ] **Step 8: Commit**

```bash
git add webapp/ tests/test_web_auth.py pytest.ini
git commit -m "feat(web): webapp app — public /health with git_sha, closed-signup auth"
```

### Task 3: Operator account bootstrap

**Files:**
- Create: `webapp/management/__init__.py`, `webapp/management/commands/__init__.py`, `webapp/management/commands/bootstrap_operator.py`
- Test: append to `tests/test_web_auth.py`

**Interfaces:**
- Consumes: `settings.ALLOWED_EMAILS`.
- Produces: `manage.py bootstrap_operator` — idempotently creates one superuser per ALLOWED_EMAILS entry with an unusable password if missing (password set interactively via `changepassword` or env `OPERATOR_PASSWORD` for first deploy).

- [ ] **Step 1: Write the failing test** (append):

```python
@pytest.mark.django_db
def test_bootstrap_operator_idempotent(settings):
    from django.contrib.auth.models import User
    from django.core.management import call_command

    settings.ALLOWED_EMAILS = ["terickson@marathoncre.com"]
    call_command("bootstrap_operator")
    call_command("bootstrap_operator")  # second run: no dupes, no error
    assert User.objects.filter(email="terickson@marathoncre.com").count() == 1


@pytest.mark.django_db
def test_bootstrap_operator_password_and_flags(settings, monkeypatch):
    from django.contrib.auth.models import User
    from django.core.management import call_command

    settings.ALLOWED_EMAILS = ["terickson@marathoncre.com"]
    call_command("bootstrap_operator")
    user = User.objects.get(email="terickson@marathoncre.com")
    assert user.has_usable_password() is False
    assert user.is_staff and user.is_superuser

    User.objects.all().delete()
    monkeypatch.setenv("OPERATOR_PASSWORD", "s3cret-pw")
    call_command("bootstrap_operator")
    user = User.objects.get(email="terickson@marathoncre.com")
    assert user.check_password("s3cret-pw")


@pytest.mark.django_db
def test_bootstrap_operator_reconciles_flags(settings):
    """A pre-existing row (createsuperuser, interrupted run) gets its
    privilege flags re-asserted on the next run — the command is
    idempotently-enforcing, not create-once."""
    from django.contrib.auth.models import User
    from django.core.management import call_command

    settings.ALLOWED_EMAILS = ["terickson@marathoncre.com"]
    User.objects.create_user(username="terickson@marathoncre.com",
                             email="terickson@marathoncre.com")
    call_command("bootstrap_operator")
    user = User.objects.get(username="terickson@marathoncre.com")
    assert user.is_staff and user.is_superuser
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_auth.py::test_bootstrap_operator_idempotent -v 2>&1 | tail -3`
Expected: FAIL (unknown command).

- [ ] **Step 3: Implement `bootstrap_operator.py`**

```python
"""Idempotently create the operator account(s) from ALLOWED_EMAILS.

Run once at deploy: password comes from OPERATOR_PASSWORD env if set,
otherwise the account is created with an unusable password (set later
via `manage.py changepassword`).
"""
import os

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create operator superuser accounts from settings.ALLOWED_EMAILS"

    def handle(self, *args, **options):
        password = os.environ.get("OPERATOR_PASSWORD")
        for email in settings.ALLOWED_EMAILS:
            user, created = User.objects.get_or_create(
                username=email, defaults={"email": email, "is_staff": True,
                                          "is_superuser": True},
            )
            if created:
                if password:
                    user.set_password(password)
                else:
                    user.set_unusable_password()
                user.save()
                self.stdout.write(f"created {email}")
            else:
                # Reconcile, don't just report: a row created any other way
                # (createsuperuser, interrupted run) must end up with the
                # operator flags, or this stops being the enforcement point.
                changed = False
                if not (user.is_staff and user.is_superuser):
                    user.is_staff = True
                    user.is_superuser = True
                    changed = True
                if user.email != email:
                    user.email = email
                    changed = True
                if changed:
                    user.save()
                    self.stdout.write(f"updated {email}")
                else:
                    self.stdout.write(f"exists {email}")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_web_auth.py -v 2>&1 | tail -6`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add webapp/management/
git commit -m "feat(web): bootstrap_operator command — idempotent operator account from ALLOWED_EMAILS"
```

### Task 4: Base template, sidebar, Tailwind

**Files:**
- Create: `tailwind.config.js`, `static/src/input.css`, `static/css/tw.css` (generated), `webapp/templates/base.html`
- Modify: `webapp/templates/webapp/home.html` (extend base)

**Interfaces:**
- Produces: `base.html` with blocks `{% block title %}` and `{% block content %}`; sidebar nav entries named exactly: New Analysis (`/analyze/`, Phase 3), Deal Pipeline (`/deals/`, Phase 2), Comps (`/comps/`, Phase 5), Settings (`/settings/`, Phase 5) — dead links render as plain text until their phase lands. All later page templates extend this file.

- [ ] **Step 1: Port `tailwind.config.js` from managertools** (`/home/terickson/managertools/manager-tool-django/tailwind.config.js`): copy the file, change `content` globs to `["./webapp/templates/**/*.html", "./webapp/**/*.py"]`, keep the accent palette + font stack as-is (visual consistency across the two tools is a feature).

- [ ] **Step 2: Create `static/src/input.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 3: Build and commit the compiled CSS**

Run: `TAILWINDCSS_VERSION=v3.4.17 .venv/bin/tailwindcss -c tailwind.config.js -i static/src/input.css -o static/css/tw.css --minify && ls -la static/css/tw.css`
Expected: file exists, non-trivial size. (pytailwindcss provides the `tailwindcss` binary; the env var pins Tailwind v3 — unpinned, pytailwindcss fetches v4, which cannot read this config. EVERY rebuild — local and the Phase 5 Render buildCommand — must carry `TAILWINDCSS_VERSION=v3.4.17`, exactly as managertools' buildCommand does.)

- [ ] **Step 4: Create `webapp/templates/base.html`** — port the skeleton of `/home/terickson/managertools/manager-tool-django/templates/base.html`: read that file first, keep its sidebar/mobile-toggle/messages structure, then apply these changes:
  - Brand text: `CIM Analyst`.
  - Sidebar sections and links (replace managertools' three sections):
    - **Underwrite**: New Analysis, Deal Pipeline
    - **Reference**: Comps, Settings
    - Bottom: Log out (`{% url 'account_logout' %}`)
  - Remove managertools-specific widgets (global search box, quick-capture input, AI sidebar include).
  - Keep: `{% if messages %}` block, active-nav highlighting via `request.resolver_match.url_name`, static tag loading `static/css/tw.css`.

- [ ] **Step 5: Rewrite `webapp/templates/webapp/home.html`**

```html
{% extends "base.html" %}
{% block title %}CIM Analyst{% endblock %}
{% block content %}
<div class="max-w-3xl">
  <h1 class="text-2xl font-semibold mb-2">CIM Analyst</h1>
  <p class="text-slate-600">Upload a CIM under New Analysis, or review past
  deals in the Deal Pipeline.</p>
</div>
{% endblock %}
```

- [ ] **Step 6: Visual smoke test**

Run: `.venv/bin/python manage.py runserver 8000` (background), then `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/accounts/login/`
Expected: 200. Log in flow renders with sidebar visible after `bootstrap_operator` + `changepassword`.

- [ ] **Step 7: UI passes** — this is a form/UI change: run the layout/compaction pass and the adversarial density pass (one agent, sequentially — small diff) on `base.html` + login template. Fix findings.

- [ ] **Step 8: Commit**

```bash
git add tailwind.config.js static/ webapp/templates/
git commit -m "feat(web): base template + sidebar + compiled Tailwind"
```

### Task 5: CI extension + Phase 1 PR

**Files:**
- Modify: `.github/workflows/test.yml`

- [ ] **Step 1: Extend the test job** — in `.github/workflows/test.yml`, the `Run tests` step already executes `python -m pytest tests/ -v`, which now includes the Django tests (requirements.txt carries pytest-django). Add one step before it:

```yaml
      - name: Django checks
        run: python manage.py check && python manage.py makemigrations --check --dry-run
```

(`makemigrations --check` fails CI if models drift from migrations — the schema-before-code tripwire.)

- [ ] **Step 2: Full local gate**

Run: `.venv/bin/python manage.py check && .venv/bin/python -m pytest tests/ -q 2>&1 | tail -2`
Expected: check silent, all tests pass.

- [ ] **Step 3: Commit, then run the HIGH-RISK PR cycle** (auth PR: implement → diff → code-review → repair → re-review until clean → push → PR → CI green + posted review → merge):

```bash
git add .github/workflows/test.yml
git commit -m "ci: run Django checks + migration drift gate in test job"
```

---

# Phase 2 tasks (next PR — standard tier)

## File Structure

- Create: `webapp/models.py` (Deal), `webapp/migrations/0001_initial.py` (generated)
- Create: `webapp/management/commands/import_deals.py`
- Create: `webapp/templates/webapp/deal_list.html`
- Modify: `webapp/views.py`, `webapp/urls.py`
- Test: `tests/test_web_deals.py`

### Task 6: Deal model

**Files:**
- Create: `webapp/models.py`
- Test: `tests/test_web_deals.py`

**Interfaces:**
- Produces: `webapp.models.Deal` with the exact fields below; `Deal.deal_id` is the slug key matching `deal_meta.json["deal_id"]`; Phase 3+ adds `assumption_overrides` and FK'd `AnalysisRun` — do not pre-create them (YAGNI).

- [ ] **Step 1: Write the failing test** — `tests/test_web_deals.py`:

```python
"""Deal model + import command + list view tests."""
import json

import pytest


@pytest.mark.django_db
def test_deal_str_and_defaults():
    from webapp.models import Deal

    d = Deal.objects.create(deal_id="test-storage", property_name="Test Storage")
    assert str(d) == "Test Storage"
    assert d.recommendation == "N/A"
    assert d.input_files == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_deals.py -v 2>&1 | tail -3`
Expected: FAIL (no models module / no Deal).

- [ ] **Step 3: Create `webapp/models.py`**

```python
from django.db import models


class Deal(models.Model):
    """One underwritten property. Row of record for the pipeline page.

    Mirrors deal_meta.json (gui/deal_manager.build_deal_meta) so existing
    deal folders import losslessly. Floats, not Decimals: this is display
    metadata sourced from a float pipeline, not accounting.
    """

    deal_id = models.SlugField(max_length=120, unique=True)
    property_name = models.CharField(max_length=200)
    city = models.CharField(max_length=100, blank=True, default="")
    state = models.CharField(max_length=2, blank=True, default="")
    asset_type = models.CharField(max_length=60, blank=True, default="")
    nrsf = models.FloatField(null=True, blank=True)
    acreage = models.FloatField(null=True, blank=True)
    asking_price = models.FloatField(null=True, blank=True)
    estimated_fair_value = models.FloatField(null=True, blank=True)
    recommendation = models.CharField(max_length=40, blank=True, default="N/A")
    analysis_date = models.DateField(null=True, blank=True)
    deal_dir = models.CharField(max_length=500, blank=True, default="")
    memo_filename = models.CharField(max_length=300, blank=True, default="")
    excel_filename = models.CharField(max_length=300, blank=True, default="")
    input_files = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-analysis_date", "-created_at"]

    def __str__(self):
        return self.property_name
```

- [ ] **Step 4: Generate the migration and run tests**

Run: `.venv/bin/python manage.py makemigrations webapp && .venv/bin/python -m pytest tests/test_web_deals.py -v 2>&1 | tail -3`
Expected: `0001_initial.py` created; test passes.

- [ ] **Step 5: Commit**

```bash
git add webapp/models.py webapp/migrations/ tests/test_web_deals.py
git commit -m "feat(web): Deal model mirroring deal_meta.json"
```

### Task 7: `import_deals` management command

**Files:**
- Create: `webapp/management/commands/import_deals.py`
- Test: append to `tests/test_web_deals.py`

**Interfaces:**
- Consumes: `Deal` (Task 6); `settings.CIM_DEALS_DIR` (Task 1).
- Produces: `manage.py import_deals` — idempotent `update_or_create` keyed on `deal_id` over every `<CIM_DEALS_DIR>/*/deal_meta.json`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_web_deals.py`):

```python
@pytest.mark.django_db
def test_import_deals_idempotent(tmp_path, settings):
    from django.core.management import call_command
    from webapp.models import Deal

    meta = {
        "deal_id": "expo-storage", "property_name": "Expo Storage",
        "city": "Belton", "state": "TX", "asset_type": "Boat & RV",
        "nrsf": 45000.0, "acreage": 5.2, "asking_price": 3_500_000,
        "estimated_fair_value": 3_100_000, "recommendation": "PURSUE",
        "analysis_date": "2026-07-01", "memo_path": "memo.docx",
        "excel_path": "model.xlsx", "input_files": ["om.pdf"],
    }
    folder = tmp_path / "expo-storage"
    folder.mkdir()
    (folder / "deal_meta.json").write_text(json.dumps(meta))
    settings.CIM_DEALS_DIR = str(tmp_path)

    call_command("import_deals")
    call_command("import_deals")  # idempotent

    assert Deal.objects.count() == 1
    d = Deal.objects.get(deal_id="expo-storage")
    assert d.state == "TX"
    assert d.analysis_date.isoformat() == "2026-07-01"
    assert d.deal_dir == str(folder)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_deals.py::test_import_deals_idempotent -v 2>&1 | tail -3`
Expected: FAIL (unknown command).

- [ ] **Step 3: Implement `import_deals.py`**

```python
"""Import existing deal folders (deal_meta.json) into Deal rows.

Idempotent: re-running updates rows in place, keyed on deal_id.
Malformed folders are reported and skipped, never fatal.
"""
import datetime
import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from webapp.models import Deal


class Command(BaseCommand):
    help = "Import deals/*/deal_meta.json into the Deal table (idempotent)"

    def handle(self, *args, **options):
        root = settings.CIM_DEALS_DIR
        if not os.path.isdir(root):
            self.stdout.write(f"no deals dir at {root}; nothing to import")
            return
        imported = skipped = 0
        for name in sorted(os.listdir(root)):
            meta_path = os.path.join(root, name, "deal_meta.json")
            if not os.path.isfile(meta_path):
                continue
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                analysis_date = None
                if meta.get("analysis_date"):
                    analysis_date = datetime.date.fromisoformat(meta["analysis_date"])
                Deal.objects.update_or_create(
                    deal_id=meta["deal_id"],
                    defaults={
                        "property_name": meta.get("property_name") or "Unknown",
                        "city": meta.get("city") or "",
                        "state": meta.get("state") or "",
                        "asset_type": meta.get("asset_type") or "",
                        "nrsf": meta.get("nrsf"),
                        "acreage": meta.get("acreage"),
                        "asking_price": meta.get("asking_price"),
                        "estimated_fair_value": meta.get("estimated_fair_value"),
                        "recommendation": meta.get("recommendation") or "N/A",
                        "analysis_date": analysis_date,
                        "deal_dir": os.path.join(root, name),
                        "memo_filename": meta.get("memo_path") or "",
                        "excel_filename": meta.get("excel_path") or "",
                        "input_files": meta.get("input_files") or [],
                    },
                )
                imported += 1
            except (KeyError, ValueError, json.JSONDecodeError) as e:
                skipped += 1
                self.stderr.write(f"skipped {name}: {e}")
        self.stdout.write(f"imported/updated {imported}, skipped {skipped}")
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_web_deals.py -v 2>&1 | tail -5`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add webapp/management/commands/import_deals.py tests/test_web_deals.py
git commit -m "feat(web): idempotent import_deals command over deal_meta.json folders"
```

### Task 8: Deal Pipeline list page

**Files:**
- Modify: `webapp/views.py`, `webapp/urls.py`
- Create: `webapp/templates/webapp/deal_list.html`
- Test: append to `tests/test_web_deals.py`

**Interfaces:**
- Consumes: `Deal` (Task 6); `base.html` blocks (Task 4).
- Produces: URL name `deal-list` at `/deals/`; GET filters `state`, `recommendation`, `asset_type` (exact query-param names). Phase 4 will link each row to a `deal-detail` page — rows are plain text for now.

- [ ] **Step 1: Write the failing test** (append):

```python
@pytest.mark.django_db
def test_deal_list_filters(client, django_user_model):
    from webapp.models import Deal

    user = django_user_model.objects.create_user(username="op", password="x")
    client.force_login(user)
    Deal.objects.create(deal_id="alpha", property_name="Alpha Storage",
                        state="TX", recommendation="PURSUE")
    Deal.objects.create(deal_id="bravo", property_name="Bravo Storage",
                        state="CO", recommendation="DECLINE")

    resp = client.get("/deals/?state=TX")
    assert resp.status_code == 200
    assert b"Alpha Storage" in resp.content
    assert b"Bravo Storage" not in resp.content
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_web_deals.py::test_deal_list_filters -v 2>&1 | tail -3`
Expected: FAIL 404 (no /deals/ route).

- [ ] **Step 3: Add the view** (append to `webapp/views.py`):

```python
@login_required
def deal_list(request):
    from webapp.models import Deal

    deals = Deal.objects.all()
    state = request.GET.get("state", "")
    recommendation = request.GET.get("recommendation", "")
    asset_type = request.GET.get("asset_type", "")
    if state:
        deals = deals.filter(state=state)
    if recommendation:
        deals = deals.filter(recommendation=recommendation)
    if asset_type:
        deals = deals.filter(asset_type=asset_type)

    def _options(field):
        return (Deal.objects.exclude(**{field: ""})
                .order_by(field).values_list(field, flat=True).distinct())

    return render(request, "webapp/deal_list.html", {
        "deals": deals,
        "state": state, "recommendation": recommendation, "asset_type": asset_type,
        "state_options": _options("state"),
        "recommendation_options": _options("recommendation"),
        "asset_type_options": _options("asset_type"),
    })
```

Add to `webapp/urls.py`: `path("deals/", views.deal_list, name="deal-list"),`

- [ ] **Step 4: Create `webapp/templates/webapp/deal_list.html`**

```html
{% extends "base.html" %}
{% block title %}Deal Pipeline{% endblock %}
{% block content %}
<div class="max-w-5xl">
  <h1 class="text-xl font-semibold mb-3">Deal Pipeline</h1>

  <form method="get" class="flex gap-2 mb-3 items-end">
    <label class="text-xs text-slate-600">State
      <select name="state" class="block border border-slate-300 rounded px-2 py-1 text-sm">
        <option value="">All</option>
        {% for s in state_options %}<option value="{{ s }}" {% if s == state %}selected{% endif %}>{{ s }}</option>{% endfor %}
      </select>
    </label>
    <label class="text-xs text-slate-600">Recommendation
      <select name="recommendation" class="block border border-slate-300 rounded px-2 py-1 text-sm">
        <option value="">All</option>
        {% for r in recommendation_options %}<option value="{{ r }}" {% if r == recommendation %}selected{% endif %}>{{ r }}</option>{% endfor %}
      </select>
    </label>
    <label class="text-xs text-slate-600">Asset type
      <select name="asset_type" class="block border border-slate-300 rounded px-2 py-1 text-sm">
        <option value="">All</option>
        {% for a in asset_type_options %}<option value="{{ a }}" {% if a == asset_type %}selected{% endif %}>{{ a }}</option>{% endfor %}
      </select>
    </label>
    <button type="submit" class="bg-accent-700 text-white text-sm px-3 py-1.5 rounded">Filter</button>
    <a href="{% url 'deal-list' %}" class="text-sm text-slate-500 underline">Clear</a>
  </form>

  <table class="w-full text-sm border-collapse">
    <thead>
      <tr class="text-left border-b border-slate-300 text-xs text-slate-600">
        <th class="py-1.5 pr-3">Property</th>
        <th class="py-1.5 pr-3">City</th>
        <th class="py-1.5 pr-3">ST</th>
        <th class="py-1.5 pr-3">Asset Type</th>
        <th class="py-1.5 pr-3 text-right">NRSF</th>
        <th class="py-1.5 pr-3 text-right">Asking</th>
        <th class="py-1.5 pr-3 text-right">Est. Fair Value</th>
        <th class="py-1.5 pr-3">Rec.</th>
        <th class="py-1.5">Date</th>
      </tr>
    </thead>
    <tbody>
      {% for d in deals %}
      <tr class="border-b border-slate-100">
        <td class="py-1.5 pr-3 font-medium">{{ d.property_name }}</td>
        <td class="py-1.5 pr-3">{{ d.city }}</td>
        <td class="py-1.5 pr-3">{{ d.state }}</td>
        <td class="py-1.5 pr-3">{{ d.asset_type }}</td>
        <td class="py-1.5 pr-3 text-right">{{ d.nrsf|floatformat:0 }}</td>
        <td class="py-1.5 pr-3 text-right">{% if d.asking_price %}${{ d.asking_price|floatformat:0 }}{% endif %}</td>
        <td class="py-1.5 pr-3 text-right">{% if d.estimated_fair_value %}${{ d.estimated_fair_value|floatformat:0 }}{% endif %}</td>
        <td class="py-1.5 pr-3">{{ d.recommendation }}</td>
        <td class="py-1.5">{{ d.analysis_date|date:"Y-m-d" }}</td>
      </tr>
      {% empty %}
      <tr><td colspan="9" class="py-4 text-slate-500">No deals yet — run
        <code>python manage.py import_deals</code> or start a New Analysis.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 5: Rebuild Tailwind** (new classes used)

Run: `.venv/bin/tailwindcss -c tailwind.config.js -i static/src/input.css -o static/css/tw.css --minify`

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_web_deals.py -v 2>&1 | tail -6`
Expected: all pass.

- [ ] **Step 7: UI passes** — layout/compaction + adversarial density (single agent, sequential — small diff over an existing pattern).

- [ ] **Step 8: Point `home` at the pipeline** — in `webapp/views.py` replace the `home` body:

```python
@login_required
def home(request):
    from django.shortcuts import redirect
    return redirect("deal-list")
```

Delete `webapp/templates/webapp/home.html`. Update `test_home_requires_login` expectation if needed (302 for anonymous still holds).

- [ ] **Step 9: Full suite + commit**

Run: `.venv/bin/python -m pytest tests/ -q 2>&1 | tail -2`
Expected: all pass.

```bash
git add webapp/ static/css/tw.css tests/test_web_deals.py
git commit -m "feat(web): Deal Pipeline list with state/recommendation/asset-type filters"
```

### Task 9: Phase 2 PR

- [ ] **Step 1:** Standard-tier cycle: diff → ONE review pass → repair → re-review only if critical/moderate findings → push → PR → CI green → squash-merge → delete branch → run `import_deals` against the real `deals/` tree locally and confirm row count matches folder count.

---

## Self-Review (performed at write time)

1. **Spec coverage:** stack decision (managertools pattern) → Tasks 1–5; deal rows replacing JSON folders → Tasks 6–9; upload/extract/assumptions/results/downloads/comps/settings/cutover → Phases 3–5 roadmap rows each name their deliverables and get detailed plans at their turn; "what gets retired from gui/" → retirement table.
2. **Placeholder scan:** the only intentional non-literal content is the `<pin from managertools>` markers in Task 1 Step 1 — deliberate: pins are copied from the file of record at execution time to avoid drift; the instruction names the exact source path and package list.
3. **Type consistency:** `deal_id` slug is the join key across `build_deal_meta`, `import_deals`, and `Deal`; `views.health` JSON keys (`status`/`db`/`git_sha`) match the tests; URL names `home`/`deal-list` consistent across urls.py, templates, and tests; `ALLOWED_EMAILS` list-of-lowercase contract consistent between settings and `bootstrap_operator`.
