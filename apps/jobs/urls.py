"""URL routes for jobs app."""

from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("", views.job_search, name="search"),
    path("search-web/", views.trigger_web_search, name="trigger_web_search"),
    path("search-runs/<uuid:pk>/", views.search_run_status, name="search_run_status"),
    path("<uuid:pk>/", views.job_detail, name="detail"),
]
