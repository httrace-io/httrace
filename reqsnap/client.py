import atexit
import threading
import queue
import logging
import httpx
from .capture import CapturedInteraction

logger = logging.getLogger("reqsnap")

_DEFAULT_ENDPOINT = "https://ingest.reqsnap.com/v1/captures"

_SENTINEL = object()


class ReqsnapClient:
    """
    Fire-and-forget uploader. Captures are queued and sent in a background
    thread so the request path is never blocked. Flushes on process exit.
    """

    def __init__(
        self,
        api_key: str,
        endpoint: str = _DEFAULT_ENDPOINT,
        batch_size: int = 50,
        max_queue: int = 10_000,
    ):
        self._api_key = api_key
        self._endpoint = endpoint
        self._batch_size = batch_size
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        # Reuse a single client across all flushes — avoids TLS handshake per batch
        self._http = httpx.Client(timeout=5.0)
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        atexit.register(self.shutdown)

    def enqueue(self, interaction: CapturedInteraction) -> None:
        try:
            self._queue.put_nowait(interaction)
        except queue.Full:
            logger.debug("traceto: queue full, dropping capture")

    def shutdown(self, timeout: float = 5.0) -> None:
        """Flush remaining captures and stop the worker thread."""
        self._queue.put(_SENTINEL)
        self._thread.join(timeout=timeout)
        self._http.close()

    def _worker(self) -> None:
        batch: list[dict] = []
        while True:
            try:
                item = self._queue.get(timeout=2.0)
                if item is _SENTINEL:
                    if batch:
                        self._flush(batch)
                    return
                batch.append(item.to_dict())
                if len(batch) >= self._batch_size:
                    self._flush(batch)
                    batch = []
            except queue.Empty:
                if batch:
                    self._flush(batch)
                    batch = []

    def _flush(self, batch: list[dict]) -> None:
        try:
            self._http.post(
                self._endpoint,
                json={"captures": batch},
                headers={"X-Api-Key": self._api_key},
            )
        except Exception as exc:
            logger.debug("traceto: failed to flush batch: %s", exc)
