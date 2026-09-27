"""Podcast evaluation: run the whole engine on real multi-person podcast clips and score it.

Section 4 - Pages, Engine & Demo. TODO: P-45.

Real conversations are harder than the TTS reels: people talk over each other, answer
in one word, laugh, and the camera shows two or three faces. This script scores the
engine against podcast clips whose captions were written by people (YouTube's manual
subtitles, never the automatic ones):

    python scripts/eval_podcast.py prepare hotones --video clip.mp4 --vtt clip.en.vtt \
        --offset 90 --speakers 3          # clip.mp4 starts 90 s into the episode
    python scripts/eval_podcast.py run hotones --out data/podcasts/hotones/run.json
    python scripts/eval_podcast.py score data/podcasts/hotones/run.json
    python scripts/eval_podcast.py run hotones --set audio.asr_chunk_ms=160   # try a setting

`prepare` writes data/podcasts/<name>/: `clip.mp4` (30 fps, no audio), `clip.wav` (16-bit
mono 48 kHz), and `ref.json`: every reference word with its time and turn. A turn is a
stretch of one voice; subtitle writers mark a new voice with "- " or ">>". Turns are
given speaker names by clustering their voice prints (CAM++, the whole clip at once,
which the live engine can never do); `ref.json` prints each cluster's lines so a person
can check them, and `"turn_speakers"` in it can be edited by hand.

`run` plays the clip through the engine exactly like eval_talker.py (video as the camera,
the WAV as the microphone on the same clock) and records every caption over the
WebSocket. Nothing is stored; the engine's data folder is a temporary directory.

Metrics (`score`):
- words: word error rate of the final captions against the reference, split into
  missed (deletions), wrong (substitutions) and extra (insertions). Filler words
  ("uh", "um", "mm-hmm") are dropped from both sides; subtitle writers leave them out.
- bubbles: share of words shown in a caption segment whose other words belong to another
  speaker (two people in one bubble), and how many segments a speaker's turn was cut into.
- speakers: each caption label ("face track 3", "Sam", "Someone") is mapped to the reference
  speaker it carries most words of; speaker accuracy is the share of words whose label maps
  to their own speaker ("Someone" counts as wrong). This is diarization purity, so it can't
  punish a label that sticks to one person across camera cuts.
- latency: from a word's reference time to the first caption carrying it.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "scripts"))

log = logging.getLogger("eval_podcast")

PODCASTS = ROOT / "data" / "podcasts"
FILLERS = {
    "uh", "um", "uhm", "umm", "erm", "er", "ah", "eh", "hm", "hmm", "mm", "mmm",
    "mhm", "mmhmm", "mmhm", "uhhuh", "huh", "ha", "haha", "hahaha", "ooh",
}  # fmt: skip
ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


# ============================================================================ text
def _number_words(n: int) -> list[str]:
    """0..9999 as words ("80" -> ["eighty"]); larger numbers stay digits."""
    if n < 20:
        return [ONES[n]]
    if n < 100:
        return [TENS[n // 10]] + ([ONES[n % 10]] if n % 10 else [])
    if n < 1000:
        return [ONES[n // 100], "hundred"] + (_number_words(n % 100) if n % 100 else [])
    if n < 10000:
        if 1100 <= n < 2100 and n % 100:  # years: 1985 -> nineteen eighty five
            return _number_words(n // 100) + _number_words(n % 100)
        return [ONES[n // 1000], "thousand"] + (_number_words(n % 1000) if n % 1000 else [])
    return [str(n)]


def normalize(text: str) -> list[str]:
    """Lowercase words without punctuation, numbers spelled out, fillers dropped."""
    text = text.lower().replace("’", "'").replace("%", " percent")
    text = re.sub(r"(\d),(\d{3})", r"\1\2", text)
    out: list[str] = []
    for raw in re.split(r"[\s\-–—/]+", text):
        w = re.sub(r"[^a-z0-9']", "", raw).strip("'")
        if not w:
            continue
        m = re.fullmatch(r"(\d+)(s|st|nd|rd|th)?", w)
        if m:
            words = _number_words(int(m.group(1)))
            out.extend(words)
            continue
        if w.replace("'", "") in FILLERS:
            continue
        out.append(w)
    return out


def display_words(text: str) -> list[str]:
    """Words for turn splitting and timing: like normalize(), one entry per spoken word."""
    return normalize(text)


# ============================================================================ reference
_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[.,](\d{3})\s*-->\s*(\d+):(\d\d):(\d\d)[.,](\d{3})")


def _secs(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_vtt(path: Path) -> list[tuple[float, float, list[str]]]:
    """Cues as (start, end, lines), sound descriptions and tags removed."""
    cues, cur = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _TIME.search(line)
        if m:
            g = m.groups()
            cur = (_secs(*g[:4]), _secs(*g[4:]), [])
            cues.append(cur)
            continue
        if cur is None:
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if line:
            cur[2].append(line)
        else:
            cur = None
    return cues


_NEW_TURN = re.compile(r"^\s*(?:-|>>|‐|–)\s*|^\s*[A-Z][A-Z .'\-]{1,30}:\s+")


def reference_words(cues, offset: float, duration: float) -> list[dict]:
    """Every spoken word: text, time (clip seconds, spread over its cue by length) and turn."""
    words: list[dict] = []
    turn = 0
    for c0, c1, lines in cues:
        pieces: list[tuple[bool, str]] = []  # (starts a new turn, text)
        for line in lines:
            new = bool(_NEW_TURN.match(line))
            text = _NEW_TURN.sub("", line, count=1)
            text = re.sub(r"\([^)]*\)|\[[^\]]*\]|♪[^♪]*♪?|♪", " ", text)
            pieces.append((new, text))
        toks: list[tuple[int, str]] = []
        for new, text in pieces:
            ws = display_words(text)
            if new and (words or toks) and ws:
                turn += 1
            toks.extend((turn, w) for w in ws)
        if not toks:
            continue
        span = (c1 - c0) / len(toks)
        for k, (tn, w) in enumerate(toks):
            t = c0 + (k + 0.5) * span - offset
            if 0.0 <= t <= duration:
                words.append({"w": w, "t": round(t, 3), "turn": tn})
    # renumber turns from 0 in this clip
    ids = {tn: k for k, tn in enumerate(dict.fromkeys(w["turn"] for w in words))}
    for w in words:
        w["turn"] = ids[w["turn"]]
    return words


def _kmeans(x: np.ndarray, wts: np.ndarray, k: int) -> np.ndarray:
    """Centroids of k-means on the unit sphere (k-means++ start, weighted, best of 30)."""
    best, best_cost = None, 1e9
    rng = np.random.default_rng(0)
    for _ in range(30):
        cent = [x[rng.choice(len(x), p=wts / wts.sum())]]
        while len(cent) < k:
            d = np.clip(1 - np.max(x @ np.stack(cent).T, axis=1), 0, None)
            p = d * wts
            cent.append(x[rng.choice(len(x), p=p / p.sum())] if p.sum() > 0 else x[0])
        c = np.stack(cent)
        for _ in range(50):
            lab = np.argmax(x @ c.T, axis=1)
            new = np.stack(
                [
                    (x[lab == j] * wts[lab == j, None]).sum(0) if np.any(lab == j) else c[j]
                    for j in range(k)
                ]
            )
            new /= np.linalg.norm(new, axis=1, keepdims=True) + 1e-9
            if np.allclose(new, c):
                break
            c = new
        cost = float(((1 - np.max(x @ c.T, axis=1)) * wts).sum())
        if cost < best_cost:
            best, best_cost = c, cost
    return best


def speaker_track(wav: Path, n_speakers: int, win: float = 1.5, hop: float = 0.25) -> list:
    """Who talks when, for subtitles without turn marks: CAM++ on sliding windows, clustered.

    Returns [t, speaker] every `hop` seconds (speaker None in silence), smoothed over 1 s.
    Coarser than turn labels (a window straddling a change is mixed), so scores built on it
    have a floor of a few percent.
    """
    from attune.audio.voiceprint import CAMExtractor
    from attune.replay.player import read_wav, resample

    pcm, rate = read_wav(str(wav))
    pcm16 = resample(pcm, rate, 16000)
    cam = CAMExtractor(str(ROOT / "models" / "cam++.onnx"))
    n = int(len(pcm16) / 16000 / hop)
    rms = np.array(
        [np.sqrt(np.mean(pcm16[int(k * hop * 16000) : int((k * hop + hop) * 16000)] ** 2) + 1e-12)
         for k in range(n)]
    )  # fmt: skip
    speech = rms > max(np.percentile(rms, 20) * 3, 0.004)
    vecs, idx = [], []
    for k in range(n):
        if not speech[k]:
            continue
        mid = (k + 0.5) * hop
        seg = pcm16[max(0, int((mid - win / 2) * 16000)) : int((mid + win / 2) * 16000)]
        try:
            v = cam(seg)
        except ValueError:
            continue
        vecs.append(v / (np.linalg.norm(v) + 1e-9))
        idx.append(k)
    x = np.stack(vecs)
    c = _kmeans(x, np.ones(len(x)), n_speakers)
    lab = np.full(n, -1)
    lab[idx] = np.argmax(x @ c.T, axis=1)
    out, half = [], int(0.5 / hop)
    for k in range(n):
        if lab[k] < 0:
            out.append([round(k * hop, 3), None])
            continue
        near = lab[max(0, k - half) : k + half + 1]
        near = near[near >= 0]
        out.append([round(k * hop, 3), int(np.bincount(near).argmax())])
    return out


def label_turns(words: list[dict], wav: Path, n_speakers: int) -> dict[str, int | None]:
    """Speaker cluster per turn from CAM++ voice prints of the whole turn (offline)."""
    from attune.audio.voiceprint import CAMExtractor
    from attune.replay.player import read_wav, resample

    pcm, rate = read_wav(str(wav))
    pcm16 = resample(pcm, rate, 16000)
    cam = CAMExtractor(str(ROOT / "models" / "cam++.onnx"))
    spans: dict[int, list[float]] = {}
    for w in words:
        s = spans.setdefault(w["turn"], [w["t"], w["t"]])
        s[0], s[1] = min(s[0], w["t"]), max(s[1], w["t"])
    vecs, keys, weights = [], [], []
    for tn, (a, b) in sorted(spans.items()):
        a, b = a - 0.25, b + 0.25
        if b - a < 1.2:
            continue
        seg = pcm16[max(0, int(a * 16000)) : int(b * 16000)]
        try:
            v = cam(seg)
        except ValueError:
            continue
        vecs.append(v / (np.linalg.norm(v) + 1e-9))
        keys.append(tn)
        weights.append(b - a)
    if len(vecs) < n_speakers:
        return {str(tn): None for tn in spans}
    x = np.stack(vecs)
    best = _kmeans(x, np.array(weights), n_speakers)  # weighted by turn length
    out: dict[str, int | None] = {}
    for tn, (a, b) in sorted(spans.items()):
        if tn in keys:
            out[str(tn)] = int(np.argmax(best @ x[keys.index(tn)]))
            continue
        # a short turn: nearest centroid of its padded audio, if there's enough of it
        mid = (a + b) / 2
        seg = pcm16[max(0, int((mid - 0.8) * 16000)) : int((mid + 0.8) * 16000)]
        try:
            v = cam(seg)
            out[str(tn)] = int(np.argmax(best @ (v / (np.linalg.norm(v) + 1e-9))))
        except ValueError:
            out[str(tn)] = None
    return out


def prepare(args) -> int:
    folder = PODCASTS / args.name
    folder.mkdir(parents=True, exist_ok=True)
    ff = args.ffmpeg
    clip, wav = folder / "clip.mp4", folder / "clip.wav"
    trim = ["-ss", str(args.trim_start)] if args.trim_start else []
    dur = ["-t", str(args.duration)] if args.duration else []
    subprocess.run(
        [ff, "-y", "-loglevel", "error", *trim, "-i", args.video, *dur, "-an", "-r", "30",
         "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-g", "30", str(clip)],
        check=True,
    )  # fmt: skip
    subprocess.run(
        [ff, "-y", "-loglevel", "error", *trim, "-i", args.video, *dur, "-vn", "-ac", "1",
         "-ar", "48000", "-c:a", "pcm_s16le", str(wav)],
        check=True,
    )  # fmt: skip
    import wave

    with wave.open(str(wav), "rb") as w:
        seconds = w.getnframes() / w.getframerate()
    offset = args.offset + (args.trim_start or 0.0)
    words = reference_words(parse_vtt(Path(args.vtt)), offset, seconds)
    n_turns = len({w["turn"] for w in words})
    turn_speakers, track = {}, []
    if args.speakers > 1 and n_turns >= max(4, len(words) // 150):
        turn_speakers = label_turns(words, wav, args.speakers)
    elif args.speakers > 1:  # no turn marks in these subtitles: who talks when, from the audio
        track = speaker_track(wav, args.speakers)
    ref = {
        "source": args.source or Path(args.video).name,
        "offset_s": offset,
        "duration_s": round(seconds, 2),
        "speakers": args.speakers,
        "speaker_names": args.names or [f"S{k}" for k in range(args.speakers)],
        "turn_speakers": turn_speakers,
        "speaker_track": track,
        "words": words,
    }
    (folder / "ref.json").write_text(json.dumps(ref, indent=1), encoding="utf-8")
    print(f"{folder}: {seconds:.1f} s, {len(words)} reference words, "
          f"{len({w['turn'] for w in words})} turns")  # fmt: skip
    show_turns(ref)
    return 0


def asd_truth(args) -> int:
    """Who talks when, for a clip whose people stay in fixed places (a static two-shot).

    Light-ASD scores every face over the whole clip at once (windows of 1-6 s, as its own
    Columbia test does; the live engine only ever sees the last 1.5 s), faces are given to
    people by where they sit (`--regions`), and the voice clusters of `speaker_track` must
    agree: a moment is truth only when the face model and the voices name the same person.
    Writes `speaker_track` (and `speaker_names`) into ref.json.
    """
    import cv2
    import torch  # noqa: F401  (before onnxruntime, see vision/runtime.py)
    from eval_talker import _full_scores, _track_boxes
    from attune.replay.player import read_wav, resample
    from attune.vision.asd import LightASD, asd_crop
    from attune.vision.settings import VisionSettings

    folder = PODCASTS / args.name
    ref = json.loads((folder / "ref.json").read_text(encoding="utf-8"))
    names = [r.split(":")[0] for r in args.regions]
    regions = [[float(v) for v in r.split(":")[1].split(",")] for r in args.regions]
    s = VisionSettings()
    ns = argparse.Namespace(
        det_size=640, det_device=args.device, threads=4, min_face_px=40, until=None
    )
    cap = cv2.VideoCapture(str(folder / "clip.mp4"))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    fw, fh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    boxes = _track_boxes(ns, s, cap, fps, fw)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    faces: dict[int, list] = defaultdict(list)
    where: dict[int, Counter] = defaultdict(Counter)
    for i, rows in enumerate(boxes):
        ok, image = cap.read()
        if not ok:
            break
        for tid, kbox, _ in rows:
            cx, cy = (kbox[0] + kbox[2]) / 2 / fw, (kbox[1] + kbox[3]) / 2 / fh
            who = next(
                (k for k, r in enumerate(regions) if r[0] <= cx <= r[2] and r[1] <= cy <= r[3]),
                None,
            )
            crop = asd_crop(image, kbox)
            if crop is not None and who is not None:
                faces[tid].append((i / fps, crop))
                where[tid][who] += 1
    model = LightASD(str(ROOT / s.asd_model), args.device)
    pcm, rate = read_wav(str(folder / "clip.wav"))
    pcm16 = resample(pcm, rate, 16000)
    hop = 0.25
    n = int(len(pcm16) / 16000 / hop)
    score = np.full((len(names), n), -np.inf)
    for tid, items in faces.items():
        got = _full_scores(model, items, pcm16, 0.0, ((1, 3), (2, 3), (3, 2), (4, 1), (5, 1), (6, 1)))
        if got is None:
            continue
        who = where[tid].most_common(1)[0][0]
        for t, v in got:
            k = int(t / hop)
            if 0 <= k < n:
                score[who, k] = max(score[who, k], v) if np.isfinite(score[who, k]) else v
    # smooth over 0.5 s, then a clear winner: talking (> 0) and ahead of everyone else by a margin
    face = [None] * n
    for k in range(n):
        col = score[:, max(0, k - 1) : k + 2]
        col = np.where(np.isfinite(col), col, np.nan)
        with np.errstate(all="ignore"):
            m = np.nanmean(col, axis=1)
        if np.all(np.isnan(m)):
            continue
        m = np.nan_to_num(m, nan=-10.0)
        order = np.argsort(m)[::-1]
        if m[order[0]] > 0 and (len(m) < 2 or m[order[0]] - m[order[1]] >= args.margin):
            face[k] = int(order[0])
    # Each person's voice, learnt from 1.5 s windows the face model is sure of; every window
    # is then checked against those voices (leaving its own print out of its person's mean).
    from attune.audio.voiceprint import CAMExtractor

    cam = CAMExtractor(str(ROOT / "models" / "cam++.onnx"))
    vec: dict[int, np.ndarray] = {}
    for k in range(n):
        if face[k] is None:
            continue
        mid = (k + 0.5) * hop
        seg = pcm16[max(0, int((mid - 0.75) * 16000)) : int((mid + 0.75) * 16000)]
        try:
            v = cam(seg)
        except ValueError:
            continue
        vec[k] = v / (np.linalg.norm(v) + 1e-9)
    sums = {p: np.zeros(len(next(iter(vec.values())))) for p in range(len(names))}
    counts = Counter()
    for k, v in vec.items():
        sums[face[k]] += v
        counts[face[k]] += 1
    track, both, faced = [], 0, 0
    for k in range(n):
        f = face[k]
        ok = False
        if f is not None and k in vec:
            faced += 1
            sims = []
            for p in range(len(names)):
                s_ = sums[p] - (vec[k] if p == f else 0)
                c_ = counts[p] - (p == f)
                sims.append(float(vec[k] @ s_) / max(np.linalg.norm(s_), 1e-9) if c_ > 0 else -1.0)
            ok = int(np.argmax(sims)) == f
            both += ok
        track.append([round(k * hop, 3), f if ok else None])
    to_person = {names[p]: counts[p] for p in range(len(names))}
    ref["regions"] = regions
    ref["speaker_names"] = names
    if ref.get("turn_speakers"):
        # the subtitles' turns stay (exact in time); their voice clusters get the names of
        # the places the face model heard them talk from
        votes: dict[int, Counter] = defaultdict(Counter)
        for w in ref["words"]:
            c = ref["turn_speakers"].get(str(w["turn"]))
            k = int(w["t"] / hop)
            if c is not None and 0 <= k < n and track[k][1] is not None:
                votes[c][track[k][1]] += 1
        to_name = {c: v.most_common(1)[0][0] for c, v in votes.items()}
        ref["turn_speakers"] = {t: to_name.get(c) for t, c in ref["turn_speakers"].items()}
        ref["speaker_track"] = []
        ref["truth"] = "subtitle turns, CAM++ voice clusters named by whole-clip Light-ASD"
        print(f"voice cluster -> seat: { {c: names[p] for c, p in to_name.items()} }")
    else:
        ref["speaker_track"] = track
        ref["turn_speakers"] = {}
        ref["truth"] = "light-asd (whole clip) + CAM++ voice clusters"
    (folder / "ref.json").write_text(json.dumps(ref, indent=1), encoding="utf-8")
    talk = sum(1 for _, v in track if v is not None)
    print(
        f"{args.name}: {talk * hop:.0f} s of speech with a speaker ({faced * hop:.0f} s named by "
        f"the face model, {100 * both / max(faced, 1):.0f}% of it confirmed by the voices); "
        f"voice cluster -> person {to_person}"
    )
    print("".join("." if v is None else "abcdefgh"[v] for _, v in track))
    return 0


def show_turns(ref: dict, limit: int = 60) -> None:
    """Each turn with its speaker cluster, so a person can check the labels."""
    by_turn: dict[int, list[dict]] = defaultdict(list)
    for w in ref["words"]:
        by_turn[w["turn"]].append(w)
    names = ref.get("speaker_names") or []
    for tn, ws in list(sorted(by_turn.items()))[:limit]:
        sp = ref.get("turn_speakers", {}).get(str(tn))
        who = names[sp] if sp is not None and sp < len(names) else "?"
        text = " ".join(w["w"] for w in ws)
        print(f"  {ws[0]['t']:6.1f}s  turn {tn:3d}  {who:>8s}: {text[:110]}")


# ============================================================================ engine run
def run(args) -> int:
    from eval_talker import Truth, engine_run

    folder = PODCASTS / args.name
    ref = json.loads((folder / "ref.json").read_text(encoding="utf-8"))
    args.video, args.wav = str(folder / "clip.mp4"), str(folder / "clip.wav")
    for key in ("no_asd", "cpu", "threads", "det_size", "tail"):
        if not hasattr(args, key):
            setattr(args, key, None)
    args.threads = args.threads or 4
    args.det_size = args.det_size or 640
    args.tail = 15.0 if args.tail is None else args.tail
    if getattr(args, "boost", False) and sys.platform == "win32":
        # above-normal priority for this process only: a laptop shared with other jobs
        # otherwise starves vision (2-15 fps) and the scores measure the load, not the engine
        import ctypes

        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x8000)
    truth = Truth(people={}, intervals=[])
    got = engine_run(args, truth)
    got["name"] = args.name
    got["settings"] = args.set or []
    got["ref"] = ref
    out = Path(args.out or folder / "run.json")
    out.write_text(json.dumps(got), encoding="utf-8")
    log.info("recorded %d messages -> %s", len(got["messages"]), out)
    report(score(got), out.with_suffix(".report.txt"))
    return 0


# ============================================================================ scoring
def align(ref: list[str], hyp: list[str]) -> list[tuple[int | None, int | None]]:
    """Levenshtein alignment: pairs (ref index, hyp index); None is a gap."""
    n, m = len(ref), len(hyp)
    d = np.zeros((n + 1, m + 1), dtype=np.int32)
    d[:, 0] = np.arange(n + 1)
    d[0, :] = np.arange(m + 1)
    hyp_arr = np.array(hyp, dtype=object)
    for i in range(1, n + 1):
        sub = d[i - 1, :-1] + (hyp_arr != ref[i - 1])
        row = np.empty(m + 1, dtype=np.int32)
        row[0] = i
        best = np.minimum(sub, d[i - 1, 1:] + 1)
        for j in range(1, m + 1):  # insertions run left to right
            row[j] = min(best[j - 1], row[j - 1] + 1)
        d[i] = row
    pairs = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i, j] == d[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]):
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and d[i, j] == d[i - 1, j] + 1:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    return pairs[::-1]


def _label(spk: dict) -> str:
    kind = spk.get("kind") or "someone"
    if kind in ("face", "probable_face"):
        return spk.get("person_id") or f"track-{spk.get('track_id')}"
    if kind == "offscreen":
        return spk.get("person_id") or spk.get("label") or "offscreen"
    return kind


def captions_of(run: dict) -> tuple[dict[str, dict], dict[str, float]]:
    """The last version of every caption segment still standing, and when each word first showed."""
    caps: dict[str, dict] = {}
    first: dict[tuple, float] = {}
    for recv_t, msg in run["messages"]:
        kind = msg.get("type")
        if kind == "caption":
            caps[msg["utt_id"]] = msg
            for w in msg.get("words") or []:
                first.setdefault((round(float(w[1]), 2), w[0].lower()), recv_t)
        elif kind == "caption_retract":
            caps.pop(msg["utt_id"], None)
    return caps, first


def score(run: dict) -> dict:
    ref = run["ref"]
    t0 = run["t0"]
    caps, first = captions_of(run)
    turn_spk = {int(k): v for k, v in (ref.get("turn_speakers") or {}).items()}

    # hypothesis words in time order, each with its segment, speaker label and finality
    hyp: list[dict] = []
    for seg_id, msg in caps.items():
        spk = _label(msg.get("speaker") or {})
        for w in msg.get("words") or []:
            ws, we = float(w[1]), float(w[2])
            for k, nw in enumerate(normalize(w[0])):
                hyp.append({
                    "w": nw, "t": (ws + we) / 2 - t0, "seg": seg_id, "spk": spk,
                    "tid": (msg.get("speaker") or {}).get("track_id"),
                    "final": bool(msg.get("final")),
                    "shown": first.get((round(ws, 2), w[0].lower()), None),
                })  # fmt: skip
    hyp.sort(key=lambda h: (h["t"], h["seg"]))
    rw = ref["words"]
    pairs = align([w["w"] for w in rw], [h["w"] for h in hyp])

    n_ref = len(rw)
    subs = sum(1 for i, j in pairs if i is not None and j is not None and rw[i]["w"] != hyp[j]["w"])
    dels = sum(1 for i, j in pairs if j is None)
    ins = sum(1 for i, j in pairs if i is None)
    out: dict = {
        "name": run.get("name"),
        "settings": run.get("settings"),
        "ref_words": n_ref,
        "hyp_words": len(hyp),
        "wer": round((subs + dels + ins) / max(n_ref, 1), 4),
        "missed": round(dels / max(n_ref, 1), 4),
        "wrong": round(subs / max(n_ref, 1), 4),
        "extra": round(ins / max(n_ref, 1), 4),
        "not_final": sum(1 for h in hyp if not h["final"]),
    }

    # longest runs of missed words: where whole sentences went missing
    gaps, cur = [], []
    for i, j in pairs:
        if j is None and i is not None:
            cur.append(i)
        elif i is not None:
            if len(cur) >= 4:
                gaps.append(cur)
            cur = []
    if len(cur) >= 4:
        gaps.append(cur)
    out["missed_runs"] = [
        {"t": rw[g[0]]["t"], "n": len(g), "text": " ".join(rw[k]["w"] for k in g)[:160]}
        for g in sorted(gaps, key=len, reverse=True)[:8]
    ]

    # matched words: reference speaker/turn next to the caption's segment and label
    track = ref.get("speaker_track") or []
    track_t = np.array([t for t, _ in track]) if track else np.zeros(0)

    def ref_speaker(r: dict, h: dict) -> int | None:
        if turn_spk:
            return turn_spk.get(r["turn"])
        if not len(track_t):
            return None
        # the audio's speaker track at the caption word's own (exact) time
        k = int(np.clip(np.searchsorted(track_t, h["t"]), 0, len(track) - 1))
        return track[k][1]

    matched = [
        ({**rw[i], "spk": ref_speaker(rw[i], hyp[j])}, hyp[j])
        for i, j in pairs
        if i is not None and j is not None
    ]
    spk_of = lambda r: r["spk"]  # noqa: E731

    # bubbles: words that share a segment with another speaker's words
    seg_ref: dict[str, Counter] = defaultdict(Counter)
    for r, h in matched:
        s = spk_of(r)
        seg_ref[h["seg"]][s if s is not None else f"turn{r['turn']}"] += 1
    mixed = sum(sum(c.values()) - max(c.values()) for c in seg_ref.values())
    out["mixed_bubble_words"] = round(mixed / max(len(matched), 1), 4)
    out["segments"] = len(seg_ref)
    turn_segs: dict[int, set] = defaultdict(set)
    for r, h in matched:
        turn_segs[r["turn"]].add(h["seg"])
    long_turns = [t for t in turn_segs if sum(1 for w in rw if w["turn"] == t) >= 8]
    out["segments_per_turn"] = round(
        statistics.mean(len(turn_segs[t]) for t in long_turns), 2
    ) if long_turns else None

    # speakers: label -> majority reference speaker (purity)
    lab_ref: dict[str, Counter] = defaultdict(Counter)
    for r, h in matched:
        s = spk_of(r)
        if s is not None:
            lab_ref[h["spk"]][s] += 1
    mapping = {
        lab: c.most_common(1)[0][0]
        for lab, c in lab_ref.items()
        if lab not in ("someone", "you", "you_typed")
    }
    scored = [(r, h) for r, h in matched if spk_of(r) is not None]
    right = sum(1 for r, h in scored if mapping.get(h["spk"]) == spk_of(r))
    out["speaker_accuracy"] = round(right / max(len(scored), 1), 4)
    out["someone_share"] = round(
        sum(1 for _, h in scored if h["spk"] == "someone") / max(len(scored), 1), 4
    )
    names = ref.get("speaker_names") or []
    out["labels"] = {
        lab: {names[k] if k < len(names) else str(k): n for k, n in c.most_common()}
        for lab, c in sorted(lab_ref.items(), key=lambda kv: -sum(kv[1].values()))
    }
    per: dict = {}
    for s in sorted({spk_of(r) for r, _ in scored}):
        mine = [(r, h) for r, h in scored if spk_of(r) == s]
        ok = sum(1 for r, h in mine if mapping.get(h["spk"]) == s)
        per[names[s] if s < len(names) else str(s)] = {
            "words": len(mine), "right": round(ok / len(mine), 3),
            "someone": round(sum(1 for _, h in mine if h["spk"] == "someone") / len(mine), 3),
        }  # fmt: skip
    out["per_speaker"] = per

    # on the right face: for clips whose people sit still (ref "regions"), the face a word
    # was drawn on must be the seat of the person who said it
    regions = ref.get("regions")
    if regions:
        fw, fh = run.get("frame_size") or [1280, 720]
        scenes = [(float(m.get("t", 0)) - t0, m) for _, m in run["messages"] if m.get("type") == "scene"]
        st = np.array([t for t, _ in scenes]) if scenes else np.zeros(0)

        def seat(tid, t: float):
            if tid is None or not len(st):
                return None
            k = int(np.clip(np.searchsorted(st, t), 0, len(st) - 1))
            for j in (k, max(k - 1, 0), min(k + 1, len(st) - 1)):
                for f in scenes[j][1].get("faces") or []:
                    if f.get("track_id") == tid:
                        x, y, w, h = f["box"]
                        cx, cy = (x + w / 2) / fw, (y + h / 2) / fh
                        return next(
                            (p for p, r in enumerate(regions) if r[0] <= cx <= r[2] and r[1] <= cy <= r[3]),
                            None,
                        )
            return None

        on_face = [(r, h) for r, h in scored if h["tid"] is not None]
        good = sum(1 for r, h in on_face if seat(h["tid"], h["t"]) == spk_of(r))
        out["right_face"] = round(good / max(len(scored), 1), 4)
        out["wrong_face"] = round((len(on_face) - good) / max(len(scored), 1), 4)
    # speaker changes: at a reference change between two words, is the caption label different too?
    changes = hits = 0
    known = [(r, h) for r, h in matched if spk_of(r) is not None]
    for (r1, h1), (r2, h2) in zip(known, known[1:]):
        s1, s2 = spk_of(r1), spk_of(r2)
        if s1 == s2 or h2["t"] - h1["t"] > 3.0:
            continue
        changes += 1
        hits += h1["seg"] != h2["seg"]
    out["speaker_changes"] = changes
    out["changes_split"] = round(hits / changes, 3) if changes else None

    # latency: reference word time -> first shown
    lat = [
        h["shown"] - t0 - r["t"]
        for r, h in matched
        if h["shown"] is not None and r["w"] == h["w"]
    ]
    if lat:
        out["latency_p50_s"] = round(float(np.percentile(lat, 50)), 2)
        out["latency_p90_s"] = round(float(np.percentile(lat, 90)), 2)

    # a readable transcript: caption segments in order, with the reference speaker mix
    lines = []
    for seg_id, msg in sorted(caps.items(), key=lambda kv: float((kv[1].get("words") or [[0, 0]])[0][1])):
        words = msg.get("words") or []
        if not words:
            continue
        mix = seg_ref.get(seg_id, Counter())
        mix_txt = ",".join(
            f"{names[k] if isinstance(k, int) and k < len(names) else k}:{n}" for k, n in mix.most_common()
        )
        lines.append(
            f"{float(words[0][1]) - t0:6.1f}s [{_label(msg.get('speaker') or {}):>12s}] "
            f"({mix_txt}) {msg.get('text', '')}"
        )
    out["transcript"] = lines
    return out


def report(m: dict, path: Path | None = None) -> None:
    rows = [
        f"== {m['name']} {' '.join(m.get('settings') or [])}",
        f"words: {m['ref_words']} said, {m['hyp_words']} captioned; WER {m['wer']:.1%} "
        f"(missed {m['missed']:.1%}, wrong {m['wrong']:.1%}, extra {m['extra']:.1%}); "
        f"{m['not_final']} words never final",
        f"bubbles: {m['segments']} segments; {m['mixed_bubble_words']:.1%} of words share a bubble "
        f"with another speaker; {m['segments_per_turn']} segments per long turn; "
        f"{m['changes_split']} of {m['speaker_changes']} speaker changes start a new bubble",
        f"speakers: accuracy {m['speaker_accuracy']:.1%}, 'Someone' {m['someone_share']:.1%}",
    ]
    if "right_face" in m:
        rows.append(
            f"  on the right face {m['right_face']:.1%}, on a wrong face {m['wrong_face']:.1%} "
            "(of all scored words; the rest is Someone or off-screen)"
        )
    for who, v in m["per_speaker"].items():
        rows.append(f"  {who:>10s}: {v['words']} words, right {v['right']:.1%}, Someone {v['someone']:.1%}")
    rows.append("  labels: " + "; ".join(f"{k} -> {v}" for k, v in list(m["labels"].items())[:12]))
    if "latency_p50_s" in m:
        rows.append(f"latency: word -> first shown p50 {m['latency_p50_s']} s, p90 {m['latency_p90_s']} s")
    if m["missed_runs"]:
        rows.append("longest missed runs:")
        rows += [f"  {g['t']:6.1f}s ({g['n']} words) {g['text']}" for g in m["missed_runs"]]
    text = "\n".join(rows)
    print(text)
    if path:
        path.write_text(text + "\n\n" + "\n".join(m["transcript"]) + "\n", encoding="utf-8")
        print(f"(transcript: {path})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="make clip.mp4, clip.wav and ref.json")
    p.add_argument("name")
    p.add_argument("--video", required=True, help="downloaded clip (video + audio)")
    p.add_argument("--vtt", required=True, help="manual subtitles of the whole episode")
    p.add_argument("--offset", type=float, default=0.0, help="episode time where the video starts, s")
    p.add_argument("--trim-start", type=float, default=0.0, help="skip this much of the video, s")
    p.add_argument("--duration", type=float, help="keep this many seconds")
    p.add_argument("--speakers", type=int, default=2, help="how many people talk")
    p.add_argument("--names", nargs="+", help="speaker names, in cluster order (check the print)")
    p.add_argument("--source", help="where the clip came from (URL)")
    p.add_argument("--ffmpeg", default="ffmpeg")
    r = sub.add_parser("run", help="play the clip through the engine and score it")
    r.add_argument("name")
    r.add_argument("--out", help="run JSON (default data/podcasts/<name>/run.json)")
    r.add_argument("--port", type=int, default=8017)
    r.add_argument("--config", help="engine config (default: config/attune.toml if present)")
    r.add_argument("--set", action="append", help="config override, e.g. audio.asr_chunk_ms=160")
    r.add_argument("--cpu", action="store_true", help="vision and Light-ASD on the CPU")
    r.add_argument("--no-asd", action="store_true")
    r.add_argument("--boost", action="store_true", help="above-normal CPU priority (Windows)")
    r.add_argument(
        "--tail", type=float, default=15.0,
        help="keep recording this long after the clip (a busy laptop needs time to catch up)",
    )  # fmt: skip
    s = sub.add_parser("score", help="score a recorded run")
    s.add_argument("runs", nargs="+")
    s.add_argument("--json", action="store_true", help="print the metrics as JSON")
    t = sub.add_parser("turns", help="print a clip's reference turns and speakers")
    t.add_argument("name")
    a = sub.add_parser("truth", help="who talks when, from Light-ASD + voices (static shots)")
    a.add_argument("name")
    a.add_argument(
        "--regions", nargs="+", required=True,
        help="name:x0,y0,x1,y1 (fractions of the frame) for each person, e.g. Rhett:0,0,0.5,1",
    )  # fmt: skip
    a.add_argument("--device", default="cuda")
    a.add_argument("--margin", type=float, default=1.0, help="logit lead over the next face")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.cmd == "prepare":
        return prepare(args)
    if args.cmd == "run":
        return run(args)
    if args.cmd == "truth":
        return asd_truth(args)
    if args.cmd == "turns":
        show_turns(json.loads((PODCASTS / args.name / "ref.json").read_text(encoding="utf-8")), 10_000)
        return 0
    for path in args.runs:
        got = json.loads(Path(path).read_text(encoding="utf-8"))
        current = PODCASTS / str(got.get("name")) / "ref.json"
        if current.is_file():  # the answer key may have been improved since the run
            got["ref"] = json.loads(current.read_text(encoding="utf-8"))
        m = score(got)
        if args.json:
            print(json.dumps({k: v for k, v in m.items() if k != "transcript"}, indent=1))
        else:
            report(m, Path(path).with_suffix(".report.txt"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
