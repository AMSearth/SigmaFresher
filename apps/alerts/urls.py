"""URL routes for alerts and saved jobs."""

from django.urls import path

from . import views

app_name = "alerts"

urlpatterns = [
    path("", views.saved_jobs_list, name="saved_list"),
    path("save/<uuid:job_id>/", views.save_job, name="save_job"),
    path("<uuid:pk>/update/", views.update_saved_job, name="update_saved"),
    path("<uuid:pk>/delete/", views.delete_saved_job, name="delete_saved"),
    path("alerts/", views.alerts_list, name="alerts_list"),
    path("alerts/<uuid:pk>/toggle/", views.toggle_alert, name="toggle_alert"),
    path("alerts/<uuid:pk>/delete/", views.delete_alert, name="delete_alert"),
]
