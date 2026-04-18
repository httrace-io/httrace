"""
Detects chains between captured requests.

A chain is a sequence of requests where:
  - they share a session_id, OR
  - an ID value from response N appears in the body of request N+1
  - they are temporally close (< 5s apart)
"""
from typing import Any


def find_id_refs(resp_body: Any, req_body: Any) -> list[str]:
    """Return list of field names whose values appear in the next request body."""
    if not isinstance(resp_body, dict) or not isinstance(req_body, dict):
        return []

    resp_ids = _extract_id_values(resp_body)
    req_values = _extract_all_values(req_body)
    return [field for field, val in resp_ids.items() if val in req_values]


def _extract_id_values(d: dict, prefix: str = "") -> dict[str, Any]:
    """Extract fields whose names suggest they are IDs."""
    result = {}
    for k, v in d.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if any(seg in k.lower() for seg in ("id", "uuid", "token", "ref", "key")):
            result[full_key] = v
        if isinstance(v, dict):
            result.update(_extract_id_values(v, full_key))
    return result


def _extract_all_values(d: Any) -> set:
    """Flatten all scalar values from a nested structure."""
    result = set()
    if isinstance(d, dict):
        for v in d.values():
            result.update(_extract_all_values(v))
    elif isinstance(d, list):
        for item in d:
            result.update(_extract_all_values(item))
    elif isinstance(d, (str, int, float)):
        result.add(d)
    return result


def group_by_session(records: list) -> dict[str, list]:
    """Group CaptureRecords by session_id, falling back to individual groups."""
    groups: dict[str, list] = {}
    for rec in records:
        key = rec.session_id or f"solo_{rec.id}"
        groups.setdefault(key, []).append(rec)
    return groups
