from django.contrib import admin
from django.urls import include, path

from webapp import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("health/", views.health, name="health"),
    path("", include("webapp.urls")),
]
