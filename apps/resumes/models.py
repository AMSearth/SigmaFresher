"""Resume models with private UUID storage and vector embeddings."""

from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.db import models

from apps.core.fields import PortableVectorField
from apps.core.models import TimeStampedModel, UUIDModel


def resume_upload_path(instance: Resume, filename: str) -> str:
    """Store uploaded resumes under randomized UUID filenames isolated per user."""
    ext = Path(filename).suffix.lower() or ".pdf"
    if ext not in {".pdf", ".docx"}:
        ext = ".bin"
    return f"resumes/{instance.user_id}/{uuid.uuid4().hex}{ext}"


class Resume(UUIDModel, TimeStampedModel):
    """Uploaded candidate resume, extracted profile data, and semantic embedding."""

    class ParseStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="resumes",
    )
    label = models.CharField(max_length=100, blank=True)
    file = models.FileField(upload_to=resume_upload_path)
    original_filename = models.CharField(max_length=255)
    file_extension = models.CharField(max_length=10)
    file_hash = models.CharField(max_length=64, db_index=True)
    text_hash = models.CharField(max_length=64, blank=True, db_index=True)
    raw_text = models.TextField(blank=True)
    parsed_data = models.JSONField(default=dict, blank=True)
    skills = models.JSONField(default=list, blank=True)
    target_roles = models.JSONField(default=list, blank=True)
    years_experience = models.FloatField(null=True, blank=True)
    seniority_level = models.CharField(max_length=32, default="unknown")
    embedding = PortableVectorField(dimensions=768, null=True, blank=True)
    parse_status = models.CharField(
        max_length=20,
        choices=ParseStatus.choices,
        default=ParseStatus.PENDING,
        db_index=True,
    )
    error_message = models.TextField(blank=True)
    is_active = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-is_active", "-created_at"]

    def __str__(self) -> str:
        return f"{self.label or self.original_filename} ({self.user_id})"
