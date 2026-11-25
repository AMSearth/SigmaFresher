"""JobPosting model storing normalized listings from company sites, ATSs, and platforms."""

from __future__ import annotations

from django.db import models

from apps.core.fields import PortableVectorField
from apps.core.models import TimeStampedModel, UUIDModel


class JobPosting(UUIDModel, TimeStampedModel):
    """Normalized job listing with direct apply link, normalized salary, and vector embedding."""

    class WorkMode(models.TextChoices):
        REMOTE = "remote", "Remote"
        HYBRID = "hybrid", "Hybrid"
        ONSITE = "onsite", "Onsite"
        UNKNOWN = "unknown", "Not Specified"

    class RemoteScope(models.TextChoices):
        GLOBAL = "global", "Global / Worldwide"
        COUNTRY_RESTRICTED = "country_restricted", "Country Restricted"
        REGION_RESTRICTED = "region_restricted", "Region Restricted"
        NOT_APPLICABLE = "not_applicable", "Onsite / N/A"

    class EmploymentType(models.TextChoices):
        FULL_TIME = "full_time", "Full-time"
        PART_TIME = "part_time", "Part-time"
        CONTRACT = "contract", "Contract"
        INTERNSHIP = "internship", "Internship"
        UNKNOWN = "unknown", "Not Specified"

    class ExperienceLevel(models.TextChoices):
        ENTRY = "entry", "Entry / Junior"
        MID = "mid", "Mid-Level"
        SENIOR = "senior", "Senior"
        LEAD = "lead", "Lead / Staff"
        EXECUTIVE = "executive", "Executive"
        UNKNOWN = "unknown", "Not Specified"

    class SourceType(models.TextChoices):
        COMPANY_ATS = "company_ats", "Company Site / ATS"
        JOB_PLATFORM = "job_platform", "Job Platform"
        WEB_SEARCH = "web_search", "Web Search"

    class ApplyURLType(models.TextChoices):
        COMPANY_DIRECT = "company_direct", "Company Direct"
        ATS_DIRECT = "ats_direct", "ATS Direct"
        JOB_PLATFORM = "job_platform", "Job Platform"

    title = models.CharField(max_length=255, db_index=True)
    company_name = models.CharField(max_length=200, db_index=True)
    company_domain = models.CharField(max_length=255, blank=True, db_index=True)
    location_text = models.CharField(max_length=255, blank=True)
    country_code = models.CharField(max_length=8, blank=True, db_index=True)
    work_mode = models.CharField(
        max_length=20, choices=WorkMode.choices, default=WorkMode.UNKNOWN, db_index=True
    )
    remote_scope = models.CharField(
        max_length=24,
        choices=RemoteScope.choices,
        default=RemoteScope.NOT_APPLICABLE,
        db_index=True,
    )
    employment_type = models.CharField(
        max_length=20,
        choices=EmploymentType.choices,
        default=EmploymentType.UNKNOWN,
        db_index=True,
    )
    experience_level = models.CharField(
        max_length=20,
        choices=ExperienceLevel.choices,
        default=ExperienceLevel.UNKNOWN,
        db_index=True,
    )

    salary_min_raw = models.PositiveIntegerField(null=True, blank=True)
    salary_max_raw = models.PositiveIntegerField(null=True, blank=True)
    salary_currency = models.CharField(max_length=8, default="USD")
    salary_interval = models.CharField(max_length=16, default="year")
    salary_annual_min_usd = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    salary_annual_max_usd = models.PositiveIntegerField(null=True, blank=True, db_index=True)

    description_html = models.TextField(blank=True)
    description_text = models.TextField(blank=True)
    skills_extracted = models.JSONField(default=list, blank=True)

    source_type = models.CharField(
        max_length=20, choices=SourceType.choices, default=SourceType.COMPANY_ATS, db_index=True
    )
    source_name = models.CharField(max_length=100, db_index=True)
    source_url = models.URLField(max_length=1000)
    apply_url = models.URLField(max_length=1000)
    apply_url_type = models.CharField(
        max_length=20, choices=ApplyURLType.choices, default=ApplyURLType.ATS_DIRECT, db_index=True
    )
    external_id = models.CharField(max_length=255, blank=True)
    dedup_hash = models.CharField(max_length=64, unique=True)
    text_hash = models.CharField(max_length=64, blank=True, db_index=True)

    posted_at = models.DateTimeField(db_index=True)
    fetched_at = models.DateTimeField(auto_now_add=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    embedding = PortableVectorField(dimensions=768, null=True, blank=True)

    class Meta:
        ordering = ["-posted_at", "-created_at"]

    def __str__(self) -> str:
        return f"{self.title} @ {self.company_name}"
