/*
 * Mode 1 - Full-colour AR, Meta Orion class (binocular waveguide, about 70 degrees diagonal).
 * Face-anchored name tags and speech bubbles (one calm slot per person, bubbles.js), docked
 * off-screen speakers, the You bar,
 * alert chips/cards with direction, name proposals, the status pill and paused state.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-06, P-07, P-08.
 *
 * Physically honest (docs/glasses-realism.md): everything stays inside the display region, about
 * 1330x960 of the 1920x1080 frame, centred. The Daylight look (P-44) is bright and hopeful:
 * luminous paper panels with deep ink text (a waveguide adds light, so bright panels are what it
 * shows best), clear friendly accents, and a soft mist-grey bubble for people not named yet.
 */

import { W, H, setRegion, setGlassStyle, regionOutline } from '../hud.js';
import { createBubbleLayer } from '../bubbles.js';
import { createBubbleLayer as createCenteredBubbleLayer } from '../bubbles-centered.js';
import { drawAlerts, drawStatus, drawToasts, drawPaused, createStack } from '../alerts.js';

export const ORION_REGION = { x: (W - 1330) / 2, y: (H - 960) / 2, w: 1330, h: 960 };

// The image fades out over the last few pixels of the display (like a waveguide's edge of field),
// so edge glows and the alarm vignette end softly instead of on a hard line.
let edgeMask = null;
function getEdgeMask() {
  if (edgeMask) return edgeMask;
  const k = 0.25; // a low-resolution mask is fine: it is all soft edges
  const c = document.createElement('canvas');
  c.width = W * k;
  c.height = H * k;
  const m = c.getContext('2d');
  const R = ORION_REGION;
  const inset = 7;
  m.filter = `blur(${14 * k}px)`;
  m.fillStyle = '#fff';
  m.beginPath();
  m.roundRect((R.x + inset) * k, (R.y + inset) * k, (R.w - inset * 2) * k, (R.h - inset * 2) * k, 34 * k);
  m.fill();
  edgeMask = c;
  return c;
}

export function createColorMode() {
  let classic = createBubbleLayer();
  let centered = createCenteredBubbleLayer();
  let active = classic;
  let style = 'classic';
  const bubbles = {
    render(ctx, view, env) {
      const next = view.config.caption_style === 'centered' ? 'centered' : 'classic';
      if (next !== style) {
        if (next === 'centered') centered = createCenteredBubbleLayer();
        else classic = createBubbleLayer();
        style = next;
      }
      active = style === 'centered' ? centered : classic;
      if (active === centered) setGlassStyle('additive');
      const placed = active.render(ctx, view, env);
      if (active === centered) setGlassStyle('bright');
      return placed;
    },
    debug: () => active.debug(),
    debugDocks: () => active.debugDocks(),
    get cards() { return active.cards; },
  };
  const stack = createStack(); // toasts, alerts and proposals glide when one arrives or leaves
  let obstacles = [];
  return {
    id: 'color',
    name: 'Full-colour AR',
    device: 'Binocular · Meta Orion class',
    blur: true,
    region: ORION_REGION,
    bubbles, // bubbles.debug() lists every card's place and text (measurements, tests)
    render(ctx, view, env) {
      const R = ORION_REGION;
      setRegion(R);
      setGlassStyle('bright');
      ctx.save();
      ctx.beginPath();
      ctx.rect(R.x, R.y, R.w, R.h);
      ctx.clip();
      bubbles.render(ctx, view, { ...env, obstacles: view.save ? [...obstacles, ...view.save.obstacles] : obstacles });
      view.save?.drawColor(ctx, env, bubbles); // P-29: save this person (save.js)
      // status top-left, then toasts, then alerts stacked below them (top centre)
      const toastRects = drawToasts(ctx, env.blur, view, env.anim, R.y + 88, stack);
      // the alerts' target place follows the toasts' targets (not their glide), so it never wobbles
      const alertTop = R.y + 88 + toastRects.length * 66;
      const rects = [...toastRects, ...drawAlerts(ctx, env.blur, view, env.anim, alertTop, stack)];
      obstacles = rects.map((r) => ({ x: r.x - 10, y: r.y, w: r.w + 20, h: r.h }));
      drawPaused(ctx, env.blur, view, env.anim);
      drawStatus(ctx, env.blur, view, env.anim);
      ctx.globalCompositeOperation = 'destination-in';
      ctx.drawImage(getEdgeMask(), 0, 0, W, H);
      ctx.restore();
      // the display area, faintly, while the demo chrome is visible (H hides it)
      if (env.chrome) regionOutline(ctx, R, 40);
      setGlassStyle('solid');
      setRegion({ x: 0, y: 0, w: W, h: H });
    },
  };
}
