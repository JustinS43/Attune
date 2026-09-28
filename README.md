![Attune](web/shared/brand/attune-wordmark-light.svg)

Attune is a laptop-powered prototype of captioning glasses for deaf and hard-of-hearing people, built for ShellHacks (MLH). A camera and microphone feed local speech, face, and active-speaker models. The lens page places captions beside a visible speaker; an unmatched caption stays at the bottom with a direction arrow when the left/right sensors have enough evidence. Phone and console pages provide controls, typed replies, alerts, and local history.

This is a **prototype**, not a safety device. Sound alerts and speaker attribution need calibration with the actual room, microphone, and glasses rig. [Current work and limitations](TODO.md) are tracked separately.

## Choose a demo setup

| Setup | Hardware | Command after installation |
|---|---|---|
| Live physical rig | Laptop, glasses camera and microphone, USB-connected UNO R3 rig below | `uv run --project engine python -m attune` |
| Camera and microphone mockup | Laptop camera and microphone; Arduino simulated in the engine | `uv run --project engine python -m attune --simulate-hardware` |
| Recorded replay | Laptop plus a video file and 16-bit WAV file you supply | `uv run --project engine python -m attune --source /path/to/video.mp4 --audio-file /path/to/speech.wav --simulate-hardware` |

Run these from the repository root. Replay files must exist; `--audio-file` replaces live microphone capture. The simulator exercises the software but cannot measure real sound direction, touch wiring, LEDs, or servo taps.

## Hardware for the physical prototype

The team's build uses an **Arduino UNO R3 and SG92R micro servo**, with the laptop doing all model inference. The Arduino reads two sound sensors and a touch sensor, then drives two LEDs and the servo tapper. A laptop webcam and microphone work for a desk mockup; glasses-mounted camera/microphone are needed to test the intended wearer view.

| Part | Quantity | Connection / purpose |
|---|---:|---|
| Laptop with camera, microphone, speaker, and USB | 1 | Runs Python engine, models, and web pages |
| Glasses frame and camera/microphone mount | 1 | Places live inputs at wearer height |
| Arduino UNO R3, USB cable, Grove Base Shield | 1 each | Sensor and actuator controller; USB serial at 115200 baud |
| Grove Sound Sensor | 2 | Left signal to **A2**, right signal to **A0** |
| Grove Touch Sensor (TTP223) | 1 | **D2**; tap/hold/double/triple gestures |
| Grove LED Socket Kit | 2 | Left **D5**, right **D6** |
| SG92R micro servo and three jumpers | 1 | Signal **D9**, red **5V**, brown **GND** |
| Grove cables, tape, labels | as needed | Mount and label each left/right connection |
| 100–470 µF capacitor, at least 6.3 V | optional | Across servo 5V/GND if taps reset the board |

Use the Arduino's USB 5 V for the rig and do not connect the servo to 3.3 V or VIN. The servo arm should touch the temple lightly without stalling. The [wiring and bench-test guide](docs/hardware/wiring.md) has the full pin map, power notes, and test procedure. An **UNO R4 WiFi plus I²C motor driver** is an alternative described in [firmware/README.md](firmware/README.md); its motor-driver path still needs a compile and bench check after the R3 changes.

### Flash the UNO R3

