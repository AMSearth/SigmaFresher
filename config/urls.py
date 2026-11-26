"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("profile/", include("apps.accounts.urls")),
    path("resumes/", include("apps.resumes.urls")),
    path("jobs/", include("apps.jobs.urls")),
    path("saved/", include("apps.alerts.urls")),
    path("", include("apps.core.urls")),
]
