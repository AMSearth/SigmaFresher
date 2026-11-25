"""Phase 1 tests covering mandatory authentication, CSP headers, and user profile setup."""

import pytest
from django.urls import reverse

from apps.accounts.models import JobSeekerProfile, User


@pytest.mark.django_db
def test_unauthenticated_dashboard_redirects_to_login(client) -> None:
    response = client.get(reverse("core:dashboard"))
    assert response.status_code == 302
    assert reverse("account_login") in response.headers["Location"]


@pytest.mark.django_db
def test_unauthenticated_admin_redirects_to_login(client) -> None:
    response = client.get("/admin/")
    assert response.status_code == 302


@pytest.mark.django_db
def test_allauth_login_and_signup_accessible_without_redirect_loop(client) -> None:
    login_resp = client.get(reverse("account_login"))
    signup_resp = client.get(reverse("account_signup"))
    assert login_resp.status_code == 200
    assert signup_resp.status_code == 200


@pytest.mark.django_db
def test_healthz_is_public(client) -> None:
    response = client.get(reverse("core:healthz"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_csp_report_endpoint_accepts_valid_and_rejects_oversized(client) -> None:
    ok_response = client.post(
        reverse("core:csp_report"),
        data='{"csp-report":{"blocked-uri":"inline"}}',
        content_type="application/json",
    )
    assert ok_response.status_code == 204

    too_big = client.post(
        reverse("core:csp_report"),
        data="x" * (17 * 1024),
        content_type="application/json",
    )
    assert too_big.status_code == 413


@pytest.mark.django_db
def test_authenticated_dashboard_renders_and_sets_strict_csp(client) -> None:
    user = User.objects.create_user(
        email="Seeker@Example.com",
        password="VeryStrongPassword!2026",
        full_name="Test Seeker",
    )
    assert user.email == "seeker@example.com"
    assert user.password.startswith("argon2$")
    assert JobSeekerProfile.objects.filter(user=user).exists()

    client.force_login(user)
    response = client.get(reverse("core:dashboard"))
    assert response.status_code == 200
    assert b"seeker@example.com" in response.content

    csp_header = response.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp_header
    assert "'unsafe-inline'" not in csp_header
    assert "'unsafe-eval'" not in csp_header
    assert response.headers.get("X-Frame-Options") == "DENY"
