# Glasses realism: how the lens view matches real devices

The lens view (`web/lens/`) draws the same captions and alerts as three kinds of glasses. This
note records what each real device can physically show, how the lens reproduces it, and what is
still unverified. The numbers come from the team's device research, with sources at the end.

## The frame as a field of view

The 1920x1080 video frame stands for about **75° horizontally** of the wearer's view. A display
that spans `deg` degrees therefore covers

```
px = 2 * 1251 * tan(deg / 2)        (1251 px = the frame's focal length)
```

`hud.js` exports this as `FOCAL` and `degToPx()`.

## Rules for every mode

- **Additive light.** Waveguides and similar optics can only add light: black is transparent, and
  they cannot draw dark fills or shade the world. The lens draws no dark panels in the compact
  modes, and the paused state no longer dims the video.
- **Only inside the display.** Each mode clips everything to its display rectangle. Edge glows,
  docked speakers and clamps all follow that rectangle, not the video frame.
- **Focus.** The virtual image sits 2–4 m away. The video stays sharp and the HUD canvases get
  a 0.4 px softness (`.hud { filter: blur(0.4px) }`).
- **Display outline.** The display rectangle shows as a very faint dotted outline while the demo
  chrome is visible. H, or `?chrome=0`, hides it along with the chrome.

## Mode 1 — Full-colour AR · binocular · Meta Orion class

| | |
|---|---|
| Optics | Binocular waveguide, about 70° diagonal |
| Display region | about 1330x960 px of the frame (69% x 89%), centred: x 295–1625, y 60–1020 |
| Anchoring | World-locked: tags and bubbles follow faces |

How the lens draws it (`modes/color.js`):

- **The film's look, kept inside the region.**
  - The status pill, toasts and alerts are laid out from the region's top-left.
  - Bubbles clamp inside the region.
  - Off-screen speakers dock to the region's left and right edges.
  - Alert edge glows light the region's edges.
- **Additive glass** (`setGlassStyle('additive')` in `hud.js`).
  - Panels keep about 70% of the world's brightness instead of about 48%, which is less dark backing.
  - They add a light frost and drop the dark drop shadow.
  - Solid bubble tails use the same light glass.
- **Edge of field.** The image fades out over the last few pixels of the region, the way a
  waveguide's field does, so glows and the smoke-alarm vignette end softly.

## Mode 2 — Mono green waveguide · binocular · Even Realities G1 class

| | |
|---|---|
| Optics | Binocular, about 25° diagonal, 640x200 display, green only, about 1000 nits, focus about 2 m |
| Display region | about 530x165 px (27.5% x 15%), centred horizontally (x 695–1225) |
| Height | At or slightly above eye level, never below the eye line. The default centre is y ≈ 430, about 5° up |
| Anchoring | Head-locked: no face-anchored bubbles |

How the lens draws it (`modes/mono.js`):

- **Native pixels.** Everything is laid out in the display's own 640x200 pixels, then scaled
  (0.828) into the frame.
- **Colour.** Pure green `rgb(40,255,70)` with a 3–4 px glow. There are no fills and no dark keyline.
- **Five lines, like Even's own caption app.**
  - A header with the speaker name, or a sound's icon and word.
  - Four caption lines of about 20 px on a 1080p frame. When a proposal footer ("ANDRE? ✓ Y ✕ N") or a "saved" line is showing, it takes the fourth line.
- **Direction.** ‹ › chevrons sit inside the band's edges; a downward chevron means behind.
- **Alerts.** An urgent alarm takes over the whole band.
- **Display height.** Levels 0–8, like the G1 setting.
  - Keys `[` and `]`, or `?height=0..8` in the URL.
  - The level is shown in the mode label.
  - The lens assumes 1° per level from 1° to 9° above eye level, so level 4 (the default) is 5° up, y ≈ 430.

## Mode 3 — Monocular display · right eye · Meta Ray-Ban Display class

| | |
|---|---|
| Optics | Right eye only, 600x600 full colour, about 20° square, translucent |
| Display region | about 307x307 px (16% x 28%), slightly below and right of centre: x 1028–1335, y 452–759 |
| Anchoring | Head-locked |

How the lens draws it (`modes/corner.js`):

- **One square, and nothing anywhere else.** There is no fake second eye; the mode label says
  "Right eye only".
- **About 80% opacity.** The whole image is translucent.
- **Captions at the very bottom of the square.**
  - A small "Live captions" label.
  - Above the text, a compact row with the speaker's dot and name, ES → EN or "on your left", eq bars, and a direction arrow.
  - Two lines of white text, about 22 px.
