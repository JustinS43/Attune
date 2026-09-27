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
- [x] V-19 Recover when a camera opens without frames: report usable video and retry a smaller capture mode — C922 hardware check pending (#56)
- [x] V-20 Prefer any connected USB webcam on macOS instead of matching a camera model name (#56)
- [x] V-21 Live talker detection: per-face noise floor, no captions to a silent face (#57)
- [x] V-22 Light-ASD active speaker model decides who is talking (#58)
- [x] V-23 Enrollment station: save a face at the laptop camera (OV02E10) with a live phone preview, the glasses' quality gate and 8-most-varied rule, an identity check against the glasses face, and frame sharing when the main camera holds the laptop camera (#60)
- [x] V-24 Attribution without waiting: the first words of an utterance are held back (up to 300 ms) only while a mouth on screen is moving or a known voice may still be matched, otherwise shown at once (#62)
- [ ] V-25 Live speaker gate rehearsal: use the built-in camera when no external camera is present, compare Light-ASD on/off on a labeled local clip, and check speech bubble placement in the lens demo — camera access and local model weights pending; runbook in tests/vision/README.md
- [x] V-26 Use the latest voice verdict so a stale match cannot keep an off-screen name or direction; keep weak identity scores inconclusive; honor a named built-in camera on macOS for the enrollment station (#68)
- [ ] V-27 Automatic contact memory: persist engaged faces, deduplicate and rank up to 150, replace weak automatic profiles — code and unit tests done; live camera check left
- [x] V-28 Light-ASD scores at 8+ fps with gaps up to 0.35 s (a busy laptop), and speaker continuity: a face that earned the speech keeps it through a dip in lip evidence while speech runs on without a pause (#87)
- [x] V-29 A voice match that still hears the last turn never vetoes the face Light-ASD hears talking; a voice whose face is in view is never shown off screen (#87)
- [x] V-30 A speaker change moves back to the pause before the reply, so a reply's first words no longer end the previous bubble (#91)

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
- [x] A-20 Make Windows capture simulation tests portable across macOS and Linux (#55)
- [ ] A-21 Station voice print from the laptop mic, a cross-mic threshold, and a bounded glasses-mic refinement of saved voice prints (#60) — built, thresholds from a simulated two-mic study; a live two-mic recording in a quiet room is left
- [x] A-22 Caption latency and completeness: words on screen ~0.4 s sooner, no lost long monologues or short replies, finals at gaps, pauses, replies and language switches; `scripts/bench_captions.py` measures it (#62)
- [x] A-23 Translation and local-model speed: no Ollama timeouts, translations never wait behind replies or descriptions (#62)
- [ ] A-24 Quiet speech and noisy rooms: gain before the VAD, a recogniser gain that rises after a loud start, a babble-noise bench (synthetic hall babble at 15/10/5/0 dB SNR) and a measured choice on a Whisper second pass for finals — in progress
- [ ] A-25 Automatic voice association and contextual names: persist harvested voice prints, offer repeated name evidence — code and unit tests done; live two-mic check left
- [x] A-26 Pace local Whisper drafts separately from frame-rate Nemotron to preserve live captions on CPU · M2 (#77)
- [x] A-27 A voice print from another voice model (another size, or another model's tag) is ignored at load with one warning, never compared (#80)
- [x] A-28 A sounding alarm stays on while the glasses tap: tapped windows can't show that it stopped, so they no longer run down the 15 s quiet-clear (it cycled 14 s on, 10 s off); it ends with "Got it" or 15 s of heard quiet (#89)
- [x] A-29 Caption words in a script the configured languages don't use are dropped (Arabic words inside English podcast captions) (#87)
- [x] A-30 Door knock alert: a knock on the door (EfficientAT "Knock" at `knock_score` or more, blocked by music but not by speech) raises a `knock` alert with the BELL pattern, on the side the sensors hear it; threshold set on synthetic knocks, real knocks on the rig still to check (#92)

## Section 3 – Hardware & Services

- [x] H-01 Firmware: READY with the I²C driver probe, LV every 50 ms, HB · M0 — written in #19; bench-checked on the UNO R3 (#88)
- [ ] H-02 Firmware: touch gestures (tap, hold, double) · M0 — written in #19; compile + bench check on the rig left
- [ ] H-03 Firmware: patterns T3, T4, BELL, NAME, OK, NO, LOST with soft start and duty limits · M2 — written in #19; taps and timing bench-checked on the R3 (#88); LEDs not yet watched
- [ ] H-04 Firmware: 2 s safety stop, LED matrix icons, CFG · M2 — written in #19; safety stop and CFG bench-checked on the R3 (#88); LED 13 codes not yet watched
- [x] H-05 Serial link: find by USB ID, no DTR reset, READY wait, HB, auto-reconnect; publishes `sensors.*` and `hw.link` · M0 (#19)
- [x] H-06 Touch router (alert > name > nothing; double tap = pause); publishes `touch.action` · M2 (#19) — P-29: double tap = save this person, triple tap = pause
- [x] H-07 ElevenLabs streaming voice (key from .env) · M2 (#19)
- [x] H-08 Kokoro offline voice, used if no audio within 1.5 s · M2 (#19)
- [x] H-09 SpeechOutService + player; publishes `speech_out.playing` and `reply.spoken` · M2 (#19)
- [x] H-10 SQLite schema (data/history.db): tables, full-text search (FTS5), talk-time query, auto-delete after 24 h · M1 (#19)
- [x] H-11 History writer on its own thread (captions keep running if it fails), and forget session · M1 (#19)
- [x] H-12 History queries and API router (`/api/history/...`) · M2 (#19)
- [ ] H-13 Rig assembly and bench tests T-H1 to T-H8, logged in docs/hardware/wiring.md · M0 – M2 — rig assembled; T-H1 passed, T-H3–T-H7 partly logged (#88); T-H2, T-H8 and the hands-on parts left
- [x] H-14 Tests in tests/hardware_services (protocol parsing, touch router, history with a test DB) · M2 (#19)
- [x] H-15 CAD rig page matches the real kit: UNO R3, servo tapper, no motor driver (#43)
- [x] H-16 Firmware runs on UNO R3 with the servo tapper; status on LED 13 (#45)
- [x] H-17 Test patterns stop after one cycle; board restarts (brown-out) noticed and logged; heartbeat kept within 0.1–1 s; simulator reboot/heartbeat/sound controls; speech_out device "none" (#61)
- [x] H-18 Firmware 1.1.1: no false link loss (the watchdog's time math wrapped on ~1 % of heartbeats and stopped a playing alarm about once a minute) and no skipped one-shot patterns; the engine plays a real alert's T3/T4 again when the board says READY (#88)
- [x] H-19 Sensor-only live demo mode: keep Arduino sound levels and heartbeats flowing without playing patterns or rearming an alarm (#95)
- [ ] H-20 Improve dual-mic sampling for direction: discard ADC channel-switch readings and isolated spikes — code ready; compile, flash and compare left/right live levels pending

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
- [x] P-35 Phone station screens: consent, laptop camera preview with an oval and hints, progress, read a sentence with a level meter, done or retry (#60)
- [x] P-36 Captions on the lens and phone survive a reconnect (the hub replays recent captions on hello) and the phone drops retracted segments (#62)
- [x] P-37 End-to-end suite (tests/e2e): real engines in replay mode, every page in headless Edge; captions, alerts, camera/pause, reconnect, privacy, robustness, soak (#61)
- [x] P-38 Only the laptop's own pages reach the engine: WebSocket origin check, Host guard (DNS rebinding), no API schema (#61)
- [x] P-39 Phone and demo fixes from the e2e run: tab bar indicator, camera switch and state, no "UND" tag, hidden demo panes out of the tab order, Apricot eyebrow contrast (#61)
- [x] P-40 Clear start-up errors: a missing --source/--audio-file stops at once (never a webcam fallback), one-line config errors with exit code 2 (#61)

- [x] P-41 ElevenLabs key and voice ID in Settings, with local .env persistence and masked key status (#67)
- [x] P-42 Pinned face, mouth, voice, and Whisper model downloads for the live speaker demo · M3 (#74)
- [ ] P-43 People pages and contact continuation: show close/familiar/other, save photo contacts, continue into face and voice enrollment — code done; live browser check left
- [x] P-44 Daylight lens: bright paper tags and a soft grey bubble for people not named yet (no dashed brackets), solid focus marks only while identifying, gliding alert stack, door-knock chip, light demo chrome; the hub replays alerts still sounding to a page that reconnects
- [x] P-45 Podcast evaluation (scripts/eval_podcast.py): real multi-person YouTube clips with human captions, seat-anchored truth, word/bubble/speaker/right-face scores, recorded fusion inputs replayed offline; scripts/replay_podcast.py shows a recorded run on the real pages (#91)
- [x] P-46 Live demo sound meter: show both Arduino amplitudes and a baseline-adjusted left/right direction estimate at the top-right of the glasses pane (#96)

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
