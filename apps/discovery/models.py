"""Models tracking company ATS boards and live discovery runs."""

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel, UUIDModel
from apps.resumes.models import Resume


class CompanyBoard(UUIDModel, TimeStampedModel):
    """Registered company career board hosted on a supported public ATS."""

    class ATSType(models.TextChoices):
        GREENHOUSE = "greenhouse", "Greenhouse"
        LEVER = "lever", "Lever"
        ASHBY = "ashby", "Ashby"
        SMARTRECRUITERS = "smartrecruiters", "SmartRecruiters"
        WORKABLE = "workable", "Workable"

    name = models.CharField(max_length=200)
    domain = models.CharField(max_length=255, blank=True)
    ats_type = models.CharField(max_length=24, choices=ATSType.choices, db_index=True)
    board_token = models.CharField(max_length=150)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ats_type", "board_token"],
                name="uniq_company_board_ats_token",
            )
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.ats_type}:{self.board_token})"


class SearchRun(UUIDModel, TimeStampedModel):
    """Tracks an asynchronous web/platform discovery run triggered by a seeker."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Completed with Warnings"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="search_runs",
    )
    resume = models.ForeignKey(
        Resume,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="search_runs",
    )
    query_text = models.CharField(max_length=300, blank=True)
    location = models.CharField(max_length=150, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    jobs_found = models.PositiveIntegerField(default=0)
    jobs_created = models.PositiveIntegerField(default=0)
    source_telemetry = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"SearchRun({self.query_text or 'resume-driven'}, {self.status})"
