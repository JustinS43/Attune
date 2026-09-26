"""One local Ollama request at a time with bounded priority scheduling."""

from __future__ import annotations

import http.client
import itertools
import json
import logging
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future

PRIORITIES = {"translation": 0, "names": 1, "replies": 2, "descriptions": 3}


class OllamaClient:
    """Use loopback only, disable thinking, and keep the model resident."""

    def __init__(self, config: dict, transport: Callable | None = None):
        self.config = config
        self.transport = transport or self._request
        self.queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=config["max_queue"])
        self.serial = itertools.count()
        self.closed = threading.Event()
        self.thread = None
        self.warm = False
        self.generation = 0

    def _request(self, payload: dict) -> dict:
        connection = http.client.HTTPConnection(
            "127.0.0.1", 11434, timeout=self.config["timeout_s"]
        )
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
        self.closed.clear()
        self.submit("translation", [], {"type": "object", "properties": {}})
        self.thread = threading.Thread(target=self._run, name="ollama", daemon=True)
        self.thread.start()

    def submit(self, kind: str, messages: list, schema: dict) -> Future:
        """Return immediately; fail explicitly if the bounded queue is full."""
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
        try:
            self.queue.put_nowait(
                (PRIORITIES[kind], next(self.serial), self.generation, future, payload)
            )
        except queue.Full:
            future.set_exception(RuntimeError("Ollama queue is full"))
        return future

    def _run(self) -> None:
        while not self.closed.is_set():
            try:
                _, _, generation, future, payload = self.queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if future.cancelled() or generation != self.generation:
                future.cancel()
                continue
            try:
                result = self.transport(payload)
                self.warm = True
                value = json.loads(result["message"]["content"])
                if not isinstance(value, dict):
                    raise TypeError("expected structured object")
                if self.closed.is_set() or generation != self.generation:
                    future.cancel()
                else:
                    future.set_result(value)
            except Exception as exc:
                logging.getLogger(__name__).exception("local Ollama request failed")
                self.warm = False
                if not future.done():
                    future.set_exception(exc)

    def cancel_pending(self) -> None:
        """Invalidate an in-flight response and erase all queued conversation data."""
        self.generation += 1
        while True:
            try:
                _, _, _, future, _ = self.queue.get_nowait()
                future.cancel()
            except queue.Empty:
                return

    def stop(self) -> None:
        self.closed.set()
        self.cancel_pending()
        if self.thread:
            self.thread.join(timeout=0.3)
