import re
import json
from typing import Any

# Patterns ordered by specificity
_PATTERNS = [
    # JWT tokens
    (re.compile(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'), "<JWT_TOKEN>"),
    # Credit card numbers (Luhn-passing sequences stripped to placeholder)
    (re.compile(r'\b(?:\d[ -]?){13,16}\b'), "<CARD_NUMBER>"),
    # Email addresses
    (re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'), "<EMAIL>"),
    # IPv4 addresses
    (re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'), "<IP_ADDRESS>"),
    # Phone numbers (international) — requires + prefix or starts with digit, no spaces allowed as sole connectors
    (re.compile(r'(?<!\d)(\+\d{1,3}[\s\-]?)?(\(?\d{2,4}\)?[\s\-]?)(\d{3,4}[\s\-]?){1,3}\d{2,4}(?!\d)'), "<PHONE>"),
    # German IBAN
    (re.compile(r'\bDE\d{2}[\s]?(?:\d{4}[\s]?){4}\d{2}\b'), "<IBAN>"),
    # UUIDs (session/user IDs — keep structure, scrub value)
    (re.compile(
        r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',
        re.IGNORECASE
    ), "<UUID>"),
]

_PII_FIELD_NAMES = {
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "credit_card", "card_number", "cvv", "ssn", "national_id",
    "date_of_birth", "dob", "phone", "mobile", "address",
    "name", "first_name", "last_name", "full_name", "display_name",
    "street", "zip", "postal_code", "city", "birth_date", "birthday",
    "email", "username", "user_name",
}


def sanitize(value: Any, _depth: int = 0) -> Any:
    if _depth > 20:
        return value
    if isinstance(value, dict):
        return {
            k: "<REDACTED>" if k.lower() in _PII_FIELD_NAMES
            else sanitize(v, _depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item, _depth + 1) for item in value]
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def _scrub_string(s: str) -> str:
    for pattern, replacement in _PATTERNS:
        s = pattern.sub(replacement, s)
    return s


def sanitize_json_body(body: Any) -> Any:
    if body is None:
        return None
    if isinstance(body, (dict, list)):
        return sanitize(body)
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
            return json.dumps(sanitize(parsed))
        except Exception:
            return _scrub_string(body)
    return body
