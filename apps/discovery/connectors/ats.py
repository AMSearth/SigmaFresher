"""Direct company ATS connectors (Greenhouse, Lever, Ashby, SmartRecruiters)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from urllib.parse import urlparse

import structlog

from apps.core.http import safe_fetch
from apps.discovery.models import CompanyBoard
from apps.discovery.schemas import NormalizedJob

from .base import BaseJobConnector, breaker

logger = structlog.get_logger(__name__)

ATS_URL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        CompanyBoard.ATSType.GREENHOUSE,
        re.compile(r"^/(?:v1/boards/)?([A-Za-z0-9_-]+)"),
    ),
    (
        CompanyBoard.ATSType.LEVER,
        re.compile(r"^/([A-Za-z0-9_-]+)"),
    ),
    (
        CompanyBoard.ATSType.ASHBY,
        re.compile(r"^/([A-Za-z0-9_-]+)"),
    ),
)


def detect_ats_board_from_url(url: str) -> tuple[str, str] | None:
    """Identify (ats_type, board_token) from a discovered ATS job URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        match = ATS_URL_PATTERNS[0][1].match(parsed.path)
        if match:
            return CompanyBoard.ATSType.GREENHOUSE, match.group(1).lower()
    elif host == "jobs.lever.co":
        match = ATS_URL_PATTERNS[1][1].match(parsed.path)
        if match:
            return CompanyBoard.ATSType.LEVER, match.group(1).lower()
    elif host == "jobs.ashbyhq.com":
        match = ATS_URL_PATTERNS[2][1].match(parsed.path)
        if match:
            return CompanyBoard.ATSType.ASHBY, match.group(1).lower()
    return None


def _matches_query(
    title: str, description: str, location_text: str, query: str, location: str
) -> bool:
    if query.strip():
        terms = [t.lower() for t in query.split() if len(t) > 1]
        haystack = f"{title} {description}".lower()
        if terms and not any(t in haystack for t in terms):
            return False
    if location.strip():
        loc_lower = location.lower()
        if loc_lower not in location_text.lower() and "remote" not in location_text.lower():
            return False
    return True


def _parse_iso_dt(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)


