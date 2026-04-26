# Httrace — Project Status

> Last updated: 2026-04-26 (session 5 — Dependency Mocking)
> Purpose: Comprehensive project state for developers and future Claude sessions.

---

## Quick Reference

```bash
# SSH into server
ssh root@46.224.203.69

# Rebuild and redeploy backend (run on server)
cd /opt/httrace
docker build -t httrace-api .
docker rm -f httrace-api
docker run -d \
  --name httrace-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v /data:/data \
  --env-file /opt/httrace/.env \
  httrace-api

# Check backend logs (on server)
docker logs httrace-api --tail 100 -f

# Check backend health
curl https://api.httrace.com/health

# Check waitlist count
curl https://api.httrace.com/v1/waitlist/count

# Deploy landing page: push to GitHub (Cloudflare Workers auto-deploys)
git push origin main
```

---

## Product Overview

**Httrace** is a SaaS developer tool that:
1. Captures real HTTP traffic from production/staging apps via SDK middleware
2. Automatically generates integration tests (pytest, Jest, Vitest, Go, RSpec) from captured traffic
3. **[NEW]** Captures all outgoing dependency calls (HTTP, SQL) made during each request and auto-generates mock fixtures so tests run without any live infrastructure

**Status:** Waitlist / pre-launch. No payment processing active yet.

**Tagline:** "Your users write your tests."

---

## Infrastructure

| Component | Technology | Location |
|-----------|-----------|----------|
| Website | Cloudflare Workers (static) | httrace.com |
| API | FastAPI + Docker | api.httrace.com → 46.224.203.69:8080 |
| Database | SQLite (bind mount /data) | /data/httrace.db on server |
| DNS / CDN | Cloudflare | — |
| Email | Resend API | — |
| Bot protection | Cloudflare Turnstile | — |
| Analytics | Plausible | data-domain="httrace.com" |

### Server (Hetzner)
- **IP:** 46.224.203.69
- **OS:** Ubuntu 24.04, Frankfurt datacenter
- **SSH:** `ssh root@46.224.203.69`
- **Stack:** Docker, Nginx, Certbot (SSL)
- **Backend path on server:** `/opt/httrace/`
- **Docker container name:** `httrace-api`
- **Data mount:** `/data` on host → `/data` in container
- **Real DB path:** `/data/httrace.db`
- **Env file:** `/opt/httrace/.env`

### GitHub
- **Repo:** https://github.com/httrace-io/httrace
- **Branch:** main
- **CI/CD:** Cloudflare Workers auto-deploys `landing/` on push to main

---

## Repository Structure

```
httrace/
├── landing/                    # Full website — pushed to GitHub, auto-deployed to httrace.com
│   ├── index.html              # Main landing page (hero, FAQ, pricing, comparison table)
│   ├── dashboard.html          # User dashboard (Coverage, Changes, Alerts tabs)
│   ├── docs/
│   │   └── index.html          # Documentation (httrace.com/docs/)
│   ├── login.html              # Login page
│   ├── forgot-password.html
│   ├── set-password.html
│   ├── profile.html
│   ├── impressum.html          # German Impressum (legal)
│   ├── datenschutz.html        # German Datenschutzerklärung
│   ├── privacy.html            # English Privacy Policy
│   ├── agb.html                # German AGB
│   ├── terms.html              # English Terms of Service
│   ├── contact.html
│   ├── sitemap.xml             # Submitted to Google Search Console ✅
│   ├── robots.txt
│   ├── llms.txt                # Full product description for LLM crawlers
│   ├── favicon.svg
│   ├── og.png                  # 1200×630px Open Graph image ✅
│   ├── profile.png             # 800×800px social profile picture
│   ├── youtube-banner.png      # 2560×1440px YouTube channel art
│   └── httrace-promo.mp4       # Promo video embedded on website (autoplay on scroll)
│
├── sdk/                        # Python SDK — pushed to GitHub
│   └── httrace/
│       ├── __init__.py
│       ├── capture.py          # CapturedInteraction, OutgoingCall dataclasses
│       ├── interceptors.py     # [NEW] httpx/requests/SQLAlchemy monkey-patching
│       ├── middleware.py       # ASGI + WSGI middleware (capture_outgoing param added)
│       ├── client.py           # Background upload client
│       ├── sanitizer.py        # PII sanitization
│       └── cli.py
│
├── sdk-node/                   # Node.js SDK — pushed to GitHub
│   └── src/
│       └── index.js            # Express middleware (captureOutgoing + AsyncLocalStorage)
│
├── sdk-go/                     # Go SDK — pushed to GitHub
│   ├── httrace.go              # Middleware + RecordingTransport + ClientFromContext
│   ├── httrace_test.go
│   └── go.mod
│
├── sdk-ruby/                   # Ruby SDK — pushed to GitHub
│   └── lib/
│       └── httrace.rb          # Rack middleware + NetHTTPInterceptor module
│
├── cli/                        # CLI tool — pushed to GitHub
│   ├── main.py
│   └── pyproject.toml
│
├── vscode-extension/           # VS Code extension (scaffold)
│
├── video/                      # Remotion video project (NOT in git)
│   └── out/
│       ├── Httrace.mp4                 # 1920×1080, 56s — for YouTube
│       ├── HttraceVerticalNative.mp4   # 1080×1920, 56s — TikTok native 9:16
│       └── HttraceVertical.mp4
│
├── demo/                       # Demo/test scripts (NOT in git)
│   ├── seed_captures.py
│   ├── shop_app.py
│   └── test-shop-app.py
│
├── PROJECT_STATUS.md           # ← This file
├── NACH_GEWERBE_ANMELDUNG.md   # Legal/business setup checklist (post Gewerbeanmeldung)
├── IMPLEMENTATION_PLAN.md      # Current feature implementation plan (copy of plan)
├── Dockerfile
├── fly.toml                    # LEGACY — obsolete, backend is on Hetzner not Fly.io
└── .dockerignore
```

