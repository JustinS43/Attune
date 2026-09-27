"""Who's-talking evaluation on a recorded clip: are words credited to the right face?

Section 1 - Vision. TODO: V-22 (reusable for later two-person clips).

    python scripts/eval_talker.py clip.mp4 clip.wav truth.json            # engine run + metrics
    python scripts/eval_talker.py clip.mp4 clip.wav truth.json --no-asd   # the lip-score path only
    python scripts/eval_talker.py clip.mp4 clip.wav truth.json --offline  # Light-ASD scores alone

Engine mode starts the whole engine in this process (`--port`, default 8007) on the
clip, plays the WAV as the microphone, and records `caption`, `scene` and `status`
over the WebSocket like a console page (and each face's scores from the bus). The
camera loops the video from when vision starts, so the WAV is started exactly on a
loop boundary of the video (on the shared clock): lips and sound line up as they did
when the clip was recorded. The engine's data folder is a temporary directory that is
deleted afterwards, so nothing is stored. `--cpu` keeps the GPU free for a live engine
(vision and Light-ASD on the CPU, no LLM, no sound alerts; `--set
vision.asd_device="cuda"` puts Light-ASD alone back on the GPU); `--seconds` prints
frames, scores and the lit speaker per second.

Offline mode runs the face finder, the tracker and Light-ASD frame by frame outside
the engine, and prints the per-second speaking score of each truth person (plus the
lip score for comparison) and how well talking and silent seconds separate.
`--crop-fps` replays the crops as a slower vision loop would deliver them.

truth.json (times in clip seconds; `region` is where a person's face is, as fractions
of the frame [x0, y0, x1, y1]; a person without one matches any face, which suits a
one-person clip):

    {
      "people": {"user": {"region": [0, 0, 1, 1]}},
      "intervals": [
        {"t0": 0.0, "t1": 25.0, "who": "offscreen"},
        {"t0": 25.0, "t1": 45.0, "who": "user"}
      ]
    }

`who` is a person from `people` (a visible face), or anything else ("offscreen",
"room", ...) for speech from nobody on camera; `null` marks silence. Words outside
every interval are not scored. An interval may carry a `"tag"` (for example
`"mouth hidden"`): its words are reported on their own line, and offline mode leaves
it out of the talking/silent separation.

Metrics:
- background words on a face: share of words said by nobody visible that were
  credited to a visible face (target about 0);
- person words on their face: share of a person's words credited to their face;
- first-caption latency: from the first word's start to the first caption carrying it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import statistics
import sys
import tempfile
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

log = logging.getLogger("eval_talker")


def _lip_lines() -> tuple[float, float]:
    """The fusion's lip-score lines (probable, talking) from its default settings."""
    from attune.vision.settings import FusionSettings

    s = FusionSettings()
    return s.lip_uncertain, s.lip_talking


# ============================================================================ truth
@dataclass
class Truth:
    people: dict[str, list[float] | None]  # name -> region (fractions) or None
    intervals: list[tuple[float, float, str | None, str]]  # t0, t1, who, tag

    @classmethod
    def load(cls, path: str) -> Truth:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        people = {
            k: (v or {}).get("region") for k, v in (raw.get("people") or {}).items()
        }
        ivs = [
            (float(i["t0"]), float(i["t1"]), i.get("who"), i.get("tag") or "")
            for i in raw["intervals"]
        ]
        return cls(people, sorted(ivs, key=lambda iv: iv[0]))

    def who(self, t: float) -> tuple[bool, str | None]:
        """(covered, who) at clip time t; who None is silence."""
        for t0, t1, who, _ in self.intervals:
            if t0 <= t < t1:
                return True, who
        return False, None

    def tag(self, t: float) -> str:
        """The interval's tag at t (e.g. "mouth hidden"), or ""."""
        return next((tag for t0, t1, _, tag in self.intervals if t0 <= t < t1), "")

    def visible(self, who: str | None) -> bool:
        return who is not None and who in self.people

    def person_for_box(self, box_frac: list[float]) -> str | None:
        """Which truth person a face box (x, y, w, h as fractions) belongs to."""
        cx, cy = box_frac[0] + box_frac[2] / 2, box_frac[1] + box_frac[3] / 2
        open_people = [p for p, r in self.people.items() if r is None]
        for p, r in self.people.items():
            if r is not None and r[0] <= cx <= r[2] and r[1] <= cy <= r[3]:
                return p
        return open_people[0] if len(open_people) == 1 else None


