"""Models for saved jobs, application status tracking, and email job alerts."""

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel, UUIDModel
from apps.jobs.models import JobPosting
from apps.resumes.models import Resume


class SavedJob(UUIDModel, TimeStampedModel):
    """Tracks a seeker's bookmarked or applied job postings."""

    class Status(models.TextChoices):
        SAVED = "saved", "Saved"
        APPLIED = "applied", "Applied"
        INTERVIEWING = "interviewing", "Interviewing"
        REJECTED = "rejected", "Rejected"
        ARCHIVED = "archived", "Archived"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_jobs",
    )
    job = models.ForeignKey(
        JobPosting,
        on_delete=models.CASCADE,
        related_name="saved_by",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SAVED, db_index=True
    )
    applied_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "job"], name="uniq_saved_job_user_job")
        ]
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.user_id} -> {self.job.title} ({self.status})"


class JobAlert(UUIDModel, TimeStampedModel):
    """Scheduled daily or weekly matching job digest for a seeker."""

    class Frequency(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="job_alerts",
    )
    resume = models.ForeignKey(
        Resume,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_alerts",
    )
    name = models.CharField(max_length=120)
    query_text = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=150, blank=True)
    work_mode = models.CharField(max_length=20, blank=True)
    min_salary_usd = models.PositiveIntegerField(null=True, blank=True)
    frequency = models.CharField(
        max_length=16, choices=Frequency.choices, default=Frequency.DAILY
    )
    last_sent_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Alert({self.name}, {self.frequency})"
