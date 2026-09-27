"""pytest wrapper for the end-to-end suite (tests/e2e/run_e2e.py).

Section 4 - Pages, Engine & Demo. TODO: P-37.

Off by default: the suite starts real engines (GPU, several minutes). Run it with

    ATTUNE_E2E=1 python -m pytest tests/e2e -q -p no:cacheprovider

It skips itself, with the reason, when Edge, node, playwright-core, the models or a film
reel are missing, so a normal `pytest tests` on any laptop stays green and fast.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness as h
import run_e2e as r

ENABLED = os.environ.get("ATTUNE_E2E", "") in ("1", "true", "yes")
REASONS = h.prerequisites() if ENABLED else []

pytestmark = [
    pytest.mark.skipif(
        not ENABLED, reason="set ATTUNE_E2E=1 to run the end-to-end suite"
    ),
    pytest.mark.skipif(bool(REASONS), reason="; ".join(REASONS) or "ready"),
]


@pytest.fixture(scope="module")
def run():
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    suite = r.Run(h.out_root() / f"pytest-{stamp}")
    yield suite
    suite.finish()


@pytest.mark.parametrize("name", list(r.SCENARIOS))
def test_scenario(run, name):
    run.breathe(name)
    checks = r.SCENARIOS[name](run)
    failed = [f"{c['name']}: {c['detail'][:200]}" for c in checks if not c["ok"]]
    assert checks, f"{name} ran no checks"
    assert not failed, "\n".join(failed)
