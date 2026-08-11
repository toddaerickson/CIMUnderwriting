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
def test_login_page_is_styled(client):
    """Pins the allauth chrome override: the login page must render
    through webapp/templates/allauth/layouts/base.html (sidebar +
    compiled Tailwind), not allauth's bare bundled template. settings_test
    uses plain StaticFilesStorage (no manifest hash), so the static tag
    resolves to the literal filename."""
    resp = client.get("/accounts/login/")
    assert resp.status_code == 200
    assert b"css/tw." in resp.content


@pytest.mark.django_db
def test_signup_is_closed(client):
    resp = client.get("/accounts/signup/")
    assert resp.status_code == 200
    # allauth renders the "signup closed" template when the adapter
    # refuses signup; ensure no form that could create an account
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
def test_operator_can_log_in_end_to_end(client, settings, monkeypatch):
    """Pins the auth composition: bootstrap_operator-created account +
    allauth email login (which falls back to User.email lookup — no
    EmailAddress row exists) must actually authenticate."""
    from django.core.management import call_command

    settings.ALLOWED_EMAILS = ["terickson@marathoncre.com"]
    monkeypatch.setenv("OPERATOR_PASSWORD", "s3cret-pw")
    call_command("bootstrap_operator")

    resp = client.post("/accounts/login/", {
        "login": "terickson@marathoncre.com",
        "password": "s3cret-pw",
    })
    assert resp.status_code == 302
    assert resp.url == "/deals/"

    # home redirects straight to the deal pipeline (Task 8); follow to
    # confirm the authenticated session actually reaches a rendered page.
    resp = client.get("/", follow=True)
    assert resp.status_code == 200
    assert resp.redirect_chain == [("/deals/", 302)]


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


@pytest.mark.django_db
def test_health_reports_missing_disk(client, monkeypatch, settings):
    monkeypatch.setenv("CIM_DEALS_DIR", "/data/deals")
    settings.CIM_DEALS_DIR = "/nonexistent/deals"
    resp = client.get("/health/")
    assert resp.status_code == 503
    assert resp.json()["disk"] is False
    assert resp.json()["db"] is True


@pytest.mark.django_db
def test_health_disk_probe_skipped_without_env(client):
    resp = client.get("/health/")
    assert resp.status_code == 200
    assert resp.json()["disk"] is True


@pytest.mark.django_db
def test_health_disk_ok_on_pristine_mount(client, monkeypatch, settings):
    """First boot: disk mounted but no data on it yet (runbook steps 4-5
    haven't run). The probe must pass on the mount alone — requiring the
    data files made the first deploy's health check unsatisfiable."""
    monkeypatch.setenv("CIM_DEALS_DIR", "/deals")
    monkeypatch.setenv("COMP_DB_PATH", "/cim_comps.db")
    settings.CIM_DEALS_DIR = "/deals"  # parent "/" is always a mount
    resp = client.get("/health/")
    assert resp.status_code == 200
    assert resp.json()["disk"] is True


# ── Production settings guards ──────────────────────────────────────

def test_insecure_secret_key_is_refused_in_production():
    """`check --deploy` only WARNS about the dev key. With DEBUG=False
    that key is a live session-forgery hole — anyone who has read this
    open-source-shaped repo can mint a signed cookie — so it must stop
    the boot, not annotate it."""
    from django.core.exceptions import ImproperlyConfigured
    from cimweb.settings import check_secret_key, INSECURE_DEFAULT_SECRET_KEY

    with pytest.raises(ImproperlyConfigured) as exc:
        check_secret_key(INSECURE_DEFAULT_SECRET_KEY, debug=False)
    assert "DJANGO_SECRET_KEY" in str(exc.value)


def test_secret_key_guard_allows_dev_and_real_keys():
    """The default must stay usable for a fresh clone (DEBUG=True), and a
    real key must pass in production — a guard that fires on either would
    just get deleted."""
    from cimweb.settings import check_secret_key, INSECURE_DEFAULT_SECRET_KEY

    check_secret_key(INSECURE_DEFAULT_SECRET_KEY, debug=True)
    check_secret_key("a-real-generated-production-key", debug=False)


def test_security_headers_present_in_production_branch():
    """HSTS is the one header the settings file must supply itself.

    nosniff and X-Frame-Options are deliberately absent from settings.py
    (Django defaults them on) — this asserts the DEFAULTS hold, so that
    if a future Django release flips one, this test fails instead of the
    protection silently disappearing."""
    from django.conf import settings as dj

    assert dj.SECURE_CONTENT_TYPE_NOSNIFF is True
    assert dj.X_FRAME_OPTIONS == "DENY"
    assert ("django.middleware.clickjacking.XFrameOptionsMiddleware"
            in dj.MIDDLEWARE)
