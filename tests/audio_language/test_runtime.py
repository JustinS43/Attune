import threading
import time

from attune.audio.runtime import Worker


def wait_for(predicate):
    deadline = time.monotonic() + 1
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


def test_control_survives_full_audio_queue(bus):
    seen = []
    worker = Worker(bus, "test", lambda topic, e, g: seen.append(topic))
    worker.subscribe("audio.block")
    worker.subscribe("session.forget")
    for _ in range(300):
        bus.publish("audio.block", {})
    bus.publish("session.forget", {})
    worker.start()
    wait_for(lambda: seen)
    worker.stop()
    assert seen == ["session.forget"]
    assert worker.dropped == 44


def test_inflight_result_cannot_publish_after_forget(bus):
    entered, release = threading.Event(), threading.Event()

    def handler(topic, event, generation):
        if topic == "caption":
            entered.set()
            release.wait(1)
            worker.publish("reply.suggestions", {"options": ["private"]}, generation)

    worker = Worker(bus, "test", handler)
    worker.subscribe("caption")
    worker.subscribe("session.forget")
    worker.start()
    bus.publish("caption", {})
    assert entered.wait(1)
    start = time.monotonic()
    bus.publish("session.forget", {})
    assert time.monotonic() - start < 0.1
    release.set()
    worker.stop()
    assert not [e for topic, e in bus.events if topic == "reply.suggestions"]


def test_stop_cleans_up_once_and_unsubscribes(bus):
    cleaned = []
    worker = Worker(bus, "test", lambda *args: None)
    worker.cleanup = lambda: cleaned.append(True)
    worker.subscribe("caption")
    worker.start()
    worker.stop()
    assert cleaned == [True]
    assert not bus.callbacks["caption"]


def test_worker_health_reports_capture_failure(bus):
    worker = Worker(bus, "audio", lambda *args: None)
    worker.health = lambda: {
        "ok": False,
        "detail": "microphone unavailable",
        "metrics": {"capture_dropped": 2},
    }
    worker.start()
    try:
        wait_for(lambda: any(topic == "status.part" for topic, _ in bus.events))
    finally:
        worker.stop()
    health = next(event for topic, event in bus.events if topic == "status.part")
    assert health["ok"] is False
    assert health["detail"] == "microphone unavailable"
    assert health["metrics"]["capture_dropped"] == 2
