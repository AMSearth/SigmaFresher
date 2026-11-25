"""Hybrid resume-to-job matching (pgvector + FTS RRF on Postgres, portable fallback)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from django.db import connection
from django.db.models import F, QuerySet

from apps.jobs.models import JobPosting
from apps.resumes.models import Resume


@dataclass
class RankedJob:
    job: JobPosting
    match_percent: int
    matched_skills: list[str]
    missing_skills: list[str]
    score: float


def cosine_similarity(vec_a: list[float] | None, vec_b: list[float] | None) -> float:
    """Compute cosine similarity in [-1, 1] safely."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def _compute_skill_overlap(resume: Resume | None, job: JobPosting) -> tuple[list[str], list[str]]:
    if resume is None:
        return [], list(job.skills_extracted[:6])
    resume_skills = {s.strip().lower(): s.strip() for s in resume.skills if s.strip()}
    job_skills = [s.strip() for s in job.skills_extracted if s.strip()]
    if not job_skills:
        # Check resume skills mentioned in job title/description
        haystack = f"{job.title} {job.description_text}".lower()
        matched = [orig for low, orig in resume_skills.items() if low in haystack]
        return matched[:8], []

    matched = [s for s in job_skills if s.lower() in resume_skills]
    missing = [s for s in job_skills if s.lower() not in resume_skills]
    return matched[:8], missing[:6]


def _recency_decay(posted_at: datetime, now: datetime) -> float:
    dt = posted_at if posted_at.tzinfo else posted_at.replace(tzinfo=UTC)
    age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
    return math.exp(-age_days / 14.0)


def _postgres_rrf_scores(
    filtered_ids: list[str],
    query_text: str,
    resume_embedding: list[float] | None,
    k: int = 60,
) -> dict[str, float]:
    """Execute parameterized FTS + pgvector RRF inside PostgreSQL over pre-filtered IDs."""
    if not filtered_ids or (not query_text.strip() and not resume_embedding):
        return {}

    sql_parts = [
        "WITH filtered AS (SELECT id, tsv, embedding FROM jobs_jobposting WHERE id = ANY(%s))"
    ]
    params: list[Any] = [filtered_ids]

    has_fts = bool(query_text.strip())
    has_vec = bool(resume_embedding)

    if has_fts:
        sql_parts.append(
            """
            , fts AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           ORDER BY ts_rank_cd(
                               tsv, websearch_to_tsquery('english', %s)
                           ) DESC, id DESC
                       ) AS rnk
                FROM filtered
                WHERE tsv @@ websearch_to_tsquery('english', %s)
                LIMIT 200
            )
            """
        )
        params.extend([query_text, query_text])

    if has_vec:
        vec_literal = "[" + ",".join(f"{x:.8f}" for x in resume_embedding or []) + "]"
        sql_parts.append(
            """
            , vec AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           ORDER BY embedding <=> %s::vector ASC, id DESC
                       ) AS rnk
                FROM filtered
                WHERE embedding IS NOT NULL
                LIMIT 200
            )
            """
        )
        params.append(vec_literal)

    if has_fts and has_vec:
        sql_parts.append(
            """
            SELECT COALESCE(f.id, v.id)::text AS id,
                   COALESCE(1.0 / (%s + f.rnk), 0.0) + COALESCE(1.0 / (%s + v.rnk), 0.0) AS rrf
            FROM fts f
            FULL OUTER JOIN vec v ON f.id = v.id
            """
        )
        params.extend([k, k])
    elif has_fts:
        sql_parts.append("SELECT id::text AS id, 1.0 / (%s + rnk) AS rrf FROM fts")
        params.append(k)
    else:
        sql_parts.append("SELECT id::text AS id, 1.0 / (%s + rnk) AS rrf FROM vec")
        params.append(k)

    full_sql = "\n".join(sql_parts)
    with connection.cursor() as cursor:
        cursor.execute(full_sql, params)
        return {str(row[0]): float(row[1]) for row in cursor.fetchall()}


def rank_jobs_for_resume(
    queryset: QuerySet[JobPosting],
    resume: Resume | None,
    *,
    query_text: str = "",
    sort_mode: str = "best_match",
    min_match: int | None = None,
) -> list[RankedJob]:
    """Rank filtered JobPosting rows combining semantic match, skill overlap, and recency decay."""
    if sort_mode == "most_recent":
        jobs = list(queryset.order_by("-posted_at", "-created_at")[:200])
    elif sort_mode == "highest_salary":
        jobs = list(
            queryset.order_by(
                F("salary_annual_max_usd").desc(nulls_last=True),
                "-posted_at",
            )[:200]
        )
    else:
        jobs = list(queryset.order_by("-posted_at", "-created_at")[:300])

    if not jobs:
        return []

    now = datetime.now(UTC)
    resume_vec = list(resume.embedding) if (resume and resume.embedding is not None) else None

    effective_query = query_text.strip()
    if not effective_query and resume and resume.target_roles:
        effective_query = " ".join(resume.target_roles[:2])

    rrf_scores: dict[str, float] = {}
    if connection.vendor == "postgresql" and sort_mode == "best_match":
        rrf_scores = _postgres_rrf_scores(
            [str(j.pk) for j in jobs],
            effective_query,
            resume_vec,
        )

    ranked: list[RankedJob] = []
    for job in jobs:
        matched, missing = _compute_skill_overlap(resume, job)
        job_vec = list(job.embedding) if job.embedding is not None else None
        sim = cosine_similarity(resume_vec, job_vec)

        total_skills = len(matched) + len(missing)
        skill_ratio = (len(matched) / total_skills) if total_skills > 0 else 0.0

        role_bonus = 0.0
        if resume and resume.target_roles:
            title_low = job.title.lower()
            if any(role.lower() in title_low for role in resume.target_roles):
                role_bonus = 0.15

        decay = _recency_decay(job.posted_at, now)
        if resume is None:
            match_pct = 0
            combined_score = decay
        else:
            raw_relevance = (0.55 * sim) + (0.35 * skill_ratio) + role_bonus
            if str(job.pk) in rrf_scores:
                # Boost with normalized Postgres RRF rank signal
                raw_relevance = min(1.0, (0.65 * raw_relevance) + (15.0 * rrf_scores[str(job.pk)]))
            match_pct = int(round(max(0.0, min(1.0, raw_relevance)) * 100))
            combined_score = (0.75 * raw_relevance) + (0.25 * decay)

        if min_match is not None and resume is not None and match_pct < min_match:
            continue

        ranked.append(
            RankedJob(
                job=job,
                match_percent=match_pct,
                matched_skills=matched,
                missing_skills=missing,
                score=combined_score,
            )
        )

    if sort_mode == "best_match":
        ranked.sort(key=lambda item: (item.score, item.job.posted_at), reverse=True)

    return ranked
