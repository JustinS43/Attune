# Attune to-do list

How to use this list (full rules in [AGENTS.md](AGENTS.md#the-to-do-list)):
- Work in your own section, top to bottom, and check open PRs first so two people don't build the same thing.
- When your PR finishes an item, tick it in that same PR and add the PR number: `- [x] V-03 ... (#12)`.
- Only edit lines in your own section. Add new items at the end of your section with the next free ID.
- **Gate** tells you when it's needed: M0 = hour 3, M1 = hour 8, M2 = hour 14, M3 = hour 18 (feature freeze), M4 = hour 22.
- Files for each item: [docs/feature-map.md](docs/feature-map.md). Events and messages: [docs/contracts.md](docs/contracts.md).

## Kickoff (whole team, hour 0–1)

- [ ] K-01 Put each person's name next to their section in AGENTS.md and docs/feature-map.md
- [ ] K-02 Pick up the hardware with the plan's checklist (section 04), and note the driver board model and webcam model here
- [ ] K-03 Read docs/contracts.md together and agree on it before anyone codes against it
- [ ] K-04 Everyone: clone, copy `.env.example` to `.env` and `config/attune.example.toml` to `config/attune.toml`, run `uv sync --project engine`

## Section 1 – Vision

- [ ] V-01 Camera reader: open the webcam by name, MJPG 1080p30, one-frame buffer, clock stamps, "camera lost" recovery · M0
- [ ] V-02 VisionService publishes `vision.frame` · M0
- [ ] V-03 Face finder (SCRFD-10G), detection size from config, ignore faces under 36 px · M1
- [ ] V-04 Tracker: overlap + motion, 1 s survive, 10 s lost list, re-identification, exit side · M1
- [ ] V-05 Face prints (ArcFace w600k_r50) with the crop quality gate · M1
- [ ] V-06 Gallery and match rules (0.45 / 0.08 / 3 times in 1 s, 2 s recheck, no name jumps) · M1
- [ ] V-07 Enrollment (face part) with consent: 8 varied crops, refuse under 5 with a reason, rename, delete · M1
- [ ] V-08 Lip-motion score (MediaPipe on crops, 1 s rolling std; 0.03 / 0.015) · M1
- [ ] V-09 Who's talking, cases 1–4, hold and switch rules; publishes `caption` and `scene` · M1 (visible speaker) / M2 (all cases)
- [ ] V-10 In-time check (lips vs. sound rhythm) · M2
- [ ] V-11 Voice-print harvesting (`voice.harvest`) · M2
- [ ] V-12 Instant colour label for strangers · M2
- [ ] V-13 Tests in tests/vision using the replay reel · M2
- [ ] V-14 Stretch: Light-ASD for hard who's-talking cases · after M3, only if time

## Section 2 – Audio & Language

- [ ] A-01 Mic reader: WASAPI 48 kHz, 10 ms blocks, 16 kHz + 32 kHz streams, ring buffer, laptop-mic fallback · M0
- [ ] A-02 Silero VAD with the plan's thresholds; publishes `audio.vad` · M1
- [ ] A-03 Captions: Nemotron via sherpa-onnx, drafts/finals, language, word times; publishes `audio.transcript` · M1
- [ ] A-04 Whisper fallback (faster-whisper large-v3-turbo) · M2
- [ ] A-05 Mute mic captions while `speech_out.playing`, plus 0.5 s · M2
- [ ] A-06 Voice prints (CAM++): enroll, match ≥ 0.5, session harvesting; publishes `audio.voice_match` · M2
- [ ] A-07 Text language check (Lingua) · M2
- [ ] A-08 Sound model (EfficientAT mn10_as), classes by name · M2
- [ ] A-09 Rhythm detector (T3 / T4) · M2
- [ ] A-10 Alert rules, direction, acknowledge, re-alert, clear; publishes `alert` and `hw.pattern` · M2
- [ ] A-11 Ollama client with priority queue and warm-up (qwen3.5:4b, think off, keep_alive -1) · M1
- [ ] A-12 Name learning: phrase filter, JSON answer, stop-list, proposal lifecycle · M2
- [ ] A-13 Translation of non-English finals · M2
- [ ] A-14 Garment descriptions from the fixed lists · M2
- [ ] A-15 Suggested replies for keys 7–9 · M2
- [ ] A-16 Calibration wizard and venue profile (+ docs/calibration.md) · M3
- [ ] A-17 Test tone generator (T3, T4) · M2
- [ ] A-18 Tests in tests/audio_language · M2

## Section 3 – Hardware & Services

- [ ] H-01 Firmware: READY with the I²C driver probe, LV every 50 ms, HB · M0
- [ ] H-02 Firmware: touch gestures (tap, hold, double) · M0
- [ ] H-03 Firmware: patterns T3, T4, BELL, NAME, OK, NO, LOST with soft start and duty limits · M2
- [ ] H-04 Firmware: 2 s safety stop, LED matrix icons, CFG · M2
- [ ] H-05 Serial link: find by USB ID, no DTR reset, READY wait, HB, auto-reconnect; publishes `sensors.*` and `hw.link` · M0
- [ ] H-06 Touch router (alert > name > nothing; double tap = pause); publishes `touch.action` · M2
- [ ] H-07 ElevenLabs streaming voice (key from .env) · M2
- [ ] H-08 Kokoro offline voice, used if no audio within 1.5 s · M2
- [ ] H-09 SpeechOutService + player; publishes `speech_out.playing` and `reply.spoken` · M2
- [ ] H-10 SQLite schema (data/history.db): tables, full-text search (FTS5), talk-time query, auto-delete after 24 h · M1
- [ ] H-11 History writer on its own thread (captions keep running if it fails), and forget session · M1
- [ ] H-12 History queries and API router (`/api/history/...`) · M2
- [ ] H-13 Rig assembly and bench tests T-H1 to T-H8, logged in docs/hardware/wiring.md · M0 – M2
- [ ] H-14 Tests in tests/hardware_services (protocol parsing, touch router, history with a test DB) · M2

## Section 4 – Pages, Engine & Demo

- [ ] P-01 `core/contracts.py` from docs/contracts.md (do this first, it unblocks everyone) · H1
- [ ] P-02 Bus, clock, ring buffers, config loader, `main.py` and `python -m attune` · M0
- [ ] P-03 FastAPI app, static pages, WebSocket hub with sequence numbers and frame dropping; `web/shared/ws.js` · M0
- [ ] P-04 Command handling (`server/commands.py`) · M1
- [ ] P-05 Status aggregation and the status strip · M0
- [ ] P-06 Lens view: video canvas, face tags, dots under 40 px, HUD theme · M0 (video) / M1 (tags)
- [ ] P-07 Speech bubbles: tails, dashed tails, off-screen docking, "You" bar, drafts, fade, push apart, ES tag · M1
- [ ] P-08 Alert banners, name proposals ("Sam? tap to confirm"), paused state · M2
- [ ] P-09 Console panel (C): enroll with thumbnails and consent, people list, switches, pattern tests, calibration UI, event log + Mark · M1
- [ ] P-10 Speak panel (S): text box, presets 1–5, suggestions 7–9 · M2
- [ ] P-11 History panel (Y): sessions, timeline, search, person filter, sounds you missed, talk-time chart · M2
- [ ] P-12 Keyboard shortcuts (C S Y H E F P, 1–9) · M1
- [ ] P-13 Session log, reel recorder and replay mode · M1
- [ ] P-14 Setup: docs/setup.md, check_setup.py, download_models.py, start script with auto-restart · M3
- [ ] P-15 OBS setup, demo script, backup video, Devpost write-up and slides · M3 – M4
- [ ] P-16 Tests in tests/pages_engine (bus, WebSocket hub, commands) · M2

## Gates and end-to-end checks (whole team)

- [ ] M0 · Camera, mic and Arduino all feed the engine; the lens view shows video
- [ ] M1 · Enrolled faces are named, and captions land in the right bubble; first rehearsal reel recorded
- [ ] M2 · Every feature works on its own (alerts, names by touch, translation, descriptions, spoken replies, history)
- [ ] T-E1 Caption delay: median ≤ 1.0 s, 90th percentile ≤ 1.5 s
- [ ] T-E2 Right speaker ≥ 90%, 0 wrong names
- [ ] T-E3 Live alerts meet the section 2 targets
- [ ] T-E4 False alarms: 0 alerts, ≤ 1 false name proposal in 10 min
- [ ] M3 · Feature freeze: full demo end to end; 30-minute soak (T-E5) running; backup video recorded
- [ ] T-E5 Soak: no crash, memory growth < 200 MB, ≥ 24 fps
- [ ] Failure drills (plan section 07) all pass
- [ ] T-E6 / M4 · Three clean rehearsals under 3:00
- [ ] Submitted on Devpost
