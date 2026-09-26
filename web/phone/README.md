# Attune phone app

The wearer's phone view of Attune: live captions, people, name proposals, sound alerts, "speak for me" and conversation history. Plain HTML, CSS and JavaScript (ES modules), no install or build step.

## Open it

**From the Attune engine (live).** The engine serves this page at `/phone/`. On the laptop, open `http://localhost:8000/phone/`.

From a real phone on the same Wi-Fi, the engine must listen on the network, not only on the laptop: start it with `--host 0.0.0.0`, then open `http://<laptop-ip>:8000/phone/` on the phone (`ipconfig` shows the laptop's IPv4 address). Windows may ask to allow Python through the firewall on private networks.

**Against an engine elsewhere.** Add `?engine=host:port`, for example when serving the pages from another port during development:

```sh
python -m http.server 8021 --bind 127.0.0.1 --directory .
# then open http://127.0.0.1:8021/web/phone/?engine=localhost:8000
```

**Demo only.** Add `?demo` to skip connecting and use the built-in sample data.

## Live and demo

The page connects to the engine's WebSocket through `web/shared/ws.js` with role `phone` (docs/contracts.md, sections 3 and 4). The corner label and the pill under the logo show the state:

- **LIVE · Connected**: every screen shows engine data, and every control sends the matching command.
- **OFFLINE · Reconnecting**: the link dropped after being live. The last data stays on screen, the link retries on its own, and commands are queued (up to 20) until it is back.
- **DEMO · Not connected**: the page has not reached an engine yet (or `?demo` is set). Sample captions and people only; controls change the page, nothing else.

| On the phone | Live behaviour |
|---|---|
| Home, Live view | `caption` messages (speaker, text, the English translation with the original shown small and an ES tag) |
| People | `people`, `person_changed`; Rename sends `person.rename`, Remove sends `person.delete` after a confirm |
| Name proposal ("Sam?") | `name_proposal`; Confirm / Not Sam send `name.answer` |
| Alert banner | `alert`; Got it sends `alert.ack` |
| Speak | typed text, presets (from `welcome.config.presets`) and suggested replies (`reply_suggestions`) send `speak` with source `typed`, `preset` or `suggestion`; `reply_spoken` confirms it |
| Pause, Turn off glasses | `pause.toggle`; state from `welcome.paused` and `paused` |
| Forget this session | `session.forget` after a confirm |
| Sound alerts, Translation switches | `switch.set` with key `alerts` / `translation` (Captions and Name labels only change this phone) |
| Conversations | `GET /api/history/sessions`, `/sessions/{id}` and `/search?q=`; falls back to this session's captions if the history API isn't there |

## Privacy

Captions, names and settings live in the page's memory and disappear when the tab closes; nothing is written to the phone. The page talks only to the Attune engine (WebSocket and `/api/history`), asks for no camera or microphone, and inserts all engine text as text, never as HTML. Its Content Security Policy allows scripts and styles from its own origin only, and network connections to its own origin, WebSockets, and `localhost` (for `?engine=` during development). Only text the wearer sends to speak leaves the laptop (to ElevenLabs), and history is deleted after 24 hours, as the engine contract says.

The link has no pairing or authentication yet: anyone on the same network who can open the page can control the engine. Use it on a trusted network for the demo.
