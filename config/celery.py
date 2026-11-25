"""Celery application configuration with isolated queues."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("sigma_fresher")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.conf.task_default_queue = "default"
app.conf.task_routes = {
    "apps.discovery.tasks.*": {"queue": "scraper"},
    "apps.resumes.tasks.*": {"queue": "default"},
    "apps.alerts.tasks.*": {"queue": "default"},
}
app.autodiscover_tasks()
