"""HTML sanitization, external apply-URL safety validation, and salary normalization."""

from __future__ import annotations

from urllib.parse import urlparse

import nh3
from bs4 import BeautifulSoup

ALLOWED_JOB_HTML_TAGS = {
    "p",
    "br",
    "ul",
    "ol",
    "li",
    "strong",
    "b",
    "em",
    "i",
    "h1",
    "h2",
    "h3",
    "h4",
    "code",
    "pre",
}

CURRENCY_TO_USD = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "CAD": 0.72,
    "AUD": 0.65,
    "INR": 0.012,
    "SGD": 0.74,
    "CHF": 1.13,
}

INTERVAL_TO_ANNUAL = {
    "year": 1.0,
    "yearly": 1.0,
    "annual": 1.0,
    "month": 12.0,
    "monthly": 12.0,
    "week": 52.0,
    "weekly": 52.0,
    "day": 260.0,
    "daily": 260.0,
    "hour": 2080.0,
    "hourly": 2080.0,
}


def sanitize_job_html(raw_html: str) -> str:
    """Strip unsafe scripts, event handlers, and embeds from scraped job HTML."""
    if not raw_html:
        return ""
    return nh3.clean(raw_html, tags=ALLOWED_JOB_HTML_TAGS, attributes={})


def html_to_plain_text(raw_html: str) -> str:
    """Convert HTML content into clean plain text."""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    text = soup.get_text(separator="\n")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def validate_apply_url(url: str) -> str:
    """Ensure external apply links use http/https with a valid network host."""
    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Unsafe or invalid apply URL: {url!r}")
    return cleaned


def normalize_salary_to_annual_usd(
    salary_min: float | int | None,
    salary_max: float | int | None,
    currency: str = "USD",
    interval: str = "year",
) -> tuple[int | None, int | None]:
    """Convert raw salary bounds into annualized USD integers for uniform filtering."""
    rate = CURRENCY_TO_USD.get((currency or "USD").upper(), 1.0)
    multiplier = INTERVAL_TO_ANNUAL.get((interval or "year").lower(), 1.0)

    def _conv(val: float | int | None) -> int | None:
        if val is None or val <= 0:
            return None
        annual = float(val) * multiplier * rate
        # Guard against hourly rates accidentally tagged as yearly or vice versa
        if annual < 5000 and multiplier == 1.0:
            annual *= 2080.0
        return int(round(annual))

    norm_min = _conv(salary_min)
    norm_max = _conv(salary_max)
    if norm_min and norm_max and norm_min > norm_max:
        norm_min, norm_max = norm_max, norm_min
    return norm_min, norm_max
