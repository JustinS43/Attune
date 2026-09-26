# models/ (gitignored)

Downloaded model files live here and are never committed. `scripts/download_models.py` (TODO P-14) fetches them; ask your human before running it, since it downloads several GB. The full list with sizes is in the plan, section 07 "Downloads needed", and in docs/setup.md.

Expected layout:
```
models/
  asr/nemotron-3.5-streaming-560ms-int8/   Section 2
  asr/faster-whisper-large-v3-turbo/        Section 2
  vad/silero-v6/                            Section 2
  voiceprint/campplus/                      Section 2
  alerts/efficientat-mn10-as/               Section 2
  faces/buffalo_l/                          Section 1
  faces/yunet-sface/                        Section 1 (fallback)
  faces/mediapipe-face-landmarker/          Section 1
  tts/kokoro-82m-multilingual/              Section 3
```
Ollama models (qwen3.5:4b, optional translategemma:4b) are managed by Ollama itself.