### Git Rules (CRITICAL)
- **NEVER** `git add backend/` or push anything from the backend directory
- Only these may be pushed: `landing/`, `sdk/`, `sdk-node/`, `sdk-go/`, `sdk-ruby/`, `cli/`, `vscode-extension/`, `Dockerfile`, `.dockerignore`
- Backend lives only on the server at `/opt/httrace/backend/` — it is NOT version controlled

---

## Backend Details (Server Only — `/opt/httrace/backend/`)

### Framework & Configuration
- **Framework:** FastAPI with SQLModel ORM
- **Server:** Uvicorn inside Docker
- **Port:** 8080 (Nginx reverse-proxies api.httrace.com → localhost:8080)
- **Docs endpoint:** Hidden in production (`docs_url=None`)
- **Rate limiting:** slowapi

### Database Models (`models.py`)
| Model | Key Fields |
|-------|-----------|
| `CaptureRecord` | HTTP traffic captures — **now includes `outgoing_calls` JSON column** |
| `WaitlistEntry` | email, signed_up_at, onboarding email tracking |
| `GeneratedTest` | Generated test files per service/format |
| `ApiKey` | API key, plan, owner email |
| `UsageRecord` | Per-key per-month usage tracking |
| `AlertConfig` | Slack/email alert configurations |

### API Routes (31 live endpoints)

**Traffic Capture & Test Generation**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/captures` | Ingest captured HTTP traffic from SDK (rate-limited 2000/min) |
| POST | `/v1/generate-tests` | Generate tests (`?service=X&format=pytest\|jest\|go\|rspec\|vitest`) |
| GET | `/v1/generate-tests/conftest` | Download conftest.py |
| GET | `/v1/openapi.yaml` | Auto-generated OpenAPI spec from observed traffic |
| GET | `/v1/openapi.json` | Same, JSON format |

**Auth**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/waitlist` | Join waitlist (Turnstile-protected) |
| GET | `/v1/waitlist/count` | Subscriber count |
| POST | `/v1/auth/login` | Login → JWT |
| POST | `/v1/auth/register` | Register with API key |
| POST | `/v1/auth/forgot-password` | Password reset flow |
| POST | `/v1/auth/set-password` | Set new password |

**Dashboard & Analytics**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/coverage` | Endpoint coverage stats per service |
| GET | `/v1/changes` | Schema changes since last generation |
| GET | `/v1/captures` | List captured interactions |
| GET | `/v1/services` | List services for authenticated user |

**Alerts**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/alerts` | Create alert config (Slack/email) |
| GET | `/v1/alerts` | List alert configs |
| DELETE | `/v1/alerts/{id}` | Delete alert |
| POST | `/v1/alerts/test` | Send test alert |

**Replay**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/replay` | Replay captured traffic against target URL |

**GitHub Actions**
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/github-actions` | Download pre-built GitHub Actions workflow YAML |

