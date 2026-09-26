# Attune contracts

These are the agreements between the four sections: the event names and fields on the engine's bus, the WebSocket messages to the pages, the page commands, the history API, the Arduino serial protocol and the fixed word lists. If you build against this file, your work will plug into everyone else's.

`engine/attune/core/contracts.py` is the code copy of this file (Section 4 writes it first, TODO P-01).

**Rules for changing contracts**
- Change this file and `core/contracts.py` together, in a small PR titled `[shared] contracts: ...`, and tell the team.
- Only **add** fields or events. Renaming or removing something needs all four owners to agree in the PR.
- New fields must have a default so older code keeps working.

Times are seconds from `core.clock` (a monotonic clock that starts with the engine). Boxes are `[x, y, w, h]` in pixels of the 1920×1080 camera frame.

---

## 1. Service convention

Every section's `service.py` exposes one class:

```
class <Name>Service:
    def __init__(self, bus, config): ...   # no heavy work here
    def start(self): ...                    # load models, start threads; raise on fatal errors
    def stop(self): ...                     # stop threads, release devices, within 2 s
```

A service reports its health by publishing `status.part` about once per second. `main.py` (Section 4) creates the services in this order: hardware, audio, vision, fusion, alerts, llm, speech_out, history, server. A service that fails to start is reported and skipped; the rest keep running.

## 2. Bus events

`bus.publish(topic, event)` / `bus.subscribe(topic, callback)`. Callbacks must return quickly; do heavy work on your own thread.

| Topic | Published by | Fields | Notes |
|---|---|---|---|
| `vision.frame` | 1 Vision | `frame_no, t, image` (BGR numpy array) | 30/s; subscribers may skip frames |
| `vision.tracks` | 1 Vision | `frame_no, t, tracks: [Track]` | Every processed frame |
| `vision.track_lost` | 1 Vision | `track_id, t, side` (`left`, `right`, `none`) | Drives off-screen arrows |
| `vision.appearance` | 1 Vision | `track_id, color, crop` (numpy) | Once a stranger is steady for 1 s |
| `vision.description` | 2 Audio & Lang | `track_id, label` (e.g. "Person in blue jacket") | From the fixed lists only |
| `audio.block` | 2 Audio | `t, sample_rate, samples` (mono float32 numpy array) | Local RAM only; timestamp is first sample; 16 kHz speech and 32 kHz alerts; never WebSocket/history |
| `audio.vad` | 2 Audio & Lang | `t, is_speech, prob` | Every 32 ms |
| `audio.transcript` | 2 Audio & Lang | `utt_id, t_start, t_end, text, final, lang, words: [(word, t0, t1)]` | No speaker yet |
| `audio.voice_match` | 2 Audio & Lang | `utt_id, person_id or None, score` | After ≥ 1 s of speech |
| `voice.harvest` | 1 Vision (fusion) | `person_id, t0, t1` | Section 2 adds that audio span to the session print |
| `caption` | 1 Vision (fusion) | `utt_id, speaker: Speaker, text, final, lang, words` | The transcript with its speaker |
| `caption.translation` | 2 Audio & Lang | `utt_id, source_lang, text_en` | 0.5–1.2 s after a final |
| `scene` | 1 Vision (fusion) | `frame_no, t, faces: [FaceState], offscreen: [Offscreen], you_speaking` | 15/s and on every change |
| `name.proposal` | 2 Audio & Lang | `proposal_id, track_id, name, state, expires_t` | `state`: proposed, confirmed, rejected, expired |
| `alert` | 2 Audio & Lang | `alert_id, kind, side, confidence, state` | `kind`: smoke, co, doorbell; `state`: start, update, watch, acknowledged, clear |
| `reply.suggestions` | 2 Audio & Lang | `options: [str, str, str]` | For keys 7–9 |
| `sensors.levels` | 3 Hardware | `t, left, right, motor_on` | Every 50 ms, 0–1023 |
| `sensors.touch` | 3 Hardware | `t, gesture` (`tap`, `hold`, `double`) | Raw gesture |
| `touch.action` | 3 Hardware | `target` (`alert`, `name`, `pause`), `id, accept` | After the priority rules |
| `hw.pattern` | anyone | `name` (T3, T4, BELL, NAME, OK, NO), `side` (L, R, B) | Section 3 sends it to the Arduino |
| `hw.stop` | anyone | — | Stop all patterns |
| `hw.link` | 3 Hardware | `connected, firmware, driver` | On change |
| `speech_out.playing` | 3 Hardware | `state` (`start`, `end`), `t` | Section 2 mutes mic captions until end + 0.5 s |
| `reply.spoken` | 3 Hardware | `text, voice` (`elevenlabs`, `kokoro`), `t` | Shown as "You (typed)" and saved to history |
| `enroll.result` | 1 Vision / 2 Audio & Lang | `person_id, part` (`face`, `voice`), `ok, reason, track_id=None` | "more light", "come closer"; face enrollment echoes the requested track_id to correlate voice consent |
| `person.changed` | 1 Vision | `person_id, name, action` (`enrolled`, `renamed`, `deleted`) | Everyone updates their caches |
| `session.forget` | 4 Pages & Engine | — | Every section wipes session-only data |
| `paused` | 4 Pages & Engine | `paused` (bool) | All recognition pauses |
| `command` | 4 Pages & Engine | `Command` (section 4) | From the pages |
| `status.part` | every service | `part, ok, detail, metrics: {}` | ~1/s |

