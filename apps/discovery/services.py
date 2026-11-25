"""Discovery orchestration, job deduplication, batched embeddings, and board seeding."""

from __future__ import annotations

import structlog
from django.utils import timezone

from apps.core.ai import AIServiceError, CloudAIClient, compute_text_hash
from apps.discovery.connectors.ats import CompanyATSBoardConnector
from apps.discovery.connectors.base import BaseJobConnector
from apps.discovery.connectors.platforms import (
    AdzunaConnector,
    ArbeitnowConnector,
    JobSpyConnector,
    RemotiveConnector,
)
from apps.discovery.connectors.web import WebSchemaOrgConnector
from apps.discovery.models import CompanyBoard, SearchRun
from apps.discovery.schemas import NormalizedJob
from apps.jobs.models import JobPosting

logger = structlog.get_logger(__name__)

APPLY_PRIORITY = {
    "company_direct": 3,
    "ats_direct": 2,
    "job_platform": 1,
}

DEFAULT_COMPANY_BOARDS = [
    ("Stripe", "stripe.com", CompanyBoard.ATSType.GREENHOUSE, "stripe"),
    ("Cloudflare", "cloudflare.com", CompanyBoard.ATSType.GREENHOUSE, "cloudflare"),
    ("Figma", "figma.com", CompanyBoard.ATSType.GREENHOUSE, "figma"),
    ("Linear", "linear.app", CompanyBoard.ATSType.ASHBY, "linear"),
    ("Notion", "notion.so", CompanyBoard.ATSType.ASHBY, "notion"),
]


def seed_default_company_boards() -> int:
    """Seed initial well-known company ATS boards if none exist yet."""
    if CompanyBoard.objects.exists():
        return 0
    created = 0
    for name, domain, ats_type, token in DEFAULT_COMPANY_BOARDS:
        _, was_created = CompanyBoard.objects.get_or_create(
            ats_type=ats_type,
            board_token=token,
            defaults={"name": name, "domain": domain},
        )
        if was_created:
            created += 1
    return created


def deduplicate_normalized_jobs(jobs: list[NormalizedJob]) -> list[NormalizedJob]:
    """Collapse duplicate postings in-memory, preferring direct company apply URLs."""
    by_hash: dict[str, NormalizedJob] = {}
    for job in jobs:
        key = job.dedup_hash
        existing = by_hash.get(key)
        if existing is None:
            by_hash[key] = job
            continue
        if APPLY_PRIORITY.get(job.apply_url_type, 0) > APPLY_PRIORITY.get(
            existing.apply_url_type, 0
        ):
            by_hash[key] = job
    return list(by_hash.values())


