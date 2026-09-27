"""One local Ollama request at a time with bounded priority scheduling.

A translation never waits behind a slower job (A-23): when one arrives while a reply or a
description is being generated, that request is stopped (Ollama stops generating when the
connection closes) and put back in the queue after the translation. A cancelled job that is
already running is stopped the same way, so an obsolete reply no longer holds the model.
Each kind has its own answer length (`num_predict`) and timeout.
"""

from __future__ import annotations

import http.client
import itertools
import json
import logging
import queue
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, InvalidStateError

PRIORITIES = {"translation": 0, "names": 1, "replies": 2, "descriptions": 3}
# Answers are short JSON objects; a cap stops a runaway answer from holding the model.
NUM_PREDICT = {"translation": 256, "names": 48, "replies": 96, "descriptions": 32}
# A job this much lower in priority than an arriving one is stopped and queued again.
PREEMPTS = {"translation": {"replies", "descriptions"}}

logger = logging.getLogger(__name__)


class Preempted(Exception):
    """The running request was stopped for a more urgent one, or its caller cancelled it."""


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
        self.stats: dict[str, dict] = {}  # per kind: count, failures, last and max seconds
        self._lock = threading.RLock()
        self._retry_at = 0.0
        self._current: tuple | None = None  # the job being answered
        self._connection: http.client.HTTPConnection | None = None
        self._stopped = False  # the current request was stopped on purpose

    def _timeout(self, payload: dict) -> float:
        # The warm-up (no messages) loads the model into memory, which takes several
        # seconds from cold; only answers once loaded are held to the short timeouts.
        if not payload["messages"]:
            return self.config.get("load_timeout_s", 60.0)
        kind = payload.get("_kind")
        return (self.config.get("timeouts") or {}).get(kind, self.config.get("timeout_s", 30.0))

    def _request(self, payload: dict) -> dict:
        body = {k: v for k, v in payload.items() if not k.startswith("_")}
        connection = http.client.HTTPConnection("127.0.0.1", 11434, timeout=self._timeout(payload))
        with self._lock:
            if self._stopped:
                raise Preempted
            self._connection = connection
        try:
            connection.request(
                "POST", "/api/chat", json.dumps(body), {"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            data = response.read(1024 * 1024 + 1)
            if response.status != 200 or len(data) > 1024 * 1024:
                raise RuntimeError("local Ollama request failed or exceeded response limit")
            return json.loads(data)
        finally:
            with self._lock:
                self._connection = None
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
        options = {"num_ctx": self.config["num_ctx"], "temperature": self.config["temperature"]}
        if messages:
            options["num_predict"] = (self.config.get("num_predict") or {}).get(
                kind, NUM_PREDICT[kind]
            )
        payload = {
            "model": self.config["model"],
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": self.config["keep_alive"],
            "format": schema,
            "options": options,
            "_kind": kind,
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
            current = self._current
            if (
                messages
                and not future.done()
                and current is not None
                and current[4].get("_kind") in PREEMPTS.get(kind, ())
            ):
                self._stop_current()  # the translation goes first; the job is queued again
        return future

    def _stop_current(self) -> None:
        """Stop the request being answered (called with the lock held)."""
        self._stopped = True
        connection = self._connection
        sock = getattr(connection, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _on_done(self, future: Future) -> None:
        # a caller cancelled the job being answered: stop generating it
        if future.cancelled():
            with self._lock:
                if self._current is not None and self._current[3] is future:
                    self._stop_current()

    def _drain(self) -> list:
        jobs = []
        while True:
            try:
                jobs.append(self.queue.get_nowait())
            except queue.Empty:
                return jobs

    def _record(self, kind: str, seconds: float, ok: bool) -> None:
        s = self.stats.setdefault(kind, {"count": 0, "failures": 0, "last_s": 0.0, "max_s": 0.0})
        s["count"] += 1
        s["failures"] += not ok
        s["last_s"] = round(seconds, 3)
        s["max_s"] = round(max(s["max_s"], seconds), 3)

    def _run(self) -> None:
        while not self.closed.is_set():
            try:
                with self._lock:
                    job = self.queue.get_nowait()
                    _, _, generation, future, payload = job
                    if not (future.cancelled() or generation != self.generation):
                        self._current, self._stopped = job, False
            except queue.Empty:
                if not self.warm and time.monotonic() >= self._retry_at:
                    self.submit("translation", [], {"type": "object", "properties": {}})
                self.closed.wait(0.05)
                continue
            if future.cancelled() or generation != self.generation:
                future.cancel()
                continue
            future.add_done_callback(self._on_done)
            kind = payload.get("_kind", "?") if payload["messages"] else "warm-up"
            began = time.perf_counter()
            try:
                result = self.transport(payload)
                if not payload["messages"] and result.get("done_reason") == "load":
                    value = {}
                else:
                    value = json.loads(result["message"]["content"])
                if not isinstance(value, dict):
                    raise TypeError("expected structured object")
                self._record(kind, time.perf_counter() - began, True)
                with self._lock:
                    self._current, self._stopped = None, False
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
                    stopped, self._stopped, self._current = self._stopped, False, None
                    if generation != self.generation or self.closed.is_set():
                        future.cancel()
                        continue
                    if stopped or isinstance(exc, Preempted):
                        if not future.done():
                            # stopped for a translation: answer it after that
                            job = (PRIORITIES.get(kind, 3), next(self.serial), generation)
                            try:
                                self.queue.put_nowait((*job, future, payload))
                            except queue.Full:
                                future.cancel()
                        continue
                    self._record(kind, time.perf_counter() - began, False)
                    if isinstance(exc, TimeoutError) and self.warm:
                        # a slow answer, not a dead server: drop this job, keep going
                        logger.warning(
                            "local model took over %.1f s for a %s job; skipped it",
                            self._timeout(payload),
                            kind,
                        )
                    else:
                        logger.exception("local Ollama request failed")
                        self.warm = False
                        self.error = (
                            "local language model unavailable or returned an invalid response"
                        )
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
            if self._current is not None:
                self._stop_current()

    def stop(self) -> None:
        self.closed.set()
        self.cancel_pending()
        if self.thread:
            self.thread.join(timeout=0.3)
