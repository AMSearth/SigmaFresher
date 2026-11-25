"""django-filter FilterSet for JobPosting search filters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import django_filters
from django import forms
from django.db.models import Q, QuerySet

from apps.jobs.models import JobPosting


class JobPostingFilter(django_filters.FilterSet):
    """Comprehensive filter set for resume-based and keyword job search."""

    q = django_filters.CharFilter(
        method="filter_query",
        label="Keywords",
        widget=forms.TextInput(attrs={"placeholder": "Role, skill, or company..."}),
    )
    location = django_filters.CharFilter(
        method="filter_location",
        label="Location / Country",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Remote, US, Bangalore"}),
    )
    work_mode = django_filters.ChoiceFilter(
        choices=JobPosting.WorkMode.choices,
        empty_label="Any Work Mode",
        label="Work Mode",
    )
    remote_scope = django_filters.ChoiceFilter(
        choices=JobPosting.RemoteScope.choices,
        empty_label="Any Remote Scope",
        label="Remote Region",
    )
    posted_within = django_filters.ChoiceFilter(
        method="filter_posted_within",
        label="Posted Recency",
        empty_label="Any Time (Last 45d)",
        choices=[
            ("1", "Last 24 hours"),
            ("3", "Last 3 days"),
            ("7", "Last 7 days"),
            ("30", "Last 30 days"),
        ],
    )
    employment_type = django_filters.ChoiceFilter(
        choices=JobPosting.EmploymentType.choices,
        empty_label="Any Type",
        label="Employment Type",
    )
    experience_level = django_filters.ChoiceFilter(
        choices=JobPosting.ExperienceLevel.choices,
        empty_label="Any Experience",
        label="Experience Level",
    )
    min_salary = django_filters.NumberFilter(
        method="filter_min_salary",
        label="Min Annual Salary (USD)",
        widget=forms.NumberInput(attrs={"placeholder": "e.g. 90000", "min": "0"}),
    )
    source_type = django_filters.ChoiceFilter(
        choices=JobPosting.SourceType.choices,
        empty_label="All Sources",
        label="Source Category",
    )
    direct_only = django_filters.BooleanFilter(
        method="filter_direct_only",
        label="Direct Company / ATS Links Only",
        widget=forms.CheckboxInput(),
    )

    class Meta:
        model = JobPosting
        fields = [
            "q",
            "location",
            "work_mode",
            "remote_scope",
            "posted_within",
            "employment_type",
            "experience_level",
            "min_salary",
            "source_type",
            "direct_only",
        ]

    def filter_query(
        self, queryset: QuerySet[JobPosting], name: str, value: str
    ) -> QuerySet[JobPosting]:
        cleaned = value.strip()
        if not cleaned:
            return queryset
        return queryset.filter(
            Q(title__icontains=cleaned)
            | Q(company_name__icontains=cleaned)
            | Q(description_text__icontains=cleaned)
        )

    def filter_location(
        self, queryset: QuerySet[JobPosting], name: str, value: str
    ) -> QuerySet[JobPosting]:
        cleaned = value.strip()
        if not cleaned:
            return queryset
        return queryset.filter(
            Q(location_text__icontains=cleaned) | Q(country_code__iexact=cleaned)
        )

    def filter_posted_within(
        self, queryset: QuerySet[JobPosting], name: str, value: str
    ) -> QuerySet[JobPosting]:
        try:
            days = int(value)
        except (TypeError, ValueError):
            return queryset
        cutoff = datetime.now(UTC) - timedelta(days=days)
        return queryset.filter(posted_at__gte=cutoff)

    def filter_min_salary(
        self, queryset: QuerySet[JobPosting], name: str, value: object
    ) -> QuerySet[JobPosting]:
        if value is None:
            return queryset
        try:
            floor = int(str(value))
        except ValueError:
            return queryset
        return queryset.filter(
            Q(salary_annual_max_usd__gte=floor) | Q(salary_annual_min_usd__gte=floor)
        )

    def filter_direct_only(
        self, queryset: QuerySet[JobPosting], name: str, value: bool
    ) -> QuerySet[JobPosting]:
        if not value:
            return queryset
        return queryset.filter(
            apply_url_type__in=[
                JobPosting.ApplyURLType.COMPANY_DIRECT,
                JobPosting.ApplyURLType.ATS_DIRECT,
            ]
        )
