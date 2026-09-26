"""Check the enrollment station's laptop camera and mic before saving people (V-23, A-21).

Finds the laptop camera and mic by the names in `[enroll]` (camera_name, mic_name), opens each
for a few seconds, measures them in memory and says what is wrong in plain words. Saves
nothing: no frames, no audio, no files.

    uv run --project engine python scripts/station_check.py
    uv run --project engine python scripts/station_check.py --faces      # also look for a face
    uv run --project engine python scripts/station_check.py --speak      # also read a sentence
    uv run --project engine python scripts/station_check.py --config config/attune.toml

It never opens the glasses camera or mic (`[vision] camera_name`, `[audio] device_name`, and
anything named Brio or C922), nor an infrared camera. Run it while the engine is running: the
engine opens the laptop camera and mic only during a save, so they should be free.
Exit code: 0 all good, 1 a device has a problem, 2 a device wasn't found.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

import cv2
import numpy as np
from attune.config import load_config
from attune.station.settings import load_enroll_settings
from attune.vision.camera import is_infrared, list_cameras

# the glasses webcam and mic belong to the live engine: never opened here
GLASSES_NAMES = ("Brio", "C922")


def avoided(name: str, avoid: tuple[str, ...]) -> bool:
    low = name.casefold()
    return any(a and a.casefold() in low for a in avoid)


# ------------------------------------------------------------------ camera
def check_camera(s, avoid: tuple[str, ...], seconds: float, faces: bool) -> int:
    print(f"\nLaptop camera (looking for a name containing {s.camera_name!r})")
    cams = list_cameras()
    if not cams:
        print("  PROBLEM: no cameras listed (is cv2-enumerate-cameras installed?)")
        return 2
    chosen = None
    for cam in cams:
        why = ""
        if is_infrared(cam.name):
            why = "infrared, never used"
        elif avoided(cam.name, avoid):
            why = "the glasses camera, not opened"
        elif chosen is None and s.camera_name.casefold() in cam.name.casefold():
            chosen = cam
            why = "the station camera"
        print(f"  seen: {cam.name!r} (index {cam.index}){f' - {why}' if why else ''}")
    if chosen is None:
        print("  PROBLEM: not found. Set [enroll] camera_name to part of one of the names above.")
        return 2
    t0 = time.perf_counter()
    cap = cv2.VideoCapture(chosen.index, chosen.backend)
    try:
        if not cap.isOpened():
            print("  PROBLEM: it would not open. Another app may be using it (close Camera, Teams,")
            print("  Zoom), or Windows Settings > Privacy > Camera blocks desktop apps.")
            return 1
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, s.camera_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, s.camera_height)
        cap.set(cv2.CAP_PROP_FPS, s.camera_fps)
        first = None
        deadline = time.perf_counter() + s.open_timeout_s
        while time.perf_counter() < deadline:
            ok, frame = cap.read()
            if ok and frame is not None:
                first = time.perf_counter()
                break
        if first is None:
            print(f"  PROBLEM: opened but sent no picture in {s.open_timeout_s:.0f} s (busy, or a")
            print("  privacy shutter / camera key is off).")
            return 1
        print(f"  opened in {first - t0:.1f} s")
        n, bright, sharp, kept = 0, [], [], []
        end = first + seconds
        while time.perf_counter() < end:
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            n += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            bright.append(float(gray.mean()))
            sharp.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
            if faces and n % 6 == 1 and len(kept) < 8:
                kept.append(frame.copy())  # in memory for the face check below, then dropped
        elapsed = time.perf_counter() - first
        h, w = frame.shape[:2]
        fps = n / elapsed if elapsed > 0 else 0.0
        exposure = cap.get(cv2.CAP_PROP_EXPOSURE)
        auto = cap.get(cv2.CAP_PROP_AUTO_EXPOSURE)
        gain = cap.get(cv2.CAP_PROP_GAIN)
        b, sh = float(np.median(bright)), float(np.median(sharp))
        print(
            f"  picture: {w}x{h}, {fps:.1f} fps measured over {elapsed:.1f} s (asked {s.camera_fps})"
        )
        print(f"  exposure {exposure:g} (auto {auto:g}), gain {gain:g}")
        print(f"  brightness {b:.0f}/255, whole-picture sharpness {sh:.0f}")
        problems = []
        if b < 8:
            problems.append("the picture is black: a privacy shutter or camera key may be closed")
        elif b < 60:
            problems.append("too dark: turn on a light in front of the person, not behind them")
        elif b > 205:
            problems.append("washed out: too much light behind or on the camera")
        if fps < 12:
            problems.append("slow: the camera drops frames in low light (add light)")
        if faces:
            detector = face_detector()
            if detector is not None:
                report_faces([largest_face(detector, f) for f in kept], s, problems)
        kept.clear()
        return report(problems)
    finally:
        cap.release()  # the camera light goes off; nothing was kept


def face_detector():
    from attune.vision.detector import FaceDetector

    path = ROOT / "models" / "faces" / "buffalo_l" / "det_10g.onnx"
    if not path.is_file():
        print(f"  (no face check: {path} is missing)")
        return None
    return FaceDetector(str(path), det_size=640, use_gpu=False)  # CPU: no GPU needed


def largest_face(detector, frame):
    """(centre x, centre y, width px, faces, quality reason) of the biggest face, or None."""
    from attune.vision.embedder import align, crop_quality

    dets = detector.detect(frame)
    if not dets:
        return None
    d = max(dets, key=lambda x: x.width)
    x0, y0, x1, y1 = (float(v) for v in d.box)
    h, w = frame.shape[:2]
    quality = crop_quality(d, align(frame, d.kps))  # the same check the station makes
    return ((x0 + x1) / 2 / w, (y0 + y1) / 2 / h, d.width, len(dets), quality.reason)


def report_faces(found, s, problems) -> None:
    seen = [f for f in found if f is not None]
    if not found:
        return
    if not seen:
        problems.append("no face seen: sit in front of the laptop, facing the screen")
        return
    cx, cy, width, count = (float(np.median([f[i] for f in seen])) for i in range(4))
    share = width / (s.camera_height * 0.75)  # the phone preview is a 3:4 crop of the height
    print(
        f"  face: seen in {len(seen)}/{len(found)} checks, centre ({cx:.2f}, {cy:.2f}), "
        f"{share:.0%} of the preview width"
    )
    reasons = [f[4] for f in seen if f[4]]
    words = {
        "small": "small",
        "turned": "turned away",
        "dark": "too dark",
        "blurry": "blurry",
    }
    if len(reasons) > len(seen) / 2:
        worst = max(set(reasons), key=reasons.count)
        problems.append(f"the face crops are {words.get(worst, worst)} for a face print")
    if share < s.min_face_share:
        problems.append("the face is small: sit closer to the laptop")
    elif share > s.max_face_share:
        problems.append("the face is too close: sit back a little")
    if abs(cx - 0.5) > s.center_tol:
        problems.append("the face is off to one side: sit in the middle")
    if count > 1:
        problems.append("more than one face: one person at a time")


# ------------------------------------------------------------------ mic
def check_mic(s, avoid: tuple[str, ...], seconds: float, speak: bool) -> int:
    import sounddevice as sd

    print(f"\nLaptop mic (looking for a WASAPI input containing {s.mic_name!r})")
    hosts = sd.query_hostapis()
    chosen = None
    for i, dev in enumerate(sd.query_devices()):
        if not dev["max_input_channels"]:
            continue
        host = hosts[dev["hostapi"]]["name"]
        if sys.platform == "win32" and "WASAPI" not in host:
            continue
        why = ""
        if avoided(dev["name"], avoid):
            why = "the glasses mic, not opened"
        elif chosen is None and s.mic_name.casefold() in dev["name"].casefold():
            chosen = (i, dev)
            why = "the station mic"
        print(f"  seen: {dev['name']!r}{f' - {why}' if why else ''}")
    if chosen is None:
        print("  PROBLEM: not found. Set [enroll] mic_name to part of one of the names above.")
        return 2
    index, dev = chosen
    rate = int(dev["default_samplerate"])
    chans = max(1, min(2, int(dev["max_input_channels"])))
    print(f"  {rate} Hz, {chans} channel(s)")
    print(f"  listening to the room for {seconds:.0f} s (stay quiet)...")
    try:
        room = sd.rec(
            int(seconds * rate),
            samplerate=rate,
            channels=chans,
            device=index,
            dtype="float32",
        )
        sd.wait()
    except Exception as exc:  # noqa: BLE001 - reported in words
        print(f"  PROBLEM: it would not open ({exc}). Another app may hold it exclusively, or")
        print("  Windows Settings > Privacy > Microphone blocks desktop apps.")
        return 1
    room = room.mean(axis=1)
    floor, loud, peak = levels(room, rate)
    print(f"  room: noise floor {floor:.0f} dBFS, loudest {loud:.0f} dBFS, peak {peak:.2f}")
    problems = []
    if floor < -95 and loud < -90:
        problems.append("silent: the mic may be muted (a mute key, or Windows privacy settings)")
    elif floor > -45:
        problems.append(
            f"noisy room ({floor:.0f} dBFS): voices must be well above this; find a quieter spot"
        )
    if speak:
        print(f'\n  Now read this aloud, facing the laptop:\n  "{s.sentence}"')
        input("  Press Enter, then read... ")
        voice = sd.rec(
            int(8 * rate),
            samplerate=rate,
            channels=chans,
            device=index,
            dtype="float32",
        )
        sd.wait()
        voice = voice.mean(axis=1)
        _, _, vpeak = levels(voice, rate)
        speech = float(np.percentile(frame_db(voice, rate), 90))
        print(
            f"  speech: {speech:.0f} dBFS (loud frames), peak {vpeak:.2f}, {speech - floor:.0f} dB above the room"
        )
        if vpeak >= s.clip_level:
            problems.append("too loud: it clips; speak a little softer or sit back")
        elif speech < s.quiet_db:
            problems.append("quiet: speak up or sit closer to the laptop")
        if speech - floor < s.min_snr_db:
            problems.append("too noisy for a good voice print: find a quieter spot")
        del voice
    del room  # measured, never kept
    return report(problems)


def frame_db(x: np.ndarray, rate: int) -> np.ndarray:
    n = max(1, int(0.05 * rate))
    frames = x[: len(x) // n * n].reshape(-1, n)
    return 20 * np.log10(np.sqrt((frames * frames).mean(axis=1)) + 1e-9)


def levels(x: np.ndarray, rate: int) -> tuple[float, float, float]:
    f = frame_db(x, rate)
    return float(np.percentile(f, 20)), float(f.max()), float(np.abs(x).max())


def report(problems: list[str]) -> int:
    if not problems:
        print("  OK")
        return 0
    for p in problems:
        print(f"  PROBLEM: {p}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", help="a local config over config/attune.example.toml")
    ap.add_argument("--camera-name", help="override [enroll] camera_name")
    ap.add_argument("--mic-name", help="override [enroll] mic_name")
    ap.add_argument("--seconds", type=float, default=3.0, help="how long to measure each")
    ap.add_argument("--faces", action="store_true", help="look for a face (CPU face finder)")
    ap.add_argument("--speak", action="store_true", help="also measure you reading the sentence")
    ap.add_argument("--no-camera", action="store_true")
    ap.add_argument("--no-mic", action="store_true")
    args = ap.parse_args()
    config = load_config(args.config)
    enroll = dict(config.get("enroll") or {})
    if args.camera_name:
        enroll["camera_name"] = args.camera_name
    if args.mic_name:
        enroll["mic_name"] = args.mic_name
    s = load_enroll_settings({**config, "enroll": enroll})
    glasses = (
        str((config.get("vision") or {}).get("camera_name", "")),
        str((config.get("audio") or {}).get("device_name", "")),
    )
    avoid = tuple(
        dict.fromkeys(
            a
            for a in (*glasses, *GLASSES_NAMES)
            if a and a.casefold() not in (s.camera_name.casefold(), s.mic_name.casefold())
        )
    )
    print(f"Enrollment station check. Glasses devices never opened here: {', '.join(avoid)}")
    codes = []
    if not args.no_camera:
        codes.append(check_camera(s, avoid, args.seconds, args.faces))
    if not args.no_mic:
        codes.append(check_mic(s, avoid, args.seconds, args.speak))
    print("\nNothing was saved.")
    return max(codes, default=0)


if __name__ == "__main__":
    sys.exit(main())
