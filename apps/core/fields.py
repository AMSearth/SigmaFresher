"""Portable database fields supporting pgvector on PostgreSQL and text fallback in unit tests."""

from __future__ import annotations

from typing import Any

from django.db.backends.base.base import BaseDatabaseWrapper
from pgvector.django import VectorField


class PortableVectorField(VectorField):
    """Uses pgvector's native vector(N) on PostgreSQL and JSON/text on SQLite test DBs."""

    def db_type(self, connection: BaseDatabaseWrapper) -> str:
        if connection.vendor == "postgresql":
            return super().db_type(connection)
        return "TEXT"

    def get_prep_value(self, value: Any) -> Any:
        if value is None:
            return None
        return super().get_prep_value(value)
