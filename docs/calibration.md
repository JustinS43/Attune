# Audio & Language setup and venue calibration

Section 2 implements `AudioService`, `AlertService`, `LLMService`, and
`CalibrationService`. All use `Service(bus, config)`, `start()`, and `stop()`.
Constructors do no model loading. Bus callbacks enqueue work; model inference runs
on service workers. Call `stop()` even when startup fails after subscriptions begin.

## Integration prerequisites

The initial shared bus, clock, entrypoint and replay player are still placeholders.
Section 4 must supply a thread-safe bus and one shared clock (`core.clock.now`, or
`config["clock"]`), create the four services, and stop them on shutdown. The
configuration mapping uses the sections from `config/attune.example.toml`.

The separate `shared/audio-contracts` branch adds model paths and tuning settings,
local-only `audio.block` events, an optional enrollment `track_id`, and optional
calibration observations. Merge that shared change before deploying this feature.
Do not serialize PCM blocks into the ordinary event log, WebSocket or history.
Replay can inject blocks with `AudioService(..., mic=False)`.

Start alerts and calibration before capture if the very first audio blocks matter.
The clock must match camera, sensor, caption and replay timestamps. Never use a
separate clock origin per service. Calibration profiles are measurement data:
Section 4 must load the selected profile when wiring fusion's wearer threshold,
sensor gains and audiovisual offset. They do not rewrite shared defaults.

Capture timestamps use one PortAudio-to-engine clock mapping per stream, so
callback scheduling jitter does not become a false audio gap. Both output sample
rates use continuous soxr resampling; a real timestamp gap or overflow discards
filter history. Windows preferred and fallback devices are explicitly selected
from WASAPI. Capture availability and overflow/drop counts appear in the audio
service health report. Utterance gain is established from the first speech chunk;
trailing quiet chunks are not independently amplified.

