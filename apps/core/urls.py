"""Core URL routes."""

from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("healthz/", views.healthz, name="healthz"),
    path("csp-report/", views.csp_report, name="csp_report"),
]
