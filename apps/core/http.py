"""SSRF-safe HTTP client with DNS IP validation, redirect checking, and size caps."""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from threading import Lock
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import structlog

logger = structlog.get_logger(__name__)

FORBIDDEN_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
)

USER_AGENT = "SigmaFresherBot/1.0 (+https://github.com/AMSearth/SigmaFresher)"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

_rate_lock = Lock()
_domain_last_request: dict[str, float] = {}
_robots_cache: dict[str, tuple[float, RobotFileParser | None]] = {}


class UnsafeURLError(ValueError):
    """Raised when a URL fails SSRF or scheme safety validation."""


@dataclass(frozen=True)
class SafeHTTPResponse:
    url: str
    status_code: int
    content: bytes
    headers: dict[str, str]

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def _is_forbidden_ip(ip_str: str) -> bool:
    ip_obj = ipaddress.ip_address(ip_str)
    if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved:
        return True
    if ip_obj.is_multicast or ip_obj.is_unspecified:
        return True
    return any(ip_obj in net for net in FORBIDDEN_NETWORKS)


def validate_public_http_url(url: str) -> str:
    """Validate URL scheme and verify all resolved DNS records point to public IPs."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError(f"Forbidden URL scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise UnsafeURLError("URL is missing a hostname.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addr_info = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Failed to resolve host {parsed.hostname!r}") from exc

    if not addr_info:
        raise UnsafeURLError(f"No IP addresses resolved for {parsed.hostname!r}")

    for _, _, _, _, sockaddr in addr_info:
        ip_str = str(sockaddr[0])
        if _is_forbidden_ip(ip_str):
            raise UnsafeURLError(f"Blocked internal/private IP address {ip_str} for {url}")

    return url


def _throttle_domain(hostname: str, min_interval_seconds: float = 0.25) -> None:
    with _rate_lock:
        now = time.monotonic()
        last = _domain_last_request.get(hostname, 0.0)
        elapsed = now - last
        if elapsed < min_interval_seconds:
            time.sleep(min_interval_seconds - elapsed)
        _domain_last_request[hostname] = time.monotonic()


def _is_allowed_by_robots(url: str, timeout: float = 5.0) -> bool:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    now = time.monotonic()

    cached = _robots_cache.get(origin)
    if cached and (now - cached[0]) < 3600:
        parser = cached[1]
        return True if parser is None else parser.can_fetch(USER_AGENT, url)

    robots_url = f"{origin}/robots.txt"
    parser = None
    try:
        validate_public_http_url(robots_url)
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            resp = client.get(robots_url, headers={"User-Agent": USER_AGENT})
            if resp.status_code == 200 and len(resp.content) <= 256 * 1024:
                rp = RobotFileParser()
                rp.parse(resp.text.splitlines())
                parser = rp
    except Exception:
        parser = None

    _robots_cache[origin] = (now, parser)
    return True if parser is None else parser.can_fetch(USER_AGENT, url)


def safe_fetch(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, str | int | float | bool | None] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
    max_bytes: int = MAX_RESPONSE_BYTES,
    max_redirects: int = 3,
    respect_robots: bool = False,
) -> SafeHTTPResponse:
    """
    Fetch an external HTTP(S) URL safely with SSRF protection, redirect validation,
    per-domain throttling, and strict response size bounds.
    """
    current_url = validate_public_http_url(url)
    req_headers = {"User-Agent": USER_AGENT, **(headers or {})}

    if respect_robots and not _is_allowed_by_robots(current_url):
        raise UnsafeURLError(f"Blocked by robots.txt: {current_url}")

    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for hop in range(max_redirects + 1):
            parsed = urlparse(current_url)
            if parsed.hostname:
                _throttle_domain(parsed.hostname)

            query_params = params if hop == 0 else None
            with client.stream(
                method, current_url, params=query_params, headers=req_headers
            ) as response:
                if 300 <= response.status_code < 400:
                    location = response.headers.get("Location")
                    if not location or hop >= max_redirects:
                        raise UnsafeURLError(f"Too many or invalid redirects from {current_url}")
                    next_url = urljoin(current_url, location)
                    current_url = validate_public_http_url(next_url)
                    continue

                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise UnsafeURLError(
                            f"Response exceeded {max_bytes} bytes for {current_url}"
                        )
                    chunks.append(chunk)

                return SafeHTTPResponse(
                    url=str(response.url),
                    status_code=response.status_code,
                    content=b"".join(chunks),
                    headers={k.lower(): v for k, v in response.headers.items()},
                )

    raise UnsafeURLError(f"Exceeded max redirects ({max_redirects}) for {url}")
