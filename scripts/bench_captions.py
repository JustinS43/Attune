"""Caption speed and completeness benchmark on the replay path.

Section 2 - Audio & Language. TODO: A-22 (caption latency and completeness).

    python scripts/bench_captions.py make --out %TEMP%/attune_eval/speed/gt
    python scripts/bench_captions.py run --gt %TEMP%/attune_eval/speed/gt --label base
    python scripts/bench_captions.py table runs/base.json runs/after.json

`make` renders test speech with exactly known text from local voices only (Kokoro in
models/tts through attune.speech_out, and the Windows SAPI voices David and Zira through
PowerShell), trims each phrase, joins the phrases with known pauses and aligns the words
of each phrase with the local faster-whisper model (word timestamps), so every word has
a start and end time. Scenarios: short phrases, a monologue longer than
[audio] max_utterance_s, fast speech, pauses of 0.3-1.5 s, numbers and names, two
alternating voices, very short one-word replies, Spanish, and mixed English/Spanish.

`run` starts the whole engine in this process (like `python -m attune --source <video>
--audio-file <wav> --no-browser --port 8012`), plays each scenario's WAV as the
microphone, and records:
- every WebSocket message a console page gets, with its receive time on the shared
  clock (`time.perf_counter`, the engine clock);
- the bus events behind them (VAD, transcripts, captions, retractions, translations);
- optionally (`--pages`) what the lens and phone pages draw, from a headless Edge
  (playwright-core) with the lens's `?bench` hook;
- the engine's history.db after it stops, CPU time and GPU load.

Metrics per scenario (times in seconds; "shown" = received by a page):
- onset -> first draft: a phrase's first word start to the first caption with any of it;
- word -> draft / word -> final: each word's end to the first draft / final showing it;
- end -> final: a phrase's last word end to the final that has it;
- WER / CER of the finals (and of each draft over the words it covers);
- coverage: ground-truth words in some final; lost: words never shown at all;
- history: ground-truth words in history.db; pages: words the lens / phone drew;
- retractions, words moved between bubbles, message rates, CPU and GPU load.

Nothing is stored outside the output folder; test speech is synthetic.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import wave
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

log = logging.getLogger("bench_captions")

SR = 24000  # ground-truth WAV rate (Kokoro's); the engine's player resamples

# ----------------------------------------------------------------------------- scenarios
# Voices: "kokoro:<sid>" (af_heart 3, af_bella 2, am_michael 16, am_adam 11, ef_dora 28,
# em_alex 29) or "sapi:<David|Zira>:<rate>". Each phrase: (voice, lang, text, pause after).
K_HEART, K_BELLA, K_MICHAEL, K_ADAM, K_DORA, K_ALEX = (
    "kokoro:3",
    "kokoro:2",
    "kokoro:16",
    "kokoro:11",
    "kokoro:28",
    "kokoro:29",
)
DAVID, ZIRA, DAVID_FAST, ZIRA_FAST = (
    "sapi:David:0",
    "sapi:Zira:0",
    "sapi:David:5",
    "sapi:Zira:5",
)

SCENARIOS: dict[str, list[tuple[str, str, str, float]]] = {
    "en_phrases": [
        (K_HEART, "en", "Good morning, how are you doing today?", 1.2),
        (K_HEART, "en", "I was hoping we could talk about the project.", 0.8),
        (K_HEART, "en", "The meeting starts at ten thirty in the big room.", 1.5),
        (K_HEART, "en", "Can you pass me the blue folder on the table?", 0.7),
        (K_HEART, "en", "We should leave before it starts raining.", 1.0),
        (K_HEART, "en", "That sounds like a great idea to me.", 1.3),
        (K_HEART, "en", "My sister is flying in from Chicago tomorrow.", 0.9),
        (K_HEART, "en", "Please remember to bring your laptop charger.", 1.1),
        (K_HEART, "en", "Let me know when you are ready to start.", 0.8),
        (K_HEART, "en", "Thanks, that was really helpful.", 1.5),
    ],
    "en_long": [
        (K_MICHAEL, "en", s, 0.15)
        for s in (
            "Last summer my family drove all the way from Miami to the mountains in North Carolina.",
            "We left before sunrise because my father wanted to beat the traffic on the highway.",
            "The first few hours were quiet, and most of us slept in the back of the car.",
            "Around lunch we stopped at a small diner that served the best peach pie I have ever tasted.",
            "After that my little brother kept asking how much longer the trip would take.",
            "By the evening we reached a cabin next to a lake surrounded by tall pine trees.",
            "We spent the whole week hiking, fishing and telling stories around the fire.",
            "On the last night it rained so hard that the power went out for hours.",
            "Nobody minded, because we played cards by candlelight until almost midnight.",
        )
    ],
    "en_fast": [
        (
            DAVID_FAST,
            "en",
            "Okay so here is the plan for the rest of the afternoon.",
            0.5,
        ),
        (
            DAVID_FAST,
            "en",
            "First we finish the slides, then we test the demo twice.",
            0.4,
        ),
        (
            ZIRA_FAST,
            "en",
            "If anything breaks we fix it right away and try again.",
            0.5,
        ),
        (
            ZIRA_FAST,
            "en",
            "Remember that the judges only have three minutes with us.",
            0.4,
        ),
        (
            DAVID_FAST,
            "en",
            "So keep every answer short and show them the glasses early.",
            1.0,
        ),
    ],
    "en_pauses": [
        (K_BELLA, "en", "So I was thinking", 0.3),
        (K_BELLA, "en", "that we could meet", 0.5),
        (K_BELLA, "en", "on Friday afternoon", 0.8),
        (K_BELLA, "en", "if that works for you", 1.0),
        (K_BELLA, "en", "or maybe Saturday", 1.5),
        (K_BELLA, "en", "is better for everyone.", 1.2),
    ],
    "en_numbers": [
        (ZIRA, "en", "My name is Siobhan Kowalski and this is my friend Arjun.", 0.9),
        (ZIRA, "en", "The train leaves at seven forty five from platform nine.", 0.9),
        (ZIRA, "en", "We met Doctor Nakamura on March third at the clinic.", 0.9),
        (ZIRA, "en", "It costs about twenty five dollars for two tickets.", 0.9),
        (ZIRA, "en", "Room four hundred twelve is on the fourth floor.", 1.2),
    ],
    "two_voices": [
        (DAVID, "en", "Hey, did you finish the report for Monday?", 0.4),
        (ZIRA, "en", "Almost, I just need to add the charts.", 0.5),
        (DAVID, "en", "Great, send it to me when it is done.", 0.3),
        (ZIRA, "en", "Sure, do you want the numbers from last year too?", 0.6),
        (DAVID, "en", "Yes please, and add a short summary at the top.", 0.4),
        (ZIRA, "en", "No problem, I will have it ready tonight.", 0.5),
        (DAVID, "en", "Thanks, you are the best.", 1.2),
    ],
    "short_words": [
        (K_HEART, "en", "Yes.", 1.5),
        (DAVID, "en", "No.", 1.5),
        (K_MICHAEL, "en", "Okay.", 1.5),
        (ZIRA, "en", "Hi.", 1.5),
        (K_HEART, "en", "Sure.", 1.5),
        (DAVID, "en", "Wait.", 1.5),
        (K_MICHAEL, "en", "Thanks.", 1.5),
    ],
    "es_phrases": [
        (K_DORA, "es", "Buenos días, ¿cómo estás hoy?", 1.0),
        (K_ALEX, "es", "¿Dónde está la estación de tren?", 1.0),
        (K_DORA, "es", "Me gustaría un café con leche, por favor.", 1.0),
        (K_ALEX, "es", "Mañana vamos a la playa con mis primos.", 1.0),
        (K_DORA, "es", "No entiendo, ¿puedes repetirlo más despacio?", 1.0),
        (K_ALEX, "es", "La reunión empieza a las tres de la tarde.", 1.0),
        (K_DORA, "es", "Mi hermano vive en Madrid desde hace cinco años.", 1.0),
        (K_ALEX, "es", "Gracias por tu ayuda, eres muy amable.", 1.5),
    ],
    "mixed": [
        (K_HEART, "en", "Hello, nice to meet you.", 0.8),
        (K_ALEX, "es", "Mucho gusto, me llamo Carlos.", 0.8),
        (K_HEART, "en", "Where are you from, Carlos?", 0.8),
        (K_ALEX, "es", "Soy de Colombia, pero vivo aquí en Miami.", 0.8),
        (K_HEART, "en", "That is wonderful, welcome to the team.", 0.8),
        (K_ALEX, "es", "Muchas gracias, estoy muy contento de estar aquí.", 1.5),
    ],
}
LEAD_S = 1.0  # silence before the first phrase
TAIL_S = 2.0  # and after the last


# ----------------------------------------------------------------------------- text
_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = [
    "_",
    "_",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
]
_ORD = {
    "1st": "first",
    "2nd": "second",
    "3rd": "third",
    "4th": "fourth",
    "5th": "fifth",
    "6th": "sixth",
    "7th": "seventh",
    "8th": "eighth",
    "9th": "ninth",
    "10th": "tenth",
}
_ABBR = {"dr": "doctor", "mr": "mister", "mrs": "missus", "ok": "okay", "st": "street"}


def _num_words(n: int) -> list[str]:
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        return [_TENS[n // 10]] + ([_ONES[n % 10]] if n % 10 else [])
    if n < 1000:
        rest = _num_words(n % 100) if n % 100 else []
        return [_ONES[n // 100], "hundred"] + rest
    if n < 1_000_000:
        rest = _num_words(n % 1000) if n % 1000 else []
        return _num_words(n // 1000) + ["thousand"] + rest
    return [_ONES[int(d)] for d in str(n)]


def _token_words(tok: str) -> list[str]:
    """One written token as spoken words (English numbers, times, money, ordinals)."""
    if tok in _ORD:
        return [_ORD[tok]]
    if tok in _ABBR:
        return [_ABBR[tok]]
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", tok)
    if m:
        h, mi = int(m[1]), int(m[2])
        return _num_words(h) + (
            [] if mi == 0 else (["oh"] if mi < 10 else []) + _num_words(mi)
        )
    m = re.fullmatch(r"\$(\d+)", tok)
    if m:
        return _num_words(int(m[1])) + ["dollars"]
    if re.fullmatch(r"\d+", tok):
        if len(tok) > 4:
            return [_ONES[int(d)] for d in tok]
        return _num_words(int(tok))
    return [tok]


def norm_words(text: str) -> list[str]:
    """Lower-case words without punctuation or accents; numbers spelled out."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("-", " ")
    out: list[str] = []
    for tok in re.findall(r"\$?[a-z0-9']+(?::\d\d)?", text):
        tok = tok.strip("'")
        if not tok:
            continue
        out.extend(_token_words(tok))
    return out


