# Httrace SDK — Configuration Reference

## Installation

```bash
pip install httrace
```

Requires Python 3.10+.

---

## HttraceCaptureMiddleware

The main integration point. Add it once to your app — it captures all traffic in the background without affecting request latency.

### ASGI (FastAPI / Starlette)

```python
from httrace import HttraceCaptureMiddleware

app.add_middleware(
    HttraceCaptureMiddleware,
    api_key="ht_your_key_here",
    service="my-api",
)
```

### WSGI (Flask / Django)

```python
from httrace import HttraceCaptureMiddleware

app = HttraceCaptureMiddleware(
    app,
    api_key="ht_your_key_here",
    service="my-api",
)
```

---

## Constructor Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `api_key` | `str` | **required** | Your `tr_` prefixed API key |
| `service` | `str` | **required** | Logical name for this service (e.g. `"payments-api"`) |
| `endpoint` | `str` | `https://ingest.httrace.com/v1/captures` | Backend ingest URL. Set to `http://localhost:8000/v1/captures` for local dev |
| `sample_rate` | `float` | `1.0` | Fraction of requests to capture (0.0–1.0). Use `0.1` for high-traffic services |
| `batch_size` | `int` | `50` | Number of captures to batch before flushing to backend |
| `max_queue` | `int` | `10_000` | Max in-memory queue size. Exceeding this drops captures silently |
| `exclude_paths` | `list[str]` | `["/health", "/metrics"]` | Paths to never capture |

### Example — production-hardened config

```python
app.add_middleware(
    HttraceCaptureMiddleware,
    api_key=os.environ["HTTRACE_API_KEY"],
    service="orders-service",
    sample_rate=0.05,          # capture 5% of traffic
    batch_size=100,
    max_queue=20_000,
    exclude_paths=["/health", "/metrics", "/internal"],
)
```

---

## PII Sanitization

Sanitization runs automatically before any data leaves your server. You cannot disable it.

### What gets redacted

**By field name** — the following keys are always replaced with `<REDACTED>` regardless of value:

`password`, `passwd`, `secret`, `token`, `api_key`, `apikey`, `credit_card`, `card_number`, `cvv`, `ssn`, `national_id`, `date_of_birth`, `dob`, `phone`, `mobile`, `address`, `name`, `first_name`, `last_name`, `full_name`, `display_name`, `street`, `zip`, `postal_code`, `city`, `birth_date`, `birthday`, `email`, `username`, `user_name`

**By pattern** — values matching these patterns are replaced inline:

| Pattern | Replacement |
|---|---|
| JWT tokens (`eyJ...`) | `<JWT_TOKEN>` |
| Credit card numbers (13–16 digits) | `<CARD_NUMBER>` |
| Email addresses | `<EMAIL>` |
| IPv4 addresses | `<IP_ADDRESS>` |
| Phone numbers (international) | `<PHONE>` |
| German IBANs (`DE...`) | `<IBAN>` |
| UUIDs | `<UUID>` |

### Known limitations

- Non-German IBANs are not detected by pattern (covered by field-name blocklist if key is `iban`)
- Integer-typed card numbers in JSON (e.g. `"card": 4111111111111111`) are not caught by regex
- Free-text fields containing names (e.g. `"note": "sent by John"`) are not redacted

For higher PII assurance (Growth plan+), contact us to enable server-side Presidio scanning.

---

## Environment Variables

The SDK itself reads no environment variables — pass config explicitly to prevent accidental misconfiguration. We recommend reading from env in your app code:

```python
import os
app.add_middleware(
    HttraceCaptureMiddleware,
    api_key=os.environ["HTTRACE_API_KEY"],   # never hardcode
    service=os.environ.get("SERVICE_NAME", "my-service"),
)
```

---

## Graceful Shutdown

The SDK registers an `atexit` handler automatically. When your process exits normally, remaining queued captures are flushed (up to 5-second timeout). For SIGKILL or OOM kills, in-queue captures are lost — this is expected behavior.

To trigger a flush manually (e.g. in a custom signal handler):

```python
from httrace.client import _client_instance  # internal, may change

_client_instance.shutdown(timeout=10.0)
```

---

## httrace.config.yaml

Created by `httrace init`. Used by the CLI only (not the SDK).

```yaml
api_key: ht_your_key_here
service: my-api
backend: http://localhost:8000   # or https://ingest.httrace.com
output: tests/integration        # where generated test files are written
```

---

## Binary / Streaming Responses

Bodies with these content types are not captured (stored as `null`):

`image/*`, `video/*`, `audio/*`, `application/octet-stream`, `application/zip`, `application/gzip`, `application/pdf`

Multipart form data is captured as-is (field names are scanned for PII).
