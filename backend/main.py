import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlmodel import SQLModel

from .database import engine
from .models import CaptureRecord, GeneratedTest
from .billing.models import ApiKey, UsageRecord
from .routes import ingest, generate, waitlist
from .billing import stripe_webhooks

logger = logging.getLogger("httrace")

limiter = Limiter(key_func=get_remote_address)

_CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:8080"  # dev defaults
).split(",")

MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MB


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    yield


app = FastAPI(
    title="Httrace API",
    version="0.1.0",
    description="Ingest production traffic captures and generate integration tests.",
    lifespan=lifespan,
    # Hide schema details in production
    docs_url="/docs" if os.getenv("ENV") != "production" else None,
    redoc_url=None,
)

# ── CORS ────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["X-Api-Key", "Content-Type"],
)

# ── Request size limit ───────────────────────────────────────────────────────
@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_BYTES:
        return JSONResponse({"error": "Request body too large (max 10MB)"}, status_code=413)
    # Guard chunked transfer encoding — no Content-Length header, must stream-check
    if not content_length:
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                return JSONResponse({"error": "Request body too large (max 10MB)"}, status_code=413)

        async def _receive() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = _receive
    return await call_next(request)

# ── Generic error handler — never leak stack traces ──────────────────────────
@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"error": "Internal server error"}, status_code=500)

# ── Rate limiting ────────────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(ingest.router)
app.include_router(generate.router)
app.include_router(waitlist.router)
app.include_router(stripe_webhooks.router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
