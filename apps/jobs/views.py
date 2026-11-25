"""Authenticated job search, filtering, detail, and live web-discovery trigger views."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import User
from apps.core.sanitizers import sanitize_job_html
from apps.discovery.models import SearchRun
from apps.discovery.tasks import execute_search_run_task
from apps.matching.services import rank_jobs_for_resume
from apps.resumes.models import Resume

from .filters import JobPostingFilter
from .models import JobPosting


def _resolve_active_resume(request: HttpRequest, user: User) -> Resume | None:
    resume_id = request.GET.get("resume")
    if resume_id:
        selected = Resume.objects.filter(
            pk=resume_id, user=user, parse_status=Resume.ParseStatus.READY
        ).first()
        if selected:
            return selected
    return Resume.objects.filter(
        user=user, is_active=True, parse_status=Resume.ParseStatus.READY
    ).first()


@require_GET
def job_search(request: HttpRequest) -> HttpResponse:
    """Resume-driven hybrid job search with live HTMX filtering and pagination."""
    assert isinstance(request.user, User)
    user_resumes = list(
        Resume.objects.filter(user=request.user, parse_status=Resume.ParseStatus.READY)
    )
    active_resume = _resolve_active_resume(request, request.user)

    cutoff_45d = datetime.now(UTC) - timedelta(days=45)
    base_qs = JobPosting.objects.filter(is_active=True, posted_at__gte=cutoff_45d)
    filterset = JobPostingFilter(request.GET or None, queryset=base_qs)

    sort_mode = request.GET.get("sort", "best_match")
    if sort_mode not in {"best_match", "most_recent", "highest_salary"}:
        sort_mode = "best_match"

    min_match_raw = request.GET.get("min_match", "")
    min_match: int | None = None
    if min_match_raw.isdigit():
        min_match = max(0, min(100, int(min_match_raw)))

    ranked = rank_jobs_for_resume(
        filterset.qs,
        active_resume,
        query_text=request.GET.get("q", ""),
        sort_mode=sort_mode,
        min_match=min_match,
    )

    paginator = Paginator(ranked, 15)
    page_obj = paginator.get_page(request.GET.get("page"))

    latest_run = SearchRun.objects.filter(user=request.user).first()

    context = {
        "filter": filterset,
        "page_obj": page_obj,
        "total_results": len(ranked),
        "user_resumes": user_resumes,
        "active_resume": active_resume,
        "sort_mode": sort_mode,
        "min_match": min_match_raw,
        "latest_run": latest_run,
    }

    if request.headers.get("HX-Request") == "true":
        return render(request, "jobs/partials/job_results.html", context)

    return render(request, "jobs/job_search.html", context)


@require_GET
def job_detail(request: HttpRequest, pk: str) -> HttpResponse:
    """View sanitized job description, skill match breakdown, and external apply link."""
    assert isinstance(request.user, User)
    job = get_object_or_404(JobPosting, pk=pk, is_active=True)
    active_resume = _resolve_active_resume(request, request.user)
    ranked_list = rank_jobs_for_resume(JobPosting.objects.filter(pk=job.pk), active_resume)
    ranked = ranked_list[0] if ranked_list else None
    safe_html = sanitize_job_html(job.description_html)

    return render(
        request,
        "jobs/job_detail.html",
        {
            "job": job,
            "safe_description_html": safe_html,
            "ranked": ranked,
            "active_resume": active_resume,
        },
    )


@require_POST
def trigger_web_search(request: HttpRequest) -> HttpResponse:
    """Trigger an async Option 2 discovery run across ATS boards, platforms, and open web."""
    assert isinstance(request.user, User)
    active_resume = _resolve_active_resume(request, request.user)
    query_text = request.POST.get("q", "").strip()[:300]
    location = request.POST.get("location", "").strip()[:150]

    if not query_text and active_resume and active_resume.target_roles:
        query_text = " ".join(active_resume.target_roles[:2])[:300]
    if not query_text:
        query_text = "software engineer"

    run = SearchRun.objects.create(
        user=request.user,
        resume=active_resume,
        query_text=query_text,
        location=location,
        status=SearchRun.Status.PENDING,
    )
    transaction.on_commit(lambda: execute_search_run_task.delay(str(run.pk)))

    if request.headers.get("HX-Request") == "true":
        return render(request, "jobs/partials/search_run_status.html", {"run": run})

    messages.info(request, "Searching company sites and job platforms for latest openings...")
    return redirect("jobs:search")


@require_GET
def search_run_status(request: HttpRequest, pk: str) -> HttpResponse:
    """HTMX polling endpoint for live SearchRun progress and per-source telemetry."""
    assert isinstance(request.user, User)
    run = get_object_or_404(SearchRun, pk=pk, user=request.user)
    response = render(request, "jobs/partials/search_run_status.html", {"run": run})
    if run.status in {
        SearchRun.Status.COMPLETED,
        SearchRun.Status.PARTIAL,
        SearchRun.Status.FAILED,
    }:
        response["HX-Trigger"] = "refreshJobs"
    return response
