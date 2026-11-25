"""Phase 3 tests for SSRF protection, HTML/URL sanitization, salary normalization, and discovery."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.accounts.models import User
from apps.core.http import UnsafeURLError, safe_fetch, validate_public_http_url
from apps.core.sanitizers import (
    normalize_salary_to_annual_usd,
    sanitize_job_html,
    validate_apply_url,
)
from apps.discovery.connectors.ats import detect_ats_board_from_url
from apps.discovery.connectors.base import BaseJobConnector
from apps.discovery.connectors.web import extract_schema_org_job_postings
from apps.discovery.models import SearchRun
from apps.discovery.schemas import NormalizedJob
from apps.discovery.services import execute_search_run, upsert_normalized_jobs
from apps.jobs.models import JobPosting


def test_ssrf_blocks_private_loopback_metadata_and_bad_schemes() -> None:
    with pytest.raises(UnsafeURLError):
        validate_public_http_url("file:///etc/passwd")
    with pytest.raises(UnsafeURLError):
        validate_public_http_url("http://127.0.0.1:8000/admin")
    with pytest.raises(UnsafeURLError):
        validate_public_http_url("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(UnsafeURLError):
        validate_public_http_url("http://10.0.0.5/")


def test_ssrf_blocks_redirect_to_internal_ip() -> None:
    redirect_resp = MagicMock()
    redirect_resp.status_code = 302
    redirect_resp.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
    redirect_resp.__enter__ = MagicMock(return_value=redirect_resp)
    redirect_resp.__exit__ = MagicMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream.return_value = redirect_resp
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch(
            "apps.core.http.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
        ),
        patch("apps.core.http.httpx.Client", return_value=mock_client),
        pytest.raises(UnsafeURLError),
    ):
        safe_fetch("https://example.com/jobs")


def test_xss_sanitization_and_apply_url_validation() -> None:
    dirty = '<p>Django role</p><script>alert(1)</script><img src=x onerror="evil()">'
    cleaned = sanitize_job_html(dirty)
    assert "<script>" not in cleaned
    assert "onerror" not in cleaned
    assert "<p>Django role</p>" in cleaned

    with pytest.raises(ValueError):
        validate_apply_url("javascript:alert(1)")
    assert validate_apply_url("https://acme.com/apply/123") == "https://acme.com/apply/123"


def test_salary_normalization_hourly_and_currency() -> None:
    s_min, s_max = normalize_salary_to_annual_usd(75, 90, currency="USD", interval="hour")
    assert s_min == 156000
    assert s_max == 187200

    eur_min, _ = normalize_salary_to_annual_usd(100000, None, currency="EUR", interval="year")
    assert eur_min == 108000


def test_schema_org_json_ld_and_ats_board_detection() -> None:
    assert detect_ats_board_from_url("https://boards.greenhouse.io/stripe/jobs/123") == (
        "greenhouse",
        "stripe",
    )
    assert detect_ats_board_from_url("https://jobs.lever.co/acme/abc") == ("lever", "acme")
    assert detect_ats_board_from_url("https://jobs.ashbyhq.com/linear/xyz") == ("ashby", "linear")

    html = """
    <html><head>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": "Senior Django Developer",
        "description": "<p>Build scalable PostgreSQL and Python APIs.</p><img src=x onerror=bad()>",
        "datePosted": "2026-11-20",
        "employmentType": "FULL_TIME",
        "jobLocationType": "TELECOMMUTE",
        "hiringOrganization": {"@type": "Organization", "name": "Acme Corp"},
        "baseSalary": {
          "@type": "MonetaryAmount",
          "currency": "USD",
          "value": {
            "@type": "QuantitativeValue",
            "minValue": 140000,
            "maxValue": 170000,
            "unitText": "YEAR"
          }
        },
        "url": "https://acme.example.com/careers/django-dev"
      }
      </script>
    </head><body></body></html>
    """
    jobs = extract_schema_org_job_postings(html, "https://acme.example.com/careers")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Senior Django Developer"
    assert job.company_name == "Acme Corp"
    assert job.work_mode == "remote"
    assert "<img" not in job.description_html
    assert "onerror" not in job.description_html
    assert "python" in job.skills_extracted
    assert job.normalized_salary_usd == (140000, 170000)


@pytest.mark.django_db
def test_deduplication_prefers_direct_apply_url_and_handles_partial_failures() -> None:
    platform_job = NormalizedJob(
        title="Backend Python Engineer",
        company_name="Stripe",
        location_text="Remote",
        work_mode="remote",
        description_text="Python Django PostgreSQL backend role.",
        source_type="job_platform",
        source_name="Adzuna",
        source_url="https://adzuna.example.com/job/1",
        apply_url="https://adzuna.example.com/apply/1",
        apply_url_type="job_platform",
    ).finalized()

    ats_job = NormalizedJob(
        title="Backend Python Engineer",
        company_name="Stripe",
        location_text="Remote",
        work_mode="remote",
        description_text="Python Django PostgreSQL backend role.",
        source_type="company_ats",
        source_name="Greenhouse",
        source_url="https://boards.greenhouse.io/stripe/jobs/999",
        apply_url="https://boards.greenhouse.io/stripe/jobs/999",
        apply_url_type="ats_direct",
    ).finalized()

    with patch("apps.core.ai.CloudAIClient.generate_embeddings_batch", return_value=[[0.05] * 768]):
        processed, created = upsert_normalized_jobs([platform_job, ats_job])

    assert processed == 1
    assert created == 1
    saved = JobPosting.objects.get()
    assert saved.apply_url == "https://boards.greenhouse.io/stripe/jobs/999"
    assert saved.apply_url_type == "ats_direct"

    class OkConnector(BaseJobConnector):
        source_name = "MockOK"
        source_type = "company_ats"

        def fetch_jobs(
            self, query: str, location: str = "", limit: int = 25
        ) -> list[NormalizedJob]:
            return [ats_job]

    class FailConnector(BaseJobConnector):
        source_name = "MockFail"
        source_type = "job_platform"

        def fetch_jobs(
            self, query: str, location: str = "", limit: int = 25
        ) -> list[NormalizedJob]:
            raise RuntimeError("429 Rate Limited")

    user = User.objects.create_user(
        email="searcher@example.com", password="VeryStrongPassword!2026"
    )
    run = SearchRun.objects.create(user=user, query_text="Python")
    execute_search_run(str(run.pk), connectors=[OkConnector(), FailConnector()])
    run.refresh_from_db()
    assert run.status == SearchRun.Status.PARTIAL
    assert run.jobs_found == 1
    assert run.source_telemetry["MockOK"]["status"] == "ok"
    assert run.source_telemetry["MockFail"]["status"] == "error"
