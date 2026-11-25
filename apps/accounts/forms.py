"""Forms for editing JobSeekerProfile preferences."""

from django import forms

from .models import JobSeekerProfile

WORK_MODE_CHOICES = [
    ("remote", "Remote"),
    ("hybrid", "Hybrid"),
    ("onsite", "Onsite"),
]


def _parse_csv_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


class JobSeekerProfileForm(forms.ModelForm):
    target_roles_csv = forms.CharField(
        label="Target Roles (comma-separated)",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. Backend Engineer, Django Developer"}),
    )
    preferred_locations_csv = forms.CharField(
        label="Preferred Locations (comma-separated)",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. Remote, Bangalore, New York"}),
    )
    preferred_work_modes = forms.MultipleChoiceField(
        label="Preferred Work Modes",
        required=False,
        choices=WORK_MODE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = JobSeekerProfile
        fields = ["min_salary_usd", "open_to_remote"]

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        if self.instance and self.instance.pk:
            self.fields["target_roles_csv"].initial = ", ".join(self.instance.target_roles)
            self.fields["preferred_locations_csv"].initial = ", ".join(
                self.instance.preferred_locations
            )
            self.fields["preferred_work_modes"].initial = self.instance.preferred_work_modes

    def save(self, commit: bool = True) -> JobSeekerProfile:
        profile: JobSeekerProfile = super().save(commit=False)
        profile.target_roles = _parse_csv_list(self.cleaned_data.get("target_roles_csv", ""))
        profile.preferred_locations = _parse_csv_list(
            self.cleaned_data.get("preferred_locations_csv", "")
        )
        profile.preferred_work_modes = list(self.cleaned_data.get("preferred_work_modes", []))
        if commit:
            profile.save()
        return profile
