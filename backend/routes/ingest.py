from fastapi import APIRouter, HTTPException, Header, Depends, Request
from pydantic import BaseModel
from typing import Optional
from sqlmodel import Session
from slowapi import Limiter
from slowapi.util import get_remote_address

from ..models import CaptureRecord
from ..pipeline.deduplication import compute_fingerprint
from ..database import get_session
from ..billing.usage import check_and_increment, QuotaExceeded

import os

router = APIRouter(prefix="/v1", tags=["ingest"])
limiter = Limiter(key_func=get_remote_address)

_DEV_KEY = "ht_local_dev" if os.getenv("ENV", "development") != "production" else ""


class RawCapture(BaseModel):
    service: str
    session_id: Optional[str] = None
    request: dict
    response: dict


class IngestPayload(BaseModel):
    captures: list[RawCapture]


@router.post("/captures", status_code=202)
@limiter.limit("2000/minute")
async def ingest_captures(
    request: Request,
    payload: IngestPayload,
    x_api_key: str = Header(...),
    session: Session = Depends(get_session),
):
    if not x_api_key.startswith("ht_"):
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Quota check (skip for local dev key)
    if x_api_key != _DEV_KEY:
        try:
            check_and_increment(session, x_api_key, len(payload.captures))
        except PermissionError:
            raise HTTPException(status_code=401, detail="Invalid or inactive API key")
        except QuotaExceeded as e:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Monthly quota exceeded ({e.used}/{e.limit} on '{e.plan}' plan). "
                    "Upgrade at httrace.com/pricing"
                ),
            )

    created = 0
    for cap in payload.captures:
        req = cap.request
        resp = cap.response

        fingerprint = compute_fingerprint(
            req.get("method", "GET"),
            req.get("path", "/"),
            req.get("body"),
        )

        record = CaptureRecord(
            # ── Tenant ownership ──────────────────────────────────────────
            api_key=x_api_key,
            # ─────────────────────────────────────────────────────────────
            service=cap.service,
            session_id=cap.session_id,
            fingerprint=fingerprint,
            method=req.get("method", "GET"),
            path=req.get("path", "/"),
            query_params=req.get("query_params", {}),
            req_headers=req.get("headers", {}),
            req_body=req.get("body"),
            status_code=resp.get("status_code", 200),
            resp_headers=resp.get("headers", {}),
            resp_body=resp.get("body"),
            latency_ms=resp.get("latency_ms", 0.0),
        )
        session.add(record)
        created += 1

    session.commit()
    return {"accepted": created}
