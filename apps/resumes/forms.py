"""Forms for uploading and labeling resumes."""

from __future__ import annotations

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.accounts.models import User

from .models import Resume
from .validators import validate_resume_upload


class ResumeUploadForm(forms.Form):
    label = forms.CharField(
        label="Resume Label (optional)",
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. Backend Django Resume"}),
    )
    file = forms.FileField(
        label="Resume File (PDF or DOCX, max 5 MB)",
        widget=forms.FileInput(attrs={"accept": ".pdf,.docx"}),
    )

    def __init__(self, *args: object, user: User | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.user = user
        self.detected_extension: str = ""

    def clean(self) -> dict[str, object]:
        cleaned = super().clean() or {}
        max_resumes: int = getattr(settings, "MAX_RESUMES_PER_USER", 5)
        if self.user is not None and Resume.objects.filter(user=self.user).count() >= max_resumes:
            raise ValidationError(
                f"Maximum of {max_resumes} resumes reached. Delete an existing resume first."
            )
        return cleaned

    def clean_file(self) -> object:
        uploaded = self.cleaned_data["file"]
        self.detected_extension = validate_resume_upload(uploaded)
        return uploaded
