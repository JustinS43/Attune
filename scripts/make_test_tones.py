"""Generate low-amplitude T3/T4 WAV fixtures; never plays audio."""

from __future__ import annotations

import argparse
import math
import struct
import wave
from pathlib import Path


def samples(
    kind: str,
    cycles: int = 2,
    frequency: float = 3100,
    rate: int = 32000,
    amplitude: float = 0.1,
) -> list[float]:
    """Synthesize alarm cadence including the inter-cycle silence."""
    if kind not in {"T3", "T4"} or cycles < 1 or rate < 2 * frequency or not 0 < amplitude <= 0.25:
        raise ValueError("invalid tone parameters")
    count, on, gap, rest = (3, 0.5, 0.5, 1.5) if kind == "T3" else (4, 0.1, 0.1, 5.0)
    output = []
    for _ in range(cycles):
        for beep in range(count):
            output.extend(
                amplitude * math.sin(2 * math.pi * frequency * i / rate)
                for i in range(round(on * rate))
            )
            output.extend([0.0] * round((rest if beep == count - 1 else gap) * rate))
    return output


def write_tone(path: Path, kind: str, **kwargs) -> None:
    """Write mono 16-bit PCM to an explicitly selected local path."""
    values = samples(kind, **kwargs)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, kwargs.get("rate", 32000), 0, "NONE", "not compressed"))
        output.writeframes(struct.pack(f"<{len(values)}h", *(round(v * 32767) for v in values)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["T3", "T4"])
    parser.add_argument("output", type=Path)
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--frequency", type=float, default=3100)
    args = parser.parse_args()
    write_tone(args.output, args.kind, cycles=args.cycles, frequency=args.frequency)
