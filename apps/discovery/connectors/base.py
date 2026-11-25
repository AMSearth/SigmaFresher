"""Base connector interface and per-source circuit breaker."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from threading import Lock

from apps.discovery.schemas import NormalizedJob


class CircuitBreaker:
    """Simple thread-safe circuit breaker to prevent repeated failing external calls."""

    def __init__(self, failure_threshold: int = 3, recovery_seconds: float = 300.0) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._lock = Lock()

    def is_open(self, key: str) -> bool:
        with self._lock:
            opened = self._opened_at.get(key)
            if opened is None:
                return False
            if (time.monotonic() - opened) >= self.recovery_seconds:
                self._opened_at.pop(key, None)
                self._failures[key] = 0
                return False
            return True

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._opened_at.pop(key, None)

    def record_failure(self, key: str) -> None:
        with self._lock:
            count = self._failures.get(key, 0) + 1
            self._failures[key] = count
            if count >= self.failure_threshold:
                self._opened_at[key] = time.monotonic()


breaker = CircuitBreaker()


class BaseJobConnector(ABC):
    """Abstract base class for all company ATS, job platform, and web connectors."""

    source_name: str
    source_type: str

    @abstractmethod
    def fetch_jobs(self, query: str, location: str = "", limit: int = 25) -> list[NormalizedJob]:
        """Fetch and normalize job postings matching the query/location."""