**Shared types**

- `Track`: `track_id, box, face_px, lip_score, person_id or None, name or None, match_score, status` (`unknown`, `proposed`, `named`, `enrolled`)
- `Speaker`: `kind` (`you`, `you_typed`, `face`, `probable_face`, `offscreen`, `someone`), `track_id or None, person_id or None, label, side`
- `FaceState`: `track_id, box, label, status, lip_score, is_speaker, dashed`
- `Offscreen`: `person_id or None, label, side`

## 3. WebSocket messages (engine → pages)

Endpoint `ws://localhost:8000/ws`. Every message is JSON `{"type": ..., "seq": n, ...}`, except frames, which are binary: an 8-byte frame number + 8-byte capture time, then a 1280×720 JPEG. The server may drop frames for a slow page but never drops other messages.

| type | Sent to | Fields |
|---|---|---|
| `frame` (binary) | lens | frame_no, t, jpeg |
| `scene` | lens, console | as the bus event, boxes scaled to 1280×720 |
| `caption` | all | utt_id, speaker, text, final, lang, translation (optional), words |
| `name_proposal` | all | proposal_id, track_id, name, state, expires_t |
| `alert` | all | alert_id, kind, side, confidence, state |
| `reply_suggestions` | all | options |
| `reply_spoken` | all | text, voice |
| `status` | console | fps, caption_delay, gpu_mem_gb, arduino, ollama, mic_level, on_battery, parts |
| `people` | console | list of `{person_id, name, consent_t, has_face, has_voice}` |
| `thumbnails` | console | list of `{track_id, jpeg_b64}` for enrollment picking |
| `event_log` | console | t, text |

## 4. Commands (pages → engine)

Pages send `{"type": "command", "name": ..., "args": {...}}` over the same WebSocket.

| name | args | Handled by |
|---|---|---|
| `enroll.start` | track_id, name, consent (true), consent_t | 1 Vision (face) then 2 Audio & Lang (voice) |
| `person.rename` | person_id, name | 1 Vision |
| `person.delete` | person_id | 1 Vision + 2 Audio & Lang (delete every file) |
| `session.forget` | — | 4 Pages & Engine publishes `session.forget` |
| `pause.toggle` | — | 4 Pages & Engine publishes `paused` |
| `switch.set` | key (`translation`, `alerts`, `debug`), value | owning section |
| `languages.set` | langs, e.g. ["en", "es"] | 2 Audio & Lang |
| `pattern.test` | name, side | 3 Hardware |
| `calibrate.step` | step, measurements={} (optional manual observations) | 2 Audio & Lang |
| `speak` | text, source (`typed`, `preset`, `suggestion`) | 3 Hardware |
| `name.answer` | proposal_id, accept | 2 Audio & Lang (keyboard fallback for tap/hold) |
| `alert.ack` | alert_id | 2 Audio & Lang |
| `mark` | note | 4 Pages & Engine (session log) |

## 5. History API (Section 3 router, mounted by Section 4)

| Route | Returns |
|---|---|
| `GET /api/history/sessions` | `[{session_id, started_t, ended_t, lines}]` |
| `GET /api/history/sessions/{id}` | timeline rows: `{t, kind, speaker_label, text, translation, lang}` |
| `GET /api/history/search?q=&person=` | matching rows |
| `GET /api/history/missed?session=` | alerts and sounds in that session |
| `GET /api/history/talktime?session=` | `{speaker_label: [{minute, seconds}]}`, computed by a query |

Row kinds: `caption`, `translation`, `reply`, `alert`, `name_confirmed`. Rows older than 24 hours are deleted. Never stored: audio, video, face prints, voice prints.

## 6. Arduino serial protocol

