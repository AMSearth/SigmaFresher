"""Phase 2 tests for resume upload security, AI parsing/caching, and IDOR/GDPR checks."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts.models import JobSeekerProfile, User
from apps.core.ai import ResumeExtractionSchema, scrub_pii
from apps.resumes.models import Resume


def _make_pdf_bytes(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _make_docx_bytes(paragraph_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types></Types>")
        zf.writestr(
            "word/document.xml",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:body><w:p><w:r><w:t>{paragraph_text}</w:t></w:r></w:p></w:body>"
                "</w:document>"
            ),
        )
    return buf.getvalue()


@pytest.fixture
def temp_media_root(settings, tmp_path: Path) -> Path:
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def test_scrub_pii_redacts_email_phone_and_address() -> None:
    raw = "Jane Doe | jane.doe@example.com | +1 (415) 555-0199 | 123 Market Street, SF"
    scrubbed = scrub_pii(raw)
    assert "jane.doe@example.com" not in scrubbed
    assert "555-0199" not in scrubbed
    assert "123 Market Street" not in scrubbed


@pytest.mark.django_db
def test_spoofed_pdf_and_fake_zip_rejected(client, temp_media_root: Path) -> None:
    user = User.objects.create_user(email="u1@example.com", password="VeryStrongPassword!2026")
    client.force_login(user)

    fake_pdf = SimpleUploadedFile(
        "evil.pdf", b"#!/bin/sh\nrm -rf /\n", content_type="application/pdf"
    )
    resp = client.post(reverse("resumes:list"), {"file": fake_pdf})
    assert resp.status_code == 200
    assert Resume.objects.count() == 0

    bad_zip_buf = io.BytesIO()
    with zipfile.ZipFile(bad_zip_buf, "w") as zf:
        zf.writestr("not_a_docx.txt", "hello")
    fake_docx = SimpleUploadedFile(
        "fake.docx",
        bad_zip_buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    resp2 = client.post(reverse("resumes:list"), {"file": fake_docx})
    assert resp2.status_code == 200
    assert Resume.objects.count() == 0


@pytest.mark.django_db
def test_valid_pdf_upload_parses_with_mocked_ai_and_caches_by_text_hash(
    client, temp_media_root: Path, django_capture_on_commit_callbacks
) -> None:
    user = User.objects.create_user(email="dev@example.com", password="VeryStrongPassword!2026")
    client.force_login(user)

    resume_text = (
        "Senior Django Backend Engineer with 6 years building PostgreSQL, Celery, Redis, "
        "and RESTful APIs in Python across distributed cloud platforms."
    )
    pdf_bytes = _make_pdf_bytes(resume_text)
    fake_parsed = ResumeExtractionSchema(
        target_roles=["Senior Backend Engineer", "Django Developer"],
        skills=["Python", "Django", "PostgreSQL", "Celery", "Redis"],
        years_experience=6.0,
        seniority_level="senior",
        preferred_locations=["Remote"],
        summary="Experienced Python/Django engineer.",
    )
    fake_vec = [0.01] * 768

    with (
        patch(
            "apps.core.ai.CloudAIClient.extract_resume_profile", return_value=fake_parsed
        ) as m_ext,
        patch("apps.core.ai.CloudAIClient.generate_embedding", return_value=fake_vec) as m_emb,
    ):
        with django_capture_on_commit_callbacks(execute=True):
            upload1 = SimpleUploadedFile("resume_v1.pdf", pdf_bytes, content_type="application/pdf")
            resp = client.post(reverse("resumes:list"), {"label": "Backend", "file": upload1})
            assert resp.status_code == 302
        assert m_ext.call_count == 1
        assert m_emb.call_count == 1

        resume1 = Resume.objects.get(user=user)
        assert resume1.parse_status == Resume.ParseStatus.READY
        assert resume1.is_active is True
        assert "Django" in resume1.skills

        profile = JobSeekerProfile.objects.get(user=user)
        assert "Django Developer" in profile.target_roles

        # Second identical resume text should hit text_hash cache and skip Cloud LLM calls
        with django_capture_on_commit_callbacks(execute=True):
            upload2 = SimpleUploadedFile("resume_v2.pdf", pdf_bytes, content_type="application/pdf")
            resp2 = client.post(reverse("resumes:list"), {"label": "Copy", "file": upload2})
            assert resp2.status_code == 302
        assert m_ext.call_count == 1
        assert m_emb.call_count == 1


@pytest.mark.django_db
def test_valid_docx_and_scanned_pdf_handling(
    client, temp_media_root: Path, django_capture_on_commit_callbacks
) -> None:
    user = User.objects.create_user(email="docx@example.com", password="VeryStrongPassword!2026")
    client.force_login(user)

    docx_bytes = _make_docx_bytes(
        "Full Stack Engineer with 5 years experience in Python, Django, HTMX, PostgreSQL, "
        "Celery, Redis, Docker, Kubernetes, and AWS cloud infrastructure."
    )
    fake_parsed = ResumeExtractionSchema(
        target_roles=["Full Stack Engineer"],
        skills=["Python", "Django", "HTMX"],
        seniority_level="mid",
    )
    with (
        patch("apps.core.ai.CloudAIClient.extract_resume_profile", return_value=fake_parsed),
        patch("apps.core.ai.CloudAIClient.generate_embedding", return_value=[0.02] * 768),
    ):
        with django_capture_on_commit_callbacks(execute=True):
            resp = client.post(
                reverse("resumes:list"),
                {"file": SimpleUploadedFile("cv.docx", docx_bytes)},
            )
            assert resp.status_code == 302
        docx_resume = Resume.objects.get(user=user)
        assert docx_resume.parse_status == Resume.ParseStatus.READY
        assert docx_resume.file_extension == "docx"

    scanned_pdf = _make_pdf_bytes("Hi")
    with django_capture_on_commit_callbacks(execute=True):
        uploaded_scan = SimpleUploadedFile(
            "scanned.pdf", scanned_pdf, content_type="application/pdf"
        )
        resp2 = client.post(reverse("resumes:list"), {"file": uploaded_scan})
        assert resp2.status_code == 302
    failed = Resume.objects.filter(user=user, parse_status=Resume.ParseStatus.FAILED).first()
    assert failed is not None
    assert "scanned" in failed.error_message.lower()


@pytest.mark.django_db
def test_idor_protection_download_headers_quota_and_gdpr_delete(
    client, temp_media_root: Path
) -> None:
    owner = User.objects.create_user(email="owner@example.com", password="VeryStrongPassword!2026")
    other = User.objects.create_user(email="other@example.com", password="VeryStrongPassword!2026")

    pdf_bytes = _make_pdf_bytes(
        "Python Backend Engineer with 4 years of Django, PostgreSQL, Redis, and Docker experience."
    )
    fake_parsed = ResumeExtractionSchema(target_roles=["Backend Engineer"], skills=["Python"])
    with (
        patch("apps.core.ai.CloudAIClient.extract_resume_profile", return_value=fake_parsed),
        patch("apps.core.ai.CloudAIClient.generate_embedding", return_value=[0.01] * 768),
    ):
        client.force_login(owner)
        client.post(
            reverse("resumes:list"),
            {"file": SimpleUploadedFile("my_cv.pdf", pdf_bytes, content_type="application/pdf")},
        )
        resume = Resume.objects.get(user=owner)
        stored_path = Path(resume.file.path)
        assert stored_path.exists()

        # Owner download includes attachment & nosniff headers
        dl = client.get(reverse("resumes:download", args=[resume.pk]))
        assert dl.status_code == 200
        assert dl.headers["X-Content-Type-Options"] == "nosniff"
        assert "attachment" in dl.headers["Content-Disposition"]

        # Other user cannot access or mutate owner's resume
        client.force_login(other)
        assert client.get(reverse("resumes:download", args=[resume.pk])).status_code == 404
        assert client.get(reverse("resumes:status", args=[resume.pk])).status_code == 404
        assert client.post(reverse("resumes:activate", args=[resume.pk])).status_code == 404
        assert client.post(reverse("resumes:delete", args=[resume.pk])).status_code == 404

        # Owner updates profile preferences & deletes account (hard-removes file)
        client.force_login(owner)
        prof_resp = client.post(
            reverse("accounts:profile"),
            {
                "target_roles_csv": "Staff Engineer, Django Architect",
                "preferred_locations_csv": "Remote, London",
                "preferred_work_modes": ["remote"],
                "min_salary_usd": "140000",
                "open_to_remote": "on",
            },
        )
        assert prof_resp.status_code == 302
        owner.profile.refresh_from_db()
        assert owner.profile.min_salary_usd == 140000
        assert "Remote" in owner.profile.preferred_locations

        del_resp = client.post(reverse("accounts:delete_account"))
        assert del_resp.status_code == 302
        assert not User.objects.filter(pk=owner.pk).exists()
        assert not stored_path.exists()