# ============================================================================ offline
def offline(args, truth: Truth) -> None:
    """Light-ASD (and the V-08 lip score) frame by frame on the clip, outside the engine."""
    import cv2
    import torch  # before onnxruntime (cuDNN, see vision/runtime.py)
    from attune.replay.player import read_wav, resample
    from attune.vision.asd import ActiveSpeakerDetector, LightASD, asd_crop
    from attune.vision.mouth import LipHistory, MouthMeter
    from attune.vision.settings import VisionSettings

    s = VisionSettings()
    torch.set_num_threads(
        args.threads
    )  # this process only; keeps a live engine responsive
    model = LightASD(str(ROOT / args.model), args.device)
    pcm, sr = read_wav(args.wav)
    pcm16 = resample(pcm, sr, 16000)
    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # 1) face boxes for every frame: the face finder and tracker, or a cache of their boxes
    if args.boxes and os.path.exists(args.boxes):
        with open(args.boxes, encoding="utf-8") as fh:
            boxes = json.load(fh)
        log.info("face boxes from %s", args.boxes)
    else:
        boxes = _track_boxes(args, s, cap, fps, frame_w)
        if args.boxes:
            with open(args.boxes, "w", encoding="utf-8") as fh:
                json.dump(boxes, fh)
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    # 2) mouth crops and lip ratios (CPU), from those boxes
    mouth = None if args.quick else MouthMeter(str(ROOT / s.landmarker_model))
    faces: dict[int, list] = defaultdict(list)  # tid -> [(t, crop, person)]
    lips: dict[int, LipHistory] = defaultdict(lambda: LipHistory(1.0))
    lip_scores: dict[int, list] = defaultdict(list)  # tid -> [(t, score)]
    t_end = args.until if args.until else 1e9
    for i, rows in enumerate(boxes):
        ok, image = cap.read()
        t = i / fps
        if not ok or t > t_end:
            break
        for tid, kbox, dbox in rows:
            x1, y1, x2, y2 = kbox
            box_frac = [
                x1 / frame_w,
                y1 / frame_h,
                (x2 - x1) / frame_w,
                (y2 - y1) / frame_h,
            ]
            crop = asd_crop(image, kbox)
            if crop is not None:
                faces[tid].append((t, crop, truth.person_for_box(box_frac)))
            if mouth is None:
                continue
            ratio = mouth.measure(image, np.array(dbox))
            if ratio is not None:
                lips[tid].add(t, ratio)
            lip_scores[tid].append((t, lips[tid].score(t)))
    if mouth is not None:
        mouth.close()

    offset = args.av_offset
    if args.offsets:
        _offset_scan(args, model, faces, pcm16, truth)

    # 3) whole-clip scores, as Light-ASD's Columbia_test.py does: windows of 1-6 s averaged,
    # weighted like its duration set {1,1,1,2,2,2,3,3,4,5,6} (repeats give the same numbers)
    full: dict[int, list] = {}
    for tid, items in [] if args.quick else faces.items():
        got = _full_scores(
            model,
            items,
            pcm16,
            offset,
            ((1, 3), (2, 3), (3, 2), (4, 1), (5, 1), (6, 1)),
        )
        if got is not None:
            full[tid] = got

    # 3) the engine's own streaming path, replayed in simulated time
    asd = ActiveSpeakerDetector(
        model,
        rate_hz=args.rate,
        window_s=args.window,
        score_s=args.score_s,
        max_gap_s=args.max_gap,
        min_fps=args.min_fps,
        av_offset_s=offset,
    )
    stream: dict[int, list] = defaultdict(list)
    events = [
        (it[0], 1, tid, it[1])
        for tid, items in faces.items()
        for it in _thin(items, args.crop_fps)
    ]
    events += [(k / 100, 0, None, None) for k in range(int(len(pcm16) / 160))]
    events.sort(key=lambda e: (e[0], e[1]))
    next_step, ms = 0.0, []
    for t, kind, tid, crop in events:
        if kind == 0:
            a = round(t * 16000)
            asd.add_audio(t, pcm16[a : a + 160])
        else:
            asd.add_face(tid, t, crop)
        if t >= next_step:
            next_step = t + 1.0 / args.rate
            t0 = time.perf_counter()
            if asd.step():
                ms.append(1000 * (time.perf_counter() - t0))
            for ftid in list(asd.faces):
                sc = asd.score(ftid, t)
                if sc is not None:
                    stream[ftid].append((t, sc))

    # 4) every face, then a per-second table for each truth person
    person_of = {
        tid: next((p for _, _, p in items if p), None) for tid, items in faces.items()
    }
    print("\nPer face (streaming Light-ASD logit; lip score V-08)")
    for tid, items in sorted(faces.items()):
        sc = np.array([v for _, v in stream.get(tid, [])])
        lp = np.array([v for _, v in lip_scores.get(tid, [])])
        span = f"{items[0][0]:5.1f}-{items[-1][0]:5.1f}s"
        if len(sc):
            asd_txt = f"ASD mean {sc.mean():5.2f} max {sc.max():5.2f} > 0 in {100 * np.mean(sc > 0):3.0f}%"
        else:
            asd_txt = "ASD -"
        line = _lip_lines()[1]
        lip_txt = (
            f"lip mean {lp.mean():.3f} >= {line:g} in {100 * np.mean(lp >= line):3.0f}%"
            if len(lp)
            else "lip -"
        )
        print(f"  track {tid:3d} {span} ({len(items)} crops) {asd_txt}; {lip_txt}")

    def per_second(series: dict[int, list], who: str) -> dict[int, float]:
        buckets: dict[int, list] = defaultdict(list)
        for tid, pts in series.items():
            if person_of.get(tid) != who:
                continue
            for t, v in pts:
                buckets[int(t)].append(v)
        return {sec: float(np.mean(v)) for sec, v in buckets.items()}

    for who in truth.people:
        f, st, lp = (
            per_second(full, who),
            per_second(stream, who),
            per_second(lip_scores, who),
        )
        print(
            f"\nPer-second scores for '{who}' (Light-ASD logit: > 0 talking; lip score: V-08)"
        )
        print(" sec  truth        ASD-full  ASD-stream  lip-score")
        for sec in range(math.ceil(len(pcm16) / 16000)):
            _, label = truth.who(sec + 0.5)
            cells = [f"{d[sec]:9.2f}" if sec in d else "        -" for d in (f, st)] + [
                f"{lp[sec]:10.3f}" if sec in lp else "         -"
            ]
            print(f"{sec:4d}  {label!s:11s} {cells[0]} {cells[1]}   {cells[2]}")
        for name, series in (
            ("ASD-full", full),
            ("ASD-stream", stream),
            ("lip-score", lip_scores),
        ):
            _separation(name, series, person_of, who, truth)

    if ms:
        print(
            f"\nstreaming: {len(ms)} rounds, {statistics.mean(ms):.1f} ms mean "
            f"(p95 {np.percentile(ms, 95):.1f} ms) per round incl. MFCC; window {args.window} s, "
            f"device {model.device}; GPU memory reserved {model.gpu_memory_mb() or 0:.0f} MB"
        )
    if args.sheet:
        _sheet(faces, args.sheet, args.sheet_range)


