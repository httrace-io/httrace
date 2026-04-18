# Httrace

**Capture real production HTTP traffic. Auto-generate integration tests.**

[![PyPI](https://img.shields.io/pypi/v/httrace)](https://pypi.org/project/httrace/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/httrace-io/httrace/actions/workflows/sdk-ci.yml/badge.svg)](https://github.com/httrace-io/httrace/actions)

```bash
pip install httrace
```

## How it works

1. **Install** — add one line of middleware to your app
2. **Capture** — real HTTP traffic is captured and sanitized in the background
3. **Generate** — `httrace generate` writes pytest files directly into your repo

## Quickstart

```python
# FastAPI / Starlette
from httrace import HttraceCaptureMiddleware

app.add_middleware(
    HttraceCaptureMiddleware,
    api_key="ht_your_key_here",
    service="my-api",
)
```

```python
# Flask / Django (WSGI)
from httrace import HttraceCaptureMiddleware

app = HttraceCaptureMiddleware(app, api_key="ht_your_key_here", service="my-api")
```

Then generate tests:

```bash
httrace init       # creates httrace.config.yaml
httrace generate   # writes pytest files to tests/integration/
httrace status     # shows endpoint coverage
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
The Httrace backend and test generation service are proprietary.
