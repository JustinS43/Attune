# Local model installation

Model weights live in this directory on each laptop. They are ignored by Git. Use the
versioned downloader rather than copying a model with the same filename from another
project: the voice matching thresholds and saved prints depend on the exact CAM++ export.

## Speaker demo setup

From the repository root, use Python 3.12 and [uv](https://docs.astral.sh/uv/). Install
the Audio and Vision extras from `engine/uv.lock`; the Audio extra includes both
`sherpa-onnx` and `sherpa-onnx-core`. On macOS, the latter provides the native library
needed to load CAM++.

```bash
uv sync --project engine --extra audio --extra vision --extra dev --locked
uv run --project engine --extra audio python -c 'import sherpa_onnx; print(sherpa_onnx.__version__)'
```

The whole engine also needs `--extra hardware`: the Arduino's serial link and ElevenLabs.

With permission to download the models, install the exact files used by the live speaker
demo. The downloader pins the source revision, byte count, and SHA-256 of each file. It
verifies a download before moving it into place and skips files already verified.

```bash
python scripts/download_models.py --yes buffalo_l face_landmarker light_asd \
  cam_plus_plus whisper_config.json whisper_model.bin \
  whisper_preprocessor_config.json whisper_tokenizer.json whisper_vocabulary.json
python scripts/download_models.py --check
```

The required installed files total about 1.85 GB. `buffalo_l` downloads a 288.6 MB
archive but installs only its face detector and recognizer; it does not install the age
or gender models. Allow extra free space while the archive and model files are being
downloaded.

| Model | Installed path | Size | SHA-256 |
|---|---|---:|---|
| 3D-Speaker CAM++ | `models/cam++.onnx` | 29.6 MB | `357a834f702b80161e5b981182c038e18553c1f2ca752ed6cec2052365d4129b` |
| Light-ASD TalkSet | `models/light_asd/finetuning_TalkSet.model` | 4.2 MB | `efc375833887eefa9d209dc92810e18519b04c3c73ea35a549f2a7f40b7d94d5` |
| MediaPipe face landmarker | `models/faces/mediapipe/face_landmarker.task` | 3.8 MB | `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff` |
| Buffalo_L face detector | `models/faces/buffalo_l/det_10g.onnx` | 16.9 MB | `5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91` |
| Buffalo_L face recognizer | `models/faces/buffalo_l/w600k_r50.onnx` | 174.4 MB | `4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43` |
| Whisper large-v3-turbo | `models/faster-whisper-large-v3-turbo/model.bin` | 1617.9 MB | `e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da` |

Whisper also needs the `config.json`, `preprocessor_config.json`, `tokenizer.json`, and
`vocabulary.json` files installed by the command above. Their exact hashes and upstream
URLs are in [`scripts/download_models.py`](../scripts/download_models.py); `--check`
verifies all of them. The source and usage terms are listed in
[`docs/setup.md`](../docs/setup.md). Buffalo_L is restricted to non-commercial research.

### Faster CPU rehearsal

On a CPU-only laptop, the optional Whisper Base model is faster for a diagnostic demo.
It is about 148 MB and does not replace the project's default large model.

```bash
python scripts/download_models.py --yes whisper_cpu_config.json \
  whisper_cpu_model.bin whisper_cpu_tokenizer.json whisper_cpu_vocabulary.txt
python scripts/download_models.py --check whisper_cpu_config.json \
  whisper_cpu_model.bin whisper_cpu_tokenizer.json whisper_cpu_vocabulary.txt
```

In the ignored `config/attune.toml`, select it with:

```toml
[whisper]
model_path = "models/faster-whisper-base"
```

### Other checkouts and mismatches

Every checkout has its own ignored `models/` directory. To install into another checkout,
run this repository's downloader with `--root /absolute/path/to/checkout` and the same
model names, then repeat `--check` with that root. If a file has the right name but the
wrong hash, rerun its named download; the downloader replaces only the invalid file.

If CAM++ was replaced with a different export, existing voice prints made with that
export will not match the new model. Re-enroll consenting people before testing saved
names. No voice recordings or voice prints belong in Git.

## Other models the engine uses

`scripts/download_models.py` doesn't fetch these yet (P-14). The engine still starts
without them: it logs what's missing and carries on as the last column says. Sizes are
from the demo laptop.

| Model | Installed path | Size | Without it |
|---|---|---:|---|
| Nemotron 3.5 streaming ASR, the sherpa-onnx INT8 export of `nvidia/nemotron-3.5-asr-streaming-0.6b` (560 ms chunks) | `models/nemotron/`: `tokens.txt`, `encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx` | 653 MB | Captions come from the Whisper model above, which is slower |
| Kokoro multi-lang v1.0, the sherpa-onnx export | `models/tts/kokoro-multi-lang-v1_0/` | 384 MB | "Speak for me" works only with an ElevenLabs key |
| EfficientAT mn10 sound tagger, exported locally from the upstream weights (see [docs/calibration.md](../docs/calibration.md)) | `models/efficientat-mn10-as.ts`, `models/audioset-labels.json` | 20 MB | Only the rhythm alerts (T3/T4 alarm patterns) run |
| Qwen 3.5, managed by Ollama | installed with `ollama pull qwen3.5:4b` (fallback `qwen3.5:2b`) | | No reply suggestions, descriptions or translation |

Silero VAD needs no file here: the `silero-vad` package includes its model.