def _thin(items: list, fps: float | None) -> list:
    """Keep at most `fps` crops a second, like a vision loop that runs slower than the video."""
    if not fps:
        return items
    kept, last = [], -1e9
    for it in items:
        if it[0] >= last + 1.0 / fps - 1e-3:
            kept.append(it)
            last = it[0]
    return kept


def _track_boxes(args, s, cap, fps: float, frame_w: int) -> list:
    """Per frame: [track_id, filtered box, detected box] of every face seen (no images kept)."""
    from attune.vision.detector import FaceDetector
    from attune.vision.tracker import FaceTracker

    det = FaceDetector(
        str(ROOT / s.det_model),
        args.det_size,
        s.det_low_score,
        s.nms,
        s.min_face_px,
        use_gpu=args.det_device == "cuda",
    )
    if args.det_device == "cpu":  # the default CPU session takes every core; cap it
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = args.threads
        opts.log_severity_level = 3
        det.session = ort.InferenceSession(
            str(ROOT / s.det_model),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
    tracker = FaceTracker(s.det_score, s.track_survive_s, s.lost_list_s, s.edge_frac)
    out, i, t_wall = [], 0, time.perf_counter()
    t_end = args.until if args.until else 1e9
    while True:
        ok, image = cap.read()
        if not ok or i / fps > t_end:
            break
        upd = tracker.update(det.detect(image), i / fps, frame_w)
        rows = []
        for tr in upd.active:
            kbox = [round(float(v), 1) for v in tr.kf.box]
            if tr.seen and kbox[2] - kbox[0] >= args.min_face_px:
                rows.append(
                    [tr.track_id, kbox, [round(float(v), 1) for v in tr.det.box]]
                )
        out.append(rows)
        i += 1
        if i % 100 == 0:
            log.info(
                "faces: %d frames, %.0f ms/frame",
                i,
                1000 * (time.perf_counter() - t_wall) / i,
            )
    log.info("faces found in %d frames in %.0f s", i, time.perf_counter() - t_wall)
    return out


def _full_scores(model, items, pcm16, offset: float, durations) -> list | None:
    """Light-ASD over a whole track, cut into windows of each duration and averaged."""
    from attune.vision.asd import FPS, mfcc

    times = np.array([it[0] for it in items])
    if times[-1] - times[0] < 1.0:
        return None
    n = int((times[-1] - times[0]) * FPS)
    grid = times[0] + np.arange(n) / FPS
    idx = np.clip(np.searchsorted(times, grid), 0, len(times) - 1)
    video = np.stack([items[k][1] for k in idx]).astype(np.float32)
    a0 = round((times[0] + offset) * 16000)
    seg = pcm16[max(a0, 0) : a0 + n * 640 + 240]
    if a0 < 0:
        seg = np.concatenate([np.zeros(-a0, np.float32), seg])
    feats = mfcc(seg * 32768.0)
    n = min(n, len(feats) // 4)
    total, weight = np.zeros(n), 0
    for dur, w in durations:
        out = np.zeros(n)
        for k in range(0, n, dur * FPS):
            m = min(dur * FPS, n - k)
            if m < 5:
                out[k : k + m] = out[k - 1] if k else 0.0
                continue
            out[k : k + m] = model.score(
                feats[None, 4 * k : 4 * (k + m)], video[None, k : k + m]
            )[0]
        total += w * out
        weight += w
    return list(zip(grid[:n], total / weight))


def _offset_scan(args, model, faces, pcm16, truth: Truth) -> None:
    """Mean score while each person talks, with the audio shifted: finds the A/V offset."""
    for who in truth.people:
        print(
            f"\nA/V offset scan for '{who}' (1 s windows; best offset = highest while talking)"
        )
        for off in args.offsets:
            vals = []
            for items in faces.values():
                if not any(p == who for _, _, p in items):
                    continue
                got = _full_scores(model, items, pcm16, off, ((1, 1),)) or []
                vals += [v for t, v in got if truth.who(t) == (True, who)]
            if vals:
                print(
                    f"  audio lags video by {off:+.2f} s: mean {np.mean(vals):6.2f} (n={len(vals)})"
                )


def _separation(name, series, person_of, who, truth: Truth) -> None:
    """How well the person's scores split their talking time from others' talking time."""
    pos, neg, tagged = [], [], defaultdict(list)
    for tid, pts in series.items():
        if person_of.get(tid) != who:
            continue
        for t, v in pts:
            covered, label = truth.who(t)
            if not covered or label is None:
                continue
            if truth.tag(t):
                tagged[truth.tag(t)].append(v)
                continue
            (pos if label == who else neg).append(v)
    if not pos or not neg:
        print(f"  {name:10s}: not enough talking/silent samples")
        return
    p, n = np.array(pos), np.array(neg)
    # AUC = P(score of a talking sample > score of a silent one)
    order = np.argsort(np.concatenate([p, n]), kind="mergesort")
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    auc = (ranks[: len(p)].sum() - len(p) * (len(p) + 1) / 2) / (len(p) * len(n))
    cuts = (-0.5, 0.0, 0.5, 1.0) if name.startswith("ASD") else _lip_lines()
    rates = ", ".join(
        f"{c:+g}: {100 * np.mean(p > c):.0f}%/{100 * np.mean(n > c):.0f}%" for c in cuts
    )
    print(
        f"  {name:10s}: talking {p.mean():6.2f} +- {p.std():5.2f} (n={len(p)}), "
        f"others talking {n.mean():6.2f} +- {n.std():5.2f} (n={len(n)}), AUC {auc:.3f}\n"
        f"  {'':10s}  above threshold, talking/silent: {rates}"
    )
    for tag, vals in tagged.items():
        v = np.array(vals)
        print(f"  {'':10s}  [{tag}] mean {v.mean():.2f} (n={len(v)})")


def _sheet(faces, path: str, rng: list[float]) -> None:
    """A contact sheet of the mouth crops at 5 per second (for checking the truth by eye)."""
    import cv2

    t0, t1 = rng
    tiles = []
    for tid, items in sorted(faces.items()):
        for t, crop, _ in items:
            if t0 <= t <= t1 and abs(t * 5 - round(t * 5)) < 0.02:
                tile = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
                cv2.putText(
                    tile,
                    f"{t:.1f}",
                    (2, 12),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.4,
                    (0, 255, 0),
                )
                tiles.append(tile)
    if not tiles:
        return
    while len(tiles) % 10:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i : i + 10]) for i in range(0, len(tiles), 10)]
    cv2.imwrite(path, np.vstack(rows))
    print(f"mouth crops: {path}")


