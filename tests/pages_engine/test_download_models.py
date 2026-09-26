"""Downloader checks for the model files used by the live demo."""

from __future__ import annotations

import hashlib
import io
import runpy
import tomllib
import zipfile
from pathlib import Path

import pytest
from attune.vision.settings import load_settings

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "download_models.py"
DOWNLOADER = runpy.run_path(str(SCRIPT))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _archive(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return stream.getvalue()


def test_archive_extracts_only_checked_speaker_models(tmp_path, monkeypatch):
    detector = b"detector"
    recognizer = b"recognizer"
    archive = _archive(
        {
            "buffalo_l/det_10g.onnx": detector,
            "buffalo_l/w600k_r50.onnx": recognizer,
            "buffalo_l/genderage.onnx": b"never extract this",
        }
    )
    model = DOWNLOADER["ModelArchive"](
        "test",
        "https://example.invalid/test.zip",
        "models/faces/buffalo_l",
        len(archive),
        _digest(archive),
        "test",
        (
            DOWNLOADER["ArchiveMember"]("det_10g.onnx", _digest(detector)),
            DOWNLOADER["ArchiveMember"]("w600k_r50.onnx", _digest(recognizer)),
        ),
    )
    monkeypatch.setattr(
        DOWNLOADER["urllib"].request, "urlopen", lambda *_a, **_kw: io.BytesIO(archive)
    )

    result = DOWNLOADER["fetch_archive"](model, tmp_path)

    assert (result / "det_10g.onnx").read_bytes() == detector
    assert (result / "w600k_r50.onnx").read_bytes() == recognizer
    assert not (result / "genderage.onnx").exists()
    assert DOWNLOADER["is_archive_ok"](model, tmp_path)
    assert not list(result.glob("*.part"))


def test_archive_rejects_bad_hash_without_installing(tmp_path, monkeypatch):
    archive = _archive({"det_10g.onnx": b"detector"})
    model = DOWNLOADER["ModelArchive"](
        "test",
        "https://example.invalid/test.zip",
        "models/faces/buffalo_l",
        len(archive),
        "0" * 64,
        "test",
        (DOWNLOADER["ArchiveMember"]("det_10g.onnx", _digest(b"detector")),),
    )
    monkeypatch.setattr(
        DOWNLOADER["urllib"].request, "urlopen", lambda *_a, **_kw: io.BytesIO(archive)
    )

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        DOWNLOADER["fetch_archive"](model, tmp_path)

    assert not list(tmp_path.rglob("*.onnx"))
    assert not list(tmp_path.rglob("*.part"))


def test_every_download_lands_where_the_engine_loads_it():
    config = tomllib.loads((ROOT / "config" / "attune.example.toml").read_text(encoding="utf-8"))
    vision, _ = load_settings(config)
    files = {m.dest for m in DOWNLOADER["MODELS"].values()}
    faces = DOWNLOADER["ARCHIVES"]["buffalo_l"]
    files |= {f"{faces.dest_dir}/{member.filename}" for member in faces.members}
    whisper = config["whisper"]["model_path"]
    loaded = [
        vision.det_model,
        vision.rec_model,
        vision.landmarker_model,
        vision.asd_model,
        config["voice"]["model_path"],
        *(
            f"{whisper}/{name}"
            for name in (
                "config.json",
                "model.bin",
                "preprocessor_config.json",
                "tokenizer.json",
                "vocabulary.json",
            )
        ),
    ]

    assert [path for path in loaded if path not in files] == []
