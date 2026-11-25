"""Free-tier job platform connectors (Adzuna, Remotive, Arbeitnow, optional JobSpy)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import structlog
from django.conf import settings

from apps.core.http import safe_fetch
from apps.discovery.schemas import NormalizedJob

from .base import BaseJobConnector, breaker

logger = structlog.get_logger(__name__)


def _parse_dt(raw: object) -> datetime:
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=UTC)
    if isinstance(raw, str) and raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


class AdzunaConnector(BaseJobConnector):
    """Queries Adzuna's free developer job search API when credentials are configured."""

    source_name = "Adzuna"
    source_type = "job_platform"

    def fetch_jobs(self, query: str, location: str = "", limit: int = 25) -> list[NormalizedJob]:
        app_id = getattr(settings, "ADZUNA_APP_ID", "")
        app_key = getattr(settings, "ADZUNA_APP_KEY", "")
        if not app_id or not app_key:
            return []
        country = getattr(settings, "ADZUNA_COUNTRY", "us").lower()
        cb_key = "platform:adzuna"
        if breaker.is_open(cb_key):
            return []

        url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
        params: dict[str, str | int | float | bool | None] = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": min(limit, 50),
            "what": query or "software engineer",
        }
        if location:
            params["where"] = location

        try:
            resp = safe_fetch(url, params=params, timeout=10.0)
            if resp.status_code != 200:
                breaker.record_failure(cb_key)
                return []
            breaker.record_success(cb_key)
            data = json.loads(resp.text)
        except Exception as exc:
            breaker.record_failure(cb_key)
            logger.warning("adzuna_fetch_failed", error=str(exc))
            return []

        results: list[NormalizedJob] = []
        for item in data.get("results", []):
            title = str(item.get("title") or "").strip()
            company = str((item.get("company") or {}).get("display_name") or "Unknown")
            loc = str((item.get("location") or {}).get("display_name") or "")
            redirect_url = str(item.get("redirect_url") or "")
            if not title or not redirect_url:
                continue
            results.append(
                NormalizedJob(
                    title=title,
                    company_name=company,
                    location_text=loc,
                    country_code=country.upper(),
                    salary_min_raw=int(item["salary_min"]) if item.get("salary_min") else None,
                    salary_max_raw=int(item["salary_max"]) if item.get("salary_max") else None,
                    description_html=str(item.get("description") or ""),
                    source_type="job_platform",
                    source_name="Adzuna",
                    source_url=redirect_url,
                    apply_url=redirect_url,
                    apply_url_type="job_platform",
                    external_id=str(item.get("id") or ""),
                    posted_at=_parse_dt(item.get("created")),
                ).finalized()
            )
        return results[:limit]


class RemotiveConnector(BaseJobConnector):
    """Queries Remotive's free public remote-jobs JSON feed."""

    source_name = "Remotive"
    source_type = "job_platform"

    def fetch_jobs(self, query: str, location: str = "", limit: int = 25) -> list[NormalizedJob]:
        cb_key = "platform:remotive"
        if breaker.is_open(cb_key):
            return []
        params: dict[str, str | int | float | bool | None] = {"limit": min(limit, 50)}
        if query.strip():
            params["search"] = query.strip()
        try:
            resp = safe_fetch("https://remotive.com/api/remote-jobs", params=params, timeout=10.0)
            if resp.status_code != 200:
                breaker.record_failure(cb_key)
                return []
            breaker.record_success(cb_key)
            data = json.loads(resp.text)
        except Exception as exc:
            breaker.record_failure(cb_key)
            logger.warning("remotive_fetch_failed", error=str(exc))
            return []

        results: list[NormalizedJob] = []
        for item in data.get("jobs", []):
            title = str(item.get("title") or "").strip()
            company = str(item.get("company_name") or "Unknown")
            url = str(item.get("url") or "")
            if not title or not url:
                continue
            results.append(
                NormalizedJob(
                    title=title,
                    company_name=company,
                    location_text=str(item.get("candidate_required_location") or "Remote"),
                    work_mode="remote",
                    employment_type=str(item.get("job_type") or "full_time"),
                    description_html=str(item.get("description") or ""),
                    source_type="job_platform",
                    source_name="Remotive",
                    source_url=url,
                    apply_url=url,
                    apply_url_type="job_platform",
                    external_id=str(item.get("id") or ""),
                    posted_at=_parse_dt(item.get("publication_date")),
                ).finalized()
            )
        return results[:limit]


