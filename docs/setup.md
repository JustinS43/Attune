# Install and setup guide

**Owner:** Section 4 - Pages, Engine & Demo  
**TODO:** P-14 (install and downloads still to be written), P-02 (running the engine)  
**Plan:** section 07 Downloads, 08 Before the event

## Running the engine

### Local models for the speaker demo

With permission to download model weights, run this from the repo root:

```bash
python scripts/download_models.py --yes buffalo_l face_landmarker light_asd \
    cam_plus_plus whisper_config.json whisper_model.bin \
    whisper_preprocessor_config.json whisper_tokenizer.json whisper_vocabulary.json
python scripts/download_models.py --check
```

Use `--root /path/to/checkout` when the running engine is in another checkout.
The downloader pins sizes and SHA-256 hashes and verifies files before installing them.
It extracts only the face detector and recognizer from Buffalo_L; its age and gender
models are not installed. InsightFace's Buffalo_L weights are for non-commercial
research use only. Model weights stay in the ignored `models/` folder.

The live demo needs the glasses camera and a microphone: name them in `config/attune.toml`
(`[vision] camera_name`, `[audio] device_name`), and on macOS allow both under System
Settings > Privacy & Security. `cam_plus_plus` is 3D-Speaker's CAM++ export, the one the
voice thresholds were tuned on; if `--check` reports it wrong, you have another export
(WeSpeaker's, say): fetch it again, then re-enroll voices, because prints from different
models never match. Without a saved person, the demo can show speech placement but cannot
verify saved-name accuracy.

On a CPU-only laptop, the default large Whisper model may decode too slowly for a
live rehearsal. For a diagnostic run, download the optional multilingual Base model
and point a local config at it:

```bash
python scripts/download_models.py --yes whisper_cpu_config.json \
    whisper_cpu_model.bin whisper_cpu_tokenizer.json whisper_cpu_vocabulary.txt
```

```toml
[whisper]
model_path = "models/faster-whisper-base"
```

This does not change the project's default model or its quality target. `--check`
verifies required models by default; pass optional model names to check those too.

Run everything from the repo root, so `config/`, `models/` and `data/` resolve:

```bash
cp config/attune.example.toml config/attune.toml     # once; edit camera_name etc. there
uv run --project engine python -m attune              # webcam (by name) + mic, opens the lens page
```

The engine starts every section's service on one bus and one clock (hardware, audio,
vision, fusion, alerts, llm, calibration, speech_out, history, then the web server).
A service that is missing or fails to start is logged, shown in the console's status
strip, and skipped; the rest keep running. Ctrl+C stops everything in about 3 seconds.

