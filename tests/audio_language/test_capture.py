"""Capture recovery tests with fake devices and the real streaming resampler."""

import logging
import queue
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from attune.audio import portaudio
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
    assert mic._device() == (1, True)
    mic.config = dict(mic.config, device_name="missing camera")
    assert mic._device() == (2, False)


def test_no_wasapi_input_is_an_explicit_error(config, monkeypatch):
    monkeypatch.setattr("attune.audio.mic.sys.platform", "win32")
    mic = MicReader(config["audio"], lambda: 0, lambda block: None, windows_devices(-1))
    mic.config = dict(mic.config, device_name="missing camera")
    with pytest.raises(RuntimeError, match="WASAPI"):
        mic._device()


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


class FakePortAudio:
    """sounddevice stand-in with PortAudio's hot-plug behaviour.

    The device list is a snapshot taken at initialisation. Once a device is
    unplugged, every open fails with -9992 until PortAudio is re-initialised.
    """

    PREFERRED, LAPTOP = "Microphone (Logitech Brio 101)", "Laptop mic array"

    def __init__(self):
        self.plugged = [self.PREFERRED, self.LAPTOP]
        self.snapshot = list(self.plugged)
        self._initialized = 1
        self.terminates = self.initializes = 0
        self.opened: list[str] = []
        self.lock = threading.Lock()

    def _terminate(self):
        self._initialized -= 1
        self.terminates += 1

    def _initialize(self):
        self._initialized += 1
        self.initializes += 1
        self.snapshot = list(self.plugged)

    def unplug(self, name):
        self.plugged.remove(name)

    def plug(self, name):
        self.plugged.insert(0, name)

    def query_devices(self):
        return [{"name": n, "max_input_channels": 1, "hostapi": 0} for n in self.snapshot]

    def query_hostapis(self):
        return [
            {"name": "Windows WASAPI", "default_input_device": self.snapshot.index(self.LAPTOP)}
        ]

    def InputStream(self, **kwargs):
        if any(name not in self.plugged for name in self.snapshot):
            raise RuntimeError("Error opening InputStream: Insufficient memory [PaErrorCode -9992]")
        return FakeStream(self, self.snapshot[kwargs["device"]], kwargs["callback"])


class FakeStream:
    """Calls back every 10 ms while its device stays plugged in."""

    def __init__(self, pa, name, callback):
        self.pa, self.name, self.callback = pa, name, callback
        self.aborted = False
        self.thread = threading.Thread(target=self._feed, daemon=True)

    @property
    def active(self):
        return not self.aborted and self.name in self.pa.plugged

    def _feed(self):
        t = 20.0
        while self.active:
            timing = SimpleNamespace(currentTime=t, inputBufferAdcTime=t)
            self.callback(np.zeros((480, 1), np.float32), 480, timing, False)
            t += 0.01
            time.sleep(0.01)

    def __enter__(self):
        self.pa.opened.append(self.name)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.aborted = True
        self.thread.join()

    def abort(self):
        self.aborted = True


@pytest.fixture
def hotplug(config, monkeypatch):
    pytest.importorskip("soxr")
    monkeypatch.setattr("attune.audio.mic.sys.platform", "win32")
    # Simulate the Windows COM entry points when this test runs on macOS or Linux.
    ole32 = SimpleNamespace(CoInitializeEx=lambda *_: 0, CoUninitialize=lambda: None)
    monkeypatch.setattr("ctypes.windll", SimpleNamespace(ole32=ole32), raising=False)
    monkeypatch.setattr(MicReader, "RECHECK_S", 0.05)
    pa = FakePortAudio()
    state = {"busy": False}
    delivered = []
    mic = MicReader(
        dict(config["audio"], device_name="Brio 101"),
        lambda: 30,
        delivered.append,
        pa,
        busy=lambda: state["busy"],
        present=lambda name: any(name.casefold() in n.casefold() for n in pa.plugged),
    )
    yield SimpleNamespace(mic=mic, pa=pa, state=state, delivered=delivered)
    mic.stop()


def test_unplug_reinitialises_portaudio_and_falls_back(hotplug, caplog):
    mic, pa = hotplug.mic, hotplug.pa
    mic.start()
    wait_for(lambda: mic.health()["detail"] == "preferred mic")
    wait_for(lambda: len(hotplug.delivered) > 0)
    assert hotplug.delivered[0]["t"] == 30
    caplog.set_level(logging.INFO, "attune.audio.mic")
    pa.unplug(pa.PREFERRED)
    wait_for(lambda: mic.health()["detail"] == "fallback mic", timeout=4)
    assert mic.health()["ok"] is True
    assert pa.terminates >= 1 and pa.initializes >= 1
    assert pa.opened == [pa.PREFERRED, pa.LAPTOP]
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1  # the loss itself, not one line per retry


def test_open_failures_back_off_and_log_once(hotplug, caplog):
    mic, pa = hotplug.mic, hotplug.pa
    caplog.set_level(logging.INFO, "attune.audio.mic")
    pa.plugged.append("gone")
    pa._initialize()
    pa.plugged.remove("gone")  # a device vanished after PortAudio listed it
    initialize = pa._initialize

    def stays_stale():  # and here re-initialising never helps
        initialize()
        pa.snapshot.append("gone")

    pa._initialize = stays_stale
    pa.initializes = 0
    mic.start()
    wait_for(lambda: mic.health()["detail"] == "microphone unavailable")
    time.sleep(2.0)
    # Retries at 0.5 s, 1 s, 2 s... not twenty times at 0.5 s.
    assert 2 <= pa.initializes <= 3
    assert mic.health()["ok"] is False
    unavailable = [r for r in caplog.records if "microphone unavailable" in r.getMessage()]
    assert len(unavailable) == 1


def test_replug_returns_to_the_preferred_mic_between_utterances(hotplug):
    mic, pa, state = hotplug.mic, hotplug.pa, hotplug.state
    pa.unplug(pa.PREFERRED)
    pa._initialize()  # start with the preferred mic already missing
    mic.start()
    wait_for(lambda: mic.health()["detail"] == "fallback mic")
    state["busy"] = True  # someone is talking
    pa.plug(pa.PREFERRED)
    time.sleep(0.4)
    assert mic.health()["detail"] == "fallback mic"
    assert pa.opened == [pa.LAPTOP]
    state["busy"] = False
    wait_for(lambda: mic.health()["detail"] == "preferred mic")
    assert pa.opened == [pa.LAPTOP, pa.PREFERRED]
    count = len(hotplug.delivered)
    wait_for(lambda: len(hotplug.delivered) > count)


def test_reinitialise_waits_for_playback_streams():
    pa = FakePortAudio()
    with portaudio.stream_open():
        assert portaudio.reinitialize(pa, timeout=0.05) is False
    assert pa.terminates == 0
    assert portaudio.reinitialize(pa, timeout=0.05) is True
    assert (pa.terminates, pa.initializes, pa._initialized) == (1, 1, 1)


def test_stop_closes_capture(hotplug):
    mic = hotplug.mic
    mic.start()
    original_thread = mic.thread
    mic.start()
    assert mic.thread is original_thread
    wait_for(lambda: mic.connected)
    mic.stop()
    assert not mic.thread.is_alive()
    assert not mic.connected
    assert mic.health()["detail"] == "stopped"
    assert mic.queue.empty()
