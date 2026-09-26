# Attune to-do list

How to use this list (full rules in [AGENTS.md](AGENTS.md#the-to-do-list)):
- Work in your own section, top to bottom, and check open PRs first so two people don't build the same thing.
- When your PR finishes an item, tick it in that same PR and add the PR number: `- [x] V-03 ... (#12)`.
- Only edit lines in your own section. Add new items at the end of your section with the next free ID.
- **Gate** tells you when it's needed: M0 = hour 3, M1 = hour 8, M2 = hour 14, M3 = hour 18 (feature freeze), M4 = hour 22.
- Files for each item: [docs/feature-map.md](docs/feature-map.md). Events and messages: [docs/contracts.md](docs/contracts.md).

## Kickoff (whole team, hour 0–1)

- [ ] K-01 Put each person's name next to their section in AGENTS.md and docs/feature-map.md
- [ ] K-02 Pick up the hardware with the plan's checklist (section 04), and note the driver board model and webcam model here — webcam: Logitech C922 Pro Stream (1080p30, built-in mic, set as camera and mic in config); driver board: not picked up yet
- [ ] K-03 Read docs/contracts.md together and agree on it before anyone codes against it
- [ ] K-04 Everyone: clone, copy `.env.example` to `.env` and `config/attune.example.toml` to `config/attune.toml`, run `uv sync --project engine`

## Section 1 – Vision

- [x] V-01 Camera reader: open the webcam by name, MJPG 1080p30, one-frame buffer, clock stamps, "camera lost" recovery · M0 (#5)
- [x] V-02 VisionService publishes `vision.frame` · M0 (#5)
- [x] V-03 Face finder (SCRFD-10G), detection size from config, ignore faces under 36 px · M1 (#5)
- [x] V-04 Tracker: overlap + motion, 1 s survive, 10 s lost list, re-identification, exit side · M1 (#5)
- [x] V-05 Face prints (ArcFace w600k_r50) with the crop quality gate · M1 (#5)
- [x] V-06 Gallery and match rules (0.45 / 0.08 / 3 times in 1 s, 2 s recheck, no name jumps) · M1 (#5)
- [x] V-07 Enrollment (face part) with consent: 8 varied crops, refuse under 5 with a reason, rename, delete · M1 (#5)
- [x] V-08 Lip-motion score (MediaPipe on crops, 1 s rolling std; 0.03 / 0.015) · M1 (#5)
- [x] V-09 Who's talking, cases 1–4, hold and switch rules; publishes `caption` and `scene` · M1 (visible speaker) / M2 (all cases) (#5)
- [x] V-10 In-time check (lips vs. sound rhythm) · M2 (#5)
- [x] V-11 Voice-print harvesting (`voice.harvest`) · M2 (#5)
- [x] V-12 Instant colour label for strangers · M2 (#5)
- [x] V-13 Tests in tests/vision using the replay reel · M2 (#5)
- [ ] V-14 Stretch: Light-ASD for hard who's-talking cases · after M3, only if time
- [x] V-15 Live dev runner: `devview --all` runs vision, fusion, audio, alerts and LLM together on one bus and clock, with `--audio-file` replay (#15)
- [x] V-16 Keep a sentence in one caption when the speaker decision flickers (short and "Someone" pieces join their neighbour) (#22, #23)
- [x] V-17 Camera on/off from a page: `camera.set` stops or restarts the webcam (#30)
- [x] V-18 Stable caption attribution: one speaker per segment across drafts, retract dropped segments, no re-labelling when a face leaves (#40)

## Section 2 – Audio & Language

- [x] A-01 Mic reader: WASAPI 48 kHz, 10 ms blocks, 16 kHz + 32 kHz streams, ring buffer, laptop-mic fallback · M0 — real Windows capture verified on a Realtek SoundWire mic array after the WASAPI COM fix (#11)
- [x] A-02 Silero VAD with the plan's thresholds; publishes `audio.vad` · M1 — real Silero VAD verified; 320 ms pre-roll keeps first words (#14)
- [x] A-03 Captions: Nemotron via sherpa-onnx, drafts/finals, language, word times; publishes `audio.transcript` · M1 — real Nemotron 3.5 int8 verified: word-perfect English test clip with word times, ~4.6x real time on CPU; Spanish and French transcribed in en+es mode (#10, #14)
- [x] A-04 Whisper fallback (faster-whisper large-v3-turbo) · M2 — real large-v3-turbo verified on English and Spanish; CPU int8 is ~1.5x real time, fine as the fallback (#10)
- [x] A-05 Mute mic captions while `speech_out.playing`, plus 0.5 s · M2 — implemented and tested; PR number pending GitHub access
- [x] A-06 Voice prints (CAM++): enroll, match ≥ 0.5, session harvesting; publishes `audio.voice_match` · M2 — real CAM++ verified: same voice 0.66, different voice 0.19 against the 0.5 threshold (#16)
- [x] A-07 Text language check (Lingua) · M2 — real Lingua verified on English and Spanish captions, under 20 ms (#16)
- [ ] A-08 Sound model (EfficientAT mn10_as), classes by name · M2 — exported and verified on T3/T4 tones, speech and noise with 10 s context (#13); doorbell class still needs a real doorbell recording
- [x] A-09 Rhythm detector (T3 / T4) · M2 — both patterns and frequency bands implemented and tested; PR number pending GitHub access
- [ ] A-10 Alert rules, direction, acknowledge, re-alert, clear; publishes `alert` and `hw.pattern` · M2 — smoke and CO alerts fire from the real model and rhythm through AlertService (#13); direction needs Section 3 sensor levels on the rig
- [x] A-11 Ollama client with priority queue and warm-up (qwen3.5:4b, think off, keep_alive -1) · M1 — real Ollama qwen3.5:4b verified from a cold start after the warm-up timeout fix (#12)
- [x] A-12 Name learning: phrase filter, JSON answer, stop-list, proposal lifecycle · M2 — real model proposed "Sam" from "Hi, my name is Sam" (#12)
- [x] A-13 Translation of non-English finals · M2 — real model translated "¿Dónde está la estación de tren?" to "Where is the train station?" (#12)
- [x] A-14 Garment descriptions from the fixed lists · M2 — real model described a crop as "Person in blue shirt"; 0.4-0.9 s per crop (#12)
- [x] A-15 Suggested replies for keys 7–9 · M2 — real model returned three reply suggestions (#12)
- [ ] A-16 Calibration wizard and venue profile (+ docs/calibration.md) · M3 — wizard, profiles and guide implemented; CalibrationService runs in `python -m attune` and the console has the step buttons (#20, #18); manual clap annotations and a venue run pending
- [x] A-17 Test tone generator (T3, T4) · M2 — generator implemented and both frequency bands tested; PR number pending GitHub access
- [x] A-18 Tests in tests/audio_language · M2 — 54 tests pass, including real soxr resampling, simulated capture recovery and ring retention/restart regressions; live-model acceptance remains in relevant items; (#6)
- [x] A-19 Hot-plug recovery: re-initialise PortAudio after a mic loss, fall back, return to `[audio] device_name` between utterances; camera returns to `[vision] camera_name` after replug (Vision file) · M2 — unit-tested with fake devices; a real unplug/replug is still to check on the rig (#52)

## Section 3 – Hardware & Services

- [ ] H-01 Firmware: READY with the I²C driver probe, LV every 50 ms, HB · M0 — written in #19; compile + bench check on the rig left
- [ ] H-02 Firmware: touch gestures (tap, hold, double) · M0 — written in #19; compile + bench check on the rig left
- [ ] H-03 Firmware: patterns T3, T4, BELL, NAME, OK, NO, LOST with soft start and duty limits · M2 — written in #19; compile + bench check on the rig left
- [ ] H-04 Firmware: 2 s safety stop, LED matrix icons, CFG · M2 — written in #19; compile + bench check on the rig left
- [x] H-05 Serial link: find by USB ID, no DTR reset, READY wait, HB, auto-reconnect; publishes `sensors.*` and `hw.link` · M0 (#19)
- [x] H-06 Touch router (alert > name > nothing; double tap = pause); publishes `touch.action` · M2 (#19) — P-29: double tap = save this person, triple tap = pause
- [x] H-07 ElevenLabs streaming voice (key from .env) · M2 (#19)
- [x] H-08 Kokoro offline voice, used if no audio within 1.5 s · M2 (#19)
- [x] H-09 SpeechOutService + player; publishes `speech_out.playing` and `reply.spoken` · M2 (#19)
- [x] H-10 SQLite schema (data/history.db): tables, full-text search (FTS5), talk-time query, auto-delete after 24 h · M1 (#19)
- [x] H-11 History writer on its own thread (captions keep running if it fails), and forget session · M1 (#19)
- [x] H-12 History queries and API router (`/api/history/...`) · M2 (#19)
- [ ] H-13 Rig assembly and bench tests T-H1 to T-H8, logged in docs/hardware/wiring.md · M0 – M2 — wiring + T-H1–T-H8 checklist ready in #19; assembly and bench tests left
- [x] H-14 Tests in tests/hardware_services (protocol parsing, touch router, history with a test DB) · M2 (#19)
- [x] H-15 CAD rig page matches the real kit: UNO R3, servo tapper, no motor driver (#43)
- [x] H-16 Firmware runs on UNO R3 with the servo tapper; status on LED 13 (#45)

## Section 4 – Pages, Engine & Demo

- [x] P-01 `core/contracts.py` from docs/contracts.md (do this first, it unblocks everyone) · H1 (#20)
- [x] P-02 Bus, clock, ring buffers, config loader, `main.py` and `python -m attune` · M0 (#20)
- [x] P-03 FastAPI app, static pages, WebSocket hub with sequence numbers and frame dropping; `web/shared/ws.js` · M0 (#20)
- [x] P-04 Command handling (`server/commands.py`) · M1 (#20)
- [x] P-05 Status aggregation and the status strip · M0 (#20)
- [x] P-06 Lens view: video canvas, face tags, dots under 40 px, HUD theme · M0 (video) / M1 (tags) (#21)
- [x] P-07 Speech bubbles: tails, dashed tails, off-screen docking, "You" bar, drafts, fade, push apart, ES tag · M1 (#21)
- [x] P-08 Alert banners, name proposals ("Sam? tap to confirm"), paused state · M2 (#21)
- [x] P-09 Console panel (C): enroll with thumbnails and consent, people list, switches, pattern tests, calibration UI, event log + Mark · M1 (#18)
- [x] P-10 Speak panel (S): text box, presets 1–5, suggestions 7–9 · M2 (#18)
- [x] P-11 History panel (Y): sessions, timeline, search, person filter, sounds you missed, talk-time chart · M2 (#18)
- [x] P-12 Keyboard shortcuts (C S Y H E F P, 1–9) · M1 (#21)
- [ ] P-13 Session log, reel recorder and replay mode · M1 — session log and WAV player done (#20); reel recorder and replay mode left
- [ ] P-14 Setup: docs/setup.md, check_setup.py, download_models.py, start script with auto-restart · M3 — docs/setup.md written (#20, #26); check_setup.py, download_models.py and the start script left
- [ ] P-15 OBS setup, demo script, backup video, Devpost write-up and slides · M3 – M4 — launch film cut (two versions) made; re-render in the three glasses looks in progress
- [x] P-16 Tests in tests/pages_engine (bus, WebSocket hub, commands) · M2 (#20)
- [x] P-17 Demo page (/demo/): glasses view and phone app side by side, or either one full size · M3 (#26)
- [x] P-18 Demo: glasses guide tab (Colour / Mono / Corner and the glasses that use each) · M3 (#29)
- [x] P-19 Demo: camera on/off switch (engine really stops the webcam; captions keep running) · M3 (#30)
- [x] P-20 Demo: Glasses POV view (what the wearer sees, true size, per device) · M3 (#34)
- [x] P-21 Three glasses looks match real devices: Colour = Meta Orion class, Mono = Even Realities G1 band with 9 heights, Corner = Meta Ray-Ban Display right-eye square (+ docs/glasses-realism.md) · M3 (#28)
- [x] P-22 Film source in the lens: two-decoder player with a watchdog (no more black footage), chrome that scales with the window · M2 (#25, #28)
- [x] P-23 Phone app: Ryan's logo designs, colorways and the Apricot Studio palette (`?palette=apricot`), kept working with the live engine · M3 (#24)
- [x] P-24 Engine works with a USB webcam (Logitech C922): MSMF hardware transforms off before OpenCV loads · M0 (#27)
- [x] P-25 Caption layout polish: one steady slot per person, no jumping bubbles, calm Mono and Corner lines · M3 (#36)
- [ ] P-26 Re-render the launch film in the three glasses looks (Colour, Mono, Corner) from the lens's own renderers · M3 – M4 — pipeline in progress, renders after P-25
- [x] P-27 Phone enrollment tab: consented face photo preview and guided voice enrollment · M3 (#33)
- [x] P-28 Calm Colour look: no flashing or pulsing, simple directional arrows (#39)
- [x] P-29 Save a person with a double tap: consent on the phone, face/voice capture animations in every glasses mode, Quick Start in Film · M3 (#41)
- [x] P-30 Remember Me full-face photo and local contact list with photo uploads · M3 (#42)
- [x] P-31 Lens review fixes: docks avoid faces, clear Mono speakers, names during save, no mixed-language lines (#46)
- [x] P-32 System debug: phone enrollment and console fixes, full-suite findings handed to owners (#51)
- [x] P-33 Clear stale live lens frames on source switch and reduce frame copy work (#53)
- [x] P-34 Remember Me captures and displays the whole face with more room (#54)

## Gates and end-to-end checks (whole team)

- [ ] M0 · Camera, mic and Arduino all feed the engine; the lens view shows video — camera (C922) and mic feed `python -m attune` and the lens shows live video with face tags; Arduino runs on the simulator until the rig is built
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