Plain text lines, 115200 baud, `\n` endings. Found by USB ID; opened without toggling DTR.

| Direction | Line | Meaning |
|---|---|---|
| Arduino → laptop | `READY <version> <driver>` | driver = `TB6612`, `L298`, or `NONE` |
| Arduino → laptop | `LV <ms> <left> <right> <motor 0/1>` | every 50 ms |
| Arduino → laptop | `TOUCH TAP` / `TOUCH HOLD` / `TOUCH DOUBLE` | gestures |
| Arduino → laptop | `HB <ms>` | every 1 s |
| Arduino → laptop | `ACK <n>` / `ERR <text>` | command n done / fault |
| laptop → Arduino | `PAT <n> <L/R/B> <name>` | play pattern (T3, T4, BELL, NAME, OK, NO) |
| laptop → Arduino | `STOP <n>` | stop all patterns |
| laptop → Arduino | `HB` | every 0.5 s; if missing for 2 s the Arduino stops everything and shows LOST |
| laptop → Arduino | `MX <icon>` | matrix icon: `HEART`, `ALERT_L`, `ALERT_R`, `PAUSE`, `LOST` |
| laptop → Arduino | `CFG <key> <value>` | e.g. `rate 50`, `tap_ms 400`, `hold_ms 800`, `led 180` |

## 7. Fixed word lists (descriptions)

Descriptions may only use these words. Nothing about gender, age, body, skin or ethnicity.

- **Colours:** black, white, grey, navy, blue, green, red, orange, yellow, purple, pink, brown, beige
- **Garments:** jacket, coat, hoodie, sweater, shirt, T-shirt, top, dress, vest
- **Accessories:** cap, hat, glasses, scarf, headphones, lanyard, backpack

## 8. Ports and paths

| What | Where |
|---|---|
| Engine web server | `http://localhost:8000` (lens `/lens/`, WebSocket `/ws`) |
| Ollama | `http://localhost:11434` |
| Conversation history | `data/history.db` (SQLite, deleted after 24 h) |
| Models | `models/` (gitignored, see `models/README.md`) |
| Runtime data | `data/` (gitignored): `people/`, `profiles/`, `sessions/`, `reels/` |
| Secrets | `.env` (gitignored), copied from `.env.example` |

## Section 2 integration additions

`audio.block` is local PCM, never serialized to WebSockets, history or the generic event log.
Replay may feed these blocks directly on the shared clock. Audio capture publishes both
16 kHz and 32 kHz mono streams. Pause/forget consumers discard queued recognition work.

`enroll.result.track_id` defaults to `None` for existing producers. Voice enrollment
requires a successful face result echoing the consenting `enroll.start.track_id`; it
refuses ambiguous results without that correlation. Only matching final `caption`
spans attributed to that face contribute to the five-second voice enrollment.

Calibration begins with `calibrate.step` using `level`, `mic`, `you`, `other`,
`balance`, `claps`, `noise`, or `faces`. `finish:<step>` saves it. Optional
`measurements` carries `confirmed: true` for level; three shared-clock
`audio_times` and `video_times` for annotated claps; or `distances_checked: [1,2,3]`
for the face check. Status is reported through `status.part`, part `calibration`.
This permits manual video observations until an automatic clap detector exists.

## Pages integration additions

Every page opens `ws://<engine>/ws` with `web/shared/ws.js` and first sends
`{"type": "hello", "role": "lens" | "console" | "phone", "frames": bool}`. Only
pages that asked for frames get the binary `frame` messages. The engine answers
with `welcome`.

**Binary frame layout:** bytes 0–7 little-endian uint64 `frame_no`, bytes 8–15
little-endian float64 capture `t` (engine clock), then a 1280×720 JPEG.

**Boxes** in `scene` are `[x, y, w, h]` in 1280×720 pixels (scaled from the camera
frame), so they line up with the frames the lens draws.

Extra engine → page messages (JSON, with `seq` like the rest):

| type | Sent to | Fields |
|---|---|---|
| `welcome` | the page that said hello | `session_id, paused, config: {bubble_chars, bubble_lines, bubble_fade_s, presets}` |
| `paused` | all | `paused` (bool), sent on every change |
| `enroll_result` | console, phone | as the bus event `enroll.result` |
| `person_changed` | console, phone | as the bus event `person.changed` |
| `hw_link` | console, phone | as the bus event `hw.link` |

`caption.translation` is not sent on its own: the engine re-sends that utterance's
`caption` with its `translation` field filled in. Times (`t`, `t_start`, word times)
are engine-clock seconds; pages only compare them with each other.