def upsert_normalized_jobs(
    jobs: list[NormalizedJob], *, generate_embeddings: bool = True
) -> tuple[int, int]:
    """
    Persist deduplicated jobs into JobPosting, reusing cached text_hash embeddings
    and batching new Cloud embedding requests. Returns (total_processed, newly_created).
    """
    deduped = deduplicate_normalized_jobs(jobs)
    if not deduped:
        return 0, 0

    existing_by_hash = {
        jp.dedup_hash: jp
        for jp in JobPosting.objects.filter(dedup_hash__in=[j.dedup_hash for j in deduped])
    }

    to_create_jobs: list[tuple[NormalizedJob, str]] = []
    updated_count = 0

    for item in deduped:
        d_hash = item.dedup_hash
        t_hash = compute_text_hash(f"{item.title}\n{item.description_text}")
        existing = existing_by_hash.get(d_hash)

        if existing is not None:
            changed = False
            if APPLY_PRIORITY.get(item.apply_url_type, 0) > APPLY_PRIORITY.get(
                existing.apply_url_type, 0
            ):
                existing.apply_url = item.apply_url
                existing.apply_url_type = item.apply_url_type
                existing.source_type = item.source_type
                existing.source_name = item.source_name
                changed = True
            if not existing.is_active:
                existing.is_active = True
                changed = True
            existing.last_verified_at = timezone.now()
            if changed:
                existing.save()
                updated_count += 1
            else:
                existing.save(update_fields=["last_verified_at", "updated_at"])
        else:
            to_create_jobs.append((item, t_hash))

    # Reuse existing embeddings for identical text_hash before calling Cloud AI
    embeddings_by_text_hash: dict[str, list[float]] = {}
    if generate_embeddings and to_create_jobs:
        needed_hashes = {t_hash for _, t_hash in to_create_jobs}
        for jp in JobPosting.objects.filter(
            text_hash__in=needed_hashes, embedding__isnull=False
        ).only("text_hash", "embedding"):
            if jp.embedding is not None:
                embeddings_by_text_hash[jp.text_hash] = list(jp.embedding)

        uncached = [
            (item, t_hash)
            for item, t_hash in to_create_jobs
            if t_hash not in embeddings_by_text_hash
        ]
        if uncached:
            try:
                ai_client = CloudAIClient()
                texts = [
                    f"{item.title} at {item.company_name}\n{item.description_text[:4000]}"
                    for item, _ in uncached
                ]
                vectors = ai_client.generate_embeddings_batch(texts)
                for (_, t_hash), vec in zip(uncached, vectors, strict=True):
                    embeddings_by_text_hash[t_hash] = vec
            except AIServiceError as exc:
                logger.warning("job_batch_embedding_skipped", error=str(exc))

    created_count = 0
    for item, t_hash in to_create_jobs:
        sal_min, sal_max = item.normalized_salary_usd
        JobPosting.objects.create(
            title=item.title,
            company_name=item.company_name,
            company_domain=item.company_domain,
            location_text=item.location_text,
            country_code=item.country_code,
            work_mode=item.work_mode,
            remote_scope=item.remote_scope,
            employment_type=item.employment_type,
            experience_level=item.experience_level,
            salary_min_raw=item.salary_min_raw,
            salary_max_raw=item.salary_max_raw,
            salary_currency=item.salary_currency,
            salary_interval=item.salary_interval,
            salary_annual_min_usd=sal_min,
            salary_annual_max_usd=sal_max,
            description_html=item.description_html,
            description_text=item.description_text,
            skills_extracted=item.skills_extracted,
            source_type=item.source_type,
            source_name=item.source_name,
            source_url=item.source_url,
            apply_url=item.apply_url,
            apply_url_type=item.apply_url_type,
            external_id=item.external_id,
            dedup_hash=item.dedup_hash,
            text_hash=t_hash,
            posted_at=item.posted_at,
            last_verified_at=timezone.now(),
            embedding=embeddings_by_text_hash.get(t_hash),
        )
        created_count += 1

    return len(deduped), created_count


def get_default_connectors() -> list[BaseJobConnector]:
    """Return configured Option 2 free-first connectors across ATS, platforms, and open web."""
    return [
        CompanyATSBoardConnector(),
        AdzunaConnector(),
        RemotiveConnector(),
        ArbeitnowConnector(),
        WebSchemaOrgConnector(),
        JobSpyConnector(),
    ]


def execute_search_run(
    search_run_id: str, connectors: list[BaseJobConnector] | None = None
) -> None:
    """Run all discovery connectors for a SearchRun and record per-source telemetry."""
    run = SearchRun.objects.filter(pk=search_run_id).select_related("resume").first()
    if run is None:
        return

    run.status = SearchRun.Status.RUNNING
    run.save(update_fields=["status", "updated_at"])

    seed_default_company_boards()

    query = run.query_text.strip()
    if not query and run.resume and run.resume.target_roles:
        query = " ".join(run.resume.target_roles[:2])

    active_connectors = connectors or get_default_connectors()
    collected: list[NormalizedJob] = []
    telemetry: dict[str, dict[str, object]] = {}
    had_errors = False

    for conn in active_connectors:
        try:
            jobs = conn.fetch_jobs(query=query, location=run.location, limit=25)
            collected.extend(jobs)
            telemetry[conn.source_name] = {"status": "ok", "count": len(jobs)}
        except Exception as exc:
            had_errors = True
            telemetry[conn.source_name] = {"status": "error", "error": str(exc), "count": 0}
            logger.warning("connector_failed", connector=conn.source_name, error=str(exc))

    total_found, newly_created = upsert_normalized_jobs(collected)

    run.jobs_found = total_found
    run.jobs_created = newly_created
    run.source_telemetry = telemetry
    run.status = SearchRun.Status.PARTIAL if (had_errors and total_found > 0) else (
        SearchRun.Status.FAILED if (had_errors and total_found == 0) else SearchRun.Status.COMPLETED
    )
    run.save(
        update_fields=[
            "jobs_found",
            "jobs_created",
            "source_telemetry",
            "status",
            "updated_at",
        ]
    )
