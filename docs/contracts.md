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
| `audio.block` | 2 Audio | `t, sample_rate, samples` (mono float32 numpy array) | Local RAM only; timestamp is first sample; 16 kHz speech and 32 kHz alerts; never WebSocket/history. The one exception: while the wearer has turned on cloud captions, the 16 kHz stream goes to Google Speech-to-Text (see "Cloud captions") |
| `audio.vad` | 2 Audio & Lang | `t, is_speech, prob` | Every 32 ms |
| `audio.transcript` | 2 Audio & Lang | `utt_id, t_start, t_end, text, final, lang, words: [(word, t0, t1)]` | No speaker yet |
| `audio.voice_match` | 2 Audio & Lang | `utt_id, person_id or None, score` | After ≥ 1 s of speech |
| `voice.harvest` | 1 Vision (fusion) | `person_id, t0, t1, talkers=1` | Section 2 adds that audio span to the session print; for a saved person it may refine their print (A-21, see "Enrollment station"). `talkers`: the most faces talking at once over the span. Sent `[voice] harvest_lag_s` after `t1`, so the span's audio has arrived |
| `caption` | 1 Vision (fusion) | `utt_id, speaker: Speaker, text, final, lang, words` | The transcript with its speaker; split into `<utt_id>`, `<utt_id>.1`, ... where the speaker changes |
| `caption.retract` | 1 Vision (fusion) | `utt_id` | A segment id sent earlier is no longer part of its utterance: drop it (see "Caption segments") |
| `caption.translation` | 2 Audio & Lang | `utt_id, source_lang, text_en` | 0.5–1.2 s after a final |
| `scene` | 1 Vision (fusion) | `frame_no, t, faces: [FaceState], offscreen: [Offscreen], you_speaking` | 15/s and on every change |
| `name.proposal` | 2 Audio & Lang | `proposal_id, track_id, name, state, expires_t` | `state`: proposed, confirmed, rejected, expired |
| `alert` | 2 Audio & Lang | `alert_id, kind, side, confidence, state` | `kind`: smoke, co, doorbell, knock, siren, horn, scream, glass, baby, dog, phone, timer, water (A-40); `state`: start, update, watch, acknowledged, clear |
| `reply.suggestions` | 2 Audio & Lang | `options: [str, str, str]` | For keys 7–9 |
| `sensors.levels` | 3 Hardware | `t, left, right, motor_on` | Every 50 ms, 0–1023 |
| `sensors.touch` | 3 Hardware | `t, gesture` (`tap`, `hold`, `double`, `triple`) | Raw gesture |
| `touch.action` | 3 Hardware | `target` (`alert`, `name`, `save`, `pause`), `id, accept` | After the priority rules; `save` (double tap) carries the pending `proposal_id` or None |
| `hw.pattern` | anyone | `name` (T3, T4, BELL, NAME, OK, NO), `side` (L, R, B) | Section 3 sends it to the Arduino |
| `hw.stop` | anyone | — | Stop all patterns |
| `hw.link` | 3 Hardware | `connected, firmware, driver` | On change |
| `speech_out.playing` | 3 Hardware | `state` (`start`, `end`), `t` | Section 2 mutes mic captions until end + 0.5 s |
| `reply.spoken` | 3 Hardware | `text, voice` (`elevenlabs`, `kokoro`), `t` | Shown as "You (typed)" and saved to history |
| `enroll.result` | 1 Vision / 2 Audio & Lang | `person_id, part` (`face`, `voice`), `ok, reason, track_id=None, source="glasses", session_id=None` | "more light", "come closer"; face enrollment echoes the requested track_id to correlate voice consent. `source: "station"`: saved at the laptop (see "Enrollment station") |
| `enroll.progress` | 1 Vision / 2 Audio & Lang | `track_id, part` (`face`, `voice`), `fraction` (0–1), `person_id=None, hint="", source="glasses", session_id=None` | While an enrollment runs; see "Save a person" |
| `enroll.state` | 1 Vision (station) | `session_id, client_id, phase, name, track_id, request_id, person_id, face_ok, voice_ok, sentence, need_s, reason` (+ `camera, shared, mic, score, message` in some phases) | Enrollment station: which screen the phone shows; see "Enrollment station" |
| `enroll.preview` | 1 Vision (station) | `session_id, client_id, jpeg` (bytes), `width, height, face` ([x, y, w, h] fractions or None), `ok, hint` | ~12/s during the face step; memory only, never stored |
| `enroll.level` | 2 Audio (station) | `session_id, client_id, level` (0–1), `db, peak, clipping, speech, noise_db, voiced_s, need_s, hint` | ~15/s during the voice step |
| `enroll.mismatch` | 1 Vision (station) | `session_id, client_id, request_id, track_id, name, score, threshold` | The laptop face isn't the glasses face the save started from |
| `save.request` | 4 Pages & Engine | `request_id, track_id, name, t, expires_t, person_id=None, proposal_id=None` | A double tap asked to save this person; pages ask them for consent |
| `save.cancel` | 4 Pages & Engine | `request_id` (or None), `reason, track_id=None, name=""` | The request ended without an enrollment, or nobody could be saved |
| `person.changed` | 1 Vision | `person_id, name, action` (`enrolled`, `renamed`, `deleted`) | Everyone updates their caches |
| `session.forget` | 4 Pages & Engine | — | Every section wipes session-only data |
| `paused` | 4 Pages & Engine | `paused` (bool) | All recognition pauses |
| `camera.state` | 4 Pages & Engine | `on` (bool) | 1 Vision stops or restarts the camera; captions and alerts keep running |
| `speaker.cloud` | 2 Audio & Lang (cloud captions) | `stream_id, words: [(word, t0, t1, tag)], final, t_end, latency_s, lang, confidence` | Only while cloud captions are on. A `tag` compares only within its `stream_id`; see "Cloud captions" |
| `cloud.state` | 2 Audio & Lang (cloud captions) | `enabled, state, reason, latency_ms, provider, model, language, languages, credentials` | On every change; see "Cloud captions" |
| `command` | 4 Pages & Engine | `Command` (section 4) | From the pages |
| `status.part` | every service | `part, ok, detail, metrics: {}` | ~1/s |

