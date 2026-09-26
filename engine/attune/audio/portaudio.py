"""Process-wide PortAudio coordination.

PortAudio reads the device list once, in ``Pa_Initialize``. After a USB mic is
unplugged the running process keeps a stale list, and every later open fails
(on Windows WASAPI with "Insufficient memory [PaErrorCode -9992]"), even for
devices that still exist. The only cure is ``Pa_Terminate`` + ``Pa_Initialize``,
which also frees every open stream in the process. So any code that holds a
sounddevice stream (the mic reader, speech_out playback) wraps it in
``stream_open()``, and ``reinitialize()`` only runs while no stream is open.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_cond = threading.Condition()
_open_streams = 0


@contextmanager
def stream_open() -> Iterator[None]:
    """Mark a sounddevice stream (or device query) as in use; waits out a reinit."""
    global _open_streams
    with _cond:
        _open_streams += 1
    try:
        yield
    finally:
        with _cond:
            _open_streams -= 1
            _cond.notify_all()


def reinitialize(sd: Any, timeout: float = 1.0) -> bool:
    """Refresh PortAudio's device list; False if another stream stayed open."""
    with _cond:
        if not _cond.wait_for(lambda: _open_streams == 0, timeout):
            return False
        # Streams opened elsewhere wait on the lock until PortAudio is back up.
        try:
            if getattr(sd, "_initialized", 1) > 0:
                sd._terminate()
        except Exception as exc:  # noqa: BLE001 - initialise again regardless
            logger.debug("PortAudio terminate failed: %s", exc)
        sd._initialize()
        return True
