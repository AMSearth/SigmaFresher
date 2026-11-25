"""Authenticated views for resume upload, polling, download, activation, and deletion."""

from __future__ import annotations

import hashlib
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.models import User

from .forms import ResumeUploadForm
from .models import Resume
from .tasks import process_resume_task


def _hash_uploaded_file(uploaded_file: object) -> str:
    hasher = hashlib.sha256()
    for chunk in uploaded_file.chunks():  # type: ignore[attr-defined]
        hasher.update(chunk)
    uploaded_file.seek(0)  # type: ignore[attr-defined]
    return hasher.hexdigest()


@require_http_methods(["GET", "POST"])
def resume_list(request: HttpRequest) -> HttpResponse:
    """List user resumes and handle secure PDF/DOCX uploads."""
    assert isinstance(request.user, User)
    resumes = list(Resume.objects.filter(user=request.user))
    max_resumes: int = getattr(settings, "MAX_RESUMES_PER_USER", 5)

    if request.method == "POST":
        form = ResumeUploadForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            uploaded = form.cleaned_data["file"]
            safe_name = Path(getattr(uploaded, "name", "resume")).name[:255]
            file_hash = _hash_uploaded_file(uploaded)
            resume = Resume.objects.create(
                user=request.user,
                label=form.cleaned_data.get("label", ""),
                file=uploaded,
                original_filename=safe_name,
                file_extension=form.detected_extension,
                file_hash=file_hash,
                parse_status=Resume.ParseStatus.PENDING,
            )
            transaction.on_commit(lambda: process_resume_task.delay(str(resume.pk)))
            messages.success(request, "Resume uploaded and queued for AI skill extraction.")
            return redirect("resumes:list")
    else:
        form = ResumeUploadForm(user=request.user)

    return render(
        request,
        "resumes/resume_list.html",
        {
            "form": form,
            "resumes": resumes,
            "max_resumes": max_resumes,
        },
    )


@require_GET
def resume_status(request: HttpRequest, pk: str) -> HttpResponse:
    """HTMX partial returning live parse status for a user's resume."""
    assert isinstance(request.user, User)
    resume = get_object_or_404(Resume, pk=pk, user=request.user)
    return render(request, "resumes/partials/resume_card.html", {"resume": resume})


@require_POST
def resume_activate(request: HttpRequest, pk: str) -> HttpResponse:
    """Set one of the user's resumes as the active search resume."""
    assert isinstance(request.user, User)
    resume = get_object_or_404(Resume, pk=pk, user=request.user)
    with transaction.atomic():
        Resume.objects.filter(user=request.user, is_active=True).update(is_active=False)
        resume.is_active = True
        resume.save(update_fields=["is_active", "updated_at"])
    messages.success(request, f"Activated resume: {resume.label or resume.original_filename}")
    return redirect("resumes:list")


@require_POST
def resume_reparse(request: HttpRequest, pk: str) -> HttpResponse:
    """Re-run Cloud LLM parsing & embedding for an existing resume."""
    assert isinstance(request.user, User)
    resume = get_object_or_404(Resume, pk=pk, user=request.user)
    resume.parse_status = Resume.ParseStatus.PENDING
    resume.error_message = ""
    resume.save(update_fields=["parse_status", "error_message", "updated_at"])
    transaction.on_commit(lambda: process_resume_task.delay(str(resume.pk)))
    messages.info(request, "Resume queued for re-parsing.")
    return redirect("resumes:list")


@require_GET
def resume_download(request: HttpRequest, pk: str) -> FileResponse:
    """Serve private resume file only to its owner with attachment headers."""
    assert isinstance(request.user, User)
    resume = get_object_or_404(Resume, pk=pk, user=request.user)
    if (
        not resume.file
        or not resume.file.name
        or not resume.file.storage.exists(resume.file.name)
    ):
        raise Http404("Resume file not found.")
    response = FileResponse(
        resume.file.open("rb"),
        as_attachment=True,
        filename=resume.original_filename,
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_POST
def resume_delete(request: HttpRequest, pk: str) -> HttpResponse:
    """Hard-delete a user's resume and its private file from storage."""
    assert isinstance(request.user, User)
    resume = get_object_or_404(Resume, pk=pk, user=request.user)
    was_active = resume.is_active
    if resume.file:
        resume.file.delete(save=False)
    resume.delete()
    if was_active:
        next_resume = Resume.objects.filter(user=request.user).first()
        if next_resume:
            next_resume.is_active = True
            next_resume.save(update_fields=["is_active", "updated_at"])
    messages.success(request, "Resume permanently deleted.")
    return redirect("resumes:list")