# ============================================================================ engine
class AlignedPlayer:
    """Publishes the WAV as `audio.block`, starting exactly on a loop boundary of the video."""

    def __init__(self, bus, wav: str, frames_per_loop: int, clock):
        from attune.replay.player import BLOCK, RATES, read_wav, resample

        pcm, rate = read_wav(wav)
        self.streams = {r: resample(pcm, rate, r) for r in RATES}
        self.block = BLOCK
        self.bus, self.clock = bus, clock
        self.frames_per_loop = frames_per_loop
        self.duration_s = len(self.streams[16000]) / 16000
        self.t0: float | None = None
        self._loop_t: float | None = None
        self._armed = False
        self._stop = threading.Event()
        self._start_evt = threading.Event()
        self.thread = threading.Thread(target=self._run, name="eval-audio", daemon=True)
        from attune.vision.types import VISION_FRAME

        bus.subscribe(VISION_FRAME, self._on_frame)

    def _on_frame(self, ev) -> None:
        if (
            self._armed
            and self._loop_t is None
            and (ev.frame_no - 1) % self.frames_per_loop == 0
        ):
            self._loop_t = ev.t
            self._start_evt.set()

    def arm(self) -> None:
        """Start on the next loop boundary."""
        self._armed = True
        self.thread.start()

    def _run(self) -> None:
        from attune.core.contracts import AUDIO_BLOCK

        if not self._start_evt.wait(timeout=self.frames_per_loop / 10 + 120):
            log.error("the video never looped; no audio played")
            return
        self.t0 = t0 = self._loop_t
        n16 = len(self.streams[16000])
        for i in range(0, n16, self.block):
            t = t0 + i / 16000
            if self._stop.wait(max(0.0, t - self.clock())):
                return
            for rate, data in self.streams.items():
                a, b = (
                    i * rate // 16000,
                    min(len(data), (i + self.block) * rate // 16000),
                )
                if b > a:
                    self.bus.publish(
                        AUDIO_BLOCK, {"t": t, "sample_rate": rate, "samples": data[a:b]}
                    )

    def stop(self) -> None:
        self._stop.set()


class WsRecorder:
    """A console page on the engine's WebSocket that keeps caption/scene/status messages."""

    def __init__(self, url: str, clock):
        self.url, self.clock = url, clock
        self.messages: list[tuple[float, dict]] = []
        self.connected = threading.Event()
        self._stop = threading.Event()
        self.thread = threading.Thread(
            target=lambda: asyncio.run(self._main()), daemon=True
        )

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(timeout=3)

    async def _main(self) -> None:
        import websockets

        for _ in range(100):
            try:
                ws = await websockets.connect(self.url, max_size=None)
                break
            except OSError:
                await asyncio.sleep(0.2)
        else:
            log.error("could not connect to %s", self.url)
            return
        async with ws:
            await ws.send(
                json.dumps({"type": "hello", "role": "console", "frames": False})
            )
            self.connected.set()
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.2)
                except TimeoutError:
                    continue
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                if msg.get("type") in ("caption", "caption_retract", "scene", "status"):
                    self.messages.append((self.clock(), msg))


