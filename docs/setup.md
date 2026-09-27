# Install and setup guide

**Owner:** Section 4 - Pages, Engine & Demo  
**TODO:** P-14 (install and downloads still to be written), P-02 (running the engine)  
**Plan:** section 07 Downloads, 08 Before the event

## Running the engine

Run everything from the repo root, so `config/`, `models/` and `data/` resolve:

Before starting live recognition, check the local model paths in
`config/attune.example.toml` against your `models/` directory. Model weights and
rehearsal reels are ignored by Git, so a fresh checkout does not include them.
`python scripts/download_models.py --list` currently covers only Light-ASD;
the other model files must come from the team's prepared setup. Do not use that
script expecting it to supply the full model set.

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

To inspect the phone interface without models or hardware, run
`python3 -m http.server 8766` from the repo root and open
`http://localhost:8766/web/phone/?demo`. This previews the pages and local photo
storage only; it does not exercise recognition or live lens captions. The film
view also needs the ignored reel files under `data/reels/film/`.

### Options

| Option | What it does |
|---|---|
| `--source PATH_OR_INDEX` | Use a video file (e.g. `data/reels/film/cafe_friends.mp4`) or a camera index instead of the webcam named in `[vision] camera_name` |
| `--audio-file WAV` | Feed a 16-bit WAV into the pipeline as if it were the mic (implies `--no-mic`). Laptop mic arrays cancel their own speakers, so playing a clip aloud does not work |
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

### Try contact memory on the rig

1. From the repo root, run `uv run --project engine python -m attune --simulate-hardware`.
   Open `http://localhost:8000/demo/` for the lens and phone together, or open
   `http://localhost:8000/phone/` on a phone connected to the same local network
   (start with `--host 0.0.0.0` and use the laptop's address in that case).
2. In **People**, use **Add a photo contact** to save a photo and name on that browser.
   The photo alone does not enroll recognition. Select **Add face + voice**, then
   follow the station's camera and microphone steps. A face that was saved before
   a voice failure has a **Finish voice** action, which keeps the same profile when
   the face matches again.
3. To test automatic memory, have an unrecognized person face the glasses and
   converse until a final caption is attributed to their face. They should appear
   under **Others** as **New person**. Keep talking to let a voice print be
   harvested; the People card then changes from **Face only** to **Face + voice**.
   Automatic profiles survive an engine restart. Repeated encounters can move one
   to **Familiar**; **Keep close** pins a person to **Close**.
4. Let that person introduce themselves to trigger a cautious name proposal, or
   address the sole visible person by name in separate utterances. Confirm or
   reject the proposal in People. Unconfirmed automatic naming needs repeated
   evidence across two days. Test an identified speaker from behind only after
   their voice print has been captured; the lens caption includes their name and
   an arrow for direction.
5. In **Glasses settings**, turn **Name labels** off and on. Face labels above
   heads should follow this setting. Speech stays in the bottom center, with
   secondary conversations in smaller left and right slots.

Photos remain in that browser's local storage. Face and voice prints are stored
under the engine's ignored `data/people/` directory. Real camera, microphone,
model and two-mic timing must be checked on the target rig; a film plus unrelated
audio cannot prove face-to-speaker or voice attribution.

### Config

`config/attune.example.toml` holds every key with the plan's defaults. The local
`config/attune.toml` (gitignored) is merged over it table by table, so it only needs
the keys you change. Secrets (ElevenLabs) go in `.env`, never in the toml.

### What the engine writes

- `data/sessions/<session_id>.jsonl`: the session log. Event types and times only
  (never caption text, names, typed replies, audio or video), plus the console's Mark notes.
- `data/history.db`: Section 3's conversation history, deleted after 24 hours.
- Only `data/reels/film/` is served over HTTP (for demo reels); people, profiles,
  sessions and history files never are.

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
