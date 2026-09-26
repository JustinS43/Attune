"""Fetches model files into models/: the one place downloads happen.

Section 4 - Pages, Engine & Demo. TODO: P-14, P-42.
Plan: docs/attune-build-plan.html, section 07 Downloads.

Every entry names a fixed upstream URL (pinned to a commit), the exact size and the
SHA-256. A file is written to `<name>.part` first and only renamed into place once
both checks pass, so a broken download never looks like a model. Files already in
place with the right size and hash are skipped.

    python scripts/download_models.py --list          # what it can fetch
    python scripts/download_models.py light_asd       # asks before downloading
    python scripts/download_models.py light_asd --yes
    python scripts/download_models.py --check         # verify what is on disk
    python scripts/download_models.py --root /path/to/checkout buffalo_l

Ask your human before running it: it downloads model weights.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
import zipfile
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


@dataclass(frozen=True)
class ArchiveMember:
    filename: str
    sha256: str


@dataclass(frozen=True)
class ModelArchive:
    name: str
    url: str
    dest_dir: str
    size: int
    sha256: str
    license: str
    members: tuple[ArchiveMember, ...]
    note: str = ""


_LIGHT_ASD_COMMIT = "ed38c232de5efe0261dbd68627c0ade7cdfe14eb"
_WHISPER_COMMIT = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
_WHISPER_BASE = (
    "https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo/resolve/"
    f"{_WHISPER_COMMIT}/"
)

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
        ModelFile(
            name="face_landmarker",
            url=(
                "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
                "face_landmarker/float16/1/face_landmarker.task"
            ),
            dest="models/faces/mediapipe/face_landmarker.task",
            size=3_758_596,
            sha256="64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff",
            license="MediaPipe model (Google)",
            note="Mouth landmarks for active-speaker matching",
        ),
        # 3D-Speaker's CAM++ export, not WeSpeaker's: the voice thresholds ([voice]
        # station_match, bank_match, adapt_min) and the 2 s block fix in
        # audio/voiceprint.py (A-21) were measured on this file, and saved voice
        # prints only compare with prints from the same model.
        ModelFile(
            name="cam_plus_plus",
            url=(
                "https://huggingface.co/csukuangfj/speaker-embedding-models/resolve/"
                "0743f301363dec56491a490f6d6cbc9d67f9a3bf/"
                "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"
            ),
            dest="models/cam++.onnx",
            size=29_596_978,
            sha256="357a834f702b80161e5b981182c038e18553c1f2ca752ed6cec2052365d4129b",
            license="Apache-2.0 (3D-Speaker, iic/speech_campplus_sv_en_voxceleb_16k)",
            note="Voice prints for consented and automatic contacts (A-21)",
        ),
        *(
            ModelFile(
                name=f"whisper_{filename}",
                url=_WHISPER_BASE + filename,
                dest=f"models/faster-whisper-large-v3-turbo/{filename}",
                size=size,
                sha256=sha256,
                license="MIT (dropbox-dash/faster-whisper-large-v3-turbo)",
                note="CTranslate2 Whisper large-v3-turbo speech recognition",
            )
            for filename, size, sha256 in (
                (
                    "config.json",
                    2_263,
                    "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
                ),
                (
                    "model.bin",
                    1_617_884_929,
                    "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
                ),
                (
                    "preprocessor_config.json",
                    340,
                    "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
                ),
                (
                    "tokenizer.json",
                    2_710_337,
                    "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
                ),
                (
                    "vocabulary.json",
                    1_068_114,
                    "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
                ),
            )
        ),
    )
}

ARCHIVES: dict[str, ModelArchive] = {
    "buffalo_l": ModelArchive(
        name="buffalo_l",
        url="https://github.com/deepinsight/insightface/releases/download/model-zoo/buffalo_l.zip",
        dest_dir="models/faces/buffalo_l",
        size=288_621_354,
        sha256="80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f",
        license="InsightFace non-commercial research use only",
        members=(
            ArchiveMember(
                "det_10g.onnx",
                "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
            ),
            ArchiveMember(
                "w600k_r50.onnx",
                "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
            ),
        ),
        note="Extracts only face detection and recognition; skips age/gender models",
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


def is_archive_ok(m: ModelArchive, root: Path = REPO_ROOT) -> bool:
    return all(
        (root / m.dest_dir / item.filename).is_file()
        and sha256_of(root / m.dest_dir / item.filename) == item.sha256
        for item in m.members
    )


def fetch_archive(m: ModelArchive, root: Path = REPO_ROOT) -> Path:
    """Verify an archive, then extract only the named, checked model files."""
    dest_dir = root / m.dest_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    part = dest_dir / (m.name + ".zip.part")
    try:
        h = hashlib.sha256()
        got = 0
        with urllib.request.urlopen(m.url, timeout=60) as resp, open(part, "wb") as out:
            for chunk in iter(lambda: resp.read(1 << 20), b""):
                out.write(chunk)
                h.update(chunk)
                got += len(chunk)
        if got != m.size or h.hexdigest() != m.sha256:
            raise RuntimeError(f"{m.name}: archive size or SHA-256 mismatch")
        with zipfile.ZipFile(part) as archive:
            for item in m.members:
                matches = [
                    name
                    for name in archive.namelist()
                    if Path(name).name == item.filename
                ]
                if len(matches) != 1:
                    raise RuntimeError(
                        f"{m.name}: expected one {item.filename} in archive"
                    )
                dest = dest_dir / item.filename
                member_part = dest.with_name(dest.name + ".part")
                try:
                    with (
                        archive.open(matches[0]) as source,
                        open(member_part, "wb") as out,
                    ):
                        for chunk in iter(lambda: source.read(1 << 20), b""):
                            out.write(chunk)
                    if sha256_of(member_part) != item.sha256:
                        raise RuntimeError(
                            f"{m.name}: {item.filename} SHA-256 mismatch"
                        )
                    member_part.replace(dest)
                finally:
                    member_part.unlink(missing_ok=True)
        return dest_dir
    finally:
        part.unlink(missing_ok=True)


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
    ap.add_argument(
        "--root", type=Path, default=REPO_ROOT, help="checkout receiving models"
    )
    a = ap.parse_args(argv)

    models = {**MODELS, **ARCHIVES}

    def ok(m: ModelFile | ModelArchive) -> bool:
        return (
            is_ok(m, a.root) if isinstance(m, ModelFile) else is_archive_ok(m, a.root)
        )

    if a.list or not (a.names or a.check):
        for m in models.values():
            state = "ok" if ok(m) else "missing"
            dest = m.dest if isinstance(m, ModelFile) else m.dest_dir
            print(
                f"{m.name:28} {m.size / 1e6:7.1f} MB  {state:7}  {dest}  [{m.license}]"
            )
            if m.note:
                print(f"{'':12} {m.note}")
        return 0
    if a.check:
        bad = [m.name for m in models.values() if not ok(m)]
        print("all present" if not bad else f"missing or wrong: {', '.join(bad)}")
        return 1 if bad else 0

    unknown = [n for n in a.names if n not in models]
    if unknown:
        print(f"unknown model(s): {', '.join(unknown)}; see --list", file=sys.stderr)
        return 2
    todo = [models[n] for n in a.names if not ok(models[n])]
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
        path = (
            fetch(m, a.root) if isinstance(m, ModelFile) else fetch_archive(m, a.root)
        )
        print(f"{m.name} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
