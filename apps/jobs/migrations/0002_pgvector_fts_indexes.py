"""Add PostgreSQL pgvector HNSW and Full-Text Search GIN indexes (no-op on SQLite)."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def create_postgres_indexes(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    schema_editor.execute(
        """
        ALTER TABLE jobs_jobposting
        ADD COLUMN IF NOT EXISTS tsv tsvector GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(company_name, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(description_text, '')), 'C')
        ) STORED;
        """
    )
    schema_editor.execute(
        "CREATE INDEX IF NOT EXISTS jobs_jobposting_tsv_gin_idx "
        "ON jobs_jobposting USING gin (tsv);"
    )
    schema_editor.execute(
        "CREATE INDEX IF NOT EXISTS jobs_jobposting_embedding_hnsw_idx "
        "ON jobs_jobposting USING hnsw (embedding vector_cosine_ops);"
    )


def drop_postgres_indexes(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP INDEX IF EXISTS jobs_jobposting_embedding_hnsw_idx;")
    schema_editor.execute("DROP INDEX IF EXISTS jobs_jobposting_tsv_gin_idx;")
    schema_editor.execute("ALTER TABLE jobs_jobposting DROP COLUMN IF EXISTS tsv;")


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_postgres_indexes, reverse_code=drop_postgres_indexes),
    ]
