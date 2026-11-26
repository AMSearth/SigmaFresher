"""Phase 5 tests for saved job tracking, IDOR isolation, job alerts, and link freshness."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts.models import User
from apps.alerts.models import JobAlert, SavedJob
from apps.alerts.tasks import send_job_alerts_task
from apps.core.http import SafeHTTPResponse
from apps.discovery.tasks import verify_active_job_links_task
from apps.jobs.models import JobPosting
from apps.resumes.models import Resume


@pytest.mark.django_db
def test_saved_jobs_lifecycle_and_idor_isolation(client) -> None:
    user = User.objects.create_user(email="tracker@example.com", password="VeryStrongPassword!2026")
    other = User.objects.create_user(email="other2@example.com", password="VeryStrongPassword!2026")
    job = JobPosting.objects.create(
        title="Django Platform Engineer",
        company_name="Cloudflare",
        source_type=JobPosting.SourceType.COMPANY_ATS,
        source_name="Greenhouse",
        source_url="https://boards.greenhouse.io/cloudflare/jobs/1",
        apply_url="https://boards.greenhouse.io/cloudflare/jobs/1",
        dedup_hash="saved_job_hash_1",
        posted_at=datetime.now(UTC),
    )

    client.force_login(user)
    save_resp = client.post(reverse("alerts:save_job", args=[job.pk]))
    assert save_resp.status_code == 302
    saved = SavedJob.objects.get(user=user, job=job)
    assert saved.status == SavedJob.Status.SAVED

    upd_resp = client.post(
        reverse("alerts:update_saved", args=[saved.pk]),
        {"status": SavedJob.Status.APPLIED, "notes": "Submitted via Greenhouse"},
    )
    assert upd_resp.status_code == 302
    saved.refresh_from_db()
    assert saved.status == SavedJob.Status.APPLIED
    assert saved.applied_at is not None

    # Other user cannot modify or delete saved job
    client.force_login(other)
    resp_idor = client.post(
        reverse("alerts:update_saved", args=[saved.pk]), {"status": "rejected"}
    )
    assert resp_idor.status_code == 404
    assert client.post(reverse("alerts:delete_saved", args=[saved.pk])).status_code == 404


@pytest.mark.django_db
def test_job_alert_digest_and_freshness_verification(client) -> None:
    user = User.objects.create_user(email="alert@example.com", password="VeryStrongPassword!2026")
    resume = Resume.objects.create(
        user=user,
        original_filename="cv.pdf",
        file_extension="pdf",
        file_hash="hash_alert_cv",
        skills=["Python", "Django"],
        target_roles=["Backend Engineer"],
        parse_status=Resume.ParseStatus.READY,
        is_active=True,
    )

    fresh_job = JobPosting.objects.create(
        title="Backend Engineer (Django)",
        company_name="Linear",
        location_text="Remote",
        work_mode=JobPosting.WorkMode.REMOTE,
        salary_annual_min_usd=160000,
        salary_annual_max_usd=190000,
        skills_extracted=["python", "django"],
        source_type=JobPosting.SourceType.COMPANY_ATS,
        source_name="Ashby",
        source_url="https://jobs.ashbyhq.com/linear/1",
        apply_url="https://jobs.ashbyhq.com/linear/1",
        dedup_hash="fresh_alert_job",
        posted_at=datetime.now(UTC) - timedelta(hours=2),
    )

    client.force_login(user)
    create_resp = client.post(
        reverse("alerts:alerts_list"),
        {
            "name": "Remote Django",
            "query_text": "Django",
            "location": "Remote",
            "work_mode": "remote",
            "min_salary_usd": "120000",
            "frequency": JobAlert.Frequency.DAILY,
        },
    )
    assert create_resp.status_code == 302
    alert = JobAlert.objects.get(user=user)
    assert alert.resume == resume

    result = send_job_alerts_task()
    assert result["alerts_sent"] == 1
    assert len(mail.outbox) == 1
    assert fresh_job.apply_url in mail.outbox[0].body

    # Test dead-link verification + 45-day pruning
    old_45d = JobPosting.objects.create(
        title="Expired Role",
        company_name="OldCo",
        source_type=JobPosting.SourceType.JOB_PLATFORM,
        source_name="Adzuna",
        source_url="https://example.com/old",
        apply_url="https://example.com/old",
        dedup_hash="expired_45d_hash",
        posted_at=datetime.now(UTC) - timedelta(days=50),
    )
    dead_link_job = JobPosting.objects.create(
        title="Closed Role",
        company_name="ClosedCo",
        source_type=JobPosting.SourceType.COMPANY_ATS,
        source_name="Greenhouse",
        source_url="https://boards.greenhouse.io/closed/1",
        apply_url="https://boards.greenhouse.io/closed/1",
        dedup_hash="dead_link_hash",
        posted_at=datetime.now(UTC) - timedelta(days=10),
    )
    JobPosting.objects.filter(pk=dead_link_job.pk).update(
        last_verified_at=datetime.now(UTC) - timedelta(days=10)
    )

    mock_404 = SafeHTTPResponse(
        url=dead_link_job.apply_url,
        status_code=404,
        content=b"Not Found",
        headers={},
    )
    with patch("apps.discovery.tasks.safe_fetch", return_value=mock_404):
        summary = verify_active_job_links_task()

    old_45d.refresh_from_db()
    dead_link_job.refresh_from_db()
    assert summary["expired_45d"] >= 1
    assert summary["deactivated"] >= 1
    assert old_45d.is_active is False
    assert dead_link_job.is_active is False
