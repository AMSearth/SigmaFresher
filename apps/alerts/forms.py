"""Forms for saved job status updates and job alert creation."""

from django import forms

from apps.jobs.models import JobPosting

from .models import JobAlert, SavedJob


class SavedJobUpdateForm(forms.ModelForm):
    class Meta:
        model = SavedJob
        fields = ["status", "notes"]
        widgets = {
            "notes": forms.Textarea(
                attrs={"rows": 2, "placeholder": "Interview notes or contacts..."}
            ),
        }


class JobAlertForm(forms.ModelForm):
    work_mode = forms.ChoiceField(
        choices=[("", "Any Work Mode"), *list(JobPosting.WorkMode.choices)],
        required=False,
    )

    class Meta:
        model = JobAlert
        fields = ["name", "query_text", "location", "work_mode", "min_salary_usd", "frequency"]