**Organizations (Team)**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/orgs` | Create organization |
| GET | `/v1/orgs/me` | My organizations |
| POST | `/v1/orgs/{slug}/invite` | Invite member |
| GET | `/v1/orgs/{slug}/members` | List members |
| DELETE | `/v1/orgs/{slug}/members/{email}` | Remove member |
| GET | `/v1/orgs/{slug}/api-keys` | Org API keys |
| POST | `/v1/orgs/{slug}/api-keys` | Create org API key |

**Billing**
| Method | Path | Description |
|--------|------|-------------|
| POST | `/billing/stripe-webhooks` | Stripe webhook handler |

### Generator Files (`backend/generator/`)
| File | Output | Mock Support |
|------|--------|-------------|
| `pytest_writer.py` | `.py` pytest files | **respx** (HTTP) + **pytest-mock** (SQL) |
| `jest_writer.py` | `.test.js` Jest files | **nock** (HTTP) |
| `vitest_writer.py` | `.test.ts` Vitest files | **MSW** (HTTP) |
| `go_writer.py` | `_test.go` Go test files | **jarcoal/httpmock** (HTTP) |
| `rspec_writer.py` | `_spec.rb` RSpec files | **webmock** (HTTP) |

All generators auto-detect `outgoing_calls` on captured records and inject mock setup code when present.

### Pipeline Files (`backend/pipeline/`)
- `deduplication.py` — removes duplicate captured requests (fingerprinting)
- `chain_analysis.py` — identifies request chains/sequences
- `state_inferrer.py` — infers fixture dependencies from traffic
- `alert_checker.py` — checks alert conditions after each ingest (background task)

### Billing / Plan Quotas (`backend/billing/`)
| Plan | Monthly Request Quota |
|------|----------------------|
| free | 10,000 |
| starter | 1,000,000 |
| growth | 10,000,000 |
| enterprise | unlimited |

---

## [NEW] Dependency Mocking Feature

This is the **Keploy-killer feature** — implemented 2026-04-26.

### What it does
When `capture_outgoing=True` is set on the middleware, every outgoing HTTP call and SQL query made during a request is captured alongside the request/response pair. The test generator then produces mock fixtures so generated tests require **zero live infrastructure** — no database, no external APIs.

### Architecture
```
Inbound Request
  → Middleware sets per-request context (ContextVar / AsyncLocalStorage / context.Context / Thread.current)
    → App logic runs
      → outgoing httpx / requests / http / Net::HTTP calls are intercepted
      → SQLAlchemy queries are intercepted (Python only)
    → After response: outgoing_calls list is attached to CapturedInteraction
  → Uploaded to backend with outgoing_calls JSON