**Shared types**

- `Track`: `track_id, box, face_px, lip_score, person_id or None, name or None, match_score, status` (`unknown`, `proposed`, `named`, `enrolled`), `mouth_open=None, asd_score=None` (see "Active speaker score")
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
| `caption_retract` | all | utt_id (a segment to drop, as the bus event `caption.retract`) |
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
| `enroll.start` | track_id, name, consent (true), consent_t (epoch seconds), request_id (optional, answers a `save.request`) | 1 Vision (face) then 2 Audio & Lang (voice) |
| `enroll.station` | action: `start` {name, consent (true), consent_t (epoch seconds), request_id?, track_id?}; `retry`, `new_person`, `skip_voice`, `cancel` {session_id} | 1 Vision's enrollment station (face, then voice at the laptop); the hub adds the page's `client_id` |
| `save.start` | track_id (optional) | 4 Pages & Engine: "save this person" without the touch pad (key D) |
| `save.cancel` | request_id | 4 Pages & Engine: the person declined on the phone or console |
| `person.rename` | person_id, name | 1 Vision |
| `person.delete` | person_id | 1 Vision + 2 Audio & Lang (delete every file) |
| `session.forget` | — | 4 Pages & Engine publishes `session.forget` |
| `pause.toggle` | — | 4 Pages & Engine publishes `paused` |
| `camera.set` | on (bool) | 4 Pages & Engine publishes `camera.state` |
| `switch.set` | key (`translation`, `alerts`, `debug`), value | owning section |
| `languages.set` | langs, e.g. ["en", "es"] | 2 Audio & Lang |
| `pattern.test` | name, side | 3 Hardware |
| `calibrate.step` | step, measurements={} (optional manual observations) | 2 Audio & Lang |
| `speak` | text, source (`typed`, `preset`, `suggestion`) | 3 Hardware |
| `name.answer` | proposal_id, accept | 2 Audio & Lang (keyboard fallback for tap/hold) |
| `alert.ack` | alert_id | 2 Audio & Lang |
| `alert.snooze` | alert_id | 2 Audio & Lang (stop it and mute that kind for `[alerts] snooze_s`; a hold on the touch sensor does the same, A-41) |
| `mark` | note | 4 Pages & Engine (session log) |
| `cloud.set` | on (bool), language (optional, one of `cloud.state.languages`) | 2 Audio & Lang: cloud captions on or off; the choice is kept on the laptop (see "Cloud captions") |

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
| Arduino → laptop | `TOUCH TAP` / `TOUCH HOLD` / `TOUCH DOUBLE` / `TOUCH TRIPLE` | gestures (each tap within `tap_ms` of the last; TRIPLE added for P-29) |
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

