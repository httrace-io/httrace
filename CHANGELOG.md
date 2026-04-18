# Changelog

All notable changes to Httrace are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.1.1] — 2026-04-18

### Fixed

**Backend**
- `NameError: name 'v' is not defined` in `pipeline/deduplication.py` — dict comprehension used `k` instead of `k, v` in `sorted(value.items())` (#bug)
- `TypeError` in `billing/stripe_webhooks.py` — `Session(next(get_session()))` double-wrapped a Session object; replaced with `Session(engine)` directly
- `datetime.utcnow()` deprecation in Python 3.12 — replaced with `datetime.now(timezone.utc)` in `billing/models.py` and `billing/usage.py`
- Generate and coverage routes were completely unauthenticated — added `X-Api-Key` validation via `_validate_key()` helper
- `/v1/billing/provision-key` had no authentication — now requires `Authorization: Bearer <PROVISION_SECRET>` header
- Comment in `routes/generate.py` said "up to 3 representative captures" but code limited to 5

**SDK**
- WSGI middleware always captured an empty body — `environ["wsgi.input"]` was read after the app consumed it; now buffered before the app call
- `httpx.Client` was instantiated per flush (TLS handshake overhead) — now reused across all flushes
- Unnecessary `import io` inside `_record` method removed

**CLI**
- All file I/O now uses `encoding="utf-8"` explicitly (Windows compatibility)

**Landing Page**
- Warning triangle icon dot was invisible — replaced `<line x2="12.01">` with `<circle>` SVG element
- Mobile layout showed only "With Httrace" pane — `.pane-before` changed from `display: none` to `display: block; opacity: 0.45`

### Security

- Added chunked transfer encoding protection in `main.py` — body size limit now enforced even without `Content-Length` header
- CLI sanitizes server-returned filenames — strips directory components, rejects non-`*.py` names (path traversal guard)
- SQLite WAL mode enabled in `database.py` — eliminates write-lock contention under concurrent requests

### Changed

- Free tier monthly quota reduced from 50 000 to 10 000 requests
- "GitHub integration" renamed to "GitHub PR comments with test diffs" on pricing page (clearer scope)
- `_PII_FIELD_NAMES` expanded: added `name`, `first_name`, `last_name`, `full_name`, `display_name`, `street`, `zip`, `postal_code`, `city`, `birth_date`, `birthday`, `email`, `username`, `user_name`

### Added

- `HttraceClient.shutdown(timeout)` method — flushes remaining queue and closes HTTP client cleanly; registered via `atexit` automatically
- Sentinel-based worker stop — daemon thread exits cleanly on `shutdown()` call instead of running forever
- Path parameter normalization in generate route — `/users/42` and `/users/1` are now grouped as `/users/{id}`
- Slug collision prevention — duplicate filenames get a `_2`, `_3` suffix instead of silently overwriting
- Array response assertions in test generator — generates `assert isinstance(data, list)` and field presence checks on first element
- `docs/api-reference.md` — full REST API reference
- `docs/sdk-configuration.md` — all middleware parameters, PII rules, env var guidance
- `docs/troubleshooting.md` — common issues and fixes
- `PROVISION_SECRET` env var documented in `.env.example`

---

## [0.1.0] — 2026-04-10

### Added

- Initial MVP release
- Python SDK with ASGI (FastAPI/Starlette) and WSGI (Flask/Django) middleware
- PII sanitization: JWT, credit card, email, IPv4, phone, German IBAN, UUID
- FastAPI backend: ingest, generate-tests, coverage, billing endpoints
- SQLite storage via SQLModel
- Stripe webhook integration for subscription lifecycle management
- Quota tracking per API key, per month, per plan
- Rate limiting via slowapi (2 000 req/min on ingest)
- CORS, body size limit (10 MB), generic error handler (no stack trace leakage)
- pytest test generator with fixture inference and chain analysis
- typer-based CLI (`httrace init`, `httrace generate`, `httrace status`)
- Marketing landing page (dark theme, Vercel-inspired)
- Legal: Terms of Service (German B2B, §14 BGB), DSGVO Art. 28 AV-Vertrag
- Open-source SDK under MIT License
- GitHub Actions CI: ruff, mypy, pip-audit on Python 3.11 and 3.12
