# Reqsnap — Troubleshooting Guide

---

## SDK / Middleware

### No captures appearing in the backend

**Check 1 — Middleware is actually registered**

For FastAPI, `add_middleware` must be called before the first request. Verify the order in `main.py`:

```python
app = FastAPI()
app.add_middleware(ReqsnapCaptureMiddleware, api_key="rq_...", service="my-api")
# ↑ must come before route registration
```

**Check 2 — sample_rate is not 0**

If you set `sample_rate=0.0` no captures are taken. Default is `1.0` (100%).

**Check 3 — path is in exclude_paths**

Default excluded: `/health`, `/metrics`. Add a log line inside your route handler to confirm the request even reaches the app.

**Check 4 — Backend unreachable**

The SDK silently drops captures when the backend is down (to avoid impacting your app). Check SDK logs:

```python
import logging
logging.getLogger("reqsnap").setLevel(logging.DEBUG)
```

You should see `reqsnap: failed to flush batch: ...` if the backend is unreachable.

---

### WSGI body always empty in tests

If you're testing a WSGI-wrapped app and the request body is empty, the middleware reads the body before passing it to your app, then restores `environ["wsgi.input"]`. Make sure you're using `ReqsnapCaptureMiddleware` as the outermost layer — not inside another middleware that consumes the stream first.

---

### `queue.Full` — captures being dropped

Your app is producing more traffic than the background thread can flush. Options:

1. Reduce `sample_rate` (e.g. `0.1`)
2. Increase `max_queue` (e.g. `50_000`)
3. Reduce `batch_size` to flush more frequently (e.g. `20`)

---

## Backend

### `500 Internal Server Error` on POST /v1/captures

Run the backend with `ENV=development` and check logs. Common causes:

| Symptom | Cause | Fix |
|---|---|---|
| `NameError: name 'v' is not defined` | Deduplication bug (fixed in 0.1.1) | Update to latest |
| `TypeError: Session(...)` | Stripe webhook Session wrapping bug (fixed in 0.1.1) | Update to latest |
| `sqlite3.OperationalError: database is locked` | WAL mode not enabled | Update to latest (WAL enabled by default now) |

---

### `401 Invalid API key` even with a valid key

- Make sure you're sending the key in the `X-Api-Key` header, not `Authorization`
- The key must start with `rq_` — keys not starting with this prefix are rejected immediately
- The key must exist in the database and `is_active` must be `True`
- In development, use the hardcoded dev key: `rq_local_dev` — no database entry needed

---

### `402 Monthly quota exceeded`

Your plan's monthly request limit is reached. Either:
- Upgrade your plan at reqsnap.com/pricing
- Wait until the 1st of next month (quota resets automatically)
- Reduce `sample_rate` in your middleware to stay under quota

Current limits:

| Plan | Monthly requests |
|---|---|
| Free | 10 000 |
| Starter | 1 000 000 |
| Growth | 10 000 000 |
| Enterprise | Unlimited |

---

### `413 Request body too large`

The ingest payload exceeds 10 MB. Split large batches into multiple requests or reduce `batch_size`.

---

### Backend port 8000 already in use

```bash
# Find and kill the process holding port 8000
lsof -ti:8000 | xargs kill -9
```

---

## CLI

### `reqsnap.config.yaml not found`

Run `reqsnap init` in your project root first.

---

### `Cannot connect to backend`

The `backend` URL in `reqsnap.config.yaml` is unreachable. For local dev:

1. Start the backend: `uvicorn backend.main:app --reload`
2. Confirm it's running: `curl http://localhost:8000/health`
3. Set `backend: http://localhost:8000` in your config

---

### Generated test file is skipped with "unsafe filename"

The backend returned a filename containing directory separators or non-alphanumeric characters. This is a safety guard against path traversal. If you're using a self-hosted backend, ensure you're running an up-to-date version.

---

### `reqsnap generate` returns "No captures found"

No captures exist for the configured `service` name. Check:
- The `service` in `reqsnap.config.yaml` exactly matches the `service` parameter in your middleware
- The middleware is receiving real traffic (make at least one HTTP request to your app)
- Run `reqsnap status` to see if any endpoints have been captured

---

## Test Output

### Generated tests all fail with `NotImplementedError`

The auto-generated `conftest.py` contains stubs (`raise NotImplementedError`). You must replace the stub implementations with real ones pointing at your test database/app:

```python
# tests/integration/conftest.py
from myapp.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

def create_test_user(plan="free"):
    # Create a real user in your test DB
    ...
```

---

### Tests pass locally but fail in CI

Common causes:
1. **Missing test database** — ensure your CI sets up a test DB and the `client` in `conftest.py` points to it
2. **Hardcoded IDs** — generated tests may reference IDs like `/orders/42` that don't exist in CI. Use path parameter normalization (automatic in 0.1.1+) and adjust fixtures accordingly
3. **PII-redacted values** — if a request body field was sanitized to `<REDACTED>`, the test may send that literal string. Adjust the fixture to provide a real value

---

## Getting help

- GitHub Issues: [github.com/reqsnap-io/reqsnap](https://github.com/reqsnap-io/reqsnap/issues)
- Email (Starter+): support@reqsnap.com
- Slack community: reqsnap.com/slack
