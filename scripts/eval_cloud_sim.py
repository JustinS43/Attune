"""Cloud captions before/after on synthetic conversations (A-32, V-32).

Section 4 - Pages, Engine & Demo. TODO: P-48.

There are no Google credentials (and no podcast clips) on the test laptop, so this builds
conversations as fusion inputs and scores the speaker attribution with and without cloud
captions. People sit in view (and in one scene a voice talks from off screen); they take
turns with long runs, quick replies, one-word backchannels, overlaps and pauses. Each face
carries a Light-ASD score computed from who is really talking over the model's window, so
it turns on late, stays on after a turn, misses replies shorter than the window, and has
some noise. The local recogniser's drafts and finals are split at its pauses, so a quick
reply often shares an utterance with the turn before it. No voice prints are simulated.

`eval_podcast.refuse` replays each conversation into this code's SpeakerFusion twice:
local only, and with a simulated cloud tag stream (`eval_podcast.simulate_cloud`: tags from
the truth, finals at pauses, a lag, a share of wrong tags, stream restarts that renumber the
voices). `eval_podcast.score` gives the same metrics as for podcast clips.

    python scripts/eval_cloud_sim.py                    # every scene, 5 seeds each
    python scripts/eval_cloud_sim.py --seeds 10 --error 0.1 --lag 1.5 --restart 290
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_podcast as ep

FPS = 30
W, H = 1280, 720
VOCAB = [
    "we",
    "could",
    "try",
    "that",
    "later",
    "maybe",
    "the",
    "red",
    "one",
    "is",
    "fine",
    "but",
    "not",
    "today",
    "okay",
    "so",
    "where",
    "did",
    "you",
    "put",
    "it",
    "I",
    "think",
    "it",
    "was",
    "near",
    "door",
    "right",
    "yeah",
    "sure",
    "well",
    "honestly",
    "never",
    "mind",
    "what",
    "about",
    "tomorrow",
    "morning",
    "sounds",
    "good",
    "thanks",
]
SCENES = {
    # name: (people in view, voices off screen, seconds, share of turn changes that overlap)
    "two people": (2, 0, 150.0, 0.12),
    "three people": (3, 0, 150.0, 0.12),
    "two + one off screen": (2, 1, 150.0, 0.12),
    "two, lots of overlap": (2, 0, 150.0, 0.35),
}
ASD_WINDOW = (0.9, 0.1)  # Light-ASD scores roughly the last 0.8 s, arriving ~0.1 s late
ASD_SHARE = 0.4  # talking over this share of the window scores "talking"


def conversation(rng, voices: int, duration: float, overlap_p: float) -> list:
    """Turns as (voice, [(word, t0, t1)]): long runs, quick replies, overlaps, pauses."""
    turns, t, who = [], 0.5, 0
    while t < duration:
        n = int(rng.integers(1, 4)) if rng.random() < 0.35 else int(rng.integers(6, 26))
        words = []
        for i in range(n):
            d = float(rng.uniform(0.18, 0.45))
            words.append((str(rng.choice(VOCAB)), t, t + d))
            t += d
            if i < n - 1:  # mostly tight, sometimes a pause mid-turn
                t += float(
                    rng.uniform(0.3, 0.8) if rng.random() < 0.08 else rng.uniform(0.03, 0.12)
                )
        turns.append((who, words))
        who = int(rng.choice([v for v in range(voices) if v != who]))
        if rng.random() < overlap_p:
            t -= float(rng.uniform(0.1, 0.6))  # the next voice starts before this one ends
        else:
            t += float(np.clip(rng.normal(0.3, 0.25), 0.05, 1.2))
    return turns


def build_run(seed: int, people: int, offscreen: int, duration: float, overlap_p: float) -> dict:
    """A run dict like eval_podcast's (fusion_inputs + ref), ready for refuse() and score()."""
    rng = np.random.default_rng(seed)
    voices = people + offscreen
    turns = conversation(rng, voices, duration, overlap_p)
    end = max(w[2] for _, ws in turns for w in ws) + 2.0
    said = np.zeros((voices, int(end * 100) + 2), bool)  # 10 ms steps: is each voice talking
    for v, ws in turns:
        for _, t0, t1 in ws:
            said[v, int(t0 * 100) : int(t1 * 100)] = True

    def talking(voice: int, a: float, b: float) -> float:
        """Share of [a, b] the voice spends saying words."""
        lo, hi = max(int(a * 100), 0), max(int(b * 100), 1)
        return float(said[voice, lo:hi].mean()) if hi > lo else 0.0

    size = 160
    seats = [((k + 1) / (people + 1)) * W for k in range(people)]
    events: list = []
    for i in range(int(end * FPS)):
        t = i / FPS
        tracks = []
        for tid, cx in enumerate(seats, start=1):
            share = talking(tid - 1, t - ASD_WINDOW[0], t - ASD_WINDOW[1])
            score = (2.0 if share >= ASD_SHARE else -2.5) + float(rng.normal(0.0, 1.0))
            now = talking(tid - 1, t - 0.05, t + 0.05) > 0
            mouth = 0.2 + 0.15 * np.sin(2 * np.pi * 4 * t) if now else 0.1
            tracks.append({
                "track_id": tid, "box": [cx - size / 2, H * 0.45 - size / 2, size, size],
                "face_px": size, "lip_score": 0.02 if now else 0.004, "person_id": None,
                "name": None, "match_score": 0.9, "status": "unknown",
                "mouth_open": round(float(mouth), 3), "asd_score": round(score, 2),
            })  # fmt: skip
        events.append((t, "vision.tracks", {"frame_no": i, "t": t, "tracks": tracks}))
        speech = any(talking(v, t - 0.02, t + 0.02) > 0 for v in range(voices))
        events.append(
            (t, "audio.vad", {"t": t, "is_speech": speech, "prob": 0.9 if speech else 0.1})
        )
        events.append((t, "audio.level", {"t": t, "db": -22.0 if speech else -55.0}))

    # the local recogniser: every word (overlaps included), utterances split at its pauses
    order = sorted((w[1], w[2], w[0]) for _, ws in turns for w in ws)
    local = [
        (w, t0 + float(rng.normal(0, 0.03)), t1 + float(rng.normal(0, 0.03))) for t0, t1, w in order
    ]
    local = [(w, round(a, 3), round(max(b, a + 0.05), 3)) for w, a, b in local]
    utts, cur, reach = [], [], -1.0
    for w in local:
        if cur and (w[1] - reach >= 0.55 or w[1] - cur[0][1] > 12.0):
            utts.append(cur)
            cur = []
        cur.append(w)
        reach = max(reach, w[2]) if len(cur) > 1 else w[2]
    if cur:
        utts.append(cur)
    for k, ws in enumerate(utts):
        uid = f"sim-{seed}-{k}"
        last = max(w[2] for w in ws)
        at = ws[0][1] + 0.5
        while at < last + 0.45:
            shown = [w for w in ws if w[2] <= at]
            if shown:
                events.append((at, "audio.transcript", _transcript(uid, shown, False)))
            at += 0.5
        events.append((last + 0.45, "audio.transcript", _transcript(uid, ws, True)))

    # the answer key, in eval_podcast's ref.json shape
    ref_words, turn_speakers = [], {}
    for k, (voice, ws) in enumerate(turns):
        turn_speakers[k] = voice
        ref_words += [{"w": w, "t": round((t0 + t1) / 2, 3), "turn": k} for w, t0, t1 in ws]
    ref_words.sort(key=lambda r: r["t"])
    regions = [
        [(k + 0.5) / (people + 1), 0.0, (k + 1.5) / (people + 1), 1.0] for k in range(people)
    ]
    names = [f"seat {k + 1}" for k in range(people)] + [
        f"off screen {k + 1}" for k in range(offscreen)
    ]
    return {
        "name": f"sim{seed}", "t0": 0.0, "camera_size": [W, H], "frame_size": [W, H],
        "fusion_inputs": events,
        "ref": {"words": ref_words, "turn_speakers": turn_speakers, "speaker_names": names,
                "regions": regions},
    }  # fmt: skip


