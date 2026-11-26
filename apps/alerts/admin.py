"""Admin configuration for alerts app."""

from django.contrib import admin

from .models import JobAlert, SavedJob


@admin.register(SavedJob)
class SavedJobAdmin(admin.ModelAdmin):
    list_display = ("job", "user", "status", "applied_at", "updated_at")
    list_filter = ("status",)
    search_fields = ("job__title", "job__company_name", "user__email")


@admin.register(JobAlert)
class JobAlertAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "frequency", "is_active", "last_sent_at")
    list_filter = ("frequency", "is_active")
    search_fields = ("name", "query_text", "user__email")
