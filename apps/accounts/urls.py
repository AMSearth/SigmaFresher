"""URL routes for accounts profile and GDPR deletion."""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("", views.profile_edit, name="profile"),
    path("delete/", views.delete_account, name="delete_account"),
]