def _transcript(uid: str, words: list, final: bool) -> dict:
    return {
        "utt_id": uid, "t_start": words[0][1], "t_end": max(w[2] for w in words),
        "text": " ".join(w[0] for w in words), "final": final, "lang": "en",
        "words": [list(w) for w in words],
    }  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--seeds", type=int, default=5, help="conversations per scene")
    ap.add_argument(
        "--lag", type=float, default=ep.CLOUD_SIM["lag_s"], help="cloud finals, s after a pause"
    )
    ap.add_argument(
        "--error", type=float, default=ep.CLOUD_SIM["error"], help="share of wrong tags"
    )
    ap.add_argument("--restart", type=float, default=60.0, help="stream length, s (Google's: 290)")
    ap.add_argument("--scene", action="append", help="only these scenes")
    args = ap.parse_args(argv)
    cloud = {"lag_s": args.lag, "error": args.error, "restart_s": args.restart}
    print(f"cloud sim: {cloud}; {args.seeds} conversations per scene")
    totals: dict[str, list] = {"before": [], "after": []}
    for scene, (people, off, duration, overlap) in SCENES.items():
        if args.scene and scene not in args.scene:
            continue
        before, after = [], []
        for seed in range(args.seeds):
            run = build_run(1000 * len(scene) + seed, people, off, duration, overlap)
            before.append(ep.score(ep.refuse(run)))
            after.append(ep.score(ep.refuse(run, cloud={**cloud, "seed": seed})))
        totals["before"] += before
        totals["after"] += after
        print(f"\n== {scene} ({people} in view, {off} off screen, {duration:.0f} s x {args.seeds})")
        print(ep.before_after(_mean(before), _mean(after)))
    print(f"\n== all scenes ({len(totals['before'])} conversations)")
    print(ep.before_after(_mean(totals["before"]), _mean(totals["after"])))
    return 0


def _mean(runs: list[dict]) -> dict:
    out = {}
    for k in ep.BEFORE_AFTER:
        vals = [r[k] for r in runs if r.get(k) is not None]
        out[k] = round(statistics.mean(vals), 3) if vals else None
    return out


if __name__ == "__main__":
    sys.exit(main())