The device selection and timing fields follow the [sounddevice capture API](https://python-sounddevice.readthedocs.io/en/0.5.3/api/checking-hardware.html).

## Local models

Services never download weights or send data to remote endpoints. Arrange downloads
with the owner of `scripts/download_models.py` first. No model weights or recordings
belong in Git. Install the repository's `audio` extra and `dev` extra on the target
machine; the CUDA package index is intended for the demo laptop, not this Mac.

- Silero uses the model bundled in the installed `silero-vad` package.
- Nemotron uses explicit encoder, decoder, joiner and token paths under `[nemotron]`.
  The released streaming Nemotron model is **English-only**. Set `audio.languages`
  to `["en"]` for it; missing local Nemotron weights select local Whisper instead.
  Runtime decoder failures also retry the accumulated utterance through local
  Whisper; unavailable fallback weights discard partial recognition and report an
  error. Multilingual lists and auto detection use Whisper from the start, avoiding an
  English recognizer silently corrupting Spanish. This differs from the build
  plan's assumption of multilingual Nemotron. See the [upstream model list](https://k2-fsa.github.io/sherpa/onnx/nemo/nemotron-streaming.html).
- Whisper requires a local CTranslate2 large-v3-turbo directory under `[whisper]`.
  Drafts contain the prefix agreed by two consecutive passes. Finals contain the
  complete latest hypothesis with word timestamps. The utterance length is bounded
  by `audio.max_utterance_s`.
- CAM++ requires a local ONNX speaker embedding model under `[voice]`. Voice
  enrollment waits for a successful face result echoing the selected track ID.
  Only final captions confidently assigned to that face contribute audio; duplicate
  captions and pre-confirmation spans cannot extend enrollment. Five seconds of
  speech are required. Consent timestamps and embeddings live in `data/people/`;
  raw enrollment audio stays in RAM. Harvested updates are session-only.
- EfficientAT requires the upstream mn10_as weights and installed upstream source.
  From an environment with that source on `PYTHONPATH`, export with:

  ```sh
  uv run --project engine python -m attune.alerts.export_model \
    --weights models/mn10_as.pt --output models/efficientat-mn10-as.ts \
    --labels models/audioset-labels.json
  ```

  This wrapper includes the mel frontend and saves the matching label order. It
  loads existing weights only; it never invokes upstream automatic downloads.
  See [EfficientAT's inference pipeline](https://github.com/fschmid56/EfficientAT/blob/main/inference.py).
- Ollama must run locally on `127.0.0.1:11434` with `qwen3.5:4b` already installed.
  Requests use structured JSON, `think=false`, `keep_alive=-1`, and the configured
  context and temperature. Jobs run one at a time: translation, names, replies,
  descriptions. Requests time out rather than holding up captions. Warm-up happens
  at startup. A cold model may exceed the request timeout; prewarm it before the
  demo. The current client does not automatically change models under GPU pressure.

Deletion removes this section's `voice.json`, never another section's face files.
Forget clears audio buffers, harvested prints, language context, descriptions and
pending names. Queued and in-flight results from an old session are invalidated.
Pause discards partial recognition. Typed speech mutes captions through playback
end plus `audio.mute_after_reply_s`, while the separate alert pipeline continues.

## The venue procedure

Send `calibrate.step` with `args.step` set to a step below to begin collection.
Finish with `args.step = "finish:<step>"`. Measurements are summarized in
`status.part`, part `calibration`, and saved atomically under `data/profiles/`.
A finish request with insufficient data fails without replacing the saved profile.

1. **level:** Look at a door frame in the lens view and rotate the webcam until it
   is vertical. Finish with `measurements: {"confirmed": true}`.
2. **mic:** Read a sentence and finish. Adjust input gain until the reported peak
   is about −6 dBFS; repeat the step to verify.
3. **you**, then **other:** Record the wearer reading a sentence, then a teammate
   reading at 1.5 m. Finish each. The wearer must be louder; the threshold is the
   midpoint of the two RMS levels in decibels.
4. **balance:** Play recorded steady noise from straight ahead at 1.5 m. Collect
   both sensors without motor activity, then finish to save their gain ratio.
5. **claps:** Make three visible claps. Annotate sound onset and the frame where
   the hands meet on the shared clock. Finish with three `audio_times` and three
   `video_times` in `measurements`. The median sound-minus-video offset is saved.
   Automatic visual clap detection is not implemented by the current contracts.
6. **noise:** Collect at least 30 seconds of ordinary hall noise, then finish.
7. **faces:** Check each enrolled teammate at 1, 2 and 3 m. Finish with
   `measurements: {"distances_checked": [1,2,3]}`. A minimum match score below 0.5
   asks for re-enrollment. Saved scores contain no person identifiers.

Example finish command:

```json
{"name":"calibrate.step","args":{"step":"finish:claps","measurements":{"audio_times":[10.12,12.10,14.11],"video_times":[10,12,14]}}}
```

Never trigger a real alarm. Generate quiet test files without playing them:

```sh
uv run --project engine python scripts/make_test_tones.py T3 data/test-t3.wav
uv run --project engine python scripts/make_test_tones.py T4 data/test-t4.wav --frequency 520
```

Playback is a separate, deliberate action using the team's recordings. Verify
left/right direction, motor suppression, acknowledge, 30-second re-alert and
15-second quiet clear with the assembled rig.

## Verification and remaining checks

Run `uv run --project engine --extra dev pytest tests/audio_language` and Ruff on
this section's files. The 50 tests use generated PCM, injected model responses and simulated devices;
the capture tests additionally exercise the real soxr library when installed;
they exercise service event flow, word timing, consent, privacy invalidation,
language validation, priorities, rhythm bands and alert lifecycle. They do not
establish real ASR accuracy, CUDA performance, EfficientAT accuracy, device
reconnection, Ollama vision behavior, or the end-to-end demo timing gates.

Before calling Section 2 complete: merge the shared contract/config additions,
wire the services through Section 4, provision and exercise the real local models,
run the target Windows WASAPI capture/reconnection check, validate enrollment with
Section 1, and run venue/replay acceptance checks. A blocking model inference may
outlive the bounded `stop()` join; its output is invalidated and its worker cleans
up once inference returns. Hard cancellation of native inference is not claimed.
