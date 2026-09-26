"""Who is talking, from lips and sound together: Light-ASD active speaker detection.

Section 1 - Vision. TODO: V-22. Model: vision/light_asd (vendored, MIT).

The lip score (mouth.py) only sees whether a mouth moves. Light-ASD looks at the
mouth region and the sound together and says whether *this* face is making *this*
sound, so a still face next to a loudspeaker, or next to someone off camera, scores
low even while the room is full of speech.

How it runs:
- The vision thread adds one 112x112 grey mouth-region crop per face per frame
  (`add_face`), cropped exactly as Light-ASD's Columbia_test.py does (box side,
  cropScale 0.4, the lower-centre half of the 224 crop).
- The 16 kHz `audio.block` stream is kept in a short ring (`add_audio`).
- A worker thread wakes `rate_hz` times a second. For every face with enough
  history it builds the last `window_s` of crops on a 25 fps grid and the matching
  MFCC (100 Hz x 13, python_speech_features settings), runs them through the model
  in one batch, and keeps the mean speaking logit over the window's last `score_s`.
  Light-ASD's back end runs a GRU forwards and then backwards, so the newest frames
  still see the whole window's past.
- `score(track_id, now)` returns that logit (> 0: talking in time with the sound)
  while it is fresh, or None. None means "can't tell"; fusion then falls back to
  the lip score.

Nothing is stored: crops and audio live in RAM for a few seconds.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

log = logging.getLogger(__name__)

FPS = 25  # Light-ASD's video rate
MFCC_HZ = 100  # 4 audio frames per video frame
SR = 16000
CROP = 112


# ---------------------------------------------------------------- features
def _hz2mel(hz):
    return 2595 * np.log10(1 + hz / 700.0)


def _mel2hz(mel):
    return 700 * (10 ** (mel / 2595.0) - 1)


def _filterbank(nfilt: int, nfft: int, samplerate: int) -> np.ndarray:
    melpoints = np.linspace(_hz2mel(0), _hz2mel(samplerate / 2), nfilt + 2)
    bins = np.floor((nfft + 1) * _mel2hz(melpoints) / samplerate)
    fb = np.zeros([nfilt, nfft // 2 + 1])
    for j in range(nfilt):
        for i in range(int(bins[j]), int(bins[j + 1])):
            fb[j, i] = (i - bins[j]) / (bins[j + 1] - bins[j])
        for i in range(int(bins[j + 1]), int(bins[j + 2])):
            fb[j, i] = (bins[j + 2] - i) / (bins[j + 2] - bins[j + 1])
    return fb


def _dct2_ortho(n: int) -> np.ndarray:
    """The orthonormal DCT-II matrix (scipy.fftpack.dct(type=2, norm='ortho'))."""
    k = np.arange(n)[:, None]
    m = np.arange(n)[None, :]
    mat = np.cos(np.pi * k * (2 * m + 1) / (2 * n)) * math.sqrt(2.0 / n)
    mat[0] /= math.sqrt(2.0)
    return mat


_FB = _filterbank(26, 512, SR)
_DCT = _dct2_ortho(26)[:13]
_LIFT = 1 + (22 / 2.0) * np.sin(np.pi * np.arange(13) / 22)


def mfcc(signal: np.ndarray) -> np.ndarray:
    """13 MFCCs at 100 Hz from 16 kHz audio in int16 units, like python_speech_features.

    Same defaults Light-ASD was trained with: 25 ms rectangular frames every 10 ms,
    pre-emphasis 0.97, 512-point FFT, 26 mel filters, lifter 22, and the first
    coefficient replaced by the log frame energy. Frame count is
    1 + ceil((len - 400) / 160).
    """
    sig = np.asarray(signal, np.float64)
    sig = np.append(sig[0], sig[1:] - 0.97 * sig[:-1])
    flen, fstep = 400, 160
    n = 1 if len(sig) <= flen else 1 + math.ceil((len(sig) - flen) / fstep)
    pad = (n - 1) * fstep + flen - len(sig)
    if pad > 0:
        sig = np.concatenate([sig, np.zeros(pad)])
    idx = np.arange(flen)[None, :] + fstep * np.arange(n)[:, None]
    frames = sig[idx]
    pspec = np.abs(np.fft.rfft(frames, 512)) ** 2 / 512
    energy = pspec.sum(axis=1)
    energy = np.where(energy == 0, np.finfo(float).eps, energy)
    feat = pspec @ _FB.T
    feat = np.where(feat == 0, np.finfo(float).eps, feat)
    feat = np.log(feat) @ _DCT.T
    feat *= _LIFT
    feat[:, 0] = np.log(energy)
    return feat


def asd_crop(image: np.ndarray, box, crop_scale: float = 0.4) -> np.ndarray | None:
    """The 112x112 grey mouth-region crop Light-ASD expects, from a face box (x1, y1, x2, y2).

    Columbia_test.py crops a (2 + 2*cs) * s square around the box (s = half the box's
    longer side), shifted down by cs * s, resizes it to 224 and keeps the middle
    112x112. That is this region, resized to 112 directly: side (1 + cs) * s, centred
    at the box centre's x and cs * s below its centre. Outside the frame is grey 110.
    """
    x1, y1, x2, y2 = (float(v) for v in box[:4])
    s = max(x2 - x1, y2 - y1) / 2
    if s < 2:
        return None
    half = (1 + crop_scale) * s / 2
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2 + crop_scale * s
    h, w = image.shape[:2]
    ax1, ay1 = int(cx - half), int(cy - half)
    ax2, ay2 = int(cx + half), int(cy + half)
    if ax2 <= 0 or ay2 <= 0 or ax1 >= w or ay1 >= h:
        return None
    region = image[max(ay1, 0) : min(ay2, h), max(ax1, 0) : min(ax2, w)]
    if region.ndim == 3:
        region = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    pad = (max(-ay1, 0), max(ay2 - h, 0), max(-ax1, 0), max(ax2 - w, 0))
    if any(pad):
        region = cv2.copyMakeBorder(region, *pad, cv2.BORDER_CONSTANT, value=110)
    side = ax2 - ax1
    interp = cv2.INTER_AREA if side > CROP else cv2.INTER_LINEAR
    return cv2.resize(region, (CROP, CROP), interpolation=interp)


# ---------------------------------------------------------------- model
class LightASD:
    """Light-ASD with the TalkSet weights; `score` runs a batch of windows."""

    def __init__(self, model_path: str, device: str = "cuda"):
        import torch

        from .light_asd import ASD_Model, lossAV

        self.torch = torch
        if device.startswith("cuda") and not torch.cuda.is_available():
            log.warning("CUDA isn't available; Light-ASD runs on the CPU")
            device = "cpu"
        self.device = torch.device(device)
        self.model = ASD_Model()
        self.head = lossAV()
        state = torch.load(model_path, map_location="cpu", weights_only=True)
        own = {f"model.{k}": v for k, v in self.model.state_dict().items()}
        own.update({f"lossAV.{k}": v for k, v in self.head.state_dict().items()})
        missing = [k for k in own if k not in state]
        if missing:
            raise ValueError(f"{model_path} is missing {len(missing)} Light-ASD tensors")
        self.model.load_state_dict(
            {k[6:]: v for k, v in state.items() if k.startswith("model.")}, strict=True
        )
        self.head.load_state_dict(
            {k[7:]: v for k, v in state.items() if k.startswith("lossAV.")}, strict=True
        )
        self.model.to(self.device).eval()
        self.head.to(self.device).eval()

    def score(self, audio: np.ndarray, video: np.ndarray) -> np.ndarray:
        """Per-frame speaking logits, shape (B, T), for MFCC (B, 4T, 13) and crops (B, T, 112, 112)."""
        torch = self.torch
        b, t = video.shape[:2]
        with torch.inference_mode():
            a = torch.from_numpy(np.ascontiguousarray(audio, np.float32)).to(self.device)
            v = torch.from_numpy(np.ascontiguousarray(video, np.float32)).to(self.device)
            emb_a = self.model.forward_audio_frontend(a)
            emb_v = self.model.forward_visual_frontend(v)
            out = self.model.forward_audio_visual_backend(emb_a, emb_v)
            return self.head(out).reshape(b, t).float().cpu().numpy()

    def gpu_memory_mb(self) -> float | None:
        if self.device.type != "cuda":
            return None
        return self.torch.cuda.memory_reserved(self.device) / 2**20


# ---------------------------------------------------------------- buffers
class AudioRing:
    """The last `keep_s` of 16 kHz audio with the shared-clock time of its end."""

    def __init__(self, keep_s: float = 4.0, jump_s: float = 0.1):
        self.keep = int(keep_s * SR)
        self.jump_s = jump_s
        self.buf = np.zeros(0, np.float32)
        self.t_end: float | None = None  # time just after the newest sample

    def add(self, t: float, samples: np.ndarray) -> None:
        samples = np.asarray(samples, np.float32).reshape(-1)
        if self.t_end is None or abs(t - self.t_end) > self.jump_s:
            self.buf = samples.copy()  # first block, or a jump (restart, pause): start over
        else:
            self.buf = np.concatenate([self.buf, samples])[-self.keep :]
        self.t_end = t + len(samples) / SR

    def get(self, t0: float, n: int) -> np.ndarray | None:
        """`n` samples starting at time `t0`, or None if the ring doesn't hold all of them."""
        if self.t_end is None:
            return None
        start = len(self.buf) - round((self.t_end - t0) * SR)
        if start < 0 or start + n > len(self.buf):
            return None
        return self.buf[start : start + n]


