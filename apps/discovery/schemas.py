"""Pydantic normalization schemas and skill/classification helpers for discovered jobs."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from apps.core.sanitizers import (
    html_to_plain_text,
    normalize_salary_to_annual_usd,
    sanitize_job_html,
    validate_apply_url,
)

COMMON_SKILLS = (
    "python",
    "django",
    "fastapi",
    "flask",
    "postgresql",
    "postgres",
    "pgvector",
    "mysql",
    "redis",
    "celery",
    "docker",
    "kubernetes",
    "aws",
    "gcp",
    "azure",
    "terraform",
    "javascript",
    "typescript",
    "react",
    "next.js",
    "htmx",
    "tailwind",
    "graphql",
    "rest",
    "java",
    "spring",
    "go",
    "rust",
    "sql",
    "git",
    "linux",
    "ci/cd",
    "llm",
    "pytorch",
)


def infer_work_mode(text: str) -> str:
    lowered = text.lower()
    if any(k in lowered for k in ("remote", "work from home", "wfh", "anywhere")):
        return "remote"
    if "hybrid" in lowered:
        return "hybrid"
    if any(k in lowered for k in ("onsite", "on-site", "in-office", "in office")):
        return "onsite"
    return "unknown"


def infer_remote_scope(work_mode: str, location_text: str) -> str:
    if work_mode != "remote":
        return "not_applicable"
    lowered = location_text.lower()
    if any(k in lowered for k in ("worldwide", "global", "anywhere")):
        return "global"
    if any(k in lowered for k in ("emea", "apac", "latam", "europe", "americas")):
        return "region_restricted"
    if lowered.strip() and lowered.strip() != "remote":
        return "country_restricted"
    return "global"


def infer_experience_level(title: str, description: str) -> str:
    combined = f"{title} {description[:500]}".lower()
    if any(k in combined for k in ("principal", "staff", "lead", "architect")):
        return "lead"
    if any(k in combined for k in ("vp ", "vice president", "director", "head of", "cto")):
        return "executive"
    if any(k in combined for k in ("senior", "sr.", "sr ")):
        return "senior"
    if any(k in combined for k in ("junior", "jr.", "entry", "fresher", "intern", "graduate")):
        return "entry"
    if any(k in combined for k in ("mid", "intermediate", "ii")):
        return "mid"
    return "unknown"


def infer_employment_type(raw_type: str) -> str:
    lowered = (raw_type or "").lower()
    if "intern" in lowered:
        return "internship"
    if "contract" in lowered or "freelance" in lowered or "temp" in lowered:
        return "contract"
    if "part" in lowered:
        return "part_time"
    if "full" in lowered:
        return "full_time"
    return "unknown"


def extract_skills_from_text(text: str) -> list[str]:
    lowered = f" {text.lower()} "
    found: list[str] = []
    for skill in COMMON_SKILLS:
        pattern = rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])"
        if re.search(pattern, lowered):
            found.append(skill)
    return found


def compute_dedup_hash(company_name: str, title: str, location_text: str, work_mode: str) -> str:
    norm_company = re.sub(r"[^a-z0-9]+", " ", company_name.lower()).strip()
    norm_title = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    norm_loc = "remote" if work_mode == "remote" else re.sub(
        r"[^a-z0-9]+", " ", location_text.lower()
    ).strip()
    key = f"{norm_company}|{norm_title}|{norm_loc}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class NormalizedJob(BaseModel):
    """Canonical validated job record produced by every discovery connector."""

    title: str = Field(min_length=2, max_length=255)
    company_name: str = Field(min_length=1, max_length=200)
    company_domain: str = ""
    location_text: str = ""
    country_code: str = ""
    work_mode: str = ""
    remote_scope: str = ""
    employment_type: str = "full_time"
    experience_level: str = ""

    salary_min_raw: int | None = None
    salary_max_raw: int | None = None
    salary_currency: str = "USD"
    salary_interval: str = "year"

    description_html: str = ""
    description_text: str = ""
    skills_extracted: list[str] = Field(default_factory=list)

    source_type: str
    source_name: str
    source_url: str
    apply_url: str
    apply_url_type: str = "ats_direct"
    external_id: str = ""
    posted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("source_url", "apply_url")
    @classmethod
    def check_http_url(cls, v: str) -> str:
        return validate_apply_url(v)

    def finalized(self) -> NormalizedJob:
        safe_html = sanitize_job_html(self.description_html)
        plain = self.description_text.strip() or html_to_plain_text(safe_html)
        mode = self.work_mode or infer_work_mode(f"{self.title} {self.location_text} {plain[:300]}")
        scope = self.remote_scope or infer_remote_scope(mode, self.location_text)
        level = self.experience_level or infer_experience_level(self.title, plain)
        emp_type = infer_employment_type(self.employment_type)
        skills = self.skills_extracted or extract_skills_from_text(f"{self.title} {plain}")
        domain = self.company_domain or (urlparse(self.apply_url).hostname or "")
        posted = self.posted_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=UTC)
        return self.model_copy(
            update={
                "description_html": safe_html,
                "description_text": plain,
                "work_mode": mode,
                "remote_scope": scope,
                "experience_level": level,
                "employment_type": emp_type,
                "skills_extracted": skills,
                "company_domain": domain[:255],
                "posted_at": posted,
            }
        )

    @property
    def dedup_hash(self) -> str:
        return compute_dedup_hash(
            self.company_name, self.title, self.location_text, self.work_mode
        )

    @property
    def normalized_salary_usd(self) -> tuple[int | None, int | None]:
        return normalize_salary_to_annual_usd(
            self.salary_min_raw,
            self.salary_max_raw,
            self.salary_currency,
            self.salary_interval,
        )
