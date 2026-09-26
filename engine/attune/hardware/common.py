"""Small helpers shared by Section 3's services (hardware, speech_out, history)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Any

logger = logging.getLogger(__name__)


def fields(event: Any) -> dict:
    """Bus events may be dicts or the shared dataclasses; always return a dict."""
    if event is None:
        return {}
    if isinstance(event, Mapping):
        return dict(event)
    if is_dataclass(event):
        return asdict(event)
    return dict(vars(event))


def section(config: Any, name: str) -> dict:
    """Return one config section as a dict (missing -> empty, so defaults apply)."""
    if config is None:
        return {}
    value = config.get(name) if isinstance(config, Mapping) else getattr(config, name, None)
    if value is None:
        return {}
    return fields(value)


def shared_clock(config: Any) -> Callable[[], float]:
    """The engine's shared clock: config['clock'], else attune.core.clock.now.

    Falls back to time.monotonic (with a warning) so the hardware still runs
    before the core clock lands; timestamps then are not aligned with audio.
    """
    if isinstance(config, Mapping) and callable(config.get("clock")):
        return config["clock"]
    try:
        from attune.core import clock

        if callable(getattr(clock, "now", None)):
            return clock.now
    except Exception:
        logger.debug("ignored error", exc_info=True)
    logger.warning("shared clock missing; using time.monotonic for Section 3 timestamps")
    return time.monotonic
