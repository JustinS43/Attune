"""Fetches model files into models/: the one place downloads happen.

Section 4 - Pages, Engine & Demo. TODO: P-14 (this file so far holds only the
Light-ASD entry, added by V-22; the other models were fetched by hand, see
docs/setup.md). Plan: docs/attune-build-plan.html, section 07 Downloads.

Every entry names a fixed upstream URL (pinned to a commit), the exact size and the
SHA-256. A file is written to `<name>.part` first and only renamed into place once
both checks pass, so a broken download never looks like a model. Files already in
place with the right size and hash are skipped.

    python scripts/download_models.py --list          # what it can fetch
    python scripts/download_models.py light_asd       # asks before downloading
    python scripts/download_models.py light_asd --yes
    python scripts/download_models.py --check         # verify what is on disk

Ask your human before running it: it downloads model weights.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ModelFile:
    name: str
    url: str
    dest: str  # relative to the repo root
    size: int
    sha256: str
    license: str
    note: str = ""


_LIGHT_ASD_COMMIT = "ed38c232de5efe0261dbd68627c0ade7cdfe14eb"

MODELS: dict[str, ModelFile] = {
    m.name: m
    for m in (
        ModelFile(
            name="light_asd",
            url=(
                "https://raw.githubusercontent.com/Junhua-Liao/Light-ASD/"
                f"{_LIGHT_ASD_COMMIT}/weight/finetuning_TalkSet.model"
            ),
            dest="models/light_asd/finetuning_TalkSet.model",
            size=4_175_289,
            sha256="efc375833887eefa9d209dc92810e18519b04c3c73ea35a549f2a7f40b7d94d5",
            license="MIT (github.com/Junhua-Liao/Light-ASD)",
            note="Light-ASD fine-tuned on TalkSet: who is talking, from lips and sound (V-22)",
        ),
    )
}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_ok(m: ModelFile, root: Path = REPO_ROOT) -> bool:
    path = root / m.dest
    return (
        path.is_file() and path.stat().st_size == m.size and sha256_of(path) == m.sha256
    )


def fetch(m: ModelFile, root: Path = REPO_ROOT) -> Path:
    """Download one file, check its size and hash, then move it into place."""
    dest = root / m.dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    got = 0
    with urllib.request.urlopen(m.url, timeout=60) as resp, open(part, "wb") as out:
        for chunk in iter(lambda: resp.read(1 << 20), b""):
            out.write(chunk)
            h.update(chunk)
            got += len(chunk)
    if got != m.size or h.hexdigest() != m.sha256:
        part.unlink(missing_ok=True)
        raise RuntimeError(
            f"{m.name}: got {got} bytes, sha256 {h.hexdigest()}; "
            f"expected {m.size} bytes, sha256 {m.sha256}"
        )
    part.replace(dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="*", help="models to fetch (see --list)")
    ap.add_argument(
        "--list", action="store_true", help="list the models this script knows"
    )
    ap.add_argument(
        "--check", action="store_true", help="verify the files already on disk"
    )
    ap.add_argument("--yes", action="store_true", help="don't ask before downloading")
    a = ap.parse_args(argv)

    if a.list or not (a.names or a.check):
        for m in MODELS.values():
            state = "ok" if is_ok(m) else "missing"
            print(
                f"{m.name:12} {m.size / 1e6:7.1f} MB  {state:7}  {m.dest}  [{m.license}]"
            )
            if m.note:
                print(f"{'':12} {m.note}")
        return 0
    if a.check:
        bad = [m.name for m in MODELS.values() if not is_ok(m)]
        print("all present" if not bad else f"missing or wrong: {', '.join(bad)}")
        return 1 if bad else 0

    unknown = [n for n in a.names if n not in MODELS]
    if unknown:
        print(f"unknown model(s): {', '.join(unknown)}; see --list", file=sys.stderr)
        return 2
    todo = [MODELS[n] for n in a.names if not is_ok(MODELS[n])]
    if not todo:
        print("already present")
        return 0
    total = sum(m.size for m in todo) / 1e6
    print("Will download:")
    for m in todo:
        print(f"  {m.name}: {m.size / 1e6:.1f} MB from {m.url}")
    if not a.yes and input(f"Download {total:.1f} MB? [y/N] ").strip().lower() != "y":
        print("cancelled")
        return 1
    for m in todo:
        print(f"{m.name} -> {fetch(m)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