| Page | URL |
|---|---|
| Demo: glasses view and phone side by side (opens on start; `` ` `` or Alt+1/2/3 switches to one full size) | http://localhost:8000/demo/ |
| Lens (full screen, what OBS records) | http://localhost:8000/lens/ |
| Panels (console, speak, history) | http://localhost:8000/panels/ |
| Phone preview | http://localhost:8000/phone/ |
| WebSocket hub | ws://localhost:8000/ws |
| History API | http://localhost:8000/api/history/sessions |

### Options

| Option | What it does |
|---|---|
| `--source PATH_OR_INDEX` | Use a video file (e.g. `data/reels/film/cafe_friends.mp4`) or a camera index instead of the webcam named in `[vision] camera_name`. A file that doesn't exist stops the engine at once (exit code 2): a typo never falls back to a webcam |
| `--audio-file WAV` | Feed a 16-bit WAV into the pipeline as if it were the mic (implies `--no-mic`). Laptop mic arrays cancel their own speakers, so playing a clip aloud does not work. A missing file stops the engine at once |
| `--repeat-audio SECONDS` | Play `--audio-file` again this long after it ends (0 = once) |
| `--audio-delay SECONDS` | Wait before the first play of `--audio-file` (default 3) |
| `--no-mic` | Don't open the microphone |
| `--simulate-hardware` | Run Section 3's simulated Arduino (same as `[hardware] simulate = true` or `ATTUNE_SIMULATE_HARDWARE=1`) |
| `--port 8000` / `--host 127.0.0.1` | Web server address (defaults from `[engine]`) |
| `--no-browser` | Don't open the lens page |
| `--config PATH` | Another local config file, merged over `config/attune.example.toml` |

Log level: set `ATTUNE_LOG=DEBUG` for more detail.

Try it without a camera, mic or Arduino:

```bash
uv run --project engine python -m attune --source data/reels/film/cafe_friends.mp4 \
    --audio-file path/to/speech.wav --repeat-audio 5 --simulate-hardware
```

### Config

`config/attune.example.toml` holds every key with the plan's defaults. The local
`config/attune.toml` (gitignored) is merged over it table by table, so it only needs
the keys you change. Secrets (ElevenLabs) go in `.env`, never in the toml.

A config file that doesn't parse stops the engine with one line, `Config error: <file>:
<what is wrong> (at line N, column M)`, and exit code 2.

Local-only settings that are not in the example file:

| Key | What it does |
|---|---|
| `[speech_out] device = "none"` | Speak-for-me runs the voice but plays nothing (tests, a silent rehearsal). `"null"`, `"off"` and `"silent"` work too |
| `[pages] allowed_origins = ["https://..."]` | Extra websites allowed to open the WebSocket (exact `scheme://host:port`) |
| `[engine] allowed_hosts = ["attune.lan"]` | Extra host names the engine answers to while it listens on 127.0.0.1 |

### Who can connect

The engine listens on 127.0.0.1 by default, and only the laptop's own pages may use it:

- Requests must name the laptop (`localhost`, `127.0.0.1`, `*.localhost`); any other `Host`
  gets 400. This blocks DNS rebinding, where a website renames itself to 127.0.0.1 to read
  the history API or captions.
- The WebSocket refuses a browser page from another origin (another website open in the
  same browser, a `file://` page) before it opens, and logs it once.
- No API schema is published (`/openapi.json` and `/docs` are 404).

To try the phone page on a real phone, run with `--host 0.0.0.0` and open
`http://<laptop-ip>:8000/phone/`. The engine then answers to any host name (it logs a
warning), but the WebSocket still only accepts pages served by the engine itself.

### Simulated Arduino controls

With `--simulate-hardware` the engine also serves (JSON only, so a form on another site
can't use them):

| Route | What it does |
|---|---|
| `POST /api/sim/touch {"gesture": "tap"}` | A touch on the pad: `tap`, `hold`, `double`, `triple` |
| `POST /api/sim/control {"heartbeat": false}` | Stop the laptop's heartbeat (the board's 2 s safety stop and LOST) |
| `POST /api/sim/control {"sound": {"left": 300, "right": 0}}` | Steady loudness on each sensor, so an alert has a side |
| `POST /api/sim/control {"reboot": true}` | Reset the board as a brown-out would |
| `GET /api/sim/state` | What the board got and is doing: lines, pattern, icon, ACKs, CFG |

### End-to-end tests

`tests/e2e/` starts real engines in replay mode on port 8013 and drives every page in a
headless Microsoft Edge: captions in order on the glasses and the phone, alerts to the
rig, camera and pause, reconnects, privacy, bad setups, the laptop station's phone screens
and a soak. See
[tests/e2e/README.md](../tests/e2e/README.md):

```bash
python tests/e2e/run_e2e.py            # about 15 minutes
python tests/e2e/run_e2e.py pages      # one scenario
```

### What the engine writes

- `data/sessions/<session_id>.jsonl`: the session log. Event types and times only
  (never caption text, names, typed replies, audio or video), plus the console's Mark notes.
- `data/history.db`: Section 3's conversation history, deleted after 24 hours.
- Only `data/reels/film/` is served over HTTP (for demo reels); people, profiles,
  sessions and history files never are.

## ElevenLabs settings

On the laptop, open `/phone/`, then **Settings → Speak for me → ElevenLabs voice**.
Enter your API key and optional voice ID, then choose **Save voice settings**. The
new details apply to the next spoken reply without restarting Attune. Leave the
key blank to keep it; leave the voice ID blank for the default voice.
The saved key is never returned to the browser. It is cleared from the input after
submission and is not stored in browser storage or sent through the event bus.

The form updates only `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` in the engine
working directory's `.env`, preserving other entries. Direct `.env` editing still
works. Nonblank environment variables retain precedence; the form indicates when
they manage a value. Secrets are only editable from the laptop using `localhost`
or a loopback address; a remote phone shows instructions to use the laptop.
Demo mode disables credential entry. A static preview cannot save settings; use
the live Attune page on the laptop. Saving does not contact ElevenLabs or verify
that the account has credits or access to the chosen voice.

The page uses `GET /api/settings/elevenlabs` for nonsecret status and
`POST /api/settings/elevenlabs` with optional `api_key` and `voice_id` strings to save.
Writes require same-origin JSON requests. Responses contain `key_configured`,
`key_source`, `voice_id`, `voice_from_environment`, and, after saving,
`restart_required`; never the key. These routes are local settings, not bus commands.

## Cloud captions (optional)

Cloud captions stream the microphone audio to Google Speech-to-Text, which labels every word
with a speaker, so a quick reply from a second person gets its own bubble. They are **off by
default**. While they are on, only the 16 kHz microphone audio leaves the laptop (never
frames, faces, voice prints, names, captions or history), and every page shows a calm
"Cloud captions on" badge. Local captions keep running the whole time, and Attune falls
back to them without a gap if Google is slow, unreachable or has no key. Design and limits:
[cloud-diarization.md](cloud-diarization.md).

**Cost.** About $0.024 per minute of audio sent, with Google's data logging off (Attune
never turns it on), and the first 60 minutes each month are free. Prices change: check
[Google's pricing page](https://cloud.google.com/speech-to-text/pricing) before relying on
these figures. Nothing is sent, and nothing is billed, while the switch is off.

**Set it up (an API key):**

1. Open the [Google Cloud console](https://console.cloud.google.com/) and create a project,
   or pick an existing one.
2. Enable billing for that project (**Billing**). The free minutes still need a billing
   account.
3. Go to **APIs & Services → Library**, search for **Cloud Speech-to-Text API**, and choose
   **Enable**.
4. Go to **APIs & Services → Credentials → Create credentials → API key**.
5. Restrict the key: open it, and under **API restrictions** choose **Restrict key** and tick
   only **Cloud Speech-to-Text API**. Save.
6. On the laptop, open `/phone/` at `localhost`, then **Settings → Cloud captions (Google) →
   Google API key**. Paste the key and choose **Save key**. (Or put
   `GOOGLE_SPEECH_API_KEY=...` in the engine's `.env` yourself.) The key is saved only in the
   laptop's `.env`; Settings then shows "Key saved ••••" and never shows it again. It applies
   without a restart.
7. Leave Google's **data logging** off (it is off unless you opt in on the Speech-to-Text
   page).
8. Turn on the **Cloud captions** switch in the same card. The status line then reads
   "Connecting…" and "On · <latency> ms"; the console shows the same.

Paste the key only into the laptop's Settings page or its `.env`: never into chat, an issue,
a commit, a screenshot or anywhere else. If it leaks, delete it in the Cloud console and make
a new one.

**Alternative: a service account.** In **IAM & Admin → Service accounts**, create a service
account with the **Cloud Speech Client** role (`roles/speech.client`), then **Keys → Add key →
Create new key → JSON**. Save the JSON file outside the repo (for example
`C:\Users\<you>\keys\attune-speech.json`) and put its full path in the engine's `.env`:

    GOOGLE_APPLICATION_CREDENTIALS=C:\Users\<you>\keys\attune-speech.json

With both set, the API key is tried first. Settings shows only whether a service-account
file is set, never its path.

The card uses `GET /api/settings/google` (returns `key_configured`, `key_source`
(`environment`, `file` or `none`), `service_account` and `restart_required`, never the key or
a path) and `POST /api/settings/google` with `{"api_key": "..."}`. Like the ElevenLabs
settings, these routes answer only on the laptop (`localhost` or a loopback address) to
same-origin JSON requests; a blank key keeps the saved one. The switch and the language send
the `cloud.set` command; the laptop remembers the choice in `data/cloud.json`.

## Windows notes

- **Keep the virtual environment on a short path** (for example `C:\venvs\attune`, via
  `UV_PROJECT_ENVIRONMENT`). Some wheels (CUDA, cuDNN) have deep file trees that break
  the 260-character path limit.
- **PortAudio can fail to load or open the mic from very long folder paths.** If the
  audio service can't start capture and the repo or venv lives deep inside another
  folder, move it to a short path such as `C:\attune` (or run with `--audio-file` /
  `--no-mic` meanwhile).
- torch is imported before onnxruntime at start-up: both ship cuDNN DLLs with the same
  names, and loading torch first lets both work in one process.
- The camera is opened by name (`[vision] camera_name`), never by index 0, so the
  laptop's own webcam isn't picked by mistake. `--source 1` forces an index.
