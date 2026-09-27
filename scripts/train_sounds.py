"""Train the glasses' everyday-sound alerts (A-40) on real recordings.

The sound model (EfficientAT, AudioSet) already knows sirens, horns, babies, dogs and the rest;
what decides whether the glasses alert is each sound's threshold. This script plays labelled
clips through the model exactly as the engine hears them (10 s context, 0.5 s hops, the same
music/speech vetoes and "heard in N windows" rule) and picks, for every sound, the lowest
threshold that none of the other clips reach. That keeps false alarms out of a quiet room
while catching as many real sounds as possible.

    python scripts/train_sounds.py --folder data/sounds            # DIR/<kind>/*.wav + DIR/background/
    python scripts/train_sounds.py --esc50 path/to/ESC-50-master   # the ESC-50 dataset
    python scripts/train_sounds.py --folder data/sounds --write    # save to config/attune.toml

Kinds: siren horn scream glass baby dog phone timer water. Put room tone, talk, TV and music
in background/ so they count against every sound. With --write the tuned <kind>_score lines
go into [alerts] of config/attune.toml (the local, untracked config); restart the engine after.
Reads 16-bit or 32-bit PCM WAV only (convert others first, for example with ffmpeg).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from attune.alerts.rules import SOUNDS, sound_score
from attune.config import load_config

RATE = 32000
HOP = RATE // 2
CONTEXT = 10 * RATE
MARGIN = 0.05  # headroom above the loudest other clip
FLOOR = 0.1  # never trust a score this low

# ESC-50 category -> kind (the categories it has; the rest count as background)
ESC50 = {
    "siren": "siren",
    "car_horn": "horn",
    "glass_breaking": "glass",
    "crying_baby": "baby",
    "dog": "dog",
    "clock_alarm": "timer",
}


def read_wav(path: Path) -> np.ndarray:
    """Mono float32 at 32 kHz."""
    with wave.open(str(path), "rb") as w:
        width, channels, rate = w.getsampwidth(), w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
    elif width == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648
    else:
        raise ValueError(f"{path.name}: {8 * width}-bit WAV is not supported")
    x = x.reshape(-1, channels).mean(axis=1)
    if rate != RATE:
        import torch
        import torchaudio.functional as F

        x = F.resample(torch.from_numpy(x.copy()), rate, RATE).numpy()
    return x.astype(np.float32)


def clip_scores(model, audio: np.ndarray, block_at: float) -> dict[str, float]:
    """Each kind's best score on this clip, as the engine's rules would see it."""
    if len(audio) < RATE:
        audio = np.resize(audio, RATE)
    ends = range(RATE, len(audio) + 1, HOP) if len(audio) > RATE else [len(audio)]
    windows = [model.score(audio[max(0, end - CONTEXT) : end]) for end in ends]
    best = {}
    for kind, sound in SOUNDS.items():
        per = [
            0.0
            if any(s.get(label, 0) >= block_at for label in sound.block)
            else sound_score(s, kind)
            for s in windows
        ]
        need = min(sound.hits, len(per))  # N windows in a row must pass
        best[kind] = max(min(per[i : i + need]) for i in range(len(per) - need + 1))
    return best


def collect(args) -> list[tuple[str, Path, int]]:
    """(kind, clip, fold): ESC-50's own folds 1-5; folder clips are fold 0."""
    items: list[tuple[str, Path, int]] = []
    if args.folder:
        for sub in sorted(Path(args.folder).iterdir()):
            if sub.is_dir():
                kind = sub.name.lower()
                if kind not in SOUNDS and kind != "background":
                    print(
                        f"skipping {sub.name}/: not a kind ({', '.join(SOUNDS)}) or background"
                    )
                    continue
                items += [(kind, p, 0) for p in sorted(sub.glob("*.wav"))]
    if args.esc50:
        root = Path(args.esc50)
        with open(root / "meta" / "esc50.csv", newline="") as f:
            for row in csv.DictReader(f):
                items.append(
                    (
                        ESC50.get(row["category"], "background"),
                        root / "audio" / row["filename"],
                        int(row["fold"]),
                    )
                )
    if args.limit:
        by: dict[str, list] = {}
        for item in items:
            by.setdefault(item[0], []).append(item)
        items = [x for group in by.values() for x in group[: args.limit]]
    return items


def train(
    results: list[tuple[str, dict]], cfg: dict, max_false: float = 0.02
) -> dict[str, dict]:
    out = {}
    for kind, sound in SOUNDS.items():
        pos = [s[kind] for k, s in results if k == kind]
        neg = [s[kind] for k, s in results if k != kind]
        if not pos:
            continue
        was = float(cfg.get(f"{kind}_score", sound.score))

        def caught(t, pos=pos):
            return sum(p >= t for p in pos) / len(pos)

        def false(t, neg=neg):
            return sum(n >= t for n in neg)

        # the threshold that catches the most, with at most max_false of the other clips
        # reaching even MARGIN below it; of the thresholds that catch that many, the middle one
        # (the most room either way); nothing caught keeps the default
        # a small set can't vouch for a quiet room: stay above half the default until 100+ others
        low = FLOOR if len(neg) >= 100 else max(FLOOR, sound.score / 2)
        grid = [round(low + i / 100, 2) for i in range(int((0.95 - low) * 100) + 1)]
        ok = [t for t in grid if false(t - MARGIN) <= max_false * max(len(neg), 1)]
        best = max((caught(t) for t in ok), default=0.0)
        tops = [t for t in ok if caught(t) == best]
        tuned = round((tops[0] + tops[-1]) / 2, 2) if best > 0 else was
        out[kind] = {
            "clips": len(pos),
            "others": len(neg),
            "was": was,
            "tuned": tuned,
            "caught_was": caught(was),
            "false_was": false(was),
            "caught": caught(tuned),
            "false": false(tuned),
        }
    return out