Install [Arduino CLI](https://arduino.github.io/arduino-cli/latest/installation/) or use Arduino IDE 2 with **Arduino AVR Boards** and the **Servo** library. From the repository root:

```bash
arduino-cli core install arduino:avr
arduino-cli lib install Servo
arduino-cli board list                         # note the connected board's port
arduino-cli compile --fqbn arduino:avr:uno firmware/attune_rig
arduino-cli upload --fqbn arduino:avr:uno -p /path/to/port firmware/attune_rig
```

Replace `/path/to/port` with the listed port (for example `/dev/cu.usbmodem...` or `COM5`). At 115200 baud, the serial monitor should show `READY 1.1.2 NONE` and `LV` sensor readings. **Close the serial monitor before launching Attune** so the engine can open the port. The engine normally finds the UNO by USB ID; set `[hardware] port` in the local config only if it selects the wrong board.

## Install the laptop software

Use **Python 3.12** and [uv](https://docs.astral.sh/uv/getting-started/installation/) on macOS or Windows. The lockfile supplies the Python packages for audio, vision, hardware, and development. On a fresh machine, install uv using its official instructions, then from the Attune checkout:

```bash
uv python install 3.12
uv sync --project engine --extra audio --extra vision --extra hardware --extra dev --locked
```

Allow camera and microphone access for the terminal/app in macOS **System Settings → Privacy & Security** (or equivalent OS permissions). The browser pages need no JavaScript build step. Keep several GB free for Python packages and weights; the pinned required model files occupy about **1.85 GB** after installation.

### Download the required local models

The versioned downloader pins each file's source, size, and SHA-256, then checks it before moving it into the ignored `models/` directory. Run from **this checkout**, since another checkout has its own `models/` folder:

```bash
uv run --project engine python scripts/download_models.py --yes \
  buffalo_l face_landmarker light_asd cam_plus_plus \
  whisper_config.json whisper_model.bin whisper_preprocessor_config.json \
  whisper_tokenizer.json whisper_vocabulary.json
uv run --project engine python scripts/download_models.py --check
```

| Component | Installed files | Job |
|---|---|---|
| Buffalo_L detector and recognizer | `models/faces/buffalo_l/` | Find faces and match saved contacts |
| MediaPipe face landmarker | `models/faces/mediapipe/face_landmarker.task` | Mouth and face landmarks |
| Light-ASD TalkSet | `models/light_asd/finetuning_TalkSet.model` | Audio/visual active-speaker evidence |
| 3D-Speaker CAM++ | `models/cam++.onnx` | Voice embeddings for speaker matching |
| Faster-Whisper large-v3-turbo | `models/faster-whisper-large-v3-turbo/` | Local speech-to-text and fallback ASR |

The downloader extracts only Buffalo_L's face detector and recognizer; its age/gender models are not installed. Buffalo_L weights are restricted to **non-commercial research**. Silero VAD arrives with the Python `silero-vad` package and needs no separate download. See [models/README.md](models/README.md) for exact hashes, source and license details, and cross-checkout installation.

### Optional models for the full feature set

These are **separate installs**. The pinned downloader above does not fetch Nemotron, Kokoro, EfficientAT, or Ollama models. The engine can start without them, with these fallbacks:

| Option | What it enables | If absent |
|---|---|---|
| Whisper Base (CPU rehearsal) | Smaller/faster local ASR selection | Default large-v3-turbo remains selected |
| Nemotron 3.5 streaming ASR | Streaming caption drafts | Local Whisper handles captions |
| EfficientAT mn10 sound tagger | Learned sound labels such as doorbells/knocks | Rhythm-based T3/T4 alarm checks remain |
| Kokoro multi-lang v1.0 | Offline typed-reply speech | ElevenLabs works only with a key; otherwise no voice |
| Ollama Qwen 3.5 | Reply suggestions, descriptions, naming, translation | Captions, camera, and basic alerts continue |

**Faster CPU rehearsal.** On a CPU-only laptop, the default large Whisper model can fall behind live audio. Install Base and select it in the ignored local config; it changes the speed/accuracy tradeoff for this machine only:

```bash
uv run --project engine python scripts/download_models.py --yes \
  whisper_cpu_config.json whisper_cpu_model.bin \
  whisper_cpu_tokenizer.json whisper_cpu_vocabulary.txt
uv run --project engine python scripts/download_models.py --check \
  whisper_cpu_config.json whisper_cpu_model.bin \
  whisper_cpu_tokenizer.json whisper_cpu_vocabulary.txt
```

```toml
[whisper]
model_path = "models/faster-whisper-base"
```

**Nemotron streaming ASR.** The engine expects `tokens.txt`, `encoder.int8.onnx`, `decoder.int8.onnx`, and `joiner.int8.onnx` in `models/nemotron/`. This uses the upstream [560 ms INT8 sherpa-onnx export](https://k2-fsa.github.io/sherpa/onnx/nemo/nemotron-streaming.html). Whisper works without it.

```bash
mkdir -p models/nemotron
curl -L --fail -o models/nemotron.tar.bz2 \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11.tar.bz2
tar -xjf models/nemotron.tar.bz2 -C models/nemotron --strip-components=1
ls models/nemotron/{tokens.txt,encoder.int8.onnx,decoder.int8.onnx,joiner.int8.onnx}
```

**Kokoro offline speech.** Install the exact [sherpa-onnx v1.0 package](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/kokoro.html) expected by `speech_out`. The upstream package documents English and Chinese support; treat Spanish voice output as unverified for this export.

```bash
mkdir -p models/tts
curl -L --fail -o models/tts/kokoro-multi-lang-v1_0.tar.bz2 \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-multi-lang-v1_0.tar.bz2
tar -xjf models/tts/kokoro-multi-lang-v1_0.tar.bz2 -C models/tts
ls models/tts/kokoro-multi-lang-v1_0/{model.onnx,voices.bin,tokens.txt}
```

**EfficientAT sound labels.** The engine consumes a TorchScript export, not the upstream `.pt` checkpoint directly. Download the upstream [mn10_as checkpoint](https://github.com/fschmid56/EfficientAT/releases/tag/v0.0.1) and source, then export with the source on `PYTHONPATH`. Follow the upstream [environment instructions](https://github.com/fschmid56/EfficientAT#environment) if an additional Python import is missing.

```bash
git clone https://github.com/fschmid56/EfficientAT.git /tmp/attune-efficientat
curl -L --fail -o models/mn10_as.pt \
  https://github.com/fschmid56/EfficientAT/releases/download/v0.0.1/mn10_as_mAP_471.pt
PYTHONPATH=/tmp/attune-efficientat uv run --project engine python -m attune.alerts.export_model \
  --weights models/mn10_as.pt --output models/efficientat-mn10-as.ts \
  --labels models/audioset-labels.json
ls models/efficientat-mn10-as.ts models/audioset-labels.json
```

**Ollama.** Install and start [Ollama](https://ollama.com/download) separately, then fetch the model named in `config/attune.example.toml`. The Python client comes from the `audio` extra; the Ollama server and weights do not.

```bash
ollama pull qwen3.5:4b
ollama pull qwen3.5:2b       # configured fallback; optional but useful
ollama list                  # confirm both are present
ollama serve                 # only if the Ollama app/service is not already running
```

Optional ElevenLabs speech needs `ELEVENLABS_API_KEY` (and optionally `ELEVENLABS_VOICE_ID`) in an ignored `.env` file. Optional Google cloud speaker labels are **off by default**; enabling them in phone Settings requires Google Speech-to-Text credentials in `.env` and sends microphone audio to Google while enabled. Neither cloud service is required for local captions. See [cloud diarization](docs/cloud-diarization.md) for the credential and consent flow. Never commit keys, recordings, prints, or weights.

## Configure and run a live demo

1. Copy the example once and edit the **actual device names** in ignored `config/attune.toml`. The example's `Logitech` camera/microphone names are placeholders. Use `[vision] camera_name`, `[audio] device_name`, and, for the enrollment station, `[enroll] camera_name` and `[enroll] mic_name`.
2. Connect and flash the Arduino if using the physical rig. Keep the serial monitor closed. Skip this for `--simulate-hardware`.
3. Start the engine from the repository root. It opens the demo page and starts the camera, microphone, model, hardware, and web services. Watch the console status strip for missing services or devices.
4. Open the lens or phone view as needed. Speak toward the selected microphone while a face is visible. A matched speaker gets a face-following bubble. Unmatched speech stays at the bottom, with a left/right arrow when direction is measured. Test alerts with **recordings**, never a real alarm.
5. Use the phone/console for typed replies, contact saving, history, settings, and hardware test patterns. Press **Ctrl+C** in the terminal to stop.

```bash
cp config/attune.example.toml config/attune.toml   # first run only
# Edit config/attune.toml with your camera and microphone names.
uv run --project engine python -m attune --simulate-hardware
# For the wired UNO, run instead: uv run --project engine python -m attune
```

| Page | Local URL |
|---|---|
| Side-by-side glasses and phone demo | http://localhost:8000/demo/ |
| Lens view | http://localhost:8000/lens/ |
| Phone controls | http://localhost:8000/phone/ |
| Console, speak controls, history | http://localhost:8000/panels/ |

The server listens on `127.0.0.1:8000` by default. For a real phone on the same network, run with `--host 0.0.0.0`, then open `http://<laptop-ip>:8000/phone/`; see [network notes](docs/setup.md#who-can-connect). `--port 8001` changes the port when 8000 is in use. `--no-browser` starts without a tab. Full replay and CLI options are in [docs/setup.md](docs/setup.md).

## When a demo does not work

| Symptom | Check |
|---|---|
| No captions or audio pickup | Confirm microphone permission and `[audio] device_name`; check the console's audio service and input level. For deterministic replay, use `--audio-file` rather than playing a clip into an echo-canceling laptop mic. |
| Long caption delay or missing words | Watch for audio backlog/dropped frames. CPU decoding of large-v3-turbo can be slow; try pinned Whisper Base above. Verify models with `download_models.py --check`. |
| Camera or face bubbles missing | Check camera permission, `[vision] camera_name`, lighting, and vision service status. `--source` can force a camera index or existing video file. |
| Left/right arrow wrong or absent | Check A2/A0 wiring and sensor labels; sensors need measurable level separation and calibration. Simulator mode has no physical direction. See [bench tests](docs/hardware/wiring.md#how-to-test-tomorrow-plug-in--link--patterns). |
| UNO disconnected or patterns absent | Close Arduino Serial Monitor, check USB/port and `READY`, then inspect `hw.link`. Servo resets call for power/capacitor checks. |
| No suggestions or translation | Check Ollama is running locally and `qwen3.5:4b` is pulled. These jobs time out rather than blocking captions. |
| No typed-reply speech | Install Kokoro or configure an ElevenLabs key; check speaker output. Neither provider means no voice. |
| Learned sound alerts missing | Export EfficientAT; rhythm alarms have a separate path. Tune thresholds with recorded examples using [calibration guidance](docs/calibration.md). |

## Data, privacy, and project map

Captions, camera frames, face/voice prints, and history stay on the laptop. History expires after **24 hours** by default. The two opt-in network paths are typed text to ElevenLabs for speech and microphone audio to Google Speech-to-Text while cloud captions are enabled. Ollama runs locally. See [contracts and privacy rules](docs/contracts.md) for the exact data flow.

| Path | Contents |
|---|---|
| `engine/` | Python 3.12 engine and locked uv project |
| `web/` | Lens, phone, console, and demo pages; plain HTML/CSS/JS |
| `firmware/` | UNO R3/R4 sketch and protocol |
| `config/` | Tracked example and ignored local settings |
| `scripts/` | Pinned model downloader and development helpers |
| `models/`, `data/` | Ignored weights and local runtime data |
| `docs/`, `tests/` | Design, setup, calibration, contracts, and tests |

For architecture and feature locations, read [feature map](docs/feature-map.md). For deeper installation details, read [setup](docs/setup.md) and [model inventory](models/README.md). Contributors should read [AGENTS.md](AGENTS.md) and [TODO.md](TODO.md). Every change goes through a branch and pull request.
