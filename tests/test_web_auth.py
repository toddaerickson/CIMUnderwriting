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
