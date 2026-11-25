"""URL routes for resumes app."""

from django.urls import path

from . import views

app_name = "resumes"

urlpatterns = [
    path("", views.resume_list, name="list"),
    path("<uuid:pk>/status/", views.resume_status, name="status"),
    path("<uuid:pk>/activate/", views.resume_activate, name="activate"),
    path("<uuid:pk>/reparse/", views.resume_reparse, name="reparse"),
    path("<uuid:pk>/download/", views.resume_download, name="download"),
    path("<uuid:pk>/delete/", views.resume_delete, name="delete"),
]