## Section 1 integration additions

**Active speaker score (V-22).** `Track.asd_score` is Light-ASD's speaking logit for
that face: whether its mouth moves in time with the sound, over the last
`[vision] asd_window_s` (1.5 s) and averaged over the newest `asd_score_s` (0.4 s).
Above 0 means talking; it has no fixed range (typically -5 to +5). It is `None`
whenever the face isn't scored: the model is off or missing, the face is under
`[vision] asd_min_face_px`, it has too little history, or the latest score is older
than `[vision] asd_max_age_s`. Consumers treat `None` as "can't tell" and fall back
to `lip_score`. It is computed from local frames and PCM only; nothing is stored.

## Section 2 integration additions

`audio.block` is local PCM, never serialized to WebSockets, history or the generic event log.
It leaves the laptop only as described in "Cloud captions" below.
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
| `welcome` | the page that said hello | `session_id, paused, camera_on, config: {bubble_chars, bubble_lines, bubble_fade_s, presets, enroll?: {source, sentence}}` (`enroll` only when `[enroll]` is configured) |
| `paused` | all | `paused` (bool), sent on every change |
| `camera` | all | `on` (bool), sent on every change |
| `enroll_result` | all (the lens since P-29) | as the bus event `enroll.result` |
| `enroll_progress` | all | as the bus event `enroll.progress` |
| `save_request` | all | as the bus event `save.request`; also sent to a page that says hello while one waits |
| `save_cancel` | all | as the bus event `save.cancel` |
| `person_changed` | console, phone | as the bus event `person.changed` |
| `hw_link` | console, phone | as the bus event `hw.link` |
| `enroll_state` | only the page that started the station save | as the bus event `enroll.state`, without `client_id`; also re-sent to a phone that says hello while that page is gone |
| `enroll_preview` | only that page | as `enroll.preview` with `jpeg_b64` instead of `jpeg`; only the newest waits if the page is slow |
| `enroll_level` | only that page | as the bus event `enroll.level` |
| `enroll_mismatch` | only that page | as the bus event `enroll.mismatch` |
| `cloud` | all | as the bus event `cloud.state`, on every change; the latest is also sent right after `welcome` |

`caption.translation` is not sent on its own: the engine re-sends that utterance's
`caption` with its `translation` field filled in. Times (`t`, `t_start`, word times)
are engine-clock seconds; pages only compare them with each other.

**Caption segments.** Fusion splits one utterance where its speaker changes: the first
segment keeps the utterance's own `utt_id`, later ones are `<utt_id>.1`, `<utt_id>.2`, ...
Every draft re-sends the segments that are still part of the utterance. A segment keeps
its speaker from draft to draft unless the evidence over most of its words changes
(`[fusion] relabel_share`, `claim_share`), and a face leaving the frame never re-labels
words said before. When a later draft or the final no longer has a segment id that was
sent (for example two segments merged), the engine sends `caption.retract` /
`caption_retract` with that id once; pages remove its text. The utterance's own
`utt_id` is never retracted, so translations (keyed by it) always have a caption to join.