→ Backend stores outgoing_calls in capturerecord.outgoing_calls (JSON column)
→ Generator reads outgoing_calls and produces mock fixtures
→ Generated test imports mock fixtures — no real infra needed
```

### Python SDK Changes

**`interceptors.py`** (new file):
- `_CONTEXT: ContextVar[list | None]` — async context isolation
- `_thread_local` — WSGI/sync thread isolation
- `_sanitize_url(url)` — strips `api_key`, `token`, `secret`, `auth`, `password` from query params
- `_truncate_body(body, 4096)` — caps large response bodies
- `patch_httpx()` — wraps `httpx.AsyncClient.send` + `httpx.Client.send`
- `patch_requests()` — wraps `requests.Session.send`
- `register_sqlalchemy_engine(engine)` — `before_cursor_execute` / `after_cursor_execute` hooks; params redacted to `"?"`; first 3 rows captured as `result_sample`; skips PRAGMA/SELECT 1/COMMIT/etc.

**`capture.py`** (modified):
- Added `OutgoingCall` dataclass (`type`, `method`, `url`, `request_body`, `response_status`, `response_body`, `query`, `params`, `result_count`, `result_sample`, `latency_ms`)
- Added `outgoing_calls: list = field(default_factory=list)` to `CapturedInteraction`
- `to_dict()` now serializes `outgoing_calls`

**`middleware.py`** (modified):
- `HttraceCaptureMiddleware.__init__` now accepts `capture_outgoing: bool = False` and `db_engines: list | None = None`
- ASGI `__call__` sets `_CONTEXT.set([])` before each request, reads outgoing calls after, resets token
- `WsgiHttraceCaptureMiddleware` mirrors this with `_thread_local`

Usage:
```python
app.add_middleware(
    HttraceCaptureMiddleware,
    api_key="ht_...",
    capture_outgoing=True,
    db_engines=[engine],   # optional SQLAlchemy engines
)
```

### Node.js SDK Changes (`sdk-node/src/index.js`)
- `AsyncLocalStorage` from `node:async_hooks` — per-request store
- `_patchHttp()` — patches both `http.request` and `https.request`
- URL sanitizer strips sensitive query params
- `captureOutgoing: false` option added to middleware factory
- `_outgoingStore.run([], () => next())` wraps each request

Usage:
```js
app.use(httrace({ apiKey: 'ht_...', captureOutgoing: true }));
```

### Go SDK Changes (`sdk-go/httrace.go`)
- `OutgoingCall` struct
- `RecordingTransport` — `http.RoundTripper` that records calls into context
- `ClientFromContext(ctx)` — returns `*http.Client` with recording transport
- `Config.CaptureOutgoing bool` — activates per-request context
- `contextWithOutgoingCapture(ctx)` — attaches `*[]OutgoingCall` to request context

Usage:
```go
cfg := httrace.Config{APIKey: "ht_...", CaptureOutgoing: true}
// In handlers:
client := httrace.ClientFromContext(r.Context())
resp, _ := client.Get("https://api.stripe.com/...")
```

### Ruby SDK Changes (`sdk-ruby/lib/httrace.rb`)
- `NetHTTPInterceptor` module — `prepend`ed into `Net::HTTP`
- Records calls to `Thread.current[:httrace_outgoing]` when set
- `capture_outgoing: false` kwarg added to `CaptureMiddleware`
- URL sanitizer (`sanitize_outgoing_url`) strips sensitive params

Usage:
```ruby
use Httrace::CaptureMiddleware, api_key: 'ht_...', capture_outgoing: true
```

### Backend Changes
- `capturerecord.outgoing_calls` JSON column added (auto-migrated at startup)
- `RawCapture` model accepts `outgoing_calls: Optional[list] = None`
- `CaptureRecord` stores `outgoing_calls` in constructor

### Generated Mock Examples

**pytest (respx + pytest-mock):**
```python
# pip install pytest-respx pytest-mock

@pytest.fixture
def mock_outgoing_http(respx_mock):
    respx_mock.get("https://api.stripe.com/v1/charges/ch_123").mock(
        return_value=httpx.Response(200, json={"id": "ch_123", "amount": 2000}),
    )
    yield respx_mock

@pytest.fixture
def mock_db_queries(mocker):
    mock_conn = mocker.MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = [{"id": "ord_1"}]
    yield mock_conn

def test_post_orders_201(client, mock_outgoing_http, mock_db_queries, post_orders_body):
    response = client.post("/orders", json=post_orders_body)
    assert response.status_code == 201
```

**Jest (nock):**
```js
// npm install --save-dev nock
const nock = require('nock');
nock('https://api.stripe.com').get('/v1/charges/ch_123').reply(200, {"id":"ch_123"});
```

**Vitest (MSW):**
```ts
// npm install --save-dev msw
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
const server = setupServer(
  http.get('https://api.stripe.com/v1/charges/ch_123', () =>
    HttpResponse.json({ id: 'ch_123' })
  )
);
```

**Go (httpmock):**
```go
// go get github.com/jarcoal/httpmock
httpmock.Activate()
defer httpmock.DeactivateAndReset()
httpmock.RegisterResponder("GET", "https://api.stripe.com/v1/charges/ch_123",
    httpmock.NewStringResponder(200, `{"id":"ch_123"}`))
```

**RSpec (webmock):**
```ruby
require 'webmock/rspec'
stub_request(:get, "https://api.stripe.com/v1/charges/ch_123")
  .to_return(status: 200, body: '{"id":"ch_123"}',
             headers: { 'Content-Type' => 'application/json' })
