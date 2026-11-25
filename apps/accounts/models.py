"""Custom User and JobSeekerProfile models."""

from typing import Any, ClassVar

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models

from apps.core.models import TimeStampedModel, UUIDModel


class UserManager(BaseUserManager["User"]):
    """Email-first user manager with no username field."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra_fields: Any) -> "User":
        if not email:
            raise ValueError("Users must have an email address.")
        normalized_email = self.normalize_email(email).lower()
        user = self.model(email=normalized_email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(
        self, email: str, password: str | None = None, **extra_fields: Any
    ) -> "User":
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(
        self, email: str, password: str | None = None, **extra_fields: Any
    ) -> "User":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(UUIDModel, TimeStampedModel, AbstractBaseUser, PermissionsMixin):
    """Custom user model authenticating via email."""

    email = models.EmailField(unique=True, max_length=254)
    full_name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.email


class JobSeekerProfile(UUIDModel, TimeStampedModel):
    """Stores editable job search preferences and extracted career targets."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    target_roles = models.JSONField(default=list, blank=True)
    preferred_locations = models.JSONField(default=list, blank=True)
    preferred_work_modes = models.JSONField(default=list, blank=True)
    min_salary_usd = models.PositiveIntegerField(null=True, blank=True)
    open_to_remote = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"Profile({self.user.email})"
