"""Celery tasks for extracting text, parsing profile via Cloud LLM, and embedding resumes."""

from __future__ import annotations

import structlog
from celery import shared_task
from django.db import transaction

from apps.accounts.models import JobSeekerProfile
from apps.core.ai import (
    AIServiceError,
    CloudAIClient,
    ResumeExtractionSchema,
    build_resume_embedding_text,
    compute_text_hash,
)

from .extractors import ScannedResumeError, extract_resume_text
from .models import Resume

logger = structlog.get_logger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=15)
def process_resume_task(self: object, resume_id: str) -> None:
    """Extract resume text, run Cloud LLM structured parsing + embedding, and update profile."""
    resume = Resume.objects.filter(pk=resume_id).select_related("user").first()
    if resume is None:
        return

    resume.parse_status = Resume.ParseStatus.PROCESSING
    resume.error_message = ""
    resume.save(update_fields=["parse_status", "error_message", "updated_at"])

    try:
        raw_text = extract_resume_text(resume.file.path, resume.file_extension)
        text_hash = compute_text_hash(raw_text)

        cached = (
            Resume.objects.filter(
                text_hash=text_hash,
                parse_status=Resume.ParseStatus.READY,
            )
            .exclude(pk=resume.pk)
            .first()
        )

        if cached and cached.parsed_data and cached.embedding is not None:
            parsed = ResumeExtractionSchema.model_validate(cached.parsed_data)
            embedding = list(cached.embedding)
        else:
            ai_client = CloudAIClient()
            parsed = ai_client.extract_resume_profile(raw_text)
            embed_text = build_resume_embedding_text(parsed, raw_text)
            embedding = ai_client.generate_embedding(embed_text)

        with transaction.atomic():
            resume.raw_text = raw_text
            resume.text_hash = text_hash
            resume.parsed_data = parsed.model_dump()
            resume.skills = parsed.skills
            resume.target_roles = parsed.target_roles
            resume.years_experience = parsed.years_experience
            resume.seniority_level = parsed.seniority_level
            resume.embedding = embedding
            resume.parse_status = Resume.ParseStatus.READY
            resume.error_message = ""
            if not Resume.objects.filter(user=resume.user, is_active=True).exclude(
                pk=resume.pk
            ).exists():
                resume.is_active = True
            resume.save()

            profile, _ = JobSeekerProfile.objects.get_or_create(user=resume.user)
            updated_profile = False
            if not profile.target_roles and parsed.target_roles:
                profile.target_roles = parsed.target_roles
                updated_profile = True
            if not profile.preferred_locations and parsed.preferred_locations:
                profile.preferred_locations = parsed.preferred_locations
                updated_profile = True
            if updated_profile:
                profile.save(update_fields=["target_roles", "preferred_locations", "updated_at"])

    except ScannedResumeError as exc:
        resume.parse_status = Resume.ParseStatus.FAILED
        resume.error_message = str(exc)
        resume.save(update_fields=["parse_status", "error_message", "updated_at"])
    except AIServiceError as exc:
        logger.warning("resume_ai_failed", resume_id=str(resume.pk), error=str(exc))
        resume.parse_status = Resume.ParseStatus.FAILED
        resume.error_message = f"AI extraction error: {exc}"
        resume.save(update_fields=["parse_status", "error_message", "updated_at"])
    except Exception as exc:
        logger.exception("resume_processing_unexpected_error", resume_id=str(resume.pk))
        resume.parse_status = Resume.ParseStatus.FAILED
        resume.error_message = f"Unexpected error while processing resume: {exc}"
        resume.save(update_fields=["parse_status", "error_message", "updated_at"])
