# Audio & Language tests

Run from the repository root:

```sh
uv run --project engine --extra dev pytest tests/audio_language
```

Tests require Python 3.12, NumPy and pytest, and do not load/download models, open
devices, play sounds or contact Ollama. They inject model responses and generated
PCM. `conftest.py` contains independently chosen test configuration; production
settings belong to the separate shared configuration change.

Coverage includes audio spans and gaps, VAD hysteresis, draft/final timing,
local-agreement decoding, mute gating, consent correlation and invalidation,
voice deletion, both T3/T4 frequency bands, motor rejection, alert lifecycle,
name traps, description allowlists, language job priority/cancellation, and
calibration persistence/validation. Live model/device acceptance remains required;
see `docs/calibration.md`.