class ArbeitnowConnector(BaseJobConnector):
    """Queries Arbeitnow's free public job board JSON API."""

    source_name = "Arbeitnow"
    source_type = "job_platform"

    def fetch_jobs(self, query: str, location: str = "", limit: int = 25) -> list[NormalizedJob]:
        cb_key = "platform:arbeitnow"
        if breaker.is_open(cb_key):
            return []
        try:
            resp = safe_fetch("https://www.arbeitnow.com/api/job-board-api", timeout=10.0)
            if resp.status_code != 200:
                breaker.record_failure(cb_key)
                return []
            breaker.record_success(cb_key)
            data = json.loads(resp.text)
        except Exception as exc:
            breaker.record_failure(cb_key)
            logger.warning("arbeitnow_fetch_failed", error=str(exc))
            return []

        terms = [t.lower() for t in query.split() if len(t) > 1]
        results: list[NormalizedJob] = []
        for item in data.get("data", []):
            title = str(item.get("title") or "").strip()
            desc = str(item.get("description") or "")
            url = str(item.get("url") or "")
            if not title or not url:
                continue
            if terms and not any(t in f"{title} {desc}".lower() for t in terms):
                continue
            loc = str(item.get("location") or "")
            results.append(
                NormalizedJob(
                    title=title,
                    company_name=str(item.get("company_name") or "Unknown"),
                    location_text=loc,
                    work_mode="remote" if item.get("remote") else "",
                    description_html=desc,
                    source_type="job_platform",
                    source_name="Arbeitnow",
                    source_url=url,
                    apply_url=url,
                    apply_url_type="job_platform",
                    external_id=str(item.get("slug") or ""),
                    posted_at=_parse_dt(item.get("created_at")),
                ).finalized()
            )
            if len(results) >= limit:
                break
        return results


class JobSpyConnector(BaseJobConnector):
    """Optional multi-board scraper using python-jobspy behind a strict circuit breaker."""

    source_name = "JobSpy"
    source_type = "job_platform"

    def fetch_jobs(self, query: str, location: str = "", limit: int = 15) -> list[NormalizedJob]:
        cb_key = "platform:jobspy"
        if breaker.is_open(cb_key):
            return []
        try:
            from jobspy import scrape_jobs
        except ImportError:
            return []

        try:
            df = scrape_jobs(
                site_name=["indeed", "linkedin", "google"],
                search_term=query or "software engineer",
                location=location or "Remote",
                results_wanted=min(limit, 15),
                hours_old=168,
            )
            breaker.record_success(cb_key)
        except Exception as exc:
            breaker.record_failure(cb_key)
            logger.warning("jobspy_scrape_failed", error=str(exc))
            return []

        results: list[NormalizedJob] = []
        for _, row in df.iterrows():
            title = str(row.get("title") or "").strip()
            apply_url = str(row.get("job_url_direct") or row.get("job_url") or "").strip()
            source_url = str(row.get("job_url") or apply_url).strip()
            if not title or not apply_url:
                continue
            is_direct = bool(row.get("job_url_direct"))
            results.append(
                NormalizedJob(
                    title=title,
                    company_name=str(row.get("company") or "Unknown"),
                    location_text=str(row.get("location") or ""),
                    work_mode="remote" if row.get("is_remote") else "",
                    description_text=str(row.get("description") or ""),
                    source_type="job_platform",
                    source_name=str(row.get("site") or "JobSpy").title(),
                    source_url=source_url,
                    apply_url=apply_url,
                    apply_url_type="company_direct" if is_direct else "job_platform",
                ).finalized()
            )
        return results[:limit]
