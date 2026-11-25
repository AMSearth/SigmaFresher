"""Admin configuration for resumes app."""

from django.contrib import admin

from .models import Resume


@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = (
        "original_filename",
        "user",
        "parse_status",
        "seniority_level",
        "is_active",
        "created_at",
    )
    list_filter = ("parse_status", "is_active", "seniority_level")
    search_fields = ("original_filename", "label", "user__email")
    readonly_fields = ("file_hash", "text_hash", "created_at", "updated_at")
