import hashlib
import json
from typing import Any


def compute_fingerprint(method: str, path: str, req_body: Any) -> str:
    """
    Fingerprint based on method + path + body schema (not body values).
    Two requests with same shape but different values share a fingerprint.
    """
    schema = _schema_of(req_body)
    key = f"{method.upper()}:{path}:{json.dumps(schema, sort_keys=True)}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _schema_of(value: Any, _depth: int = 0) -> Any:
    """Return the structural schema of a value without its concrete data."""
    if _depth > 8:
        return "..."
    if isinstance(value, dict):
        return {k: _schema_of(v, _depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        if not value:
            return []
        return [_schema_of(value[0], _depth + 1)]
    if isinstance(value, str):
        return "str"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if value is None:
        return "null"
    return "any"
