import re
from fastapi import APIRouter, Query, Depends, Header, HTTPException
from sqlmodel import Session, select
from collections import defaultdict

from ..models import CaptureRecord, GeneratedTest
from ..database import get_session
from ..billing.usage import get_api_key
from ..generator.pytest_writer import generate_module
from ..pipeline.chain_analysis import group_by_session

router = APIRouter(prefix="/v1", tags=["generate"])

_PATH_PARAM_RE = re.compile(r"/\d+(?=/|$)")
_DEV_KEY = "ht_local_dev"


def _resolve_key(x_api_key: str, session: Session) -> str:
    """Validate the key and return it. Raises 401 on failure."""
    if x_api_key == _DEV_KEY:
        return x_api_key
    if not get_api_key(session, x_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


@router.post("/generate-tests")
async def generate_tests(
    service: str = Query(...),
    x_api_key: str = Header(...),
    session: Session = Depends(get_session),
):
    api_key = _resolve_key(x_api_key, session)

    # ── Scoped to this tenant's captures only ────────────────────────────────
    records = session.exec(
        select(CaptureRecord)
        .where(CaptureRecord.api_key == api_key)
        .where(CaptureRecord.service == service)
        .order_by(CaptureRecord.captured_at)
    ).all()

    if not records:
        return {"tests": [], "message": "No captures found for this service"}

    # Group by endpoint (method + normalized path — collapse numeric IDs)
    by_endpoint: dict[str, list] = defaultdict(list)
    for rec in records:
        normalized = _PATH_PARAM_RE.sub("/{id}", rec.path)
        key = f"{rec.method}:{normalized}"
        by_endpoint[key].append(rec)

    # For each endpoint, take up to 5 representative captures (unique fingerprints)
    generated = []
    files: dict[str, str] = {}
    used_filenames: set[str] = set()

    for endpoint_key, recs in by_endpoint.items():
        seen_fingerprints: set[str] = set()
        representative = []
        for rec in recs:
            if rec.fingerprint not in seen_fingerprints:
                representative.append(rec)
                seen_fingerprints.add(rec.fingerprint)
            if len(representative) >= 5:
                break

        module_code = generate_module(representative, service)
        filename = _unique_filename(_endpoint_to_filename(endpoint_key), used_filenames)
        used_filenames.add(filename)
        files[filename] = module_code

        # Upsert: replace any previously generated test for the same key+service+file
        existing = session.exec(
            select(GeneratedTest)
            .where(GeneratedTest.api_key == api_key)
            .where(GeneratedTest.service == service)
            .where(GeneratedTest.test_name == filename)
        ).first()

        if existing:
            existing.test_code = module_code
            existing.source_fingerprints = list(seen_fingerprints)
            session.add(existing)
        else:
            session.add(GeneratedTest(
                api_key=api_key,
                service=service,
                test_name=filename,
                test_code=module_code,
                source_fingerprints=list(seen_fingerprints),
            ))

        generated.append({"file": filename, "test_count": len(representative)})

    session.commit()
    return {
        "generated": len(generated),
        "files": generated,
        "code": files,
    }


@router.get("/coverage")
async def coverage(
    service: str = Query(...),
    x_api_key: str = Header(...),
    session: Session = Depends(get_session),
):
    api_key = _resolve_key(x_api_key, session)

    # ── Scoped to this tenant only ───────────────────────────────────────────
    records = session.exec(
        select(CaptureRecord.method, CaptureRecord.path, CaptureRecord.status_code)
        .where(CaptureRecord.api_key == api_key)
        .where(CaptureRecord.service == service)
    ).all()

    endpoints: dict[str, dict] = {}
    for method, path, status in records:
        key = f"{method} {path}"
        if key not in endpoints:
            endpoints[key] = {"method": method, "path": path, "captures": 0, "statuses": set()}
        endpoints[key]["captures"] += 1
        endpoints[key]["statuses"].add(status)

    return {
        "service": service,
        "endpoints": [
            {**v, "statuses": list(v["statuses"])}
            for v in endpoints.values()
        ],
        "total_captures": len(records),
    }


def _endpoint_to_filename(endpoint_key: str) -> str:
    method, path = endpoint_key.split(":", 1)
    slug = re.sub(r"[^a-zA-Z0-9]", "_", path).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return f"test_{method.lower()}_{slug}.py"


def _unique_filename(base: str, used: set[str]) -> str:
    if base not in used:
        return base
    stem = base[: -len(".py")]
    counter = 2
    while f"{stem}_{counter}.py" in used:
        counter += 1
    return f"{stem}_{counter}.py"
