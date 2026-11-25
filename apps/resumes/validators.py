"""Strict security validators for uploaded PDF and DOCX resumes."""

from __future__ import annotations

import zipfile
from typing import IO

import filetype
from django.conf import settings
from django.core.exceptions import ValidationError


def validate_resume_upload(uploaded_file: IO[bytes]) -> str:
    """
    Validate uploaded resume size, magic bytes, and DOCX zip-bomb limits.

    Returns normalized extension ('pdf' or 'docx').
    """
    max_bytes: int = getattr(settings, "MAX_RESUME_UPLOAD_BYTES", 5 * 1024 * 1024)
    size = getattr(uploaded_file, "size", None)
    if size is not None:
        if size == 0:
            raise ValidationError("Uploaded file is empty.")
        if size > max_bytes:
            raise ValidationError("Resume exceeds maximum size of 5 MB.")

    header = uploaded_file.read(4096)
    uploaded_file.seek(0)
    if not header:
        raise ValidationError("Uploaded file is empty.")

    kind = filetype.guess(header)
    if kind is None:
        raise ValidationError("Unrecognized file format. Please upload a valid PDF or DOCX file.")

    if kind.mime == "application/pdf":
        return "pdf"

    if kind.mime in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    }:
        _validate_docx_zip(uploaded_file)
        return "docx"

    raise ValidationError("Unsupported file type. Only PDF and DOCX resumes are allowed.")


def _validate_docx_zip(uploaded_file: IO[bytes]) -> None:
    """Verify OOXML structure and guard against zip-bomb decompression attacks."""
    max_uncompressed: int = getattr(settings, "MAX_DOCX_UNCOMPRESSED_BYTES", 25 * 1024 * 1024)
    try:
        with zipfile.ZipFile(uploaded_file, "r") as zf:
            names = set(zf.namelist())
            if "word/document.xml" not in names:
                raise ValidationError("Invalid DOCX archive: missing word/document.xml.")
            total_uncompressed = sum(info.file_size for info in zf.infolist())
            if total_uncompressed > max_uncompressed:
                raise ValidationError("DOCX archive exceeds safe uncompressed size limit.")
    except zipfile.BadZipFile as exc:
        raise ValidationError("Corrupted DOCX file.") from exc
    finally:
        uploaded_file.seek(0)
