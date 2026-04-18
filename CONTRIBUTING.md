# Contributing to Reqsnap SDK

Thanks for considering a contribution! The SDK is the open-source core of Reqsnap — PRs are welcome.

## What's in scope for the SDK

The SDK (`sdk/`) covers:
- ASGI/WSGI middleware
- PII sanitization
- Background capture uploader

The backend (test generation, billing, storage) is proprietary and not part of this repo.

## Getting started

```bash
git clone https://github.com/reqsnap/reqsnap-python
cd reqsnap-python
pip install -e ".[dev]"
pytest
```

## Pull Request guidelines

- One feature or fix per PR
- Add or update tests for any changed behaviour
- Keep the public API backwards compatible
- Run `ruff check .` and `mypy reqsnap/` before submitting

## Reporting security issues

**Do not open a GitHub issue for security vulnerabilities.**
Email security@reqsnap.io instead. We aim to respond within 48 hours.

## Improving PII detection

The sanitizer (`reqsnap/sanitizer.py`) is the most community-valuable part.
If you find a PII pattern it misses, please open an issue or PR with:
1. The pattern (anonymized example)
2. A failing test case
3. Your proposed regex or detection approach

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
