"""Admin configuration for jobs app."""

from django.contrib import admin

from .models import JobPosting


@admin.register(JobPosting)
class JobPostingAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "company_name",
        "work_mode",
        "source_name",
        "apply_url_type",
        "posted_at",
        "is_active",
    )
    list_filter = ("is_active", "work_mode", "source_type", "apply_url_type", "experience_level")
    search_fields = ("title", "company_name", "location_text", "source_name")
    readonly_fields = ("dedup_hash", "text_hash", "fetched_at", "created_at", "updated_at")
