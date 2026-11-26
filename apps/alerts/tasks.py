"""Celery tasks for sending scheduled resume-matched job alert digests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.jobs.filters import JobPostingFilter
from apps.jobs.models import JobPosting
from apps.matching.services import rank_jobs_for_resume

from .models import JobAlert


@shared_task
def send_job_alerts_task() -> dict[str, int]:
    """Send due daily/weekly matching job digests to users."""
    now = datetime.now(UTC)
    sent_count = 0

    alerts = JobAlert.objects.filter(is_active=True).select_related("user", "resume")
    for alert in alerts:
        interval = timedelta(days=7 if alert.frequency == JobAlert.Frequency.WEEKLY else 1)
        if alert.last_sent_at and (now - alert.last_sent_at) < interval:
            continue

        since = alert.last_sent_at or (now - interval)
        qs = JobPosting.objects.filter(is_active=True, posted_at__gte=since)
        filter_params: dict[str, str] = {}
        if alert.query_text:
            filter_params["q"] = alert.query_text
        if alert.location:
            filter_params["location"] = alert.location
        if alert.work_mode:
            filter_params["work_mode"] = alert.work_mode
        if alert.min_salary_usd:
            filter_params["min_salary"] = str(alert.min_salary_usd)

        filtered_qs = JobPostingFilter(filter_params, queryset=qs).qs
        ranked = rank_jobs_for_resume(
            filtered_qs, alert.resume, query_text=alert.query_text
        )[:10]
        if not ranked:
            continue

        lines = [
            f"Hi {alert.user.full_name or alert.user.email},",
            "",
            f"Top fresh job matches for your alert '{alert.name}':",
            "",
        ]
        for item in ranked:
            match_str = f" ({item.match_percent}% match)" if alert.resume else ""
            lines.append(
                f"- {item.job.title} at {item.job.company_name}{match_str}\n"
                f"  Apply: {item.job.apply_url}"
            )

        send_mail(
            subject=f"SigmaFresher Alert: {len(ranked)} new jobs for '{alert.name}'",
            message="\n".join(lines),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[alert.user.email],
            fail_silently=False,
        )
        alert.last_sent_at = timezone.now()
        alert.save(update_fields=["last_sent_at", "updated_at"])
        sent_count += 1

    return {"alerts_sent": sent_count}