def _record_tracks(bus) -> list:
    """Each face's scores from `vision.tracks`: (t, track_id, box, asd_score, lip_score)."""
    from attune.vision.types import VISION_TRACKS, get

    rows: list = []

    def on_tracks(ev) -> None:
        t = float(get(ev, "t"))
        for tr in get(ev, "tracks", []) or []:
            rows.append(
                (
                    t,
                    get(tr, "track_id"),
                    list(get(tr, "box")),
                    get(tr, "asd_score"),
                    get(tr, "lip_score"),
                )
            )

    bus.subscribe(VISION_TRACKS, on_tracks)
    return rows


def _cpu_only(args, vision: dict) -> None:
    """Vision and Light-ASD on the CPU with `--threads` each, leaving the GPU to a live engine."""
    import onnxruntime as ort
    import torch
    from attune.vision import detector, embedder, runtime

    torch.set_num_threads(args.threads)
    vision.update(use_gpu=False, asd_device="cpu", det_size=args.det_size)

    def make_session(model_path: str, use_gpu: bool = True):
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = args.threads
        opts.log_severity_level = 3
        return ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )

    for mod in (runtime, detector, embedder):
        mod.make_session = make_session


def engine_run(args, truth: Truth, on_engine=None) -> dict:
    """Run the engine on the clip and return what the WebSocket carried.

    `on_engine(engine)` is called before the engine starts (to listen on its bus)."""
    import cv2
    from attune.config import load_config
    from attune.core import clock
    from attune.main import Engine, Options, preload_torch

    preload_torch()
    cap = cv2.VideoCapture(args.video)
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap_w, cap_h = (
        int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    )
    cap.release()

    tmp = tempfile.TemporaryDirectory(prefix="attune-eval-")
    config = load_config(args.config, cwd=ROOT)
    config.setdefault("engine", {})["data_dir"] = tmp.name
    config.setdefault("history", {})["db_path"] = os.path.join(tmp.name, "history.db")
    vision = config.setdefault("vision", {})
    vision["people_dir"] = os.path.join(tmp.name, "people")
    if args.no_asd:
        vision["asd_enabled"] = False
    if args.cpu:
        _cpu_only(args, vision)
    for kv in args.set or []:
        key, value = kv.split("=", 1)
        table, name = key.split(".", 1)
        config.setdefault(table, {})[name] = json.loads(value)

    opts = Options(
        source=args.video,
        no_mic=True,
        port=args.port,
        no_browser=True,
        simulate_hardware=True,
    )
    cwd = os.getcwd()
    os.chdir(ROOT)  # models/ and config/ resolve from the repo root
    engine = Engine(opts, config)
    if args.cpu:
        # Neither picks speakers: the LLM (Ollama) shares the GPU with a live engine, and
        # the sound alerts' model takes CPU from vision
        steps = engine._steps
        engine._steps = lambda: [
            (name, build) for name, build in steps() if name not in ("llm", "alerts")
        ]
    player = AlignedPlayer(engine.bus, args.wav, n_frames, clock.now)
    track_rows = _record_tracks(engine.bus)
    if on_engine is not None:
        on_engine(engine)
    rec = WsRecorder(f"ws://127.0.0.1:{args.port}/ws", clock.now)
    try:
        engine.start()
        rec.start()
        rec.connected.wait(20)
        player.arm()
        log.info(
            "waiting for the video to loop, then playing %.1f s", player.duration_s
        )
        while player.t0 is None and player.thread.is_alive():
            time.sleep(0.1)
        if player.t0 is None:
            raise RuntimeError("the audio never started")
        end = player.t0 + player.duration_s + args.tail
        while clock.now() < end:
            time.sleep(0.2)
    finally:
        player.stop()
        rec.stop()
        engine.stop()
        os.chdir(cwd)
        tmp.cleanup()
    return {
        "t0": player.t0,
        "fps": fps,
        "duration_s": player.duration_s,
        "frame_size": [
            int((config.get("pages") or {}).get("frame_width", 1280)),
            int((config.get("pages") or {}).get("frame_height", 720)),
        ],
        "messages": rec.messages,
        "tracks": track_rows,
        "camera_size": [cap_w, cap_h],
    }