def edit_ops(ref: list, hyp: list) -> tuple[int, int, int, int]:
    """(substitutions, deletions, insertions, hits) of a minimal alignment."""
    n, m = len(ref), len(hyp)
    d = np.zeros((n + 1, m + 1), dtype=np.int32)
    d[:, 0] = np.arange(n + 1)
    d[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i, j] = min(
                d[i - 1, j] + 1,
                d[i, j - 1] + 1,
                d[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]),
            )
    i, j, s, dl, ins, hit = n, m, 0, 0, 0, 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i, j] == d[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]):
            if ref[i - 1] == hyp[j - 1]:
                hit += 1
            else:
                s += 1
            i, j = i - 1, j - 1
        elif i > 0 and d[i, j] == d[i - 1, j] + 1:
            dl, i = dl + 1, i - 1
        else:
            ins, j = ins + 1, j - 1
    return s, dl, ins, hit


def wer(ref: list, hyp: list) -> float | None:
    if not ref:
        return None
    s, dl, ins, _ = edit_ops(ref, hyp)
    return (s + dl + ins) / len(ref)


def cer(ref: str, hyp: str) -> float | None:
    ref, hyp = " ".join(norm_words(ref)), " ".join(norm_words(hyp))
    if not ref:
        return None
    return wer(list(ref), list(hyp))


