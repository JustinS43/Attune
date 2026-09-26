"""Fixtures for Section 3 tests: a tiny thread-safe bus and a wait helper. No devices."""

import copy
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))


class Bus:
    def __init__(self):
        self.callbacks = defaultdict(list)
        self.events = []
        self.lock = threading.RLock()

    def subscribe(self, topic, callback):
        with self.lock:
            self.callbacks[topic].append(callback)
        return lambda: self.callbacks[topic].remove(callback)

    def publish(self, topic, event):
        with self.lock:
            self.events.append((topic, copy.deepcopy(event)))
            callbacks = list(self.callbacks[topic])
        for callback in callbacks:
            callback(event)

    def of(self, topic):
        with self.lock:
            return [e for t, e in self.events if t == topic]


def wait_for(predicate, timeout=3.0, step=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


@pytest.fixture
def bus():
    return Bus()
