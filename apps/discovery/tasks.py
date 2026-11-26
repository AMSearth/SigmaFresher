"""Celery tasks for asynchronous web/platform discovery and link freshness verification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from celery import shared_task
from django.utils import timezone

from apps.core.http import UnsafeURLError, safe_fetch
from apps.discovery.connectors.ats import CompanyATSBoardConnector
from apps.discovery.connectors.platforms import ArbeitnowConnector, RemotiveConnector
from apps.jobs.models import JobPosting

from .services import execute_search_run, seed_default_company_boards, upsert_normalized_jobs

logger = structlog.get_logger(__name__)


@shared_task(bind=True, max_retries=1, default_retry_delay=20)
def execute_search_run_task(self: object, search_run_id: str) -> None:
    """Run all Option 2 connectors asynchronously on the isolated scraper queue."""
    execute_search_run(search_run_id)


@shared_task
def sync_company_boards_task() -> dict[str, int]:
    """Periodically sync direct company ATS boards and public remote job feeds."""
    seed_default_company_boards()
    connectors = [CompanyATSBoardConnector(), RemotiveConnector(), ArbeitnowConnector()]
    collected = []
    for conn in connectors:
        try:
            collected.extend(conn.fetch_jobs(query="", location="", limit=30))
        except Exception as exc:
            logger.warning(
                "periodic_sync_connector_failed",
                connector=conn.source_name,
                error=str(exc),
            )
    processed, created = upsert_normalized_jobs(collected)
    return {"processed": processed, "created": created}


@shared_task
def verify_active_job_links_task(batch_size: int = 50) -> dict[str, int]:
    """Verify external apply links for older active jobs and deactivate stale/404 postings."""
    now = datetime.now(UTC)
    stale_cutoff = now - timedelta(days=45)
    expired_count = JobPosting.objects.filter(
        is_active=True, posted_at__lt=stale_cutoff
    ).update(is_active=False)

    check_cutoff = now - timedelta(days=7)
    candidates = JobPosting.objects.filter(
        is_active=True,
        last_verified_at__lt=check_cutoff,
    ).order_by("last_verified_at")[:batch_size]

    deactivated = 0
    verified = 0
    for job in candidates:
        try:
            resp = safe_fetch(job.apply_url, method="HEAD", timeout=6.0)
            if resp.status_code == 405:
                resp = safe_fetch(job.apply_url, method="GET", timeout=6.0)
            if resp.status_code in {404, 410}:
                job.is_active = False
                job.last_verified_at = timezone.now()
                job.save(update_fields=["is_active", "last_verified_at", "updated_at"])
                deactivated += 1
            else:
                job.last_verified_at = timezone.now()
                job.save(update_fields=["last_verified_at", "updated_at"])
                verified += 1
        except UnsafeURLError:
            job.is_active = False
            job.last_verified_at = timezone.now()
            job.save(update_fields=["is_active", "last_verified_at", "updated_at"])
            deactivated += 1
        except Exception as exc:
            logger.debug("link_verify_skipped", job_id=str(job.pk), error=str(exc))

    return {"expired_45d": expired_count, "deactivated": deactivated, "verified": verified}
