"""Phase 4 tests for hybrid matching, django-filter combinations, HTMX search, and job details."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.discovery.models import SearchRun
from apps.jobs.models import JobPosting
from apps.resumes.models import Resume


def _unit_vector(dim_index: int, dims: int = 768) -> list[float]:
    vec = [0.0] * dims
    vec[dim_index] = 1.0
    return vec


@pytest.mark.django_db
def test_hybrid_matching_ranking_filters_and_htmx_partial(
    client, django_capture_on_commit_callbacks
) -> None:
    user = User.objects.create_user(email="matcher@example.com", password="VeryStrongPassword!2026")
    other = User.objects.create_user(email="other@example.com", password="VeryStrongPassword!2026")

    resume = Resume.objects.create(
        user=user,
        label="Python Backend",
        original_filename="cv.pdf",
        file_extension="pdf",
        file_hash="abc123",
        skills=["Python", "Django", "PostgreSQL", "Docker"],
        target_roles=["Backend Engineer"],
        embedding=_unit_vector(0),
        parse_status=Resume.ParseStatus.READY,
        is_active=True,
    )

    now = datetime.now(UTC)
    top_job = JobPosting.objects.create(
        title="Senior Backend Engineer (Django)",
        company_name="Stripe",
        location_text="Remote, US",
        country_code="US",
        work_mode=JobPosting.WorkMode.REMOTE,
        remote_scope=JobPosting.RemoteScope.COUNTRY_RESTRICTED,
        employment_type=JobPosting.EmploymentType.FULL_TIME,
        experience_level=JobPosting.ExperienceLevel.SENIOR,
        salary_annual_min_usd=150000,
        salary_annual_max_usd=190000,
        description_html="<p>Django PostgreSQL Docker</p>",
        description_text="Looking for Python, Django, PostgreSQL, and Docker engineer.",
        skills_extracted=["python", "django", "postgresql", "docker"],
        source_type=JobPosting.SourceType.COMPANY_ATS,
        source_name="Greenhouse",
        source_url="https://boards.greenhouse.io/stripe/jobs/1",
        apply_url="https://boards.greenhouse.io/stripe/jobs/1",
        apply_url_type=JobPosting.ApplyURLType.ATS_DIRECT,
        dedup_hash="hash_top_job",
        posted_at=now - timedelta(hours=6),
        embedding=_unit_vector(0),
    )

    non_match_job = JobPosting.objects.create(
        title="Java Legacy Onsite Specialist",
        company_name="OldBank",
        location_text="London, UK",
        country_code="GB",
        work_mode=JobPosting.WorkMode.ONSITE,
        remote_scope=JobPosting.RemoteScope.NOT_APPLICABLE,
        employment_type=JobPosting.EmploymentType.CONTRACT,
        experience_level=JobPosting.ExperienceLevel.MID,
        salary_annual_min_usd=70000,
        salary_annual_max_usd=85000,
        description_text="Java Spring Oracle onsite contract role.",
        skills_extracted=["java", "spring"],
        source_type=JobPosting.SourceType.JOB_PLATFORM,
        source_name="Adzuna",
        source_url="https://adzuna.example.com/2",
        apply_url="https://adzuna.example.com/2",
        apply_url_type=JobPosting.ApplyURLType.JOB_PLATFORM,
        dedup_hash="hash_other_job",
        posted_at=now - timedelta(days=10),
        embedding=_unit_vector(1),
    )

    client.force_login(user)

    # Full page search ranks Django job above Java job
    resp = client.get(reverse("jobs:search"), {"resume": str(resume.pk)})
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    assert body.index(top_job.title) < body.index(non_match_job.title)
    assert 'rel="noopener noreferrer nofollow"' in body

    # HTMX partial filtering (remote + direct_only + min_salary) excludes OldBank
    htmx_resp = client.get(
        reverse("jobs:search"),
        {
            "work_mode": "remote",
            "direct_only": "on",
            "min_salary": "120000",
            "posted_within": "3",
        },
        HTTP_HX_REQUEST="true",
    )
    assert htmx_resp.status_code == 200
    htmx_body = htmx_resp.content.decode("utf-8")
    assert top_job.title in htmx_body
    assert non_match_job.title not in htmx_body
    assert "<!DOCTYPE html>" not in htmx_body

    # Job detail page renders sanitized description and apply link
    detail_resp = client.get(reverse("jobs:detail", args=[top_job.pk]))
    assert detail_resp.status_code == 200
    assert top_job.apply_url.encode() in detail_resp.content

    # Trigger web search creates SearchRun and blocks cross-user polling
    with (
        patch("apps.jobs.views.execute_search_run_task.delay") as mock_delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        trig_resp = client.post(
            reverse("jobs:trigger_web_search"),
            {"q": "Django", "location": "Remote"},
            HTTP_HX_REQUEST="true",
        )
        assert trig_resp.status_code == 200
    assert mock_delay.call_count == 1

    run = SearchRun.objects.get(user=user)
    assert client.get(reverse("jobs:search_run_status", args=[run.pk])).status_code == 200

    client.force_login(other)
    assert client.get(reverse("jobs:search_run_status", args=[run.pk])).status_code == 404