- **In the label row's place:** a name proposal ("Is this Andre?  Y  N") or a "saved" line.
- **Alerts take over the square.** The icon, word and detail sit in the middle; the side arrow and "A acknowledge" sit at the bottom.
- **Google Glass variant** (key `G`, or `?variant=glass`): the same layout in a 285x160 display
  above the right eye's line of sight, at x 1000–1285, y 204–364.

## Where the simulation departs from physics, on purpose

- **Brightness headroom (monocular only).**
  - The real display is far brighter than video white (thousands of nits against a monitor), so white text reads on the glasses even over a white page.
  - A video can't show "brighter than white". So behind the monocular text block, the world is attenuated by about 25% with soft edges.
  - This stands in for that headroom. It is not a panel the glasses draw.
  - Remove `headroom()` in `modes/corner.js` for the strictly additive look.
- **Mono over a bright street.**
  - The mono mode keeps its no-fill rule. Over the film's bright street scene, the green text washes out much as it would on a G1 in daylight.
  - It is readable in the indoor scenes.
- **Orion panels.**
  - They keep a little dark backing, about 70% brightness. A fully additive panel would be invisible, and captions on bright scenes would not read.

## Unverified

- **G1 height.** The default level and the size of one step are assumptions: level 4 = 5° up, and 1° per level.
- **Meta Ray-Ban Display offsets.** The exact angular offset is from reviews ("slightly below and to the right of centre"). There is no spec for it.
- **Orion region.** The region is derived from the 70° diagonal. The actual eyebox and the fading at the edges aren't measured.
- **Google Glass placement.** It is approximate, from the support page's description.

## Deterministic offline rendering (for re-rendering the launch film)

`attuneLens.renderAt(filmTime, mode)` shows film time `t` exactly:

- **The video frame for `t`.** Each clip is seeked to its in-point + (t − scene start) + 1 ms, and the call waits until that frame is painted.
- **The HUD as it is at `t`.** While rendering offline, the animation clock is pinned to film time, and the rAF loop and all CSS transitions stand aside. So word fade-ins, eq bars, pulses and the bubbles' smoothing all derive from film time.
- **Order doesn't matter.**
  - Consecutive calls (t rising by at most 0.5 s within a scene) continue the simulation like playback, in 1/60 s steps.
  - Any other call replays the script from up to 7 s before `t`, or from the scene start, with fresh HUD state.
  - So the same `t` always gives the same picture. Checked: `renderAt(31)` gives identical pixels after renders of 12.9 s, 50 s, or a run of 30 consecutive frames.
- `attuneLens.hold(t, mode)` does the same thing. `attuneLens.release()`, or Space, returns to normal playback.

A driver for playwright-core and Edge, frame by frame at 30 fps:

```js
await page.goto('http://localhost:8000/lens/?source=film&mode=mono&height=4&chrome=0');
await page.waitForFunction(() => window.attuneLens?.film);
for (let f = 0; f < 80 * 30; f++) {
  const r = await page.evaluate((t) => window.attuneLens.renderAt(t), f / 30);
  if (!r.painted) console.warn('late frame', f);
  await page.screenshot({ path: `frames/${String(f).padStart(5, '0')}.png` });
}
```

- **URL parameters:**
  - `?mode=color|mono|mono-corner`
  - `?height=0..8` (mono display height)
  - `?variant=glass` (monocular placement)
  - `?chrome=0` (no demo chrome and no display outline)
  - `?assets=` (footage folder)
- **Cost.** Serve the page from the engine, or from any server with HTTP Range requests; the seeks need them. Measured cost is about 0.1 s a frame, mostly the video seek.

## Sources

- KGOnTech, Even Realities G1 through-lens photos: https://kguttag.com/2024/08/18/even-realities-g1-minimalist-ar-glasses-with-integrated-prescription-lenses/
- Even Realities G2 renders: https://www.evenrealities.com/subtitle-glasses
- Even Realities support, display adjustment: https://support.evenrealities.com/hc/en-us/articles/13755064994831-Display-Adjustment
- UploadVR, Meta Ray-Ban Display review: https://www.uploadvr.com/meta-ray-ban-display-review/
- UploadVR, Meta Ray-Ban Display hands-on: https://www.uploadvr.com/meta-ray-ban-display-hands-on-meta-neural-band/
- Road to VR, Meta Ray-Ban Display specs: https://roadtovr.com/meta-ray-ban-smart-glasses-display-price-release-date-specs/
- KGOnTech, Meta Orion through the optics: https://kguttag.com/2024/12/05/meta-orion-through-the-optics-pictures/
- Google Glass Enterprise support: https://support.google.com/glass-enterprise/customer/answer/9220199
- arXiv 2505.09047
