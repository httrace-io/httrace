import time
import json
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class CapturedRequest:
    method: str
    path: str
    query_params: dict
    headers: dict
    body: Optional[dict | list | str]
    timestamp: float = field(default_factory=time.time)


@dataclass
class CapturedResponse:
    status_code: int
    headers: dict
    body: Optional[dict | list | str]
    latency_ms: float


@dataclass
class CapturedInteraction:
    service: str
    request: CapturedRequest
    response: CapturedResponse
    session_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "service": self.service,
            "session_id": self.session_id,
            "request": asdict(self.request),
            "response": asdict(self.response),
        }


_BINARY_CONTENT_TYPES = (
    "application/octet-stream", "image/", "audio/", "video/",
    "application/gzip", "application/zip", "application/pdf",
    "application/protobuf", "multipart/",
)


def _safe_parse_body(raw: bytes, content_type: str) -> Optional[dict | list | str]:
    if not raw:
        return None
    # Skip binary content entirely — don't store or attempt to decode
    if any(t in content_type for t in _BINARY_CONTENT_TYPES):
        return None
    if "application/json" in content_type:
        try:
            return json.loads(raw)
        except Exception:
            pass
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


def _filter_headers(headers: dict) -> dict:
    sensitive = {
        "authorization", "cookie", "set-cookie", "x-api-key",
        "x-auth-token", "proxy-authorization",
    }
    return {
        k: v for k, v in headers.items()
        if k.lower() not in sensitive
    }