```

---

## SDK Overview

| SDK | Language | Middleware Type | Outgoing Capture |
|-----|----------|-----------------|-----------------|
| `httrace` (PyPI) | Python | ASGI + WSGI | httpx, requests, SQLAlchemy |
| `httrace` (npm) | Node.js | Express | http, https |
| `httrace-go` (Go module) | Go | net/http | RecordingTransport |
| `httrace` (RubyGems) | Ruby | Rack | Net::HTTP |

---

## Feature Completeness

| Feature | Status |
|---------|--------|
| Python SDK (ASGI middleware) | ✅ Done |
| Python SDK (WSGI middleware) | ✅ Done |
| Node.js SDK (Express) | ✅ Done |
| Go SDK (net/http) | ✅ Done |
| Ruby SDK (Rack) | ✅ Done |
| pytest generator | ✅ Done |
| Jest generator | ✅ Done |
| Vitest generator | ✅ Done |
| Go test generator | ✅ Done |
| RSpec generator | ✅ Done |
| Auth fixture auto-detection | ✅ Done |
| Dependency mock generation (all SDKs + generators) | ✅ Done (2026-04-26) |
| Multi-format test output (`?format=`) | ✅ Done |
| OpenAPI export | ✅ Done |
| GitHub Actions workflow | ✅ Done |
| API drift detection (`/v1/changes`) | ✅ Done |
| Dashboard (coverage + changes + alerts tabs) | ✅ Done |
| Alerts (Slack + email) | ✅ Done |
| Replay testing (`/v1/replay`) | ✅ Done |
| Teams / Organizations | ✅ Done |
| VS Code Extension (scaffold) | ✅ Done |
| Stripe billing (webhooks wired) | Partial — no active payments |
| CLI (`httrace generate`, `diff`, `replay`) | ✅ Done |

---

## Email Setup

| Email | Trigger | Status |
|-------|---------|--------|
| Founder notification | New waitlist signup | ✅ Live |
| Subscriber welcome + API key | New waitlist signup | ✅ Live |
| Day 1 onboarding (quick-start tip) | Cron 09:00 UTC daily | ✅ Live |
| Day 3 check-in | Cron 09:00 UTC daily | ✅ Live |

- **Provider:** Resend (`re_NZRuxido_5qcuTU9neyzG8YXH3xvrGDBE`)
- **From:** `Httrace <noreply@httrace.com>`
- **Notify:** `arikwittlich@gmail.com`
- Onboarding script: `/opt/httrace/backend/scripts/send_onboarding.py`
- Cron log: `/var/log/httrace_onboarding.log`

---

## Video Assets

| File | Spec | Platform |
|------|------|---------|
| `video/out/Httrace.mp4` | 1920×1080, 56s | YouTube |
| `video/out/HttraceVerticalNative.mp4` | 1080×1920, 56s | TikTok (native 9:16) |
| `landing/httrace-promo.mp4` | Copy of above | Website (autoplay on scroll) |
| `landing/profile.png` | 800×800px | Social media profile picture |
| `landing/youtube-banner.png` | 2560×1440px | YouTube channel art |

Video source: `video/` (Remotion project, `remotion.config.ts`)

---

## Landing Page Structure

**File:** `landing/index.html` — live at https://httrace.com

### Sections (in order)
1. Navbar — logo, links, Login button
2. Hero — headline "Your users write your tests.", waitlist CTA
3. How it works — 3-step overview + `httrace diff` in CI
4. Feature highlights — multi-format, GitHub Actions, drift detection, replay, alerts
5. Code compare — before/after with auth_headers fixture
6. Framework badges
7. Comparison table — Httrace vs Keploy vs GoReplay (includes "Auth fixtures", "Dependency mocks")
8. Pricing — Free / Hobby / Starter / Growth / Enterprise
9. FAQ
10. Investor section
11. Waitlist CTA form (Turnstile)
12. Footer

### Pricing Tiers
| Tier | Price | Status |
|------|-------|--------|
| Free | €0/mo | Live |
| Hobby | €19/mo | Coming soon |
| Starter | €79/mo | Coming soon |
| Growth | €399/mo | Coming soon |
| Enterprise | €2,000+/mo | Coming soon |

---

## SEO & Discoverability

| Asset | Status |
|-------|--------|
| `sitemap.xml` | ✅ Exists — 5 URLs |
| `robots.txt` | ✅ Exists |
| `llms.txt` | ✅ Exists — full product description for LLM crawlers |
| `og.png` (1200×630) | ✅ Exists |
| Google Search Console | ✅ Sitemap submitted |
| Plausible analytics | ✅ Running (no custom goals yet) |

---

## Social Media & Marketing

| Platform | Status |
|----------|--------|
| TikTok | Video ready (`HttraceVerticalNative.mp4`), not yet posted |
| YouTube | Channel art ready, video ready (`Httrace.mp4`), not yet uploaded |
| Twitter/X | Account not yet created |
| Hacker News (Show HN) | Post written, needs karma to post |
| Reddit | Post written, not yet submitted |
| ProductHunt | Not yet prepared |

---

## Legal

| File | Status |
|------|--------|
| `landing/impressum.html` | Has placeholders — fill after Gewerbeanmeldung |
| `landing/datenschutz.html` | Has placeholders |
| `landing/privacy.html` | ✅ English version |
| `landing/agb.html` | Has placeholders |
| `landing/terms.html` | ✅ English version |

**Gewerbe:** Not yet registered. No payment processing active — legally acceptable for now.
See `NACH_GEWERBE_ANMELDUNG.md` for step-by-step checklist.

---

## Environment Variables (Server — Never Commit)

Stored in `/opt/httrace/.env` on the server.

| Variable | Value / Notes |
|----------|--------------|
| `DATABASE_URL` | `sqlite:////data/httrace.db` |
| `ENV` | `production` |
| `RESEND_API_KEY` | `re_NZRuxido_5qcuTU9neyzG8YXH3xvrGDBE` |
| `RESEND_DOMAIN_VERIFIED` | `true` |
| `FROM_EMAIL` | `Httrace <noreply@httrace.com>` |
| `NOTIFY_EMAIL` | `arikwittlich@gmail.com` |
| `CORS_ORIGINS` | `https://httrace.com,https://www.httrace.com,http://localhost:3000,http://localhost:8080` |
| `TURNSTILE_SECRET_KEY` | `0x4AAAAAAC_reYI-EU_yUcs2nC0UBtdQwlQ` |
| `PROVISION_SECRET` | `1ffd294d4f7ca75f270491048e0c2f3dea353752eb17107a12ad6d78c550ebbe` |
| `JWT_SECRET` | Set at container start with `openssl rand -hex 32` |

