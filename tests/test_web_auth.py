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
