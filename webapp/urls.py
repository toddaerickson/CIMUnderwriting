from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("deals/", views.deal_list, name="deal-list"),
    path("deals/<int:pk>/assumptions/", views.deal_assumptions, name="deal-assumptions"),
    path("deals/<int:pk>/extract-status/", views.extract_status, name="extract-status"),
    path("deals/<int:pk>/extract-retry/", views.extract_retry, name="extract-retry"),
]
