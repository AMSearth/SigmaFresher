"""Admin configuration for discovery app."""

from django.contrib import admin

from .models import CompanyBoard, SearchRun


@admin.register(CompanyBoard)
class CompanyBoardAdmin(admin.ModelAdmin):
    list_display = ("name", "ats_type", "board_token", "is_active", "last_synced_at")
    list_filter = ("ats_type", "is_active")
    search_fields = ("name", "board_token", "domain")


@admin.register(SearchRun)
class SearchRunAdmin(admin.ModelAdmin):
    list_display = ("query_text", "user", "status", "jobs_found", "jobs_created", "created_at")
    list_filter = ("status",)
    search_fields = ("query_text", "user__email")