---

## Deployment Workflow

### Landing page (website changes)
```bash
# Edit files in landing/
git add landing/
git commit -m "..."
git push origin main
# → Cloudflare Workers auto-deploys, live within seconds
```

### Backend changes
```bash
ssh root@46.224.203.69
# Edit files in /opt/httrace/backend/
cd /opt/httrace
docker build -t httrace-api .
docker rm -f httrace-api
docker run -d --name httrace-api --restart unless-stopped \
  -p 8080:8080 -v /data:/data \
  --env-file /opt/httrace/.env \
  httrace-api
docker logs httrace-api --tail 20
```

### SDK changes (Python)
```bash
# Local changes already done in /Users/marcuswinter/Claude/httrace/sdk/
git add sdk/
git commit -m "sdk: ..."
git push origin main
# Then publish to PyPI separately when ready
```

---

## Pending Work

### Immediate (dependency mocking — in progress)
- [ ] **Docker rebuild + redeploy** — needed to activate `outgoing_calls` DB column and new generator code
- [ ] **Docs update** — add "Dependency Mocking" section to `landing/docs/index.html`
- [ ] **Landing page update** — update comparison table + hero code example
- [ ] **End-to-end test** — ingest capture with `outgoing_calls`, generate test, verify mock fixtures appear

### Near-term
- [ ] SDK: publish updated Python SDK to PyPI (has `interceptors.py`, updated `middleware.py`)
- [ ] SDK: publish updated Node.js SDK to npm
- [ ] SDK: publish updated Go module
- [ ] SDK: publish updated Ruby gem
- [ ] Testimonials — replace fake placeholders with real ones when available
- [ ] Plausible custom goals — set up conversion events

### Legal / Business
- [ ] Gewerbeanmeldung (see `NACH_GEWERBE_ANMELDUNG.md`)
- [ ] Fill Impressum/Datenschutz/AGB placeholders after Gewerbeanmeldung
- [ ] Activate Stripe payments

---

## Architecture Notes

- The backend is intentionally kept off GitHub. Only copy is on the Hetzner server at `/opt/httrace/`. A local mirror exists at `/Users/marcuswinter/Claude/httrace/backend/` but may be incomplete — always prefer the server copy.
- Nginx handles SSL termination (Certbot) and proxies to Docker on port 8080.
- SQLite WAL mode is enabled. When backing up: copy both `.db`, `.db-shm`, and `.db-wal`.
- The `outgoing_calls` column is auto-migrated at container startup via `_migrate(engine)` in `database.py` — no manual SQL needed.
- `ContextVar` is used for Python async isolation; `threading.local` for WSGI. Both are checked by `_get_calls()` in `interceptors.py`.
- The Turnstile site key in frontend HTML is public and safe to commit. Only the secret key stays in `.env`.