def metrics(run: dict, truth: Truth, label: str) -> dict:
    """Word attribution and latency from a recorded run."""
    t0 = run["t0"]
    fw, fh = run["frame_size"]
    captions: dict[str, dict] = {}
    first_seen: dict[str, float] = {}
    scenes: list[tuple[float, dict]] = []
    status: list[dict] = []
    for recv_t, msg in run["messages"]:
        kind = msg.get("type")
        if kind == "caption":
            captions[msg["utt_id"]] = msg
            base = str(msg["utt_id"]).split(".")[0]
            first_seen.setdefault(base, recv_t)
            words = msg.get("words") or []
            if words and base + "#w" not in first_seen:
                first_seen[base + "#w"] = float(words[0][1])
        elif kind == "caption_retract":
            captions.pop(msg["utt_id"], None)
        elif kind == "scene":
            scenes.append((float(msg.get("t", recv_t)), msg))
        elif kind == "status":
            status.append(msg)

    scene_times = np.array([t for t, _ in scenes]) if scenes else np.zeros(0)

    def person_of_track(track_id, t) -> str | None:
        if not len(scene_times):
            return None
        k = int(np.clip(np.searchsorted(scene_times, t), 0, len(scenes) - 1))
        for j in (k, max(k - 1, 0), min(k + 1, len(scenes) - 1)):
            for face in scenes[j][1].get("faces") or []:
                if face.get("track_id") == track_id:
                    x, y, w, h = face["box"]
                    return truth.person_for_box([x / fw, y / fh, w / fw, h / fh])
        return None

    def group(t: float, who: str) -> str:
        """Who talks at clip time t, as reported: a person (and the interval's tag), or nobody."""
        if not truth.visible(who):
            return "(nobody visible)"
        tag = truth.tag(t)
        return f"{who} [{tag}]" if tag else who

    # every final word, with who said it (truth) and who it was credited to
    counts: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    rows = []
    for msg in captions.values():
        spk = msg.get("speaker") or {}
        for w in msg.get("words") or []:
            text, ws, we = w[0], float(w[1]), float(w[2])
            ct = (ws + we) / 2 - t0
            covered, who = truth.who(ct)
            if not covered or who is None:
                continue
            kind = spk.get("kind")
            if kind in ("face", "probable_face"):
                credited = f"face:{person_of_track(spk.get('track_id'), ws) or '?'}"
            else:
                credited = str(kind)
            counts[group(ct, who)][credited] += 1
            rows.append((ct, text, group(ct, who), credited))

    out: dict = {"label": label, "credits": {k: dict(v) for k, v in counts.items()}}
    bg = counts.get("(nobody visible)", {})
    n_bg = sum(bg.values())
    out["background_words"] = n_bg
    out["background_on_face"] = (
        sum(v for k, v in bg.items() if k.startswith("face:")) / n_bg if n_bg else None
    )
    out[
        "own_face"
    ] = {}  # group -> (share of its words on the person's own face, words)
    for key, c in counts.items():
        if key == "(nobody visible)":
            continue
        person = key.split(" [")[0]
        n = sum(c.values())
        out["own_face"][key] = (c.get(f"face:{person}", 0) / n if n else None, n)

    # scene ticks: a face lit as the speaker while nobody visible talks, or while a person does
    lit = defaultdict(lambda: [0, 0])
    for t, sc in scenes:
        covered, who = truth.who(t - t0)
        if not covered or who is None:
            continue
        speaker = next((f for f in sc.get("faces") or [] if f.get("is_speaker")), None)
        key = group(t - t0, who)
        lit[key][1] += 1
        if speaker is not None and (
            not truth.visible(who) or person_of_track(speaker["track_id"], t) == who
        ):
            lit[key][0] += 1
    out["scene_lit"] = {k: v[0] / v[1] for k, v in lit.items() if v[1]}

    # the scores each truth group's face got: Light-ASD (share scored, mean, share >= 0.5)
    # and the lip score, for the person's own face (or any face while nobody visible talks)
    cw, ch = run.get("camera_size") or run["frame_size"]
    per: dict[str, dict[str, list]] = defaultdict(
        lambda: {"asd": [], "lip": [], "n": []}
    )
    for t, _tid, box, asd, lip in run.get("tracks") or []:
        covered, who = truth.who(t - t0)
        if not covered or who is None:
            continue
        x, y, w, h = box
        person = truth.person_for_box([x / cw, y / ch, w / cw, h / ch])
        if truth.visible(who) and person != who:
            continue
        g = per[group(t - t0, who)]
        g["n"].append(1)
        if asd is not None:
            g["asd"].append(float(asd))
        if lip is not None:
            g["lip"].append(float(lip))
    lip_line = _lip_lines()[1]
    out["face_scores"] = {
        k: {
            "frames": len(v["n"]),
            "asd_scored": len(v["asd"]) / len(v["n"]) if v["n"] else None,
            "asd_mean": round(statistics.mean(v["asd"]), 2) if v["asd"] else None,
            "asd_ge_0.5": sum(a >= 0.5 for a in v["asd"]) / len(v["asd"])
            if v["asd"]
            else None,
            "lip_mean": round(statistics.mean(v["lip"]), 3) if v["lip"] else None,
            "lip_talking": sum(x >= lip_line for x in v["lip"]) / len(v["lip"])
            if v["lip"]
            else None,
        }
        for k, v in per.items()
    }

    lat = []
    for base, recv_t in first_seen.items():
        if base.endswith("#w"):
            continue
        w0 = first_seen.get(base + "#w")
        if w0 is not None and w0 >= t0:
            lat.append((w0 - t0, recv_t - w0))
    lat.sort()
    out["first_caption_latency_s"] = round(lat[0][1], 2) if lat else None
    out["caption_latency_median_s"] = (
        round(statistics.median(x[1] for x in lat), 2) if lat else None
    )
    vis = [s.get("parts", {}).get("vision", {}).get("metrics", {}) for s in status]
    fps = [m["vision_fps"] for m in vis if m.get("vision_fps")]
    out["vision_fps"] = round(statistics.median(fps), 1) if fps else None
    vis = [m for m in vis if m.get("asd_ms") is not None]
    if vis:
        out["asd_ms"] = round(statistics.mean(m["asd_ms"] for m in vis), 1)
        out["asd_gpu_mb"] = max(m.get("asd_gpu_mb") or 0 for m in vis)
    # per clip second: frames processed, the scores, and whether a face was lit as speaker
    secs: dict[int, dict] = defaultdict(
        lambda: {"t": set(), "asd": [], "lip": [], "lit": []}
    )
    for t, _tid, _box, asd, lip in run.get("tracks") or []:
        sec = secs[int(t - t0)]
        sec["t"].add(t)
        if asd is not None:
            sec["asd"].append(float(asd))
        if lip is not None:
            sec["lip"].append(float(lip))
    for t, sc in scenes:
        secs[int(t - t0)]["lit"].append(
            any(f.get("is_speaker") for f in sc.get("faces") or [])
        )
    out["per_second"] = {
        k: {
            "fps": len(v["t"]),
            "asd_n": len(v["asd"]),
            "asd": round(statistics.mean(v["asd"]), 2) if v["asd"] else None,
            "lip": round(statistics.mean(v["lip"]), 3) if v["lip"] else None,
            "lit": round(statistics.mean(v["lit"]), 2) if v["lit"] else None,
        }
        for k, v in sorted(secs.items())
        if 0 <= k <= run.get("duration_s", 1e9) + 1
    }
    out["rows"] = rows
    return out


