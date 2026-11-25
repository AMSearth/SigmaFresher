"""Profile management and GDPR account deletion views."""

from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .forms import JobSeekerProfileForm
from .models import JobSeekerProfile, User


@require_http_methods(["GET", "POST"])
def profile_edit(request: HttpRequest) -> HttpResponse:
    """View and update editable job seeker preferences."""
    assert isinstance(request.user, User)
    profile, _ = JobSeekerProfile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        form = JobSeekerProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Job preferences updated.")
            return redirect("accounts:profile")
    else:
        form = JobSeekerProfileForm(instance=profile)

    return render(request, "accounts/profile.html", {"form": form, "profile": profile})


@require_POST
def delete_account(request: HttpRequest) -> HttpResponse:
    """Hard-delete the authenticated user's account and private resume files."""
    assert isinstance(request.user, User)
    user = request.user
    for resume in user.resumes.all():
        if resume.file:
            resume.file.delete(save=False)
    logout(request)
    user.delete()
    messages.success(
        request, "Your account and all uploaded resumes have been permanently deleted."
    )
    return redirect("account_login")