class CompanyATSBoardConnector(BaseJobConnector):
    """Queries active registered CompanyBoard entries across Greenhouse, Lever, Ashby, etc."""

    source_name = "Company ATS Boards"
    source_type = "company_ats"

    def fetch_jobs(self, query: str, location: str = "", limit: int = 40) -> list[NormalizedJob]:
        boards = list(CompanyBoard.objects.filter(is_active=True)[:25])
        results: list[NormalizedJob] = []
        for board in boards:
            if len(results) >= limit:
                break
            cb_key = f"ats:{board.ats_type}:{board.board_token}"
            if breaker.is_open(cb_key):
                continue
            try:
                if board.ats_type == CompanyBoard.ATSType.GREENHOUSE:
                    jobs = self._fetch_greenhouse(board, query, location)
                elif board.ats_type == CompanyBoard.ATSType.LEVER:
                    jobs = self._fetch_lever(board, query, location)
                elif board.ats_type == CompanyBoard.ATSType.ASHBY:
                    jobs = self._fetch_ashby(board, query, location)
                elif board.ats_type == CompanyBoard.ATSType.SMARTRECRUITERS:
                    jobs = self._fetch_smartrecruiters(board, query, location)
                else:
                    jobs = []
                breaker.record_success(cb_key)
                results.extend(jobs)
            except Exception as exc:
                breaker.record_failure(cb_key)
                logger.warning(
                    "ats_board_fetch_failed",
                    board=board.board_token,
                    ats=board.ats_type,
                    error=str(exc),
                )
        return results[:limit]

    def _fetch_greenhouse(
        self, board: CompanyBoard, query: str, location: str
    ) -> list[NormalizedJob]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{board.board_token}/jobs"
        resp = safe_fetch(url, params={"content": "true"}, timeout=8.0)
        if resp.status_code != 200:
            return []
        data = json.loads(resp.text)
        out: list[NormalizedJob] = []
        for item in data.get("jobs", []):
            title = str(item.get("title") or "").strip()
            loc = str((item.get("location") or {}).get("name") or "")
            content_html = str(item.get("content") or "")
            apply_url = str(item.get("absolute_url") or "")
            if not title or not apply_url:
                continue
            if not _matches_query(title, content_html, loc, query, location):
                continue
            out.append(
                NormalizedJob(
                    title=title,
                    company_name=board.name,
                    company_domain=board.domain,
                    location_text=loc,
                    description_html=content_html,
                    source_type="company_ats",
                    source_name="Greenhouse",
                    source_url=apply_url,
                    apply_url=apply_url,
                    apply_url_type="ats_direct",
                    external_id=str(item.get("id") or ""),
                    posted_at=_parse_iso_dt(item.get("updated_at")),
                ).finalized()
            )
        return out

    def _fetch_lever(self, board: CompanyBoard, query: str, location: str) -> list[NormalizedJob]:
        url = f"https://api.lever.co/v0/postings/{board.board_token}"
        resp = safe_fetch(url, params={"mode": "json"}, timeout=8.0)
        if resp.status_code != 200:
            return []
        items = json.loads(resp.text)
        out: list[NormalizedJob] = []
        for item in items if isinstance(items, list) else []:
            title = str(item.get("text") or "").strip()
            categories = item.get("categories") or {}
            loc = str(categories.get("location") or "")
            desc_html = str(item.get("description") or "") + str(item.get("additional") or "")
            apply_url = str(item.get("applyUrl") or item.get("hostedUrl") or "")
            source_url = str(item.get("hostedUrl") or apply_url)
            if not title or not apply_url:
                continue
            if not _matches_query(title, desc_html, loc, query, location):
                continue
            created_ms = item.get("createdAt")
            posted = (
                datetime.fromtimestamp(created_ms / 1000.0, tz=UTC)
                if isinstance(created_ms, (int, float))
                else datetime.now(UTC)
            )
            out.append(
                NormalizedJob(
                    title=title,
                    company_name=board.name,
                    company_domain=board.domain,
                    location_text=loc,
                    work_mode=(
                        "remote"
                        if str(item.get("workplaceType") or "").lower() == "remote"
                        else ""
                    ),
                    employment_type=str(categories.get("commitment") or "full_time"),
                    description_html=desc_html,
                    source_type="company_ats",
                    source_name="Lever",
                    source_url=source_url,
                    apply_url=apply_url,
                    apply_url_type="ats_direct",
                    external_id=str(item.get("id") or ""),
                    posted_at=posted,
                ).finalized()
            )
        return out

    def _fetch_ashby(self, board: CompanyBoard, query: str, location: str) -> list[NormalizedJob]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{board.board_token}"
        resp = safe_fetch(url, timeout=8.0)
        if resp.status_code != 200:
            return []
        data = json.loads(resp.text)
        out: list[NormalizedJob] = []
        for item in data.get("jobs", []):
            title = str(item.get("title") or "").strip()
            loc = str(item.get("location") or "")
            desc_html = str(item.get("descriptionHtml") or "")
            desc_plain = str(item.get("descriptionPlain") or "")
            apply_url = str(item.get("applyUrl") or item.get("jobUrl") or "")
            source_url = str(item.get("jobUrl") or apply_url)
            if not title or not apply_url:
                continue
            if not _matches_query(title, desc_plain or desc_html, loc, query, location):
                continue
            out.append(
                NormalizedJob(
                    title=title,
                    company_name=board.name,
                    company_domain=board.domain,
                    location_text=loc,
                    work_mode="remote" if item.get("isRemote") else "",
                    employment_type=str(item.get("employmentType") or "full_time"),
                    description_html=desc_html,
                    description_text=desc_plain,
                    source_type="company_ats",
                    source_name="Ashby",
                    source_url=source_url,
                    apply_url=apply_url,
                    apply_url_type="ats_direct",
                    external_id=str(item.get("id") or ""),
                    posted_at=_parse_iso_dt(item.get("publishedAt")),
                ).finalized()
            )
        return out

    def _fetch_smartrecruiters(
        self, board: CompanyBoard, query: str, location: str
    ) -> list[NormalizedJob]:
        url = f"https://api.smartrecruiters.com/v1/companies/{board.board_token}/postings"
        resp = safe_fetch(url, params={"limit": 50}, timeout=8.0)
        if resp.status_code != 200:
            return []
        data = json.loads(resp.text)
        out: list[NormalizedJob] = []
        for item in data.get("content", []):
            title = str(item.get("name") or "").strip()
            loc_obj = item.get("location") or {}
            loc = ", ".join(
                p for p in [loc_obj.get("city"), loc_obj.get("region"), loc_obj.get("country")] if p
            )
            posting_id = str(item.get("id") or "")
            apply_url = (
                f"https://jobs.smartrecruiters.com/{board.board_token}/{posting_id}"
                if posting_id
                else ""
            )
            if not title or not apply_url:
                continue
            if not _matches_query(title, "", loc, query, location):
                continue
            out.append(
                NormalizedJob(
                    title=title,
                    company_name=board.name,
                    company_domain=board.domain,
                    location_text=loc,
                    country_code=str(loc_obj.get("country") or "")[:8].upper(),
                    work_mode="remote" if loc_obj.get("remote") else "",
                    source_type="company_ats",
                    source_name="SmartRecruiters",
                    source_url=apply_url,
                    apply_url=apply_url,
                    apply_url_type="ats_direct",
                    external_id=posting_id,
                    posted_at=_parse_iso_dt(item.get("releasedDate")),
                ).finalized()
            )
        return out
