from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("deals/", views.deal_list, name="deal-list"),
    path("deals/<int:pk>/assumptions/", views.deal_assumptions, name="deal-assumptions"),
    path("deals/<int:pk>/extract-status/", views.extract_status, name="extract-status"),
    path("deals/<int:pk>/extract-retry/", views.extract_retry, name="extract-retry"),
    path("analyze/", views.analyze, name="analyze"),
    path("deals/<int:pk>/discard/", views.deal_discard, name="deal-discard"),
    path("deals/unit-mix-row/", views.unit_mix_row, name="unit-mix-row"),
    path("deals/<int:pk>/", views.deal_detail, name="deal-detail"),
    path("deals/<int:pk>/run/", views.deal_run, name="deal-run"),
    path("deals/<int:pk>/run-status/", views.run_status, name="run-status"),
    path("deals/<int:pk>/download/<str:kind>/", views.deal_download, name="deal-download"),
    path("comps/", views.comps, name="comps"),
    path("settings/", views.settings_page, name="settings"),
    path("settings/overrides/<int:pk>/delete/", views.override_delete,
         name="override-delete"),
]