def report(m: dict) -> None:
    print(f"\n=== {m['label']}")

    def pct(v):
        return "n/a" if v is None else f"{100 * v:.0f}%"

    print(
        f"background words on a visible face: {pct(m['background_on_face'])} "
        f"of {m['background_words']:.0f} words (target ~0)"
    )
    for key, (share, n) in m["own_face"].items():
        person = key.split(" [")[0]
        print(f"{key}: words on {person}'s face {pct(share)} of {n:.0f} words")
    for who, c in m["credits"].items():
        credits = ", ".join(f"{k} {v:.0f}" for k, v in sorted(c.items()))
        print(f"  {who:24s} credited to: {credits}")
    for who, v in m["scene_lit"].items():
        whose = "a face" if who.startswith("(") else "their face"
        print(f"  scene ticks with {whose} lit as speaker while {who} talks: {pct(v)}")
    for who, f in (m.get("face_scores") or {}).items():
        asd = (
            f"Light-ASD scored {pct(f['asd_scored'])}, mean {f['asd_mean']}, "
            f">=0.5 {pct(f['asd_ge_0.5'])}"
            if f["asd_mean"] is not None
            else "no Light-ASD score"
        )
        print(
            f"  face scores while {who} talks ({f['frames']} face-frames): {asd}; "
            f"lip mean {f['lip_mean']}, >= lip_talking {pct(f['lip_talking'])}"
        )
    print(
        f"first-caption latency {m['first_caption_latency_s']} s, "
        f"median per utterance {m['caption_latency_median_s']} s"
    )
    print(f"vision {m.get('vision_fps')} fps (median of status reports)")
    if "asd_ms" in m:
        print(
            f"Light-ASD {m['asd_ms']} ms per round, GPU reserved {m['asd_gpu_mb']} MB"
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("video")
    ap.add_argument("wav")
    ap.add_argument("truth")
    ap.add_argument(
        "--offline", action="store_true", help="Light-ASD alone, outside the engine"
    )
    ap.add_argument(
        "--no-asd", action="store_true", help="engine with [vision] asd_enabled=false"
    )
    ap.add_argument(
        "--cpu",
        action="store_true",
        help="vision and Light-ASD on the CPU (--threads, --det-size): leaves the GPU alone",
    )
    ap.add_argument("--port", type=int, default=8007)
    ap.add_argument(
        "--config", help="engine config (default: config/attune.toml if present)"
    )
    ap.add_argument(
        "--set", action="append", help="config override, e.g. fusion.asd_on=0.5"
    )
    ap.add_argument(
        "--tail", type=float, default=4.0, help="seconds to keep recording after"
    )
    ap.add_argument(
        "--out", help="write the metrics (and every word) to this JSON file"
    )
    ap.add_argument("--words", action="store_true", help="print every scored word")
    ap.add_argument("--seconds", action="store_true", help="print a per-second table")
    g = ap.add_argument_group("offline")
    g.add_argument("--model", default="models/light_asd/finetuning_TalkSet.model")
    g.add_argument("--device", default="cuda", help="Light-ASD device (cuda or cpu)")
    g.add_argument(
        "--det-device", default="cuda", help="face finder device (cuda or cpu)"
    )
    g.add_argument("--det-size", type=int, default=640)
    g.add_argument("--threads", type=int, default=4, help="CPU threads for cpu devices")
    g.add_argument(
        "--boxes", help="cache of face boxes (JSON): read if present, else written"
    )
    g.add_argument("--until", type=float, help="only the first this many seconds")
    g.add_argument("--window", type=float, default=1.5, help="streaming window, s")
    g.add_argument("--score-s", type=float, default=0.4, help="streaming score span, s")
    g.add_argument(
        "--rate", type=float, default=5.0, help="streaming rounds per second"
    )
    g.add_argument(
        "--crop-fps",
        type=float,
        help="streaming: thin crops to this rate (a slow vision loop)",
    )
    g.add_argument(
        "--max-gap", type=float, default=0.2, help="streaming: longest crop gap, s"
    )
    g.add_argument(
        "--min-fps", type=float, default=12.0, help="streaming: fewest crops a second"
    )
    g.add_argument(
        "--av-offset", type=float, default=0.0, help="audio lags video by this, s"
    )
    g.add_argument(
        "--offsets", type=float, nargs="+", help="scan these audio offsets, s"
    )
    g.add_argument("--min-face-px", type=int, default=40)
    g.add_argument(
        "--quick",
        action="store_true",
        help="streaming scores only (no lips, no whole-clip)",
    )
    g.add_argument("--sheet", help="write a contact sheet of mouth crops here")
    g.add_argument("--sheet-range", type=float, nargs=2, default=[0.0, 1e9])
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("ATTUNE_LOG", "WARNING").upper(),
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log.setLevel(logging.INFO)
    truth = Truth.load(args.truth)
    if args.offline:
        offline(args, truth)
        return 0
    run = engine_run(args, truth)
    m = metrics(
        run,
        truth,
        "engine without Light-ASD" if args.no_asd else "engine with Light-ASD",
    )
    report(m)
    if args.seconds:
        print(" sec  truth        frames  ASD-n  ASD-mean  lip-mean  face-lit")
        for sec, v in m["per_second"].items():
            _, who = truth.who(int(sec) + 0.5)
            asd = "-" if v["asd"] is None else f"{v['asd']:.2f}"
            lip = "-" if v["lip"] is None else f"{v['lip']:.3f}"
            lit = "-" if v["lit"] is None else f"{100 * v['lit']:.0f}%"
            print(
                f"{int(sec):4d}  {who!s:11s} {v['fps']:6d} {v['asd_n']:6d} {asd:>9s} "
                f"{lip:>9s} {lit:>9s}"
            )
    if args.words:
        for ct, text, who, credited in sorted(m["rows"]):
            print(f"  {ct:6.2f}s {text:14s} said by {who!s:10s} -> {credited}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(m, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
