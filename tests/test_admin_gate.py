"""The /admin/ env-gate (post-cutover hardening): mounted only when
ADMIN_ENABLED is on.

The gate is the URL mount, not the app: `django.contrib.admin` stays in
INSTALLED_APPS in both states, so flipping the flag never changes
migration state. Off means 404 — the path does not exist, so an
unauthenticated probe learns nothing (no redirect confirming an admin
lives here).

cimweb/urls.py builds `urlpatterns` at IMPORT time, so flipping the
setting inside a test does nothing by itself: the module must be
reloaded and Django's resolver cache cleared — both ways, or the flipped
URLconf leaks into every later test.
"""
import importlib

import pytest
from django.conf import settings as django_settings
from django.urls import clear_url_caches

import cimweb.urls


def _remount():
    importlib.reload(cimweb.urls)
    clear_url_caches()


@pytest.fixture
def admin_enabled(settings):
    settings.ADMIN_ENABLED = True
    _remount()
    yield
    settings.ADMIN_ENABLED = False
    _remount()


@pytest.mark.django_db
def test_admin_is_absent_by_default_in_tests(client):
    # settings_test pins ADMIN_ENABLED = False explicitly — the shipped
    # default is DEBUG, which reads the developer's .env and would make
    # the suite's URLconf machine-dependent.
    assert django_settings.ADMIN_ENABLED is False
    assert client.get("/admin/").status_code == 404


@pytest.mark.django_db
def test_disabled_admin_login_is_a_404_too(client):
    # The whole subtree is unmounted, not just the index — /admin/login/
    # answering differently from /admin/nonsense/ would leak the same
    # bit the 404 exists to withhold.
    assert client.get("/admin/login/").status_code == 404


@pytest.mark.django_db
def test_enabled_admin_redirects_to_its_own_login(client, admin_enabled):
    resp = client.get("/admin/")
    assert resp.status_code == 302
    # The admin's OWN login with a next param — not the allauth
    # allowlist login. Asserting the exact target pins that the enabled
    # state is the stock admin, reachable for the DEPLOY.md runbook's
    # verification step.
    assert resp.url == "/admin/login/?next=/admin/"


def test_admin_app_is_installed_in_both_states():
    # If unmounting also removed the app, flipping ADMIN_ENABLED would
    # change migration state — the gate must stay a URL-level decision.
    assert "django.contrib.admin" in django_settings.INSTALLED_APPS
