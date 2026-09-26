"""Capture recovery tests with fake devices and the real streaming resampler."""

import queue
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from attune.audio.mic import CaptureResampler, MicReader


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


def windows_devices(default=2):
    return SimpleNamespace(
        query_devices=lambda: [
            {"name": "Logitech MME", "max_input_channels": 1, "hostapi": 0},
            {"name": "Logitech WASAPI", "max_input_channels": 1, "hostapi": 1},
            {"name": "Laptop WASAPI", "max_input_channels": 1, "hostapi": 1},
        ],
        query_hostapis=lambda: [
            {"name": "MME", "default_input_device": 0},
            {"name": "Windows WASAPI", "default_input_device": default},
        ],
    )


def test_windows_preferred_and_fallback_use_wasapi(config, monkeypatch):
    monkeypatch.setattr("attune.audio.mic.sys.platform", "win32")
    mic = MicReader(config["audio"], lambda: 0, lambda block: None, windows_devices())
    assert mic._device(False) == 1
    assert mic._device(True) == 2
    mic.config = dict(mic.config, device_name="missing camera")
    assert mic._device(False) == 2


def test_no_wasapi_input_is_an_explicit_error(config, monkeypatch):
    monkeypatch.setattr("attune.audio.mic.sys.platform", "win32")
    mic = MicReader(config["audio"], lambda: 0, lambda block: None, windows_devices(-1))
    with pytest.raises(RuntimeError, match="WASAPI"):
        mic._device(True)


def test_callback_timestamps_ignore_scheduling_jitter_and_own_the_pcm(config):
    clock_calls = []

    def clock():
        clock_calls.append(True)
        return 100 + 0.020 * len(clock_calls)

    mic = MicReader(config["audio"], clock, lambda block: None)
    pcm = np.ones((480, 1), np.float32)
    mic._callback(pcm, 480, SimpleNamespace(currentTime=10, inputBufferAdcTime=9.98), False)
    mic._callback(pcm, 480, SimpleNamespace(currentTime=10.03, inputBufferAdcTime=9.99), False)
    pcm[:] = 0
    first, second = mic.queue.get_nowait(), mic.queue.get_nowait()
    assert first[0] == pytest.approx(100)
    assert second[0] - first[0] == pytest.approx(0.01)
    assert np.all(first[1] == 1)
    assert len(clock_calls) == 1


def test_callback_overload_is_nonblocking_and_stop_rejects_late_data(config):
    mic = MicReader(config["audio"], lambda: 1, lambda block: None)
    mic.queue = queue.Queue(maxsize=1)
    timing = SimpleNamespace(currentTime=1, inputBufferAdcTime=1)
    for _ in range(3):
        mic._callback(np.ones((480, 1), np.float32), 480, timing, True)
    assert mic.dropped == 2
    assert mic.overflows == 3
    mic.stop()
    mic.queue.get_nowait()
    mic._callback(np.ones((480, 1), np.float32), 480, timing, False)
    assert mic.queue.empty()


def run_blocks(resampler, origin, count=100):
    output = []
    for i in range(count):
        source_t = (np.arange(480) + i * 480) / 48000
        pcm = np.sin(2 * np.pi * 1000 * source_t).astype(np.float32)
        output.extend(resampler.feed(origin + i * 0.01, pcm))
    return output


def test_real_resampling_has_contiguous_timestamps_and_correct_frequency():
    pytest.importorskip("soxr")
    resampler = CaptureResampler(48000, 0.005)
    blocks = run_blocks(resampler, 10)
    for rate in (16000, 32000):
        stream = [b for b in blocks if b["sample_rate"] == rate]
        count = 0
        for block in stream:
            assert block["t"] == pytest.approx(10 + count / rate)
            assert block["samples"].dtype == np.float32
            count += len(block["samples"])
        assert rate * 0.9 < count <= rate
        # Count positive-going zero crossings after filter startup.
        pcm = np.concatenate([b["samples"] for b in stream])[100:]
        crossings = np.count_nonzero((pcm[:-1] <= 0) & (pcm[1:] > 0))
        assert crossings / (len(pcm) / rate) == pytest.approx(1000, abs=3)


@pytest.mark.parametrize("overflow", [False, True])
def test_resampling_discards_old_filter_history_after_a_gap(overflow):
    pytest.importorskip("soxr")
    resampler = CaptureResampler(48000, 0.005)
    run_blocks(resampler, 0)
    origin = 1.0 if overflow else 5.0
    output = resampler.feed(origin, np.zeros(480, np.float32), overflow)
    for i in range(1, 100):
        output.extend(resampler.feed(origin + i * 0.01, np.zeros(480, np.float32)))
    for rate in (16000, 32000):
        stream = [b for b in output if b["sample_rate"] == rate]
        assert stream[0]["t"] == origin
        assert not np.any(np.concatenate([b["samples"] for b in stream]))


def test_disconnect_reopens_fallback_and_stop_closes_capture(config, monkeypatch):
    pytest.importorskip("soxr")
    monkeypatch.setattr("attune.audio.mic.sys.platform", "win32")
    sd = windows_devices()
    opened, closed, delivered = [], [], []
    fallback_entered = threading.Event()

    class Stream:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.active = len(opened) > 0
            opened.append(kwargs["device"])

        def __enter__(self):
            if self.active:
                for i in range(100):
                    self.kwargs["callback"](
                        np.zeros((480, 1), np.float32),
                        480,
                        SimpleNamespace(
                            currentTime=20 + i * 0.01, inputBufferAdcTime=20 + i * 0.01
                        ),
                        False,
                    )
                fallback_entered.set()
            return self

        def __exit__(self, *args):
            closed.append(self.kwargs["device"])

        def abort(self):
            self.active = False

    sd.InputStream = Stream
    mic = MicReader(config["audio"], lambda: 30, delivered.append, sd)
    mic.start()
    original_thread = mic.thread
    mic.start()
    assert mic.thread is original_thread
    try:
        assert fallback_entered.wait(2)
        wait_for(lambda: len(delivered) > 0)
        assert opened == [1, 2]
        assert mic.health()["ok"] is True
        assert mic.health()["detail"] == "fallback mic"
        assert delivered[0]["t"] == 30
    finally:
        mic.stop()
    assert closed == [1, 2]
    assert not mic.thread.is_alive()
    assert not mic.connected
    assert mic.queue.empty()