def held_out(results: list[tuple[str, dict]], report: dict) -> dict[str, tuple]:
    """(caught, false alarms, clips, others) of each tuned threshold on clips it never saw."""
    out = {}
    for kind, r in report.items():
        pos = [s[kind] for k, s in results if k == kind]
        neg = [s[kind] for k, s in results if k != kind]
        if pos:
            caught = sum(p >= r["tuned"] for p in pos) / len(pos)
            out[kind] = (caught, sum(n >= r["tuned"] for n in neg), len(pos), len(neg))
    return out


def write_config(path: Path, tuned: dict[str, float]) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = [
        f"{kind}_score = {value}  # scripts/train_sounds.py"
        for kind, value in tuned.items()
    ]
    m = re.search(r"(?m)^\[alerts\]\s*$", text)
    if not m:
        text = text.rstrip() + "\n\n[alerts]\n" + "\n".join(lines) + "\n"
    else:
        end = re.search(r"(?m)^\[", text[m.end() :])
        body = text[m.end() : m.end() + end.start()] if end else text[m.end() :]
        for kind in tuned:
            body = re.sub(rf"(?m)^{kind}_score\s*=.*\n?", "", body)
        body = "\n" + "\n".join(lines) + "\n" + body.lstrip("\n")
        text = text[: m.end()] + body + (text[m.end() + end.start() :] if end else "")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--folder", help="DIR/<kind>/*.wav and DIR/background/*.wav")
    ap.add_argument(
        "--esc50", help="an unpacked ESC-50 dataset (meta/esc50.csv, audio/)"
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="at most this many clips per kind (quick runs)",
    )
    ap.add_argument(
        "--max-false",
        type=float,
        default=0.02,
        help="share of other clips allowed to fire",
    )
    ap.add_argument(
        "--holdout",
        type=int,
        default=0,
        help="tune without ESC-50 fold N and report how the thresholds do on it",
    )
    ap.add_argument(
        "--device", help="run the model here (cuda) instead of [sound_model] device"
    )
    ap.add_argument(
        "--cache",
        nargs="+",
        help="JSON files of clip scores: all are read, the first is written (re-runs are instant)",
    )
    ap.add_argument(
        "--shard", help="i/n: score only this share of the clips (parallel runs)"
    )
    ap.add_argument(
        "--write",
        action="store_true",
        help="save the tuned thresholds to config/attune.toml",
    )
    args = ap.parse_args()
    if not (args.folder or args.esc50):
        ap.error("give --folder and/or --esc50")

    import os

    os.chdir(ROOT)  # the model paths in the config are relative to the repo
    cfg = load_config()
    from attune.alerts.sound_model import SoundModel

    if args.device:
        cfg["sound_model"]["device"] = args.device
    model = SoundModel(cfg["sound_model"])
    items = collect(args)
    if (
        args.shard
    ):  # "i/n": score only every n-th clip, for parallel runs sharing --cache files
        i, n = (int(x) for x in args.shard.split("/"))
        items = items[i - 1 :: n]
    if not items:
        sys.exit("no clips found")
    cache = {}
    for name in args.cache or []:
        if Path(name).is_file():
            cache.update(json.loads(Path(name).read_text(encoding="utf-8")))
    results, folds = [], []
    for i, (kind, path, fold) in enumerate(items, 1):
        try:
            key = str(path.resolve())
            if key not in cache:
                block = cfg["alerts"]["speech_music_block"]
                cache[key] = clip_scores(model, read_wav(path), block)
            results.append((kind, cache[key]))
            folds.append(fold)
        except (
            OSError,
            ValueError,
            EOFError,
            wave.Error,
        ) as exc:  # a bad file shouldn't stop the run
            print(f"  skipped {path.name}: {exc}")
        if i % 25 == 0 or i == len(items):
            print(f"  scored {i}/{len(items)} clips", flush=True)
    if args.cache:
        Path(args.cache[0]).write_text(json.dumps(cache), encoding="utf-8")

    tune = [
        r
        for r, f in zip(results, folds, strict=True)
        if f != args.holdout or not args.holdout
    ]
    test = [
        r
        for r, f in zip(results, folds, strict=True)
        if args.holdout and f == args.holdout
    ]
    report = train(tune, cfg["alerts"], args.max_false)
    if not report:
        sys.exit("no clips for any kind")
    print(
        f"\n{'sound':8} {'clips':>5} {'others':>6}  {'threshold':>14}  {'caught':>13}  false alarms"
    )
    for kind, r in report.items():
        print(
            f"{kind:8} {r['clips']:5} {r['others']:6}  {r['was']:5.2f} -> {r['tuned']:5.2f}"
            f"  {r['caught_was']:5.0%} -> {r['caught']:4.0%}  {r['false_was']:3} -> {r['false']}"
        )
    if test:
        print(f"\nOn ESC-50 fold {args.holdout}, which the thresholds never saw:")
        for kind, (caught, false, n, others) in held_out(test, report).items():
            print(
                f"{kind:8} caught {caught:4.0%} of {n:3}   false alarms {false} of {others}"
            )
    if args.write:
        target = ROOT / "config" / "attune.toml"
        write_config(target, {k: r["tuned"] for k, r in report.items()})
        print(f"\nSaved to {target.relative_to(ROOT)}. Restart the engine to use them.")


if __name__ == "__main__":
    main()
