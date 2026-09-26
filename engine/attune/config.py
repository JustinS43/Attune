"""Loads config/attune.toml over config/attune.example.toml.

Section 4 - Pages, Engine & Demo. TODO: P-02.

`load_config()` reads the example file (every key, with the plan's defaults) and
merges the local `config/attune.toml` over it table by table, so a local file only
needs the keys it changes. It adds `config["clock"]` (the shared clock every service
must stamp with). Secrets never live here; they come from `.env`.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

from .core import clock

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_NAME = Path("config") / "attune.toml"
EXAMPLE_NAME = Path("config") / "attune.example.toml"


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge `over` into a copy of `base`; tables merge, everything else replaces."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def _find(name: Path, cwd: Path) -> Path | None:
    for root in (cwd, REPO_ROOT):
        path = root / name
        if path.is_file():
            return path
    return None


def load_config(path: str | Path | None = None, cwd: str | Path | None = None) -> dict[str, Any]:
    """Load the engine config.

    `path` is an explicit local config (it must exist). Without it, `config/attune.toml`
    is looked up in the working directory, then the repo root; if neither exists only
    the example defaults are used.
    """
    cwd = Path(cwd) if cwd else Path.cwd()
    config: dict[str, Any] = {}
    example = _find(EXAMPLE_NAME, cwd)
    if example:
        with open(example, "rb") as fh:
            config = tomllib.load(fh)
    else:
        log.warning("No %s found; starting from empty defaults", EXAMPLE_NAME)
    if path is not None:
        local = Path(path)
        if not local.is_file():
            raise FileNotFoundError(f"Config file not found: {local}")
    else:
        local = _find(LOCAL_NAME, cwd)
    if local:
        with open(local, "rb") as fh:
            config = merge(config, tomllib.load(fh))
        log.info("Config: %s%s", local, f" over {example}" if example else "")
    else:
        log.info("Config: %s only (copy it to %s to change settings)", example, LOCAL_NAME)
    config["clock"] = clock.now
    return config


def section(config: dict[str, Any], name: str) -> dict[str, Any]:
    """A config table, or {} when it is missing."""
    value = config.get(name)
    return value if isinstance(value, dict) else {}
