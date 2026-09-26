# End-to-end suite (P-37)

Real engines in replay mode, real pages in a headless Microsoft Edge. It checks what a judge
and a deaf or hard-of-hearing user would notice: every page and key works, captions arrive
complete and in order on the glasses and the phone, alerts show and reach the rig, the camera
and pause switches reach every page, pages recover from an engine restart, nothing private is
served or written, and bad setups fail with a clear message instead of a crash loop.

## Run it

From the repo root, with the engine's Python:

```bash
python tests/e2e/run_e2e.py                     # everything except the soak (~15 min)
python tests/e2e/run_e2e.py pages captions      # some scenarios (--list shows them)
python tests/e2e/run_e2e.py soak --soak-min 9   # the soak (keep it under the 10 min GPU-lock turn)
ATTUNE_E2E=1 python -m pytest tests/e2e -q -p no:cacheprovider   # the same, as pytest
```

It prints a pass/fail table; `results.json`, the engine logs and the screenshots go to
`%TEMP%/attune_eval/qa/e2e-<time>/` (set `ATTUNE_E2E_OUT` to change it).

Plain `pytest tests` skips this folder unless `ATTUNE_E2E=1`, and the suite skips itself
(with the reason) when something it needs is missing, so it never fails on a laptop that
can't run it.

## What it needs

| Need | Found at | Override |
|---|---|---|
| Microsoft Edge | the usual install folders | - |
| node + `playwright-core` | `tests/e2e/node_modules`, `node_modules`, or `../attune-video/render/node_modules` next to the checkout | `ATTUNE_PLAYWRIGHT_DIR` |
| Models (Nemotron, buffalo_l) | `models/` | `ATTUNE_MODELS_DIR` |
| A film reel | `data/reels/film/cafe_friends.mp4` (or `%TEMP%/liveclip1/clip.mp4`) | `ATTUNE_E2E_VIDEO` |
| Speech with known text | made once with Windows SAPI voices (David, Zira) | - |
| Alarm tones | made with `scripts/make_test_tones.py` | - |

Nothing is downloaded or installed.

## How it stays safe

- Each engine runs in its own temporary folder (config, `data/`, a link to `models/`) on port
  **8013** (`ATTUNE_E2E_PORT`), never on the live engine's 8000, and is stopped at the end.
- It never opens a camera or a microphone: the video comes from a file (`--source`), the sound
  from a WAV (`--audio-file`), and the config names a camera that doesn't exist with the
  "any camera" fallback off, so even a bad `--source` can't pick up a webcam.
- The Arduino is the simulated one (`--simulate-hardware`); COM ports are never opened.
- Spoken replies go through the offline voice and are thrown away (`[speech_out] device =
  "none"`); ElevenLabs is off and its key is removed from the engine's environment. The
  browser is muted. Alarm tones only ever go into the engine as a WAV.
- The engine's process is found by its listening port: a venv's `python.exe` on Windows is a
  launcher, so the child `Popen` returns is not the process to measure.
- Engines take the team's GPU lock (`%TEMP%/attune_gpu.lock`, `ATTUNE_E2E_NO_LOCK=1` on a
  laptop without the shared setup) and give it back between scenarios and at least every ~9
  minutes. A soak is one turn of at most 9 minutes (`--soak-min` above 9 is capped while
  the lock is used); run it again for a longer total.

## Scenarios

| Scenario | What it checks |
|---|---|
| `security` | `data/people`, config, `.env`, history and `../` tricks are not served; no API schema; the WebSocket refuses other websites; other host names are refused (DNS rebinding); simulator controls take JSON only |
| `code_network` | the engine's code only names loopback hosts |
| `hardware` | simulated Arduino: READY, CFG, heartbeat rate, every pattern as `PAT` with its `ACK`, a test pattern stops by itself after one cycle, touches (tap, hold, double, triple), the 2 s safety stop (LOST) and relink, a board reset (brown-out) is noticed and the link recovers, PAUSE icon |
| `camera_pause` | camera off/on and pause/resume reach every page (and pages that connect later); frames stop and come back; captions keep running with the camera off and stop while paused |
| `pages` | demo (5 views, `` ` ``, Alt+1..5, Alt+C, hidden panes out of the tab order), lens (colour, mono, corner, Glass placement, keys M [ ] G H ? P C V F), POV (Closer/Everything, looks, Esc), glasses guide ("Try" buttons), phone (every screen at 390x844 and 360x740, both palettes, history search, speak-for-me, presets, switches, camera off/on, pause, forget, dark theme, tab bar), panels; no console errors or failed requests; layout (overflow, overlaps, tiny or cut-off text), WCAG AA contrast, visible keyboard focus, screen-reader names |
| `save` | D on the glasses (the double tap) reaches the phone's consent sheet in all three looks; Save stays off until the person ticks consent; Cancel enrolls nothing |
| `reconnect` | stop and restart the engine: every page shows it lost the link and recovers by itself; a reply typed offline is spoken after reconnecting |
| `privacy` | forget session wipes strangers' lines from this session's history and any names heard; no photos, audio or prints written; the session log has no caption text; the engine process opens no outside connection all the time it runs (checked every 10 s, with the host name from the DNS cache) |
| `captions` | a 6-sentence script with known text: on the lens (messages, the drawn captions and the screen-reader line) and the phone (messages and the live view): every sentence, in order, no duplicates, word error rate |
| `first_words` | speech that starts right after start-up keeps its first words |
| `alerts` | T3 and T4 recordings: the right alert and side on the glasses and phone, `PAT T3`/`PAT T4` to the rig on that side, "Got it" acknowledges everywhere and stops the rig |
| `station` | the laptop enrollment station's phone screens against `tests/pages_engine/station_e2e/fake_engine.py` (the real hub, save flow and station with a fake camera, mic and models; nothing opened, prints in a temp folder): its own flow test (`phone_station.mjs`), then every station screen at 390x844 and 360x740, Apricot and dark: layout, contrast, names, focus, buttons reachable, the mismatch and no-camera screens, Escape, and asking the same person again right after a cancel |
| `robustness` | no mic, no camera, a `--source` typo, a missing model, bad config values, a broken TOML: clear log lines, no crash loop; a typo or a broken config stops at once with exit code 2 |
| `soak` | looping speech for `--soak-min` minutes: engine memory, CPU, threads, handles, GPU, fps, caption delay, message rates, outside connections, and the pages' JS heap and DOM size |

A failing check names what a judge or a wearer would notice; `results.json` has the details
and the screenshot for each. A few checks fail on purpose until another stream fixes its part
(the PR that added this suite lists them).

Files: `harness.py` (engines, GPU lock, WebSocket pages, fixtures, scoring), `run_e2e.py`
(scenarios and the table), `test_e2e.py` (pytest), `lib.mjs` (browser helpers and audits),
`pages.mjs`, `captions.mjs`, `alerts.mjs`, `save.mjs`, `reconnect.mjs`, `soak.mjs`,
`station.mjs`.
