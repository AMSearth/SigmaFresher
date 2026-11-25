"""Text extraction for PDF and DOCX resumes with scanned-PDF detection."""

from __future__ import annotations

import zipfile
from pathlib import Path

import defusedxml.ElementTree as DefusedET
import pymupdf

MAX_PDF_PAGES = 25
MIN_EXTRACTED_CHARS = 100


class ScannedResumeError(Exception):
    """Raised when an uploaded PDF contains insufficient selectable text."""


def extract_resume_text(file_path: str | Path, extension: str) -> str:
    """Extract clean plain text from a PDF or DOCX file."""
    ext = extension.lower().lstrip(".")
    if ext == "pdf":
        text = _extract_pdf(Path(file_path))
    elif ext == "docx":
        text = _extract_docx(Path(file_path))
    else:
        raise ValueError(f"Unsupported extension: {extension}")

    cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(cleaned) < MIN_EXTRACTED_CHARS:
        raise ScannedResumeError(
            "This file appears to be scanned or image-only (too little selectable text). "
            "Please upload a text-based PDF or DOCX resume."
        )
    return cleaned


def _extract_pdf(path: Path) -> str:
    parts: list[str] = []
    with pymupdf.open(path) as doc:
        for page in doc[:MAX_PDF_PAGES]:
            parts.append(page.get_text("text"))
    return "\n".join(parts)


def _extract_docx(path: Path) -> str:
    """Extract DOCX paragraphs safely via defusedxml to block XXE attacks."""
    paragraphs: list[str] = []
    with zipfile.ZipFile(path, "r") as zf:
        xml_bytes = zf.read("word/document.xml")
    root = DefusedET.fromstring(xml_bytes)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    for para in root.iter(f"{ns}p"):
        texts = [node.text for node in para.iter(f"{ns}t") if node.text]
        if texts:
            paragraphs.append("".join(texts))
    return "\n".join(paragraphs)
