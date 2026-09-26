# Attune phone preview

An interactive, laptop-run phone simulator for the Attune interface. It uses plain HTML, CSS, and JavaScript with no install or build step.

From the repository root, run:

```sh
python3 -m http.server 8765 --bind 127.0.0.1 --directory web/phone
```

Open `http://127.0.0.1:8765/` in a browser. Use the four tabs to explore the preview. On a narrow browser window, the layout fills the viewport.

The simulator has sample captions and people only. Its feature switches, name proposal, search, quick replies, pause, power, and forget controls update the preview state. **It does not connect to the Attune engine or control hardware.** Speech uses a locally installed English system voice when the browser exposes one; it never sends text to ElevenLabs.

Privacy in this preview: conversation text, names, settings, and theme stay in memory and disappear when the tab closes. The page makes no network requests beyond its own three local files, asks for no camera or microphone access, and has a restrictive Content Security Policy. Dynamic text is inserted as text rather than HTML. A production app needs authenticated transport, device pairing, encrypted storage, and full integration with the engine. The engine contract specifies a 24-hour history limit, which takes precedence over the seven-day text in the reference images.