**Lens placement.** In the full-colour lens, a caption follows its speaker's visible face.
Speech with no visible face uses one fixed bottom caption; its arrow points left, right or
behind when `speaker.side` is known. Unknown direction has no arrow. If a tracked face
leaves the frame, its continuing caption moves to the bottom after the short face hold.
The compact lens modes already keep captions in fixed areas with direction indicators.

## Save a person (P-29)

A double tap on the side of the glasses saves the person in front of the wearer, with that
person's consent. Gestures: tap = yes (acknowledge an alert, else confirm a name proposal),
hold = no, **double tap = save this person**, **triple tap = pause** (was double). On the lens,
Y / N / P are tap / hold / pause and D (or Y twice quickly) is the double tap.

1. The touch router publishes `touch.action` {target: `save`, id: proposal_id or None}; key D
   sends the command `save.start`. The engine's save flow (`engine/attune/core/save_flow.py`)
   picks who: the face with an active (or just confirmed) name proposal, which it also
   confirms with `name.answer`; else the named speaker who talked last; else the most
   prominent named face in view. Nobody named: `save.cancel` {request_id: None, reason:
   `no_name`} (the glasses say "Say their name first"); only saved people in view: `already_saved`.
2. `save.request` goes to every page. The phone and the console show a consent sheet that the
   person being saved ticks themselves; its Save sends `enroll.start` {track_id, name,
   consent: true, consent_t, request_id}. Nobody else can consent for them, and nothing is
   enrolled without that command.
3. Their Cancel sends `save.cancel` {request_id}; the engine then publishes `save.cancel` with
   reason `declined`. Other reasons: `timeout` (no answer in `save.consent_timeout_s`, 60 s),
   `lost` (their face left the view), `replaced` (a double tap on someone else), `cancelled`
   (pause or forget session).
