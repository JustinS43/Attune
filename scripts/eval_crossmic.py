"""Cross-microphone speaker verification for CAM++ voice prints (station mic -> glasses mic).

A person is enrolled at the laptop ("station" mic) from the enrollment sentence and is
later recognised on the glasses mic (Brio 101) from 1-4 s utterances. This script
measures how CAM++ cosine scores behave across that mic change, using TTS voices
(Kokoro English voices and the Windows SAPI voices David and Zira) as stand-in talkers.

    python scripts/eval_crossmic.py --synth              # make TTS clips into --out/tts
    python scripts/eval_crossmic.py --record             # play clips, record both mics
    python scripts/eval_crossmic.py --simulate           # file-level channel simulation
    python scripts/eval_crossmic.py --synth --simulate --record
    python scripts/eval_crossmic.py --probe              # one clip: mic levels only
    python scripts/eval_crossmic.py --clean              # delete --out when done

Every step also runs the analysis of whatever is present in --out and prints tables.

--record plays each clip once through the default WASAPI output (normal volume, the
system volume is never changed) while two WASAPI shared-mode input streams record the
laptop array mic and the Brio 101 mic at the same time. It refuses to run if a device
is missing. Nothing is written outside --out, which must lie under
%TEMP%\\attune_eval (default %TEMP%\\attune_eval\\ENROLL); delete it with --clean.

Scores are cosines of unit CAM++ vectors. Prints come from voiced speech (Silero VAD)
of the enrollment sentence; tests are short sentences, optionally cropped to 1.0 /
1.5 / 2.0 s of voiced speech. Refinement simulation: bank = k glasses embeddings of
other utterances of the claimed voice (only those whose station score passes the
adapt gate), score = max(station score, cosine to the unit mean of the bank).

By default audio is tiled to a multiple of 2.0 s before CAM++ (--fit tile), as the engine's
CAMExtractor does since A-21; rows marked BEFORE-FIX use --fit none, the clip as it is, as
the engine did before that fix (see Embedder).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

ENGINE = Path(__file__).resolve().parents[1] / "engine"
MODELS = Path(__file__).resolve().parents[1] / "models"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

EVAL_ROOT = Path(os.environ.get("TEMP", "/tmp")) / "attune_eval"
SR = 16000
ENROLL_TEXT = (
    "Every morning I walk my dog through the park, buy fresh bread and orange juice, "
    "and enjoy watching children play by the quiet river."
)
SHORTS = [
    "I left my keys on the kitchen table this morning.",
    "Could you pass me the salt and pepper, please?",
    "The bus was late again on the way to work today.",
    "Let's meet at the coffee shop after lunch.",
    "It looks like it might rain later this afternoon.",
    "I need to buy some milk and eggs at the store.",
    "Did you remember to water the plants yesterday?",
    "The movie starts at seven thirty tonight.",
    "Please turn the music down a little bit.",
    "We should clean out the garage this weekend.",
    "My phone battery is almost empty again.",
    "That soup smells really good, can I have some?",
]
CLIPS = ["A"] + [f"s{i + 1:02d}" for i in range(len(SHORTS))]
TEXTS = dict(zip(CLIPS, [ENROLL_TEXT, *SHORTS], strict=True))
KOKORO_EN = [
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole",
    "af_nova", "af_river", "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir",
    "am_liam", "am_michael", "am_onyx", "am_puck", "bf_alice", "bf_emma", "bf_isabella",
    "bf_lily", "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
]  # fmt: skip
KOKORO_SID = {name: i for i, name in enumerate(KOKORO_EN[:19])} | {
    name: 20 + i for i, name in enumerate(KOKORO_EN[19:])
}  # am_santa (19) is left out: a novelty voice
SAPI = {"sapi_david": "Microsoft David Desktop", "sapi_zira": "Microsoft Zira Desktop"}
VOICES = KOKORO_EN + list(SAPI)
LIVE_VOICES = [
    "af_heart", "af_bella", "af_sarah", "am_michael", "am_eric", "am_onyx",
    "bf_emma", "bf_isabella", "bm_george", "bm_lewis", "sapi_david", "sapi_zira",
]  # fmt: skip
LIVE_CLIPS = ["A", "s01", "s02", "s04", "s05"]
STATION_MIC = "Microphone Array on SoundWire Device"
GLASSES_MIC = "Microphone (Brio 101)"
MATCH = 0.5  # [fusion] voice_match today


# ---------------------------------------------------------------- files and safety


def _inside(path: Path, root: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(root.resolve()):
        raise SystemExit(f"refusing to write outside {root}: {path}")
    return path


def write_wav(path: Path, x: np.ndarray, sr: int, root: Path) -> None:
    path = _inside(path, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"{path}: only 16-bit PCM is supported")
    x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
    return x.reshape(-1, ch).mean(axis=1), sr


def to16k(x: np.ndarray, sr: int) -> np.ndarray:
    import soxr

    return x.astype(np.float32) if sr == SR else soxr.resample(x, sr, SR).astype(np.float32)


# ---------------------------------------------------------------- synthesis


SAPI_PS1 = r"""
param([string]$Voice, [string]$Dir, [string]$Names, [string]$Texts)
Add-Type -AssemblyName System.Speech
$nameList = $Names -split '\|'; $textList = $Texts -split '\|'
for ($i = 0; $i -lt $nameList.Count; $i++) {
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.SelectVoice($Voice)
  $s.SetOutputToWaveFile((Join-Path $Dir ($nameList[$i] + '.wav')))
  $s.Speak($textList[$i])
  $s.Dispose()
}
"""


def synth(out: Path) -> None:
    from attune.speech_out.kokoro_tts import KokoroTTS

    tts = KokoroTTS(MODELS / "tts" / "kokoro-multi-lang-v1_0", num_threads=4)
    t0 = time.time()
    for voice in KOKORO_EN:
        tts.sid = KOKORO_SID[voice]
        for clip in CLIPS:
            path = out / "tts" / voice / f"{clip}.wav"
            if not path.exists():
                x, sr = tts.synthesize(TEXTS[clip])
                write_wav(path, 0.9 * x / max(1e-6, float(np.abs(x).max())), sr, out)
        print(f"  synth {voice} ({time.time() - t0:.0f} s)", flush=True)
    script = _inside(out / "tts" / "sapi.ps1", out)
    script.write_text(SAPI_PS1, encoding="utf-8")
    for voice, sapi_name in SAPI.items():
        folder = _inside(out / "tts" / voice, out)
        folder.mkdir(parents=True, exist_ok=True)
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
        cmd += [sapi_name, str(folder), "|".join(CLIPS), "|".join(TEXTS[c] for c in CLIPS)]
        subprocess.run(cmd, check=True)
        if not all((folder / f"{clip}.wav").exists() for clip in CLIPS):
            raise SystemExit(f"SAPI synthesis failed for {voice}")
        for clip in CLIPS:  # peak-normalise like the Kokoro clips
            x, sr = read_wav(folder / f"{clip}.wav")
            write_wav(folder / f"{clip}.wav", 0.9 * x / max(1e-6, float(np.abs(x).max())), sr, out)
        print(f"  synth {voice}", flush=True)
    script.unlink()


# ---------------------------------------------------------------- live recording


def find_devices() -> tuple[int, int, int]:
    import sounddevice as sd

    apis = sd.query_hostapis()
    wasapi = next((i for i, a in enumerate(apis) if a["name"] == "Windows WASAPI"), None)
    if wasapi is None:
        raise SystemExit("--record: no Windows WASAPI host API")
    devices = sd.query_devices()

    def find(fragment: str) -> int:
        for i in apis[wasapi]["devices"]:
            if fragment in devices[i]["name"] and devices[i]["max_input_channels"] > 0:
                return i
        raise SystemExit(f"--record: input device containing {fragment!r} not found")

    output = apis[wasapi]["default_output_device"]
    if output < 0:
        raise SystemExit("--record: no default WASAPI output device")
    return find(STATION_MIC), find(GLASSES_MIC), output


class Recorder:
    """A shared-mode WASAPI input stream that keeps every block (no exclusive settings)."""

    def __init__(self, device: int):
        import sounddevice as sd

        info = sd.query_devices(device)
        self.rate = int(info["default_samplerate"])
        self.blocks: list[np.ndarray] = []
        self.frames = 0
        self.status = 0
        self.lock = threading.Lock()
        self.stream = sd.InputStream(
            device=device,
            samplerate=self.rate,
            channels=min(2, info["max_input_channels"]),
            dtype="float32",
            callback=self._callback,
        )  # default extra_settings = WASAPI shared mode

    def _callback(self, data, _frames, _time, status) -> None:
        with self.lock:
            self.blocks.append(data.mean(axis=1).copy())
            self.frames += len(data)
            self.status += bool(status)

    def mark(self) -> int:
        with self.lock:
            return self.frames

    def span(self, start: int, end: int) -> np.ndarray:
        with self.lock:
            audio = np.concatenate(self.blocks) if self.blocks else np.zeros(0, np.float32)
        return audio[start:end]


def dbfs(x: np.ndarray) -> str:
    rms = float(np.sqrt(np.mean(x**2))) if len(x) else 0.0
    peak = float(np.abs(x).max()) if len(x) else 0.0
    return f"rms {20 * np.log10(rms + 1e-9):6.1f} dBFS, peak {20 * np.log10(peak + 1e-9):6.1f} dBFS"


def record(out: Path, voices: list[str], clips: list[str], save: bool = True) -> float:
    """Play each clip once and cut the matching span (0.5 s lead-in and tail) from both mics."""
    import sounddevice as sd
    import soxr

    station_i, glasses_i, output_i = find_devices()
    out_rate = int(sd.query_devices(output_i)["default_samplerate"])
    print(f"  devices: station={station_i} glasses={glasses_i} output={output_i} ({out_rate} Hz)")
    missing = [v for v in voices for c in clips if not (out / "tts" / v / f"{c}.wav").exists()]
    if missing:
        raise SystemExit("--record needs --synth first")
    station, glasses = Recorder(station_i), Recorder(glasses_i)
    played = 0.0
    station.stream.start()
    glasses.stream.start()
    try:
        time.sleep(1.0)
        quiet = {
            name: rec.span(rec.mark() - rec.rate // 2, rec.mark())
            for rec, name in ((station, "station"), (glasses, "glasses"))
        }
        for name, x in quiet.items():
            print(f"  room before playback, {name}: {dbfs(x)}")
        for voice in voices:
            for clip in clips:
                x, sr = read_wav(out / "tts" / voice / f"{clip}.wav")
                x48 = soxr.resample(0.6 * x, sr, out_rate).astype(np.float32)
                a, b = station.mark(), glasses.mark()
                time.sleep(0.5)
                sd.play(x48, out_rate, device=output_i)
                sd.wait()
                played += len(x48) / out_rate
                time.sleep(0.5)
                for rec, start, name in ((station, a, "station"), (glasses, b, "glasses")):
                    y = to16k(rec.span(start, rec.mark()), rec.rate)
                    if not save:
                        print(f"  {voice}/{clip} {name}: {dbfs(y)}")
                    else:
                        write_wav(out / "live" / voice / f"{clip}_{name}.wav", y, SR, out)
                time.sleep(0.3)
            print(f"  recorded {voice} (played {played:.1f} s so far)", flush=True)
    finally:
        station.stream.stop()
        glasses.stream.stop()
        station.stream.close()
        glasses.stream.close()
    print(f"  input status flags: station {station.status}, glasses {glasses.status}")
    return played


# ---------------------------------------------------------------- channel simulation


def bandpass(x: np.ndarray, lo: float, hi: float, order: int = 4) -> np.ndarray:
    n = len(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    f[0] = 1e-3
    h = 1 / np.sqrt(1 + (lo / f) ** (2 * order)) / np.sqrt(1 + (f / hi) ** (2 * order))
    return np.fft.irfft(np.fft.rfft(x) * h, n).astype(np.float32)


def room(x: np.ndarray, rt60: float, drr_db: float, rng: np.random.Generator) -> np.ndarray:
    """Exponentially decaying noise RIR with a direct path at a given direct/reverb ratio."""
    n = int(rt60 * SR)
    t = np.arange(n) / SR
    tail = rng.standard_normal(n) * np.exp(-6.9 * t / rt60)
    tail[: int(0.003 * SR)] = 0
    tail *= 10 ** (-drr_db / 20) / np.sqrt(np.sum(tail**2))
    tail[0] = 1.0
    y = np.fft.irfft(np.fft.rfft(x, len(x) + n) * np.fft.rfft(tail, len(x) + n))[: len(x)]
    return (y / np.sqrt(np.sum(tail**2))).astype(np.float32)


def pink(n: int, rng: np.random.Generator) -> np.ndarray:
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(len(spec), dtype=np.float64)
    f[0] = 1
    return np.fft.irfft(spec / np.sqrt(f), n).astype(np.float32)


CHANNELS = {
    "sim_station": {"lo": 100, "hi": 7500, "rt60": 0.3, "drr": 6.0, "snr": 25.0, "gain": 1.0},
    "sim_glasses": {"lo": 150, "hi": 7000, "rt60": 0.5, "drr": 0.0, "snr": 12.0, "gain": 0.3},
}
# A talkative room (the live room measured 60-90% speech frames): babble of 3 other voices.
BABBLE = {"sim_station_babble": ("sim_station", 10.0), "sim_glasses_babble": ("sim_glasses", 5.0)}


def simulate(
    x: np.ndarray,
    channel: str,
    rng: np.random.Generator,
    babble: np.ndarray | None = None,
    babble_snr: float = 10.0,
) -> np.ndarray:
    """Band-limit, reverberate and add pink noise (and optionally babble at babble_snr dB)."""
    c = CHANNELS[channel]
    pad = np.zeros(int(0.4 * SR), np.float32)
    x = np.concatenate([pad, x, pad])
    y = bandpass(room(x, c["rt60"], c["drr"], rng), c["lo"], c["hi"])
    power = float(np.mean(y[np.abs(y) > 0.02 * np.abs(y).max()] ** 2))
    noise = pink(len(y), rng)
    noise *= np.sqrt(power / 10 ** (c["snr"] / 10) / np.mean(noise**2))
    if babble is not None:
        b = bandpass(room(np.resize(babble, len(y)), 0.6, -3.0, rng), c["lo"], c["hi"])
        noise += b * np.sqrt(power / 10 ** (babble_snr / 10) / np.mean(b**2))
    return (c["gain"] * (y + noise)).astype(np.float32)


# ---------------------------------------------------------------- embeddings


class Embedder:
    """CAM++ (as the engine loads it) plus Silero VAD trimming.

    fit="tile" repeats the audio up to the next multiple of 2.0 s (32000 samples) before
    extraction (the engine's CAMExtractor does this since A-21); fit="none" passes it as is,
    like the engine before that fix. With this cam++.onnx
    and sherpa-onnx the raw embedding norm follows a 2.0 s sawtooth (it jumps just past
    each multiple of 32000 samples) and the jump adds a direction shared by all talkers,
    so unfitted lengths give high impostor scores.
    """

    def __init__(self, fit: str = "tile"):
        self.fit = fit
        from attune.audio.vad import SileroVAD
        from attune.audio.voiceprint import CAMExtractor

        self.cam = CAMExtractor(str(MODELS / "cam++.onnx"), "cpu")
        self.vad = SileroVAD()

    def voiced(self, x: np.ndarray) -> np.ndarray:
        """Silero frames with prob > 0.5, widened by 2 frames on each side."""
        self.vad.reset()
        n = len(x) // 512
        speech = np.array([self.vad(x[i * 512 : (i + 1) * 512]) > 0.5 for i in range(n)])
        if not speech.any():
            return np.zeros(0, np.float32)
        wide = np.convolve(speech.astype(int), np.ones(5, int), mode="same") > 0
        return np.concatenate([x[i * 512 : (i + 1) * 512] for i in np.flatnonzero(wide)])

    def __call__(self, x: np.ndarray, fit: str | None = None) -> np.ndarray:
        if (fit or self.fit) == "tile":
            v = self.cam(x)  # the engine's extractor fits the length itself (A-21)
        else:  # the clip exactly as it is: the engine before the A-21 fix
            stream = self.cam.extractor.create_stream()
            stream.accept_waveform(sample_rate=SR, waveform=np.asarray(x, np.float32))
            stream.input_finished()
            v = self.cam.extractor.compute(stream)
        v = np.asarray(v, dtype=np.float32)
        return v / np.linalg.norm(v)


def crop(x: np.ndarray, seconds: float | None) -> np.ndarray | None:
    if seconds is None:
        return x
    n = int(seconds * SR)
    if len(x) < n:
        return None
    start = (len(x) - n) // 2
    return x[start : start + n]


def chunks(x: np.ndarray, seconds: float = 2.0) -> list[np.ndarray]:
    n = int(seconds * SR)
    return [x[i : i + n] for i in range(0, len(x) - n + 1, n)]


# ---------------------------------------------------------------- metrics


def metrics(gen: np.ndarray, imp: np.ndarray) -> dict:
    gen, imp = np.sort(np.asarray(gen)), np.sort(np.asarray(imp))
    ts = np.unique(np.concatenate([gen, imp]))
    far = np.array([np.mean(imp >= t) for t in ts])
    frr = np.array([np.mean(gen < t) for t in ts])
    i = int(np.argmin(np.abs(far - frr)))

    def at_far(target: float) -> tuple[float, float, bool]:
        desc = imp[::-1]
        m = int(np.floor(target * len(imp)))
        t = float(desc[m]) + 1e-6 if m < len(desc) else float(desc[-1])
        return t, float(np.mean(gen < t)), m == 0 and len(imp) < 1 / target

    t1, frr1, _ = at_far(0.01)
    t01, frr01, capped = at_far(0.001)
    return {
        "ng": len(gen),
        "ni": len(imp),
        "g_mean": gen.mean(),
        "g_min": gen.min(),
        "g_p5": np.percentile(gen, 5),
        "i_mean": imp.mean(),
        "i_max": imp.max(),
        "i_p95": np.percentile(imp, 95),
        "i_p99": np.percentile(imp, 99),
        "eer": (far[i] + frr[i]) / 2,
        "eer_t": ts[i],
        "t1": t1,
        "frr1": frr1,
        "t01": t01,
        "frr01": frr01,
        "capped": capped,
        "far_m": float(np.mean(imp >= MATCH)),
        "frr_m": float(np.mean(gen < MATCH)),
    }


HEADER = (
    f"{'condition':<34}{'nG':>5}{'nI':>6} | {'Gmean':>6}{'Gmin':>6}{'Gp5':>6} | "
    f"{'Imean':>6}{'Imax':>6}{'Ip95':>6}{'Ip99':>6} | {'EER%':>5}{'@thr':>6} | "
    f"{'t@1%':>6}{'FRR%':>5} | {'t@.1%':>7}{'FRR%':>5} | {'@0.50 FAR/FRR%':>14}"
)


def row(name: str, m: dict) -> str:
    cap = "*" if m["capped"] else " "
    return (
        f"{name:<34}{m['ng']:>5}{m['ni']:>6} | {m['g_mean']:6.3f}{m['g_min']:6.3f}"
        f"{m['g_p5']:6.3f} | {m['i_mean']:6.3f}{m['i_max']:6.3f}{m['i_p95']:6.3f}"
        f"{m['i_p99']:6.3f} | {100 * m['eer']:5.1f}{m['eer_t']:6.3f} | {m['t1']:6.3f}"
        f"{100 * m['frr1']:5.1f} | {m['t01']:6.3f}{cap}{100 * m['frr01']:5.1f} | "
        f"{100 * m['far_m']:6.2f}/{100 * m['frr_m']:5.1f}"
    )


# ---------------------------------------------------------------- analysis


class Study:
    def __init__(self, out: Path, emb: Embedder, seed: int):
        self.out, self.emb, self.rng = out, emb, np.random.default_rng(seed)
        self.audio: dict[str, dict[str, dict[str, np.ndarray]]] = {}
        self.cache: dict[tuple, np.ndarray] = {}
        self.lines: list[str] = []

    def say(self, text: str = "") -> None:
        print(text, flush=True)
        self.lines.append(text)

    def load(self, simulate_channels: bool) -> None:
        clean = {}
        for voice in VOICES:
            if all((self.out / "tts" / voice / f"{c}.wav").exists() for c in CLIPS):
                clean[voice] = {
                    c: to16k(*read_wav(self.out / "tts" / voice / f"{c}.wav")) for c in CLIPS
                }
        self.audio["clean"] = {
            v: {c: self.emb.voiced(x) for c, x in d.items()} for v, d in clean.items()
        }
        for mic in ("station", "glasses"):
            live = {}
            for voice in LIVE_VOICES:
                paths = {c: self.out / "live" / voice / f"{c}_{mic}.wav" for c in LIVE_CLIPS}
                if all(p.exists() for p in paths.values()):
                    live[voice] = {c: self.emb.voiced(read_wav(p)[0]) for c, p in paths.items()}
            if live:
                self.audio[mic] = live
        if simulate_channels:
            for channel in CHANNELS:
                self.audio[channel] = {
                    v: {c: self.emb.voiced(simulate(x, channel, self.rng)) for c, x in d.items()}
                    for v, d in clean.items()
                }
            names = list(clean)
            for name, (channel, snr) in BABBLE.items():
                self.audio[name] = {}
                for v, d in clean.items():
                    self.audio[name][v] = {}
                    for c, x in d.items():
                        others = self.rng.choice([n for n in names if n != v], 3, replace=False)
                        parts = [clean[o][CLIPS[self.rng.integers(1, len(CLIPS))]] for o in others]
                        length = max(len(x), *(len(q) for q in parts))
                        babble = sum(np.resize(q, length) for q in parts)
                        y = simulate(x, channel, self.rng, babble, snr)
                        self.audio[name][v][c] = self.emb.voiced(y)

    def vec(self, cond: str, voice: str, clip: str, seconds=None, fit: str | None = None):
        key = (cond, voice, clip, seconds, fit or self.emb.fit)
        if key not in self.cache:
            x = crop(self.audio[cond][voice][clip], seconds)
            self.cache[key] = None if x is None else self.emb(x, fit)
        return self.cache[key]

    def trials(self, enrol: str, test: str, tests: list[str], seconds=None, fit=None):
        voices = [v for v in self.audio[enrol] if v in self.audio[test]]
        prints = {v: self.vec(enrol, v, "A", None, fit) for v in voices}
        gen, imp, detail = [], [], []
        for w in voices:
            for c in tests:
                t = self.vec(test, w, c, seconds, fit)
                if t is None:
                    continue
                for v in voices:
                    s = float(prints[v] @ t)
                    (gen if v == w else imp).append(s)
                    detail.append((v, w, c, s))
        return np.array(gen), np.array(imp), detail

    def pool(self, cond: str, voice: str, tests: list[str], with_a: bool):
        items = [(c, self.vec(cond, voice, c)) for c in tests]
        if with_a:
            for i, x in enumerate(chunks(self.audio[cond][voice]["A"])):
                key = (cond, voice, f"A#{i}", None, self.emb.fit)
                if key not in self.cache:
                    self.cache[key] = self.emb(x)
                items.append((f"A#{i}", self.cache[key]))
        return items

    def refine(self, enrol, test, tests, k, adapt, with_a):
        voices = [v for v in self.audio[enrol] if v in self.audio[test]]
        prints = {v: self.vec(enrol, v, "A") for v in voices}
        pools = {
            v: [(c, e) for c, e in self.pool(test, v, tests, with_a) if prints[v] @ e >= adapt]
            for v in voices
        }
        gen, imp, sizes = [], [], []
        for w in voices:
            for c in tests:
                t = self.vec(test, w, c)
                for v in voices:
                    cand = [e for cc, e in pools[v] if cc != c]
                    base = float(prints[v] @ t)
                    if cand:
                        pick = self.rng.choice(len(cand), size=min(k, len(cand)), replace=False)
                        mean = np.mean([cand[j] for j in pick], axis=0)
                        base = max(base, float(t @ mean / np.linalg.norm(mean)))
                        sizes.append(len(pick))
                    else:
                        sizes.append(0)
                    (gen if v == w else imp).append(base)
        return np.array(gen), np.array(imp), float(np.mean(sizes))

    def table(self, rows: list[tuple]) -> None:
        for name, enrol, test, tests, secs, *fit in rows:
            if enrol in self.audio and test in self.audio:
                trial = self.trials(enrol, test, tests, secs, fit[0] if fit else None)
                self.say(row(name, metrics(*trial[:2])))

    def report(self) -> None:
        a = self.audio
        shorts4, shorts, live = CLIPS[1:5], CLIPS[1:], LIVE_CLIPS[1:]
        if "clean" in a and "glasses" in a:
            a["clean_live"] = {v: a["clean"][v] for v in a["glasses"]}
        if "sim_station" in a and "glasses" in a:
            a["sim_station_live"] = {v: a["sim_station"][v] for v in a["glasses"]}
        self.say("\nVoiced enrollment speech (A), seconds:")
        for cond in a:
            lens = [len(d["A"]) / SR for d in a[cond].values()]
            self.say(f"  {cond:<16} min {min(lens):.1f}  mean {np.mean(lens):.1f}  (n={len(lens)})")
        lengths = (1.0, 1.5, 2.0, None)

        def by_len(prefix, enrol, test, tests):
            return [
                (f"{prefix} {f'{s}s' if s else 'full'}", enrol, test, tests, s) for s in lengths
            ]

        self.say("\n" + HEADER)
        self.table(
            [
                ("BEFORE-FIX clean->clean", "clean", "clean", shorts, None, "none"),
                ("BEFORE-FIX LIVE station->glasses", "station", "glasses", live, None, "none"),
                ("BEFORE-FIX SIM st->gl", "sim_station", "sim_glasses", shorts, None, "none"),
                ("clean->clean all voices, 12 tests", "clean", "clean", shorts, None),
                ("clean->clean live voices, 3 tests", "clean_live", "clean_live", live, None),
                ("LIVE station->station", "station", "station", live, None),
                ("LIVE glasses->glasses", "glasses", "glasses", live, None),
                ("LIVE clean-file->glasses", "clean_live", "glasses", live, None),
                *by_len("LIVE station->glasses", "station", "glasses", live),
                *by_len("SIMst->LIVEgl", "sim_station_live", "glasses", live),
                ("SIM station->station", "sim_station", "sim_station", shorts, None),
                ("SIM glasses->glasses", "sim_glasses", "sim_glasses", shorts, None),
                ("SIM station->glasses 4 tests", "sim_station", "sim_glasses", shorts4, None),
                *by_len("SIM station->glasses", "sim_station", "sim_glasses", shorts),
                ("SIM st->gl+babble5dB", "sim_station", "sim_glasses_babble", shorts, None),
                ("SIM st+babble10dB->gl", "sim_station_babble", "sim_glasses", shorts, None),
                ("SIM st+bab10->gl+bab5", "sim_station_babble", "sim_glasses_babble", shorts, None),
            ]
        )
        self.say(
            "* = fewer than 1000 impostor trials: the FAR 0.1% threshold is just above the max."
        )
        self.say("@0.50 = FAR/FRR at today's voice_match threshold.")

        pairs = (
            ("LIVE", "station", "glasses", live, True),
            ("SIMst->LIVEgl", "sim_station_live", "glasses", live, True),
            ("SIM", "sim_station", "sim_glasses", shorts, False),
        )
        for label, enrol, test, tests, _ in pairs:
            if enrol not in a or test not in a:
                continue
            gen, imp, detail = self.trials(enrol, test, tests, 1.5)
            self.say(f"\n{label} adapt gate (station print vs glasses utterance, 1.5 s voiced):")
            self.say("  thr   genuine pass%  impostor pair pass%  stranger-utt pass%")
            best: dict[tuple[str, str], float] = {}
            for v, w, c, s in detail:
                if v != w:
                    best[(w, c)] = max(best.get((w, c), -1.0), s)
            strangers = np.array(list(best.values()))
            for thr in np.arange(0.20, 0.751, 0.05):
                self.say(
                    f"  {thr:.2f}  {100 * np.mean(gen >= thr):12.1f}"
                    f"  {100 * np.mean(imp >= thr):18.2f}  {100 * np.mean(strangers >= thr):17.1f}"
                )
            self.say(
                "  (stranger-utt: share of other-voice utterances scoring >= thr on any print)"
            )

        for label, enrol, test, tests, with_a in pairs:
            if enrol not in a or test not in a:
                continue
            for adapt in (-1.0, self.adapt):
                gate = "no gate" if adapt < 0 else f"adapt>={adapt:.2f}"
                self.say(f"\n{label} refinement, score = max(station, bank mean), {gate}:")
                self.say(HEADER.replace("condition" + " " * 25, "k (mean bank size)".ljust(34)))
                base = self.trials(enrol, test, tests)[:2]
                self.say(row("k=0 (station print only)", metrics(*base)))
                for k in (1, 2, 4, 8):
                    gen, imp, size = self.refine(enrol, test, tests, k, adapt, with_a)
                    self.say(row(f"k={k} (bank {size:.1f})", metrics(gen, imp)))


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--out", type=Path, default=EVAL_ROOT / "ENROLL")
    p.add_argument("--synth", action="store_true", help="make TTS clips into --out/tts")
    p.add_argument("--record", action="store_true", help="play clips and record both mics")
    p.add_argument("--simulate", action="store_true", help="file-level channel simulation")
    p.add_argument("--probe", action="store_true", help="play one clip, print mic levels, exit")
    p.add_argument("--adapt", type=float, default=0.45, help="adapt gate for the refinement")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument(
        "--fit",
        choices=("tile", "none"),
        default="tile",
        help="repeat audio to a multiple of 2.0 s before CAM++ (none = the engine before the A-21 fix)",
    )
    p.add_argument("--clean", action="store_true", help="delete --out and exit")
    args = p.parse_args()
    out = _inside(args.out, EVAL_ROOT)
    if args.clean:
        shutil.rmtree(out, ignore_errors=True)
        print(f"deleted {out}")
        return
    out.mkdir(parents=True, exist_ok=True)
    if args.synth:
        print("synthesising ...")
        synth(out)
    if args.probe:
        print("probe: one short clip, levels only (nothing saved) ...")
        played = record(out, LIVE_VOICES[:1], ["s01"], save=False)
        print(f"  playback {played:.1f} s")
        return
    if args.record:
        print("recording ...")
        t0 = time.time()
        played = record(out, LIVE_VOICES, LIVE_CLIPS)
        print(f"  playback {played:.1f} s of speech, session {time.time() - t0:.1f} s")
    study = Study(out, Embedder(args.fit), args.seed)
    study.adapt = args.adapt
    study.load(args.simulate)
    study.report()
    summary = _inside(out / "summary.txt", out)
    summary.write_text("\n".join(study.lines), encoding="utf-8")


if __name__ == "__main__":
    main()
