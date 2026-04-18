# Reqsnap

**Capture real production traffic. Auto-generate integration tests.**

[![PyPI](https://img.shields.io/pypi/v/reqsnap)](https://pypi.org/project/reqsnap/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/reqsnap/reqsnap/actions/workflows/sdk-ci.yml/badge.svg)](https://github.com/reqsnap/reqsnap/actions)

```bash
pip install reqsnap
```

## How it works

1. **Install** — add one line of middleware to your app
2. **Capture** — real traffic is captured and sanitized in the background
3. **Generate** — `reqsnap generate` writes pytest files directly into your repo

## Quickstart

```python
# FastAPI / Starlette
from reqsnap import ReqsnapCaptureMiddleware

app.add_middleware(
    ReqsnapCaptureMiddleware,
    api_key="rq_your_key_here",
    service="my-api",
)
```

```python
# Flask / Django (WSGI)
from reqsnap import ReqsnapCaptureMiddleware

app = ReqsnapCaptureMiddleware(app, api_key="rq_your_key_here", service="my-api")
```

Then generate tests:

```bash
reqsnap init       # creates reqsnap.config.yaml
reqsnap generate   # writes pytest files to tests/integration/
reqsnap status     # shows endpoint coverage
```

## Features

- Zero latency impact — fire-and-forget background queue
- Automatic PII sanitization (JWT, email, credit card, IBAN, phone, UUIDs)
- Deduplication — same endpoint shape captured once
- Session chain analysis — detects request dependencies
- Fixture inference — generates pytest setUp helpers from request bodies

## Documentation

- [SDK Configuration](docs/sdk-configuration.md)
- [API Reference](docs/api-reference.md)
- [Troubleshooting](docs/troubleshooting.md)

## License

MIT — see [LICENSE](LICENSE).  
The Reqsnap backend and test generation service are proprietary.