# ----------------------------------------------------------------------------- make
def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    assert width == 2, path
    pcm = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    return pcm.reshape(-1, ch).mean(axis=1), rate


def write_wav(path: Path, pcm: np.ndarray, rate: int = SR) -> None:
    data = (np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)


def _resample(pcm: np.ndarray, rate: int, target: int) -> np.ndarray:
    if rate == target:
        return pcm.astype(np.float32)
    import soxr

    return soxr.resample(pcm, rate, target).astype(np.float32)


def trim(
    pcm: np.ndarray, rate: int, floor_db: float = -45.0, keep_s: float = 0.02
) -> np.ndarray:
    """Cut leading and trailing silence (10 ms frames below `floor_db` of the peak)."""
    hop = rate // 100
    n = len(pcm) // hop
    if n == 0:
        return pcm
    rms = np.sqrt(np.mean(pcm[: n * hop].reshape(n, hop) ** 2, axis=1)) + 1e-9
    db = 20 * np.log10(rms / rms.max())
    on = np.flatnonzero(db > floor_db)
    if not len(on):
        return pcm
    keep = int(keep_s * rate)
    return pcm[max(0, on[0] * hop - keep) : min(len(pcm), (on[-1] + 1) * hop + keep)]


def _sapi(jobs: list[tuple[str, int, str, Path]]) -> None:
    """Render (voice, rate, text, path) with Windows SAPI in one PowerShell run."""
    if not jobs:
        return
    fd, name = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    spec = Path(name)
    spec.write_text(
        json.dumps(
            [{"voice": v, "rate": r, "text": t, "path": str(p)} for v, r, t, p in jobs]
        ),
        encoding="utf-8",
    )
    script = rf"""
Add-Type -AssemblyName System.Speech
$jobs = Get-Content -Raw -Encoding UTF8 '{spec}' | ConvertFrom-Json
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo({SR}, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($j in $jobs) {{
  $s.SelectVoice("Microsoft $($j.voice) Desktop"); $s.Rate = [int]$j.rate
  $s.SetOutputToWaveFile($j.path, $fmt); $s.Speak($j.text); $s.SetOutputToNull()
}}
$s.Dispose()
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True,
            capture_output=True,
        )
    finally:
        spec.unlink(missing_ok=True)


def _align(model, pcm: np.ndarray, rate: int, text: str, lang: str) -> list[list]:
    """Word start/end times in a trimmed phrase clip, from faster-whisper word timestamps."""
    audio = _resample(pcm, rate, 16000)
    duration = len(audio) / 16000
    segments, _ = model.transcribe(
        audio, language=lang, word_timestamps=True, beam_size=5, initial_prompt=text
    )
    hyp = [(w.word, w.start, w.end) for s in segments for w in (s.words or [])]
    ref_tokens = text.split()
    ref_norm = [" ".join(norm_words(t)) for t in ref_tokens]
    hyp_norm = [" ".join(norm_words(w)) for w, _, _ in hyp]
    times: list[tuple[float, float] | None] = [None] * len(ref_tokens)
    sm = difflib.SequenceMatcher(a=ref_norm, b=hyp_norm, autojunk=False)
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            _, t0, t1 = hyp[b + k]
            times[a + k] = (float(t0), float(t1))
    # unmatched words: share the gap between matched neighbours by length
    i = 0
    while i < len(times):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(times) and times[j] is None:
            j += 1
        lo = times[i - 1][1] if i > 0 else 0.0
        hi = times[j][0] if j < len(times) else duration
        span = max(hi - lo, 0.05 * (j - i))
        weights = [max(len(ref_tokens[k]), 1) for k in range(i, j)]
        acc = lo
        for k, wgt in zip(range(i, j), weights):
            step = span * wgt / sum(weights)
            times[k] = (acc, acc + step)
            acc += step
        i = j
    # the phrase clip is trimmed: the first word starts at 0 and the last ends at its end
    if times:
        times[0] = (0.0, times[0][1])
        times[-1] = (times[-1][0], duration)
    return [
        [tok, round(a, 3), round(max(b, a + 0.02), 3)]
        for tok, (a, b) in zip(ref_tokens, times)
    ]


def make(args) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    clips = out / "clips"
    clips.mkdir(exist_ok=True)
    names = args.only or list(SCENARIOS)
    phrases = [(name, i, *p) for name in names for i, p in enumerate(SCENARIOS[name])]

    # 1. render every phrase once
    sapi_jobs, kokoro_jobs = [], []
    for name, i, voice, lang, text, _pause in phrases:
        path = clips / f"{name}_{i:02d}.wav"
        if path.exists() and not args.force:
            continue
        kind, *rest = voice.split(":")
        if kind == "sapi":
            sapi_jobs.append((rest[0], int(rest[1]), text, path))
        else:
            kokoro_jobs.append((int(rest[0]), lang, text, path))
    _sapi(sapi_jobs)
    if kokoro_jobs:
        from attune.speech_out.kokoro_tts import KokoroTTS

        tts = {}
        for sid, lang, text, path in kokoro_jobs:
            engine = tts.get(sid) or tts.setdefault(
                sid,
                KokoroTTS(
                    ROOT / "models/tts/kokoro-multi-lang-v1_0", sid=sid, sid_es=sid
                ),
            )
            pcm, rate = engine.synthesize(text, lang)
            write_wav(path, _resample(pcm, rate, SR))

    # 2. align words inside each trimmed phrase
    from faster_whisper import WhisperModel

    model = WhisperModel(
        str(ROOT / "models/faster-whisper-large-v3-turbo"),
        device=args.device,
        compute_type="int8" if args.device == "cpu" else "float16",
        local_files_only=True,
    )
    for name in names:
        rows = [p for p in phrases if p[0] == name]
        audio = [np.zeros(int(LEAD_S * SR), np.float32)]
        t = LEAD_S
        truth = []
        for _, i, voice, lang, text, pause in rows:
            pcm, rate = _read_wav(clips / f"{name}_{i:02d}.wav")
            pcm = trim(_resample(pcm, rate, SR), SR)
            words = _align(model, pcm, SR, text, lang)
            dur = len(pcm) / SR
            truth.append(
                {
                    "t0": round(t, 3),
                    "t1": round(t + dur, 3),
                    "text": text,
                    "lang": lang,
                    "voice": voice,
                    "pause_after": pause,
                    "words": [
                        [w, round(t + a, 3), round(t + b, 3)] for w, a, b in words
                    ],
                }
            )
            audio += [pcm, np.zeros(int(pause * SR), np.float32)]
            t += dur + pause
        audio.append(np.zeros(int(TAIL_S * SR), np.float32))
        pcm = np.concatenate(audio)
        write_wav(out / f"{name}.wav", pcm)
        meta = {
            "name": name,
            "wav": f"{name}.wav",
            "duration_s": round(len(pcm) / SR, 3),
        }
        (out / f"{name}.json").write_text(
            json.dumps(meta | {"phrases": truth}, indent=1, ensure_ascii=False),
            encoding="utf-8",
        )
        n_words = sum(len(p["words"]) for p in truth)
        log.info(
            "%s: %.1f s, %d phrases, %d words",
            name,
            meta["duration_s"],
            len(truth),
            n_words,
        )


# ----------------------------------------------------------------------------- run
# English references for the Spanish phrases (translation quality: word overlap)
TRANSLATIONS = {
    "Buenos días, ¿cómo estás hoy?": "Good morning, how are you today?",
    "¿Dónde está la estación de tren?": "Where is the train station?",
    "Me gustaría un café con leche, por favor.": "I would like a coffee with milk, please.",
    "Mañana vamos a la playa con mis primos.": "Tomorrow we are going to the beach with my cousins.",
    "No entiendo, ¿puedes repetirlo más despacio?": "I don't understand, can you repeat it more slowly?",
    "La reunión empieza a las tres de la tarde.": "The meeting starts at three in the afternoon.",
    "Mi hermano vive en Madrid desde hace cinco años.": "My brother has lived in Madrid for five years.",
    "Gracias por tu ayuda, eres muy amable.": "Thank you for your help, you are very kind.",
    "Mucho gusto, me llamo Carlos.": "Nice to meet you, my name is Carlos.",
    "Soy de Colombia, pero vivo aquí en Miami.": "I am from Colombia, but I live here in Miami.",
    "Muchas gracias, estoy muy contento de estar aquí.": "Thank you very much, I am very happy to be here.",
}
LOCK = Path(tempfile.gettempdir()) / "attune_gpu.lock"


class GpuLock:
    """The shared GPU lock (a folder with owner.txt); wait for it, hold it for one run."""

    def __init__(self, owner: str, stale_s: float = 20 * 60) -> None:
        self.owner, self.stale_s, self.held = owner, stale_s, False
        self._seen = ""

    def __enter__(self):
        while True:
            try:
                LOCK.mkdir()
                break
            except FileExistsError:
                who = LOCK / "owner.txt"
                try:
                    age = time.time() - who.stat().st_mtime
                    text = who.read_text(encoding="utf-8").strip()
                except OSError:
                    age, text = 0.0, "?"
                if age > self.stale_s:
                    log.warning(
                        "GPU lock held by %r for %.0f min: stale, taking it",
                        text,
                        age / 60,
                    )
                    shutil.rmtree(LOCK, ignore_errors=True)
                    continue
                if text != self._seen:
                    log.info("GPU lock held by %r; waiting", text)
                    self._seen = text
                time.sleep(0.5)  # poll often: others take it again right after releasing
        (LOCK / "owner.txt").write_text(
            f"{self.owner} {time.strftime('%Y-%m-%d %H:%M:%S')}", encoding="utf-8"
        )
        self.held = True
        return self

    def __exit__(self, *exc) -> None:
        if self.held:
            shutil.rmtree(LOCK, ignore_errors=True)
            self.held = False


def load_truth(gt: Path, names: list[str]) -> list[dict]:
    out = []
    for name in names:
        meta = json.loads((gt / f"{name}.json").read_text(encoding="utf-8"))
        pcm, rate = _read_wav(gt / meta["wav"])
        meta["pcm"] = _resample(pcm, rate, 16000)
        out.append(meta)
    return out


class Timeline:
    """All scenarios back to back as one continuous microphone stream (real time)."""

    def __init__(
        self, bus, clock, truths: list[dict], warmup: np.ndarray, gap_s: float
    ):
        from attune.core.contracts import AUDIO_BLOCK
        from attune.replay.player import BLOCK, RATES

        self.topic, self.block, self.rates = AUDIO_BLOCK, BLOCK, RATES
        self.bus, self.clock = bus, clock
        parts = [
            np.zeros(16000, np.float32),
            warmup,
            np.zeros(int(gap_s * 16000), np.float32),
        ]
        self.offsets: dict[str, float] = {}
        pos = sum(len(p) for p in parts)
        for t in truths:
            self.offsets[t["name"]] = pos / 16000
            parts += [t["pcm"], np.zeros(int(gap_s * 16000), np.float32)]
            pos += len(t["pcm"]) + int(gap_s * 16000)
        pcm16 = np.concatenate(parts).astype(np.float32)
        self.streams = {16000: pcm16, 32000: _resample(pcm16, 16000, 32000)}
        self.duration_s = len(pcm16) / 16000
        self.t0: float | None = None
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="bench-audio", daemon=True
        )

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        self.t0 = t0 = self.clock() + 0.2
        n16 = len(self.streams[16000])
        for i in range(0, n16, self.block):
            t = t0 + i / 16000
            if self.stop_event.wait(max(0.0, t - self.clock())):
                return
            for rate, data in self.streams.items():
                a, b = (
                    i * rate // 16000,
                    min(len(data), (i + self.block) * rate // 16000),
                )
                if b > a:
                    self.bus.publish(
                        self.topic, {"t": t, "sample_rate": rate, "samples": data[a:b]}
                    )

    def stop(self) -> None:
        self.stop_event.set()


class WsRecorder:
    """A console page on the engine WebSocket: every JSON message with its receive time."""

    def __init__(self, url: str, clock) -> None:
        self.url, self.clock = url, clock
        self.messages: list[tuple[float, dict]] = []
        self.counts: Counter = Counter()
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

        for _ in range(150):
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
                t = self.clock()
                if isinstance(raw, bytes):
                    self.counts["frame"] += 1
                    continue
                msg = json.loads(raw)
                kind = msg.get("type")
                self.counts[kind] += 1
                if kind in ("caption", "caption_retract", "status"):
                    self.messages.append((t, msg))


class BusTap:
    """Engine-side timestamps: VAD edges, transcripts, captions, retractions, translations."""

    def __init__(self, bus, clock) -> None:
        self.clock = clock
        self.events: list[tuple[float, str, dict]] = []
        self._speech = False
        from attune.audio.runtime import fields

        self._fields = fields
        for topic in (
            "audio.vad",
            "audio.transcript",
            "caption",
            "caption.retract",
            "caption.translation",
            "status.part",
        ):
            bus.subscribe(topic, lambda ev, topic=topic: self._on(topic, ev))

    def _on(self, topic: str, ev) -> None:
        t = self.clock()
        e = self._fields(ev)
        if topic == "audio.vad":
            if bool(e.get("is_speech")) != self._speech:
                self._speech = not self._speech
                self.events.append((t, "vad", {"t": e["t"], "speech": self._speech}))
            return
        if topic == "status.part":
            if e.get("part") in ("vision", "audio", "llm", "fusion"):
                self.events.append(
                    (t, "status", {"part": e["part"], "metrics": e.get("metrics")})
                )
            return
        if topic == "caption":
            sp = self._fields(e.get("speaker") or {})
            e = {k: e.get(k) for k in ("utt_id", "text", "final", "lang", "words")}
            e["speaker"] = {"kind": sp.get("kind"), "track_id": sp.get("track_id")}
        elif topic == "audio.transcript":
            e = {
                k: e.get(k)
                for k in (
                    "utt_id",
                    "text",
                    "final",
                    "lang",
                    "words",
                    "t_start",
                    "t_end",
                )
            }
        self.events.append((t, topic, json.loads(json.dumps(e, default=str))))


class GpuSampler:
    def __init__(self) -> None:
        self.samples: list[tuple[float, float]] = []
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(timeout=3)

    def _run(self) -> None:
        cmd = [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used",
            "--format=csv,noheader,nounits",
        ]
        while not self._stop.wait(1.0):
            try:
                out = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=3, check=False
                ).stdout
                util, mem = (float(x) for x in out.strip().splitlines()[0].split(","))
                self.samples.append((util, mem))
            except Exception:  # noqa: BLE001 - no GPU, no samples
                return


def _history_rows(db_path: Path) -> list[dict]:
    if not db_path.is_file():
        return []
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM rows ORDER BY engine_t")]
    finally:
        con.close()


def run(args) -> dict:
    from attune.config import load_config
    from attune.core import clock
    from attune.main import Engine, Options, preload_torch

    gt = Path(args.gt)
    names = args.scenarios or [n for n in SCENARIOS if (gt / f"{n}.json").exists()]
    truths = load_truth(gt, names)
    warm, rate = _read_wav(gt / "clips" / "en_phrases_00.wav")
    warmup = _resample(trim(warm, rate), rate, 16000)

    preload_torch()
    tmp = tempfile.TemporaryDirectory(prefix="attune-bench-")
    config = load_config(args.config, cwd=ROOT)
    config.setdefault("engine", {})["data_dir"] = tmp.name
    db_path = Path(tmp.name) / "history.db"
    config.setdefault("history", {})["db_path"] = str(db_path)
    config.setdefault("vision", {})["people_dir"] = os.path.join(tmp.name, "people")
    for kv in args.set or []:
        key, value = kv.split("=", 1)
        table, name = key.split(".", 1)
        try:
            config.setdefault(table, {})[name] = json.loads(value)
        except json.JSONDecodeError:
            config.setdefault(table, {})[name] = value
    opts = Options(
        source=args.video,
        no_mic=True,
        port=args.port,
        no_browser=True,
        simulate_hardware=True,
    )
    cwd = os.getcwd()
    os.chdir(ROOT)
    pages = None
    with GpuLock("SPEED"):
        engine = Engine(opts, config)
        tap = BusTap(engine.bus, clock.now)
        timeline = Timeline(engine.bus, clock.now, truths, warmup, args.gap)
        rec = WsRecorder(f"ws://127.0.0.1:{args.port}/ws", clock.now)
        gpu = GpuSampler()
        cpu0 = os.times()
        try:
            engine.start()
            rec.start()
            rec.connected.wait(30)
            if args.pages:
                pages = PageRecorder(args.port, Path(tmp.name))
                pages.start()
            time.sleep(args.settle)
            gpu.start()
            cpu0, w0 = os.times(), time.perf_counter()
            timeline.start()
            log.info("playing %d scenarios, %.0f s", len(truths), timeline.duration_s)
            while timeline.t0 is None:
                time.sleep(0.05)
            end = timeline.t0 + timeline.duration_s + args.tail
            while clock.now() < end:
                time.sleep(0.2)
            cpu1, w1 = os.times(), time.perf_counter()
        finally:
            timeline.stop()
            gpu.stop()
            if pages:
                pages.stop()
            rec.stop()
            engine.stop()
            os.chdir(cwd)
        history = _history_rows(db_path)
        page_log = pages.read() if pages else None
        tmp.cleanup()
    cpu_s = (cpu1.user - cpu0.user) + (cpu1.system - cpu0.system)
    return {
        "label": args.label,
        "sets": args.set or [],
        "t0": timeline.t0,
        "offsets": timeline.offsets,
        "truth": [{k: v for k, v in t.items() if k != "pcm"} for t in truths],
        "ws": rec.messages,
        "ws_counts": dict(rec.counts),
        "bus": tap.events,
        "history": history,
        "pages": page_log,
        "cpu_cores": round(cpu_s / max(w1 - w0, 1e-6), 2),
        "gpu": gpu.samples,
        "wall_s": round(w1 - w0, 1),
    }


class PageRecorder:
    """Headless Edge (playwright-core) on /lens/ and /phone/, logging when words are drawn."""

    NODE_DIR = ROOT.parent / "attune-video" / "render"

    def __init__(self, port: int, out: Path) -> None:
        self.port, self.out = port, out / "pages.jsonl"
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        script = Path(__file__).with_name("bench_pages.mjs")
        # page times are epoch seconds; the engine clock is perf_counter
        self.epoch_to_clock = time.perf_counter() - time.time()
        self.proc = subprocess.Popen(
            ["node", str(script), str(self.port), str(self.out), str(self.NODE_DIR)],
            cwd=str(self.NODE_DIR),
            stdin=subprocess.PIPE,
        )
        deadline = time.time() + 30
        while time.time() < deadline and not self.out.exists():
            time.sleep(0.2)
        time.sleep(2.0)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(b"q\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                self.proc.kill()

    def read(self) -> list[dict]:
        if not self.out.exists():
            return []
        rows = [
            json.loads(line)
            for line in self.out.read_text(encoding="utf-8").splitlines()
            if line
        ]
        for row in rows:
            row["t"] = row["t"] + self.epoch_to_clock
        return rows


# ----------------------------------------------------------------------------- score
@dataclass
class _Word:
    text: str
    norm: list[str]
    t0: float
    t1: float
    phrase: int
    lang: str
    draft_t: float | None = None  # first received in a draft
    final_t: float | None = None  # first received in a final
    history: bool = False
    lens_t: float | None = None
    phone_t: float | None = None


def _q(values: list[float], p: float) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    k = (len(values) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 3)


def _hyp_words(words) -> list[tuple[str, float, float]]:
    out = []
    for w in words or []:
        text, a, b = (
            (w[0], w[1], w[2])
            if not isinstance(w, dict)
            else (w["word"], w["t0"], w["t1"])
        )
        for n in norm_words(str(text)):
            out.append((n, float(a), float(b)))
    return out


def _match(
    truth: list[_Word], hyp: list[tuple[str, float, float]], slack: float = 1.5
) -> list[int]:
    """Indexes of ground-truth words that this hypothesis shows (same word, near in time)."""
    if not hyp:
        return []
    lo, hi = hyp[0][1] - slack, hyp[-1][2] + slack
    cand = [i for i, w in enumerate(truth) if w.t1 >= lo and w.t0 <= hi]
    ref = [truth[i].norm[0] if truth[i].norm else "" for i in cand]
    hyp_n = [h[0] for h in hyp]
    sm = difflib.SequenceMatcher(a=ref, b=hyp_n, autojunk=False)
    out = []
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            i = cand[a + k]
            h = hyp[b + k]
            mid_t, mid_h = (truth[i].t0 + truth[i].t1) / 2, (h[1] + h[2]) / 2
            if abs(mid_t - mid_h) <= slack:
                out.append(i)
    return out


def _truth_words(meta: dict, t0: float) -> list[_Word]:
    words = []
    for pi, p in enumerate(meta["phrases"]):
        for w, a, b in p["words"]:
            n = norm_words(w)
            if not n:
                continue
            # a token that spells out as several words ("4:30") stays one scored word
            words.append(_Word(w, n, t0 + a, t0 + b, pi, p["lang"]))
    return words


def score(run_data: dict) -> dict:
    t0 = run_data["t0"]
    out = {
        "label": run_data["label"],
        "sets": run_data.get("sets", []),
        "scenarios": {},
    }
    ws = run_data["ws"]
    bus = run_data["bus"]
    history = run_data.get("history") or []
    pages = run_data.get("pages") or []
    for meta in run_data["truth"]:
        name = meta["name"]
        start = t0 + run_data["offsets"][name]
        end = start + meta["duration_s"]
        words = _truth_words(meta, start)
        lo, hi = start - 0.5, end + 3.0
        caps = [
            (t, m) for t, m in ws if m.get("type") == "caption" and lo <= t <= hi + 5
        ]
        # only captions whose words fall inside this scenario
        caps = [
            (t, m)
            for t, m in caps
            if m.get("words") and lo <= float(m["words"][0][1]) <= hi
        ]
        retracts = [
            (t, m)
            for t, m in ws
            if m.get("type") == "caption_retract" and lo <= t <= hi + 5
        ]
        final_text: dict[str, tuple[float, dict]] = {}
        first_final: dict[str, float] = {}
        speakers: dict[str, tuple] = {}
        relabels = 0
        translations: dict[str, tuple[float, str]] = {}
        draft_wer_num = draft_wer_den = 0
        for t, m in caps:
            hyp = _hyp_words(m.get("words"))
            idx = _match(words, hyp)
            final = bool(m.get("final"))
            for i in idx:
                w = words[i]
                if final:
                    if w.final_t is None:
                        w.final_t = t
                elif w.draft_t is None:
                    w.draft_t = t
            uid = str(m["utt_id"])
            sp = m.get("speaker") or {}
            key = (sp.get("kind"), sp.get("track_id"), sp.get("label"))
            if uid in speakers and speakers[uid] != key:
                relabels += 1
            speakers[uid] = key
            if final:
                final_text[uid] = (t, m)
                first_final.setdefault(uid, t)
                if m.get("translation") and uid not in translations:
                    translations[uid] = (t, m["translation"])
            else:
                ref = [
                    w.norm[0]
                    for w in words
                    if hyp and hyp[0][1] - 0.3 <= (w.t0 + w.t1) / 2 <= hyp[-1][2]
                ]
                if ref:
                    s, dl, ins, _ = edit_ops(ref, [h[0] for h in hyp])
                    draft_wer_num += s + dl + ins
                    draft_wer_den += len(ref)
        retracted = {str(m["utt_id"]) for _, m in retracts}
        finals = sorted(
            (v for k, v in final_text.items() if k not in retracted),
            key=lambda v: float(v[1]["words"][0][1]),
        )
        hyp_all = [n for _, m in finals for n in norm_words(m.get("text", ""))]
        ref_all = [n for w in words for n in w.norm]
        # history: its caption rows in this scenario's time span
        hist = [
            r
            for r in history
            if r.get("kind") == "caption" and lo <= (r.get("engine_t") or 0) <= hi
        ]
        hist_words = [
            (
                n,
                float(r["engine_t"]),
                float(r["engine_t"]) + float(r.get("duration_s") or 0),
            )
            for r in hist
            for n in norm_words(r.get("text") or "")
        ]
        for i in _match(words, hist_words, slack=max(3.0, meta["duration_s"])):
            words[i].history = True
        # pages: first time each word was drawn on the lens / the phone. A page row is the
        # text a card shows; it is aligned (in order) with the words said in the 20 s before.
        moves = 0
        for row in pages:
            t = row.get("t", 0)
            if not (lo <= t <= hi + 5):
                continue
            if "move" in row:
                moves += 1
                continue
            if row.get("page") not in ("lens", "phone"):
                continue
            attr = "lens_t" if row["page"] == "lens" else "phone_t"
            seen = norm_words(row.get("text", ""))
            cand = [w for w in words if w.norm and w.t0 <= t + 0.3 and w.t1 >= t - 20]
            sm = difflib.SequenceMatcher(
                a=[w.norm[0] for w in cand], b=seen, autojunk=False
            )
            for a, _b, size in sm.get_matching_blocks():
                for k in range(size):
                    if getattr(cand[a + k], attr) is None:
                        setattr(cand[a + k], attr, t)
        shown = [
            min(x for x in (w.draft_t, w.final_t) if x is not None) - w.t1
            for w in words
            if w.draft_t or w.final_t
        ]
        to_final = [w.final_t - w.t1 for w in words if w.final_t]
        phrases = defaultdict(list)
        for w in words:
            phrases[w.phrase].append(w)
        onset, end_final = [], []
        for ws_ in phrases.values():
            firsts = [
                min(x for x in (w.draft_t, w.final_t) if x is not None)
                for w in ws_
                if w.draft_t or w.final_t
            ]
            if firsts:
                onset.append(min(firsts) - ws_[0].t0)
            finals_t = [w.final_t for w in ws_ if w.final_t]
            if finals_t:
                end_final.append(max(finals_t) - ws_[-1].t1)
        # VAD speech onsets inside the scenario
        vad_on = [
            e["t"]
            for t, k, e in bus
            if k == "vad" and e["speech"] and lo <= e["t"] <= hi
        ]
        tr = []
        tr_ok = []
        for uid, (t_tr, text_en) in translations.items():
            t_final = first_final[uid]
            tr.append(round(t_tr - t_final, 3))
            src = final_text[uid][1].get("text", "")
            ref_en = next(
                (
                    TRANSLATIONS[p["text"]]
                    for p in meta["phrases"]
                    if p["text"] in TRANSLATIONS
                    and set(norm_words(p["text"])) & set(norm_words(src))
                ),
                None,
            )
            if ref_en:
                r, h = set(norm_words(ref_en)), set(norm_words(text_en))
                tr_ok.append(len(r & h) / max(len(r), 1))
        n_non_en_finals = sum(
            1 for _, m in finals if (m.get("lang") or "en") not in ("en", "und")
        )
        lost = [w.text for w in words if not w.draft_t and not w.final_t]
        out["scenarios"][name] = {
            "words": len(words),
            "onset_first_word_p50": _q(onset, 0.5),
            "onset_first_word_p90": _q(onset, 0.9),
            "word_shown_p50": _q(shown, 0.5),
            "word_shown_p90": _q(shown, 0.9),
            "word_final_p50": _q(to_final, 0.5),
            "word_final_p90": _q(to_final, 0.9),
            "end_final_p50": _q(end_final, 0.5),
            "end_final_p90": _q(end_final, 0.9),
            "wer_final": round(wer(ref_all, hyp_all), 3) if ref_all else None,
            "cer_final": round(cer(" ".join(ref_all), " ".join(hyp_all)) or 0, 3),
            "wer_draft": round(draft_wer_num / draft_wer_den, 3)
            if draft_wer_den
            else None,
            "coverage_final": round(sum(1 for w in words if w.final_t) / len(words), 3),
            "coverage_shown": round(
                sum(1 for w in words if w.final_t or w.draft_t) / len(words), 3
            ),
            "coverage_history": round(
                sum(1 for w in words if w.history) / len(words), 3
            ),
            "coverage_lens": round(sum(1 for w in words if w.lens_t) / len(words), 3)
            if pages
            else None,
            "coverage_phone": round(sum(1 for w in words if w.phone_t) / len(words), 3)
            if pages
            else None,
            "lens_shown_p50": _q([w.lens_t - w.t1 for w in words if w.lens_t], 0.5)
            if pages
            else None,
            "phone_shown_p50": _q([w.phone_t - w.t1 for w in words if w.phone_t], 0.5)
            if pages
            else None,
            "lost": lost,
            "retractions": len(retracts),
            "relabels": relabels,
            "lens_moves": moves if pages else None,
            "segments": len(final_text),
            "vad_onsets": len(vad_on),
            "translation_after_final_p50": _q(tr, 0.5),
            "translation_after_final_max": max(tr) if tr else None,
            "translations": len(tr),
            "non_en_finals": n_non_en_finals,
            "translation_overlap": round(statistics.mean(tr_ok), 3) if tr_ok else None,
            "final_text": [m.get("text") for _, m in finals],
        }
    wall = max(run_data.get("wall_s") or 1, 1)
    out["rates"] = {
        k: round(v / wall, 2) for k, v in run_data.get("ws_counts", {}).items()
    }
    out["cpu_cores"] = run_data.get("cpu_cores")
    g = run_data.get("gpu") or []
    out["gpu_util_mean"] = round(statistics.mean(u for u, _ in g), 1) if g else None
    out["gpu_mem_max"] = max(m for _, m in g) if g else None
    # engine-side: transcript -> caption publish delay (fusion), caption bus -> WS receive
    tr_t = {}
    for t, k, e in bus:
        if k == "audio.transcript":
            tr_t.setdefault((e["utt_id"], e["text"], e["final"]), t)
    cap_delay = []
    for t, k, e in bus:
        if k == "caption":
            base = str(e["utt_id"]).split(".")[0]
            cands = [
                tt
                for (u, _txt, f), tt in tr_t.items()
                if u == base and f == e["final"] and tt <= t
            ]
            if cands:
                cap_delay.append(t - max(cands))
    out["fusion_delay_p50"] = _q(cap_delay, 0.5)
    out["fusion_delay_p90"] = _q(cap_delay, 0.9)
    return out


SUMMARY_KEYS = [
    ("onset_first_word_p50", "onset->1st word p50"),
    ("word_shown_p50", "word shown p50"),
    ("word_shown_p90", "word shown p90"),
    ("word_final_p50", "word final p50"),
    ("end_final_p50", "end->final p50"),
    ("wer_final", "WER final"),
    ("wer_draft", "WER draft"),
    ("coverage_final", "in finals"),
    ("coverage_shown", "shown"),
    ("coverage_history", "in history"),
    ("coverage_lens", "on lens"),
    ("coverage_phone", "on phone"),
    ("lens_shown_p50", "lens word p50"),
    ("phone_shown_p50", "phone word p50"),
    ("retractions", "retracts"),
    ("relabels", "relabels"),
    ("lens_moves", "lens moves"),
    ("translation_after_final_p50", "transl. p50"),
]


def _agg(scores: dict) -> dict:
    """Word-weighted means over scenarios (latency medians averaged by words)."""
    sc = scores["scenarios"]
    total = sum(s["words"] for s in sc.values())
    agg = {}
    for key, _ in SUMMARY_KEYS:
        vals = [
            (s[key], s["words"])
            for s in sc.values()
            if isinstance(s.get(key), (int, float))
        ]
        if not vals:
            agg[key] = None
        elif key in ("retractions", "relabels", "lens_moves"):
            agg[key] = sum(v for v, _ in vals)
        else:
            agg[key] = round(sum(v * n for v, n in vals) / sum(n for _, n in vals), 3)
    agg["words"] = total
    agg["lost"] = sum(len(s["lost"]) for s in sc.values())
    return agg


def table(paths: list[str]) -> str:
    runs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    lines = ["| metric | " + " | ".join(r["label"] for r in runs) + " |"]
    lines.append("|---|" + "---|" * len(runs))
    aggs = [_agg(r) for r in runs]
    for key, title in SUMMARY_KEYS + [("lost", "words never shown")]:
        cells = []
        for a in aggs:
            v = a.get(key)
            cells.append(
                ""
                if v is None
                else f"{v:.3f}".rstrip("0").rstrip(".")
                if isinstance(v, float)
                else str(v)
            )
        lines.append(f"| {title} | " + " | ".join(cells) + " |")
    for key in ("fusion_delay_p50", "cpu_cores", "gpu_util_mean"):
        lines.append(f"| {key} | " + " | ".join(str(r.get(key)) for r in runs) + " |")
    lines.append("")
    names = list(runs[0]["scenarios"])
    for key, title in (
        ("word_shown_p50", "word shown p50 (s)"),
        ("end_final_p50", "end->final p50 (s)"),
        ("wer_final", "WER final"),
        ("coverage_final", "words in finals"),
    ):
        lines.append(f"| {title} | " + " | ".join(r["label"] for r in runs) + " |")
        lines.append("|---|" + "---|" * len(runs))
        for n in names:
            cells = [str(r["scenarios"].get(n, {}).get(key)) for r in runs]
            lines.append(f"| {n} | " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make", help="render the ground-truth test speech")
    m.add_argument("--out", required=True)
    m.add_argument("--only", nargs="+", choices=list(SCENARIOS))
    m.add_argument(
        "--device", default="cpu", help="faster-whisper device for alignment"
    )
    m.add_argument("--force", action="store_true", help="render phrases again")
    r = sub.add_parser("run", help="run the engine on the test speech and score it")
    r.add_argument("--gt", required=True, help="folder written by `make`")
    r.add_argument("--label", required=True)
    r.add_argument("--out", help="results folder (default: <gt>/../runs)")
    r.add_argument("--scenarios", nargs="+", choices=list(SCENARIOS))
    r.add_argument(
        "--video",
        default=str(Path(tempfile.gettempdir()) / "liveclip1" / "clip.mp4"),
        help="video file for the camera (loops)",
    )
    r.add_argument(
        "--config", help="engine config (default: config/attune.toml if present)"
    )
    r.add_argument(
        "--set", action="append", help="config override, e.g. audio.asr_chunk_ms=160"
    )
    r.add_argument("--port", type=int, default=8012)
    r.add_argument(
        "--gap", type=float, default=3.0, help="silence between scenarios, s"
    )
    r.add_argument(
        "--tail", type=float, default=5.0, help="recording after the audio, s"
    )
    r.add_argument("--settle", type=float, default=4.0, help="wait after start-up, s")
    r.add_argument(
        "--pages", action="store_true", help="also record the lens and phone pages"
    )
    s = sub.add_parser("score", help="score a raw run file again")
    s.add_argument("raw")
    t = sub.add_parser("table", help="markdown table comparing scored runs")
    t.add_argument("scores", nargs="+")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("ATTUNE_LOG", "WARNING").upper(),
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log.setLevel(logging.INFO)
    if args.cmd == "make":
        make(args)
    elif args.cmd == "run":
        out = Path(args.out or Path(args.gt).parent / "runs")
        out.mkdir(parents=True, exist_ok=True)
        raw = run(args)
        (out / f"{args.label}.raw.json").write_text(json.dumps(raw), encoding="utf-8")
        scores = score(raw)
        path = out / f"{args.label}.json"
        path.write_text(
            json.dumps(scores, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        print(table([str(path)]))
    elif args.cmd == "score":
        raw = json.loads(Path(args.raw).read_text(encoding="utf-8"))
        scores = score(raw)
        path = Path(args.raw.replace(".raw.json", ".json"))
        path.write_text(
            json.dumps(scores, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        print(table([str(path)]))
    elif args.cmd == "table":
        print(table(args.scores))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