4. Vision publishes `enroll.progress` {part: `face`} about 5 times a second while it collects
   face crops: `fraction` = min(good crops / `enroll_crops`, elapsed / `enroll_s`), `hint` a
   reason from its list when the latest crops are unusable ("more light", "come closer").
   Audio then publishes {part: `voice`}: `fraction` = seconds of their attributed speech /
   `voice.enroll_s`. `enroll.result` keeps its meaning; audio also reports a failed voice
   result when their face leaves ("stay in view") or `voice.enroll_timeout_s` passes ("not
   enough speech"). As before, only prints are stored, never photos or audio.
5. Once the face part succeeds the person is saved: a later voice failure leaves them saved
   with their face only (every page says so, "voice later"). The saved person replaces the
   session-only entry the confirmed name made for the same face (vision drops it), so they are
   recognised as saved from then on. Audio handles `enroll.result` ahead of its audio backlog,
   and `person.changed` {action: `enrolled`} no longer invalidates it.

## Enrollment station (V-23, A-21, P-35)

The glasses camera and mic are for the world outside. People are saved at the laptop: its
own camera (`[enroll] camera_name`, "OV02E10"; infrared cameras never) and mic
(`[enroll] mic_name`, "Microphone Array", WASAPI, never another mic). `[enroll] source =
"glasses"` keeps the P-29 flow above. Code: `engine/attune/station/`.

1. **Start.** The person being saved ticks consent on the phone, which sends `enroll.station`
   {action: `start`, name, consent: true, consent_t, request_id?, track_id?} (a double tap
   on the glasses carries that face's `track_id` and the `save.request` id; the phone's own
   "Remember me" tab carries neither). The hub adds `client_id` (the page that sent it). The
   save flow treats it like `enroll.start` for its request; walking to the laptop (the face
   leaving the glasses view) does not cancel the request in station mode. A new start
   replaces a running save (its last state has reason `replaced`).
2. **Face.** `enroll.state` {phase: `opening`}, then `face` {camera, shared}. The laptop
   camera is opened only now (or, if the main camera already holds it because the glasses
   webcam is missing, its frames are shared: `shared: true`). `enroll.preview` goes to that
   page only: a 3:4 JPEG from the middle of the frame, ~360 px wide, with the face box and a
   hint ("look at the laptop camera", "move to the middle", "come closer", "move back a
   little", "face the camera", "more light", "hold still", "one person at a time").
   `enroll.progress` {part: `face`, source: `station`, session_id} as in P-29. The camera
   closes when the step ends. No usable face in `face_timeout_s`: phase `face_failed`
   {reason}; the phone offers `retry`.
3. **Identity check** (only with a `track_id`): the laptop prints are compared with that
   glasses face's prints (median of best matches). Below `[enroll] identity_match`:
   `enroll.mismatch` and phase `mismatch` {score}; the phone shows "That isn't the person
   you were looking at" with **Try again** (`retry`: the face step again) and **Save as
   someone new** (`new_person`: saved without the link to that glasses face). Nothing is
   saved before one is chosen; no choice in `decision_timeout_s` cancels.
4. **Saved.** Phase `saving`; vision saves the face prints (`enroll.result` {part: `face`,
   source: `station`, track_id if linked}, `person.changed` {`enrolled`}) and names every
   glasses track with this face at once, replacing the session-only entry of the linked face
   and of any other session entry with the same face.
5. **Voice.** Phase `voice` {mic, sentence, need_s}: the laptop mic opens; the person reads
   the sentence shown on the phone. `enroll.level` (meter and one hint: "a bit softer",
   "too noisy", "speak up", "read the sentence aloud", "keep talking") and `enroll.progress`
   {part: `voice`}. After `[voice] enroll_s` of voiced speech the mic closes, CAM++ makes the
   print, `voice.json` is written with `source: "station"`, and `enroll.result` {part:
   `voice`, ok, source: `station`} makes the audio side load it. Failure: phase
   `voice_failed` {reason}; `retry` or `skip_voice` (the face stays saved).
6. **End.** Phase `done` (face_ok, voice_ok), `cancelled` {reason: `cancelled`, `paused`,
   `timeout`, `replaced`, `consent is required`, ...} or `fallback` {reason:
   `camera_unavailable`, `camera_lost`, `station_off`; message}: the laptop camera can't be
   used, so the phone offers saving at the glasses instead (`enroll.start`). Pause and
   forget session cancel a running save.

**Voice prints across mics (A-21).** A station print is made on the laptop mic but heard on
the glasses mic. Its scores are compared with `[voice] station_match` and shifted onto the
`[fusion] voice_match` scale. A saved person's print is refined only from `voice.harvest`
spans with `talkers == 1` (a confident face match that is the lip-synced talker, at least
`harvest_after_s`) of at least `adapt_min_s` voiced seconds that score at least `adapt_min`
for them and higher than for anyone else, at most once per `adapt_gap_s`: the embedding joins
a bank of at most `adapt_max_prints` glasses prints, and their score is the better of the
base print and the bank's mean. The base print is never replaced. `voice.json`: `consent,
consent_t, source` (`station` or `glasses`; missing = `glasses`), `embedding`, `adapted`
(the bank, when `adapt_persist`). Deleted with the person.

**Privacy.** Frames, face crops and audio exist only in memory during the save; the preview
goes only to the page that started it and is never stored. Only prints are saved.

## Cloud captions (A-32, V-32, P-48)

Optional and **off by default**. When the wearer turns them on in the phone's Settings, the
16 kHz mic audio (the same stream the local captions hear) is also streamed to Google
Speech-to-Text (v1 `StreamingRecognize`, speaker diarization on), which returns a speaker tag
for every word. Fusion uses those tags as strong "who is speaking" evidence. Design, API
choice and limits: [cloud-diarization.md](cloud-diarization.md). Code:
`engine/attune/audio/cloud_diarize.py` (2), `engine/attune/fusion/cloud_tags.py` (1).

**What leaves the laptop.** Only that audio, and only while `cloud.state.enabled` is true and
recognition isn't paused. Never frames, face or voice prints, names, captions or history.
During the wearer's own spoken reply (`speech_out.playing`) silence is sent instead of the
audio. `off` and `unavailable`: no network client exists and nothing is sent. Google's
optional data logging stays off.

**Turning it on.** The command `cloud.set` {on, language?} (Section 2 handles it) turns it on
or off. The choice and the language are kept on the laptop in `<data_dir>/cloud.json`
(`[cloud] remember`), so they survive a restart; without that file, `[cloud] enabled` (false)
applies. `cloud.state` goes out on every change, and the hub sends the latest one as `cloud`
to every page, also right after `welcome`. While `enabled` is true, every page shows a calm
"Cloud captions on" badge (the lens in all three modes, and the phone).

**Credentials** live only in `.env`, typed by a person: `GOOGLE_SPEECH_API_KEY` (primary; the
phone's Settings saves it there through the laptop-only `/api/settings/google`, like the
ElevenLabs key, and shows only that a key is saved) or `GOOGLE_APPLICATION_CREDENTIALS` (the
path of a service-account JSON key file). They are read when a stream opens, so a new key
applies without a restart. They never go on the bus, to a page or into a log; the log says
only "cloud: credentials present" or "cloud: credentials missing".

**States** (`cloud.state.state`, `reason`):

| state | meaning | fusion |
|---|---|---|
| `off` | the wearer hasn't turned it on | local speaker evidence only |
| `connecting` | opening a stream | local only |
| `on` | streaming, results fresh | uses the tags; may hold a final up to `[cloud] final_wait_ms` for them |
| `fallback` | on, but not usable right now: `network`, `quota`, `slow` (recent results later than `latency_fallback_ms`, or speech with no result for `stall_s`), `error` (Google refused a request); it retries by itself | uses tags that still arrive, never waits |
| `unavailable` | on, but can't run: `credentials missing`, `credentials rejected`, `library missing`; nothing is sent (a new key is picked up by itself) | local only |
| `paused` | recognition is paused: the stream is closed | — |

Local captions always run: caption words, utterance ids, drafts, translation and history come
from the local recogniser whatever the cloud does, so falling back never leaves a gap.

**`speaker.cloud`.** `words` are `(word, t0, t1, tag)` on the engine clock (the stream's first
audio sample time plus Google's offsets). Google re-sends a stream's words with revised tags
as it learns the voices, so an event carries only the words that are new or changed; a later
event overrides an earlier one for the same `stream_id` and word start. `final` results carry
the tags (an interim result is published only if it carries tags). `t_end` is how far into
the audio the cloud has heard: fusion stops waiting for a final caption once `t_end` passes
its last word. A `tag` is meaningful only within its `stream_id`: a stream is restarted before
Google's ~5 minute limit (and after an error), and the new stream numbers the speakers
afresh. It starts with the last `[cloud] overlap_s` of audio the old stream heard, so words
near the restart are tagged in both streams and fusion can carry each old tag's face over to
the new tag.

**In fusion.** A tag's speech that overlaps a visible face that Light-ASD (or the lip checks)
hears talking binds the tag to that face (and to its person, when known).
- **Turn changes.** Afterwards a change of tag between words starts a new caption segment for
  the other speaker at once, even for a short reply (`[cloud] turn_min_s`). Inside one other
  voice's sentence it also needs two words, since a single wrongly tagged word isn't a turn.
  Google tags only final results, so a reply inside a segment already shown as a draft is cut
  out into its own segment when the final comes. The rest keeps its segment id; new ids are
  new segments, and ids that drop out are retracted as usual.
- **Words of a bound tag** go to its face. If that face is out of view, they go to the dock
  (`offscreen`) with its label and exit side. Words said while it was in view keep the face.
- **Words of a tag bound to no face yet** go to one of these, in order:
  1. the face the local evidence gives them, unless that face is another tag's;
  2. a face seen talking then;
  3. a voice print's off-screen person;
  4. their own dock segment: `offscreen`, label "Someone", `person_id` `session-cloud-N`,
     side from the sensors or `none`. It is session-only like any stranger, so forget session
     removes it from history.
- **Finals.** A final caption is never changed after it is sent.