@dataclass
class _Face:
    crops: deque = field(default_factory=deque)  # (t, 112x112 uint8)
    scored_to: float = -1e9  # end of the last window scored
    score: float | None = None
    score_t: float = -1e9


@dataclass
class _Job:
    track_id: int
    t_end: float
    video: np.ndarray  # (T, 112, 112) uint8
    audio: np.ndarray  # 16 kHz PCM, then its MFCC (4T, 13)


class ActiveSpeakerDetector:
    """Per-face Light-ASD scores, computed on a worker thread (see the module docstring)."""

    def __init__(
        self,
        model,
        rate_hz: float = 10.0,
        window_s: float = 1.0,
        score_s: float = 0.2,
        max_gap_s: float = 0.2,
        min_fps: float = 12.0,
        av_offset_s: float = 0.0,
        max_age_s: float = 0.5,
    ):
        self.model = model
        self.rate_hz = rate_hz
        self.frames = max(8, round(window_s * FPS))
        self.score_frames = max(1, min(self.frames, round(score_s * FPS)))
        self.max_gap_s = max_gap_s
        self.min_fps = min_fps
        self.av_offset_s = av_offset_s
        self.max_age_s = max_age_s
        self.keep_s = self.frames / FPS + 1.0
        self.audio = AudioRing(keep_s=self.keep_s + abs(av_offset_s) + 1.0)
        self.faces: dict[int, _Face] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.infer_ms: deque = deque(maxlen=50)
        self.batch_sizes: deque = deque(maxlen=50)
        self.rounds = 0
        self.failed = False

    # ---- inputs (any thread; cheap) ----
    def add_audio(self, t: float, samples: np.ndarray) -> None:
        with self._lock:
            self.audio.add(t, samples)

    def add_face(self, track_id: int, t: float, crop: np.ndarray) -> None:
        with self._lock:
            face = self.faces.setdefault(track_id, _Face())
            face.crops.append((t, crop))
            while face.crops and t - face.crops[0][0] > self.keep_s:
                face.crops.popleft()

    def keep_only(self, track_ids: set[int]) -> None:
        """Forget faces that are no longer tracked."""
        with self._lock:
            for tid in [k for k in self.faces if k not in track_ids]:
                del self.faces[tid]

    def score(self, track_id: int, now: float) -> float | None:
        """The face's latest speaking logit, or None when it has none from the last `max_age_s`."""
        face = self.faces.get(track_id)
        if face is None or face.score is None or now - face.score_t > self.max_age_s:
            return None
        return face.score

    # ---- worker ----
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="asd", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        period = 1.0 / self.rate_hz
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                self.step()
            except Exception:
                if not self.failed:
                    log.exception("Light-ASD failed; faces fall back to the lip score")
                self.failed = True
            self._stop.wait(max(0.0, period - (time.perf_counter() - t0)))

    def step(self) -> int:
        """Score every face that has a full new window; returns how many were scored."""
        with self._lock:  # only gathering under the lock: the bus and vision threads add to it
            jobs = self._jobs()
        n = 4 * self.frames
        for job in jobs:
            job.audio = mfcc(job.audio * 32768.0)[:n]  # PCM in, MFCC out
        jobs = [j for j in jobs if len(j.audio) == n]
        if not jobs:
            return 0
        t0 = time.perf_counter()
        logits = self.model.score(
            np.stack([j.audio for j in jobs]), np.stack([j.video for j in jobs])
        )
        self.infer_ms.append(1000 * (time.perf_counter() - t0))
        self.batch_sizes.append(len(jobs))
        self.rounds += 1
        self.failed = False
        with self._lock:
            for job, row in zip(jobs, logits):
                face = self.faces.get(job.track_id)
                if face is not None:
                    face.score = float(np.mean(row[-self.score_frames :]))
                    face.score_t = job.t_end
        return len(jobs)

    def _jobs(self) -> list[_Job]:
        ring = self.audio
        if ring.t_end is None:
            return []
        n_frames = self.frames
        n_samples = n_frames * SR // FPS + 240  # 240 more gives exactly 4 MFCC frames per frame
        jobs = []
        for tid, face in self.faces.items():
            if len(face.crops) < 2:
                continue
            # the newest frame whose audio has fully arrived
            last = min(face.crops[-1][0], ring.t_end - self.av_offset_s - 1 / FPS - 0.016)
            first = last - (n_frames - 1) / FPS
            if first < face.crops[0][0] - self.max_gap_s or last <= face.scored_to + 1e-6:
                continue
            video = self._grid(face.crops, first, n_frames)
            if video is None:
                continue
            # a view is safe to use after the lock: the ring replaces its buffer, never edits it
            pcm = ring.get(first + self.av_offset_s, n_samples)
            if pcm is None:
                continue
            face.scored_to = last
            jobs.append(_Job(tid, last + 1 / FPS, video, pcm))
        return jobs

    def _grid(self, crops: deque, first: float, n: int) -> np.ndarray | None:
        """The crops nearest each 25 fps grid time; None if the face's frames are too sparse.

        Too sparse: a hole of more than `max_gap_s` between two crops (or at either end
        of the window), or fewer than `min_fps` crops a second on average. Light-ASD
        reads lip movement at 25 fps; from a vision loop much slower than that its
        scores stop meaning much, and the lip score is the better guess.
        """
        times = np.fromiter((c[0] for c in crops), float, len(crops))
        grid = first + np.arange(n) / FPS
        idx = np.clip(np.searchsorted(times, grid), 1, len(times) - 1)
        left_closer = (grid - times[idx - 1]) <= (times[idx] - grid)
        idx = np.where(left_closer, idx - 1, idx)
        used = times[idx[0] : idx[-1] + 1]
        holes = [used[0] - grid[0], grid[-1] - used[-1], *np.diff(used)]
        if max(holes) > self.max_gap_s or len(used) < self.min_fps * n / FPS:
            return None
        return np.stack([crops[i][1] for i in idx])

    def metrics(self) -> dict:
        """For `status.part` (vision): ms per round, faces per round, GPU memory held."""
        ms = list(self.infer_ms)
        sizes = list(self.batch_sizes)
        gpu = getattr(self.model, "gpu_memory_mb", None)
        mb = gpu() if callable(gpu) else None
        return {
            "asd_ms": round(sum(ms) / len(ms), 1) if ms else None,
            "asd_batch": round(sum(sizes) / len(sizes), 1) if sizes else 0,
            "asd_gpu_mb": None if mb is None else round(mb),
        }
