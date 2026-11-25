"""Open-web search discovery and schema.org/JobPosting JSON-LD extractor."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import structlog
from bs4 import BeautifulSoup

from apps.core.http import UnsafeURLError, safe_fetch
from apps.discovery.models import CompanyBoard
from apps.discovery.schemas import NormalizedJob

from .ats import detect_ats_board_from_url
from .base import BaseJobConnector, breaker

logger = structlog.get_logger(__name__)


def extract_schema_org_job_postings(html: str, page_url: str) -> list[NormalizedJob]:
    """Extract standardized schema.org/JobPosting JSON-LD blocks from any career page."""
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[NormalizedJob] = []

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw_json = script.string or script.get_text()
        if not raw_json or not raw_json.strip():
            continue
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            continue

        candidates = payload if isinstance(payload, list) else [payload]
        expanded: list[dict[str, object]] = []
        for item in candidates:
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                expanded.extend(g for g in item["@graph"] if isinstance(g, dict))
            elif isinstance(item, dict):
                expanded.append(item)

        for obj in expanded:
            schema_type = obj.get("@type")
            is_job = schema_type == "JobPosting" or (
                isinstance(schema_type, list) and "JobPosting" in schema_type
            )
            if not is_job:
                continue

            title = str(obj.get("title") or "").strip()
            if not title:
                continue

            org = obj.get("hiringOrganization")
            company = (
                str(org.get("name") or "").strip()
                if isinstance(org, dict)
                else str(org or "Company Site").strip()
            ) or "Company Site"

            apply_url = str(obj.get("url") or page_url).strip()
            desc_html = str(obj.get("description") or "")
            loc_text = _extract_job_location(obj)
            work_mode = (
                "remote"
                if str(obj.get("jobLocationType") or "").upper() == "TELECOMMUTE"
                else ""
            )

            sal_min, sal_max, sal_curr, sal_unit = _extract_salary(obj)
            posted = _parse_date(obj.get("datePosted"))

            try:
                jobs.append(
                    NormalizedJob(
                        title=title,
                        company_name=company,
                        location_text=loc_text,
                        work_mode=work_mode,
                        employment_type=str(obj.get("employmentType") or "full_time"),
                        salary_min_raw=sal_min,
                        salary_max_raw=sal_max,
                        salary_currency=sal_curr,
                        salary_interval=sal_unit,
                        description_html=desc_html,
                        source_type="web_search",
                        source_name="Company Career Site",
                        source_url=page_url,
                        apply_url=apply_url,
                        apply_url_type="company_direct",
                        posted_at=posted,
                    ).finalized()
                )
            except ValueError:
                continue

    return jobs


def _extract_job_location(obj: dict[str, object]) -> str:
    loc = obj.get("jobLocation")
    if isinstance(loc, list) and loc:
        loc = loc[0]
    if isinstance(loc, dict):
        addr = loc.get("address")
        if isinstance(addr, dict):
            parts = [
                addr.get("addressLocality"),
                addr.get("addressRegion"),
                addr.get("addressCountry"),
            ]
            return ", ".join(str(p) for p in parts if p)
        if isinstance(addr, str):
            return addr
    return ""


def _extract_salary(obj: dict[str, object]) -> tuple[int | None, int | None, str, str]:
    base = obj.get("baseSalary")
    if not isinstance(base, dict):
        return None, None, "USD", "year"
    currency = str(base.get("currency") or "USD")
    val = base.get("value")
    if isinstance(val, dict):
        unit = str(val.get("unitText") or "year").lower()
        s_min = _to_int(val.get("minValue") or val.get("value"))
        s_max = _to_int(val.get("maxValue") or val.get("value"))
        return s_min, s_max, currency, unit
    num = _to_int(val)
    return num, num, currency, "year"


def _to_int(raw: object) -> int | None:
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        digits = "".join(ch for ch in raw if ch.isdigit())
        return int(digits) if digits else None
    return None


def _parse_date(raw: object) -> datetime:
    if isinstance(raw, str) and raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


class WebSchemaOrgConnector(BaseJobConnector):
    """Searches open web via DuckDuckGo for direct company career pages & ATS listings."""

    source_name = "Open Web Search"
    source_type = "web_search"

    def fetch_jobs(self, query: str, location: str = "", limit: int = 15) -> list[NormalizedJob]:
        cb_key = "web:ddgs"
        if breaker.is_open(cb_key):
            return []

        search_q = (
            f'{query or "software engineer"} {location} '
            '(careers OR "apply" OR site:boards.greenhouse.io '
            "OR site:jobs.lever.co OR site:jobs.ashbyhq.com)"
        ).strip()
        try:
            from ddgs import DDGS

            with DDGS() as ddgs:
                hits = list(ddgs.text(search_q, max_results=min(limit, 12)))
            breaker.record_success(cb_key)
        except Exception as exc:
            breaker.record_failure(cb_key)
            logger.warning("ddgs_search_failed", error=str(exc))
            return []

        results: list[NormalizedJob] = []
        for hit in hits:
            href = str(hit.get("href") or "").strip()
            if not href:
                continue

            # Dynamically register newly discovered ATS boards!
            ats_info = detect_ats_board_from_url(href)
            if ats_info:
                ats_type, token = ats_info
                CompanyBoard.objects.get_or_create(
                    ats_type=ats_type,
                    board_token=token,
                    defaults={"name": token.replace("-", " ").title()},
                )

            try:
                resp = safe_fetch(href, timeout=6.0, respect_robots=True)
            except UnsafeURLError as exc:
                logger.debug("web_fetch_unsafe_url", url=href, error=str(exc))
                continue
            except Exception as exc:
                logger.debug("web_fetch_failed", url=href, error=str(exc))
                continue

            if resp.status_code != 200:
                continue

            extracted = extract_schema_org_job_postings(resp.text, resp.url)
            if extracted:
                results.extend(extracted)
            elif hit.get("title"):
                try:
                    results.append(
                        NormalizedJob(
                            title=str(hit["title"])[:255],
                            company_name="Company Career Page",
                            location_text=location,
                            description_text=str(hit.get("body") or ""),
                            source_type="web_search",
                            source_name="Web Search",
                            source_url=resp.url,
                            apply_url=resp.url,
                            apply_url_type="ats_direct" if ats_info else "company_direct",
                        ).finalized()
                    )
                except ValueError:
                    continue

            if len(results) >= limit:
                break

        return results[:limit]
