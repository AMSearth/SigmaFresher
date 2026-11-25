"""Signals for accounts app."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import JobSeekerProfile, User


@receiver(post_save, sender=User)
def create_job_seeker_profile(
    sender: type[User], instance: User, created: bool, **kwargs: object
) -> None:
    """Automatically create a JobSeekerProfile when a new User registers."""
    if created:
        JobSeekerProfile.objects.get_or_create(user=instance)
