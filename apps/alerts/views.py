"""Authenticated views for saved job tracking and email alerts."""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.models import User
from apps.jobs.models import JobPosting
from apps.resumes.models import Resume

from .forms import JobAlertForm, SavedJobUpdateForm
from .models import JobAlert, SavedJob


@require_GET
def saved_jobs_list(request: HttpRequest) -> HttpResponse:
    """List user's bookmarked and applied jobs."""
    assert isinstance(request.user, User)
    saved_items = SavedJob.objects.filter(user=request.user).select_related("job")
    status_filter = request.GET.get("status", "")
    if status_filter:
        saved_items = saved_items.filter(status=status_filter)

    return render(
        request,
        "alerts/saved_jobs.html",
        {
            "saved_items": saved_items,
            "status_choices": SavedJob.Status.choices,
            "current_status": status_filter,
        },
    )


@require_POST
def save_job(request: HttpRequest, job_id: str) -> HttpResponse:
    """Save a job posting to the user's tracker."""
    assert isinstance(request.user, User)
    job = get_object_or_404(JobPosting, pk=job_id, is_active=True)
    _, created = SavedJob.objects.get_or_create(user=request.user, job=job)
    if created:
        messages.success(request, f"Saved '{job.title}' to your tracker.")
    else:
        messages.info(request, f"'{job.title}' is already in your saved jobs.")
    return redirect("alerts:saved_list")


@require_POST
def update_saved_job(request: HttpRequest, pk: str) -> HttpResponse:
    """Update status/notes for an owned SavedJob."""
    assert isinstance(request.user, User)
    saved = get_object_or_404(SavedJob, pk=pk, user=request.user)
    form = SavedJobUpdateForm(request.POST, instance=saved)
    if form.is_valid():
        updated: SavedJob = form.save(commit=False)
        if updated.status == SavedJob.Status.APPLIED and updated.applied_at is None:
            updated.applied_at = timezone.now()
        updated.save()
        messages.success(request, "Saved job updated.")
    return redirect("alerts:saved_list")


@require_POST
def delete_saved_job(request: HttpRequest, pk: str) -> HttpResponse:
    """Remove a SavedJob owned by the user."""
    assert isinstance(request.user, User)
    saved = get_object_or_404(SavedJob, pk=pk, user=request.user)
    saved.delete()
    messages.success(request, "Removed job from tracker.")
    return redirect("alerts:saved_list")


@require_http_methods(["GET", "POST"])
def alerts_list(request: HttpRequest) -> HttpResponse:
    """Manage daily/weekly email job alerts."""
    assert isinstance(request.user, User)
    if request.method == "POST":
        form = JobAlertForm(request.POST)
        if form.is_valid():
            alert: JobAlert = form.save(commit=False)
            alert.user = request.user
            alert.resume = Resume.objects.filter(
                user=request.user, is_active=True, parse_status=Resume.ParseStatus.READY
            ).first()
            alert.save()
            messages.success(request, f"Created alert '{alert.name}'.")
            return redirect("alerts:alerts_list")
    else:
        form = JobAlertForm()

    alerts = JobAlert.objects.filter(user=request.user)
    return render(request, "alerts/alerts_list.html", {"form": form, "alerts": alerts})


@require_POST
def toggle_alert(request: HttpRequest, pk: str) -> HttpResponse:
    """Pause or resume an owned JobAlert."""
    assert isinstance(request.user, User)
    alert = get_object_or_404(JobAlert, pk=pk, user=request.user)
    alert.is_active = not alert.is_active
    alert.save(update_fields=["is_active", "updated_at"])
    return redirect("alerts:alerts_list")


@require_POST
def delete_alert(request: HttpRequest, pk: str) -> HttpResponse:
    """Delete an owned JobAlert."""
    assert isinstance(request.user, User)
    alert = get_object_or_404(JobAlert, pk=pk, user=request.user)
    alert.delete()
    messages.success(request, "Job alert deleted.")
    return redirect("alerts:alerts_list")
