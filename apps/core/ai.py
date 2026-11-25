"""Cloud LLM and embedding client with PII scrubbing, caching, and circuit-breaker guards."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

import structlog
from django.conf import settings
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

MAX_LLM_INPUT_CHARS = 15_000
MAX_EMBEDDING_BATCH_SIZE = 100

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)?\d{3}[\s.-]?\d{4}")
STREET_RE = re.compile(
    r"\b\d{1,5}\s+[A-Za-z0-9.\s]{2,40}\s(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln)\b",
    re.IGNORECASE,
)


class AIServiceError(Exception):
    """Raised when the configured Cloud LLM or embedding API fails."""


class WorkExperienceItem(BaseModel):
    company: str = ""
    role: str = ""
    duration: str = ""
    highlights: list[str] = Field(default_factory=list)


class ResumeExtractionSchema(BaseModel):
    """Validated structured profile extracted from a resume."""

    target_roles: list[str] = Field(default_factory=list, max_length=10)
    skills: list[str] = Field(default_factory=list, max_length=60)
    years_experience: float | None = Field(default=None, ge=0, le=60)
    seniority_level: Literal["entry", "mid", "senior", "lead", "executive", "unknown"] = "unknown"
    preferred_locations: list[str] = Field(default_factory=list, max_length=10)
    work_history: list[WorkExperienceItem] = Field(default_factory=list, max_length=15)
    education: list[str] = Field(default_factory=list, max_length=10)
    summary: str = ""


def scrub_pii(text: str) -> str:
    """Redact common contact PII before transmitting resume text to external LLM APIs."""
    cleaned = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    cleaned = PHONE_RE.sub("[REDACTED_PHONE]", cleaned)
    cleaned = STREET_RE.sub("[REDACTED_ADDRESS]", cleaned)
    return cleaned


def compute_text_hash(text: str) -> str:
    """Return deterministic SHA-256 hash of normalized text for AI cost caching."""
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_resume_embedding_text(profile: ResumeExtractionSchema, raw_text: str) -> str:
    """Construct compact high-signal text for vector embedding."""
    parts = [
        f"Target Roles: {', '.join(profile.target_roles)}" if profile.target_roles else "",
        f"Seniority: {profile.seniority_level}",
        f"Skills: {', '.join(profile.skills)}" if profile.skills else "",
        f"Summary: {profile.summary}" if profile.summary else "",
        scrub_pii(raw_text)[:4000],
    ]
    return "\n".join(p for p in parts if p)


class CloudAIClient:
    """Provider-agnostic Cloud LLM and embedding client supporting OpenAI and Gemini."""

    def __init__(self) -> None:
        self.provider: str = getattr(settings, "LLM_PROVIDER", "openai").lower()
        self.dimensions: int = getattr(settings, "EMBEDDING_DIMENSIONS", 768)

    def extract_resume_profile(self, raw_text: str) -> ResumeExtractionSchema:
        """Extract structured career data from scrubbed resume text."""
        truncated = scrub_pii(raw_text)[:MAX_LLM_INPUT_CHARS]
        system_prompt = (
            "You are an expert technical career parser. Extract accurate target job titles, "
            "normalized skills, total years of experience, seniority level, preferred locations, "
            "concise work history, education, and a short summary. Return strictly valid JSON."
        )
        if self.provider == "openai":
            return self._extract_openai(system_prompt, truncated)
        if self.provider == "gemini":
            return self._extract_gemini(system_prompt, truncated)
        raise AIServiceError(f"Unsupported LLM_PROVIDER: {self.provider}")

    def generate_embedding(self, text: str) -> list[float]:
        """Generate a single embedding vector."""
        vectors = self.generate_embeddings_batch([text])
        return vectors[0]

    def generate_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors in bounded batches."""
        if not texts:
            return []
        results: list[list[float]] = []
        for idx in range(0, len(texts), MAX_EMBEDDING_BATCH_SIZE):
            chunk = texts[idx : idx + MAX_EMBEDDING_BATCH_SIZE]
            batch = [t[:8000] if t.strip() else "empty" for t in chunk]
            if self.provider == "openai":
                results.extend(self._embed_openai(batch))
            elif self.provider == "gemini":
                results.extend(self._embed_gemini(batch))
            else:
                raise AIServiceError(f"Unsupported LLM_PROVIDER: {self.provider}")
        return results

    def _extract_openai(self, system_prompt: str, text: str) -> ResumeExtractionSchema:
        api_key = getattr(settings, "OPENAI_API_KEY", "")
        if not api_key:
            raise AIServiceError("OPENAI_API_KEY is not configured.")
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=25.0, max_retries=2)
            completion = client.beta.chat.completions.parse(
                model=getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                response_format=ResumeExtractionSchema,
                temperature=0.0,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise AIServiceError("OpenAI returned empty structured output.")
            return parsed
        except AIServiceError:
            raise
        except Exception as exc:
            logger.exception("openai_resume_extraction_failed")
            raise AIServiceError(f"OpenAI extraction failed: {exc}") from exc

    def _extract_gemini(self, system_prompt: str, text: str) -> ResumeExtractionSchema:
        api_key = getattr(settings, "GEMINI_API_KEY", "")
        if not api_key:
            raise AIServiceError("GEMINI_API_KEY is not configured.")
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash"),
                contents=f"{system_prompt}\n\nRESUME TEXT:\n{text}",
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ResumeExtractionSchema,
                    temperature=0.0,
                ),
            )
            if not response.text:
                raise AIServiceError("Gemini returned empty response.")
            return ResumeExtractionSchema.model_validate_json(response.text)
        except AIServiceError:
            raise
        except Exception as exc:
            logger.exception("gemini_resume_extraction_failed")
            raise AIServiceError(f"Gemini extraction failed: {exc}") from exc

    def _embed_openai(self, batch: list[str]) -> list[list[float]]:
        api_key = getattr(settings, "OPENAI_API_KEY", "")
        if not api_key:
            raise AIServiceError("OPENAI_API_KEY is not configured.")
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=25.0, max_retries=2)
            resp = client.embeddings.create(
                model=getattr(settings, "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
                input=batch,
                dimensions=self.dimensions,
            )
            return [list(item.embedding) for item in resp.data]
        except Exception as exc:
            logger.exception("openai_embedding_failed")
            raise AIServiceError(f"OpenAI embedding failed: {exc}") from exc

    def _embed_gemini(self, batch: list[str]) -> list[list[float]]:
        api_key = getattr(settings, "GEMINI_API_KEY", "")
        if not api_key:
            raise AIServiceError("GEMINI_API_KEY is not configured.")
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            payload: list[Any] = list(batch)
            resp = client.models.embed_content(
                model=getattr(settings, "GEMINI_EMBEDDING_MODEL", "text-embedding-004"),
                contents=payload,
                config=types.EmbedContentConfig(output_dimensionality=self.dimensions),
            )
            embeddings = resp.embeddings or []
            return [list(item.values or []) for item in embeddings]
        except Exception as exc:
            logger.exception("gemini_embedding_failed")
            raise AIServiceError(f"Gemini embedding failed: {exc}") from exc
