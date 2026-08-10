from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from webapp import views

urlpatterns = [
    path("accounts/", include("allauth.urls")),
    path("health/", views.health, name="health"),
    path("", include("webapp.urls")),
]

# The admin is mounted only when ADMIN_ENABLED is on (default: DEBUG —
# see cimweb/settings.py). Off means the path simply does not exist: a
# 404, not a redirect, so a probe of a production host cannot tell
# /admin/ from any other unmapped path. The app itself stays in
# INSTALLED_APPS in both states — the gate is the URL mount, so flipping
# the flag never changes migration state. Note the mount happens at
# IMPORT time: tests that flip the flag must reload this module and
# clear Django's URL caches (tests/test_admin_gate.py).
if settings.ADMIN_ENABLED:
    urlpatterns.insert(0, path("admin/", admin.site.urls))
