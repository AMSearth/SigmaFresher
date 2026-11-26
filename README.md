# SigmaFresher — Resume-Driven Web Job Search Platform

A security-first Django 6.x platform where job seekers must authenticate before uploading a resume, extracting structured skills/roles via Cloud LLM + embeddings, discovering fresh jobs across company career sites and job platforms, filtering results, and applying directly.

## Key Capabilities & Security Controls

- **Mandatory Authentication (`LoginRequiredMiddleware` + `django-allauth`):** Every application route requires login by default, backed by Argon2id password hashing, mandatory email verification, rate limiting, and optional Google/GitHub OAuth.
- **Native Content Security Policy (`ContentSecurityPolicyMiddleware`):** Nonce-based strict CSP (`CSP.SELF` + `CSP.NONCE`) with vendored HTMX and zero `'unsafe-inline'`.
- **Secure Resume Processing:** Magic-byte validation (`filetype`), 5 MB size cap, DOCX zip-bomb & `defusedxml` XXE defenses, private UUID storage with authenticated attachment downloads, PII regex scrubbing before Cloud LLM extraction, and scanned-PDF detection.
- **Option 2 Free-First All-Web Job Discovery:**
  - Direct Company ATS feeds: Greenhouse, Lever, Ashby, SmartRecruiters + dynamic `CompanyBoard` discovery.
  - Job Platforms: Adzuna free API, Remotive, Arbeitnow, and optional `python-jobspy`.
  - Open Web: `ddgs` search + `schema.org/JobPosting` JSON-LD extraction via SSRF-safe `safe_fetch()` (`httpx` blocking private/loopback/metadata IPs and redirect-to-internal bypasses).
  - Stored XSS protection via `nh3` HTML sanitization + strict `http/https` `apply_url` validation.
- **Hybrid Matching & Filtering (`pgvector` + FTS + `django-filter` + HTMX):** Combines semantic vector similarity, PostgreSQL Full-Text Search (RRF), skill overlap, and recency decay with live URL-synced filters, saved job tracking, and scheduled email alerts.

## Quickstart (Local / Docker)

```bash
cp .env.example .env
# Add your DJANGO_SECRET_KEY, OPENAI_API_KEY or GEMINI_API_KEY (and optional ADZUNA_APP_ID/KEY)

docker compose up --build
docker compose exec web python manage.py migrate
```

## Verification

```bash
uv run ruff check .
uv run mypy apps config
uv run bandit -c pyproject.toml -r apps config
uv run pytest
```
