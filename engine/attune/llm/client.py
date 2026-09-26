"""One local Ollama request at a time with bounded priority scheduling."""

from __future__ import annotations

import http.client
import itertools
import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, InvalidStateError

PRIORITIES = {"translation": 0, "names": 1, "replies": 2, "descriptions": 3}


class OllamaClient:
    """Use loopback only, disable thinking, and keep the model resident."""

    def __init__(self, config: dict, transport: Callable | None = None):
        self.config = config
        self.transport = transport or self._request
        self.queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=config.get("max_queue", 32))
        if self.queue.maxsize < 1:
            raise ValueError("Ollama max_queue must be positive")
        self.serial = itertools.count()
        self.closed = threading.Event()
        self.thread = None
        self.warm = False
        self.error = ""
        self.generation = 0
        self._lock = threading.RLock()
        self._retry_at = 0.0

    def _request(self, payload: dict) -> dict:
        # The warm-up (no messages) loads the model into memory, which takes several
        # seconds from cold; only answers once loaded are held to the short timeout.
        timeout = (
            self.config.get("load_timeout_s", 60.0)
            if not payload["messages"]
            else self.config.get("timeout_s", 30.0)
        )
        connection = http.client.HTTPConnection("127.0.0.1", 11434, timeout=timeout)
        try:
            connection.request(
                "POST", "/api/chat", json.dumps(payload), {"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            data = response.read(1024 * 1024 + 1)
            if response.status != 200 or len(data) > 1024 * 1024:
                raise RuntimeError("local Ollama request failed or exceeded response limit")
            return json.loads(data)
        finally:
            connection.close()

    def start(self) -> None:
        """Queue a warm-up and launch the single model worker."""
        if self.thread and self.thread.is_alive():
            return
        self.closed.clear()
        if self.queue.empty():
            self.submit("translation", [], {"type": "object", "properties": {}})
        self.thread = threading.Thread(target=self._run, name="ollama", daemon=True)
        self.thread.start()

    def submit(self, kind: str, messages: list, schema: dict) -> Future:
        """Enqueue promptly, replacing a lower-priority job when necessary."""
        future = Future()
        if self.closed.is_set():
            future.cancel()
            return future
        payload = {
            "model": self.config["model"],
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": self.config["keep_alive"],
            "format": schema,
            "options": {
                "num_ctx": self.config["num_ctx"],
                "temperature": self.config["temperature"],
            },
        }
        with self._lock:
            if self.closed.is_set():
                future.cancel()
                return future
            queued = self._drain()
            queued = [job for job in queued if not job[3].cancelled()]
            priority = PRIORITIES[kind]
            if len(queued) >= self.queue.maxsize:
                worst = max(queued, key=lambda job: (job[0], job[1]))
                if worst[0] > priority:
                    queued.remove(worst)
                    worst[3].cancel()
                else:
                    future.set_exception(RuntimeError("Ollama queue is full"))
            if not future.done():
                queued.append((priority, next(self.serial), self.generation, future, payload))
            for job in queued:
                self.queue.put_nowait(job)
        return future

    def _drain(self) -> list:
        jobs = []
        while True:
            try:
                jobs.append(self.queue.get_nowait())
            except queue.Empty:
                return jobs

    def _run(self) -> None:
        while not self.closed.is_set():
            try:
                with self._lock:
                    _, _, generation, future, payload = self.queue.get_nowait()
            except queue.Empty:
                if not self.warm and time.monotonic() >= self._retry_at:
                    self.submit("translation", [], {"type": "object", "properties": {}})
                self.closed.wait(0.05)
                continue
            if future.cancelled() or generation != self.generation:
                future.cancel()
                continue
            try:
                result = self.transport(payload)
                if not payload["messages"] and result.get("done_reason") == "load":
                    value = {}
                else:
                    value = json.loads(result["message"]["content"])
                if not isinstance(value, dict):
                    raise TypeError("expected structured object")
                with self._lock:
                    if self.closed.is_set() or generation != self.generation:
                        future.cancel()
                    else:
                        self.warm = True
                        self.error = ""
                        if not future.done():
                            future.set_result(value)
            except InvalidStateError:
                # A caller may cancel an obsolete reply during completion.
                pass
            except Exception as exc:
                with self._lock:
                    if generation != self.generation or self.closed.is_set():
                        future.cancel()
                        continue
                    logging.getLogger(__name__).exception("local Ollama request failed")
                    self.warm = False
                    self.error = "local language model unavailable or returned an invalid response"
                    self._retry_at = time.monotonic() + self.config.get("retry_s", 5.0)
                    if not future.done():
                        try:
                            future.set_exception(exc)
                        except InvalidStateError:
                            pass

    def cancel_pending(self) -> None:
        """Invalidate an in-flight response and erase all queued conversation data."""
        with self._lock:
            self.generation += 1
            for _, _, _, future, _ in self._drain():
                future.cancel()

    def stop(self) -> None:
        self.closed.set()
        self.cancel_pending()
        if self.thread:
            self.thread.join(timeout=0.3)
