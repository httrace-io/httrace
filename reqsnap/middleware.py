import random
import time
from typing import Callable

from .capture import (
    CapturedInteraction, CapturedRequest, CapturedResponse,
    _safe_parse_body, _filter_headers,
)
from .sanitizer import sanitize_json_body, sanitize
from .client import ReqsnapClient


class ReqsnapCaptureMiddleware:
    """
    ASGI middleware (FastAPI / Starlette / any ASGI app).

    Usage:
        app.add_middleware(
            ReqsnapCaptureMiddleware,
            api_key="rq_...",
            service="checkout-api",
            sample_rate=0.1,   # capture 10% of traffic
        )
    """

    def __init__(
        self,
        app,
        api_key: str,
        service: str = "default",
        sample_rate: float = 0.1,
        endpoint: str | None = None,
        exclude_paths: list[str] | None = None,
    ):
        self._app = app
        self._service = service
        self._sample_rate = sample_rate
        self._exclude = set(exclude_paths or ["/health", "/metrics", "/favicon.ico"])
        kwargs = {"api_key": api_key}
        if endpoint:
            kwargs["endpoint"] = endpoint
        self._client = ReqsnapClient(**kwargs)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not self._should_capture(scope):
            await self._app(scope, receive, send)
            return

        # Buffer the request body so we can read it without consuming the stream
        body_chunks: list[bytes] = []
        original_receive = receive

        async def receive_and_buffer():
            msg = await original_receive()
            if msg["type"] == "http.request":
                body_chunks.append(msg.get("body", b""))
            return msg

        # Buffer the response
        response_chunks: list[bytes] = []
        response_status: list[int] = []
        response_headers: list[dict] = []
        original_send = send

        async def capture_send(message):
            if message["type"] == "http.response.start":
                response_status.append(message["status"])
                response_headers.append(
                    {k.decode(): v.decode() for k, v in message.get("headers", [])}
                )
            elif message["type"] == "http.response.body":
                response_chunks.append(message.get("body", b""))
            await original_send(message)

        t_start = time.monotonic()
        await self._app(scope, receive_and_buffer, capture_send)
        latency_ms = (time.monotonic() - t_start) * 1000

        self._record(scope, body_chunks, response_status, response_headers, response_chunks, latency_ms)

    def _should_capture(self, scope: dict) -> bool:
        path = scope.get("path", "")
        if path in self._exclude:
            return False
        return random.random() < self._sample_rate

    def _record(self, scope, req_chunks, resp_status, resp_headers, resp_chunks, latency_ms):
        try:
            req_body_raw = b"".join(req_chunks)
            resp_body_raw = b"".join(resp_chunks)

            req_headers_raw = {
                k.decode(): v.decode()
                for k, v in scope.get("headers", [])
            }
            content_type = req_headers_raw.get("content-type", "")
            resp_content_type = (resp_headers[0] if resp_headers else {}).get("content-type", "")

            req_body = _safe_parse_body(req_body_raw, content_type)
            resp_body = _safe_parse_body(resp_body_raw, resp_content_type)

            session_id = req_headers_raw.get("x-session-id") or req_headers_raw.get("x-request-id")

            interaction = CapturedInteraction(
                service=self._service,
                session_id=session_id,
                request=CapturedRequest(
                    method=scope["method"],
                    path=scope["path"],
                    query_params=dict(
                        pair.split("=", 1) if "=" in pair else (pair, "")
                        for pair in scope.get("query_string", b"").decode().split("&")
                        if pair
                    ),
                    headers=sanitize(_filter_headers(req_headers_raw)),
                    body=sanitize_json_body(req_body),
                ),
                response=CapturedResponse(
                    status_code=resp_status[0] if resp_status else 0,
                    headers=resp_headers[0] if resp_headers else {},
                    body=sanitize_json_body(resp_body),
                    latency_ms=round(latency_ms, 2),
                ),
            )
            self._client.enqueue(interaction)
        except Exception:
            pass  # Never crash the app due to instrumentation


class WsgiReqsnapCaptureMiddleware:
    """
    WSGI middleware (Flask / Django).

    Usage (Flask):
        from reqsnap import WsgiReqsnapCaptureMiddleware
        app.wsgi_app = WsgiReqsnapCaptureMiddleware(app.wsgi_app, api_key="rq_...")
    """

    def __init__(self, app, api_key: str, service: str = "default", sample_rate: float = 0.1):
        self._app = app
        self._service = service
        self._sample_rate = sample_rate
        self._client = ReqsnapClient(api_key=api_key)

    def __call__(self, environ, start_response):
        if random.random() >= self._sample_rate:
            return self._app(environ, start_response)

        captured_status: list[str] = []
        captured_resp_headers: list[list] = []

        def capturing_start_response(status, headers, exc_info=None):
            captured_status.append(status)
            captured_resp_headers.append(headers)
            return start_response(status, headers, exc_info)

        # Buffer request body BEFORE passing to the app — stream is consumed by the app
        import io
        raw_body = environ.get("wsgi.input", io.BytesIO()).read()
        environ["wsgi.input"] = io.BytesIO(raw_body)  # restore for the app to read

        t_start = time.monotonic()
        result = self._app(environ, capturing_start_response)
        latency_ms = (time.monotonic() - t_start) * 1000

        resp_body_chunks = list(result)
        self._record(environ, raw_body, captured_status, captured_resp_headers, resp_body_chunks, latency_ms)
        return resp_body_chunks

    def _record(self, environ, raw_body, status_list, headers_list, resp_chunks, latency_ms):
        try:
            content_type = environ.get("CONTENT_TYPE", "")
            req_body = _safe_parse_body(raw_body, content_type)

            resp_headers = dict(headers_list[0]) if headers_list else {}
            resp_body_raw = b"".join(resp_chunks)
            resp_body = _safe_parse_body(resp_body_raw, resp_headers.get("Content-Type", ""))

            status_code = int(status_list[0].split()[0]) if status_list else 0

            interaction = CapturedInteraction(
                service=self._service,
                request=CapturedRequest(
                    method=environ.get("REQUEST_METHOD", "GET"),
                    path=environ.get("PATH_INFO", "/"),
                    query_params={},
                    headers={},
                    body=sanitize_json_body(req_body),
                ),
                response=CapturedResponse(
                    status_code=status_code,
                    headers=resp_headers,
                    body=sanitize_json_body(resp_body),
                    latency_ms=round(latency_ms, 2),
                ),
            )
            self._client.enqueue(interaction)
        except Exception:
            pass
