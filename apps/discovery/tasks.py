"""Celery tasks for asynchronous web/platform job discovery."""

from celery import shared_task

from .services import execute_search_run


@shared_task(bind=True, max_retries=1, default_retry_delay=20)
def execute_search_run_task(self: object, search_run_id: str) -> None:
    """Run all Option 2 connectors asynchronously on the isolated scraper queue."""
    execute_search_run(search_run_id)
