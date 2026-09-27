/*
 * HUD drawing primitives for the lens (ported from the launch film's render/hud.js).
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-06.
 * Every draw works in a 1920x1080 design space; lens.js scales the canvas to the stage and
 * devicePixelRatio, so text stays crisp at any size. Contract boxes (1280x720) are scaled by 1.5.
 */

export const W = 1920;
export const H = 1080;
export const K = W / 1280; // contract space (1280x720) -> design space

export const FD = '"Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif';
export const FT = '"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif';

export const MINT = '#7CF5D6';
export const SKY = '#6AB8FF';
export const ACCENT = '#8FF3E0';
export const AMBER = '#FFC857';
export const RED = '#FF4D4F';
export const ORANGE = '#FF9F43';
export const INFO = '#7CC8FF';
export const PHOSPHOR = '#6BFF8F';

// ---------------------------------------------------------------- Daylight palette (P-44)
// The full-colour look is bright and hopeful: luminous paper panels, deep ink text and a few
// clear, friendly accents. People nobody has named yet get a soft mist-grey bubble (no dashes).
export const INK = '#16202E'; // names and captions
export const INK_2 = '#4A586B'; // secondary text
export const INK_3 = '#7B8798'; // hints and keys
export const PAPER = 'rgba(255,255,255,0.93)';
export const MIST = 'rgba(226,231,238,0.95)'; // someone Attune doesn't know yet
export const MIST_INK = '#3B4758';
export const TEAL = '#0EA897'; // the brand, deep enough for paper
export const BLUE = '#2F80ED';
export const SUN = '#F6A623'; // attention: doorbell, knock
export const CORAL = '#F0453A'; // urgent: smoke, CO
export const LEAF = '#1AAE68'; // listening, confirmed, saved

export const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
export const lerp = (a, b, t) => a + (b - a) * t;
export const easeOut = (t) => 1 - Math.pow(1 - clamp(t), 3);
export const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
export const easeBack = (t, s = 1.4) => 1 + (s + 1) * Math.pow(t - 1, 3) + s * Math.pow(t - 1, 2);
export const font = (w, px, fam = FT) => `${w} ${px}px ${fam}`;
// ---------------------------------------------------------------- display region and glass style
// Real glasses only draw inside their display: each mode sets the region (design px) it may use,
// and edge glows, docking and clamps follow it. See docs/glasses-realism.md for the numbers.
/** 1251 px focal length: a 1920x1080 frame spans about 75 degrees horizontally. */
export const FOCAL = 1251;
export const degToPx = (deg) => FOCAL * Math.tan((deg * Math.PI) / 180);
export let REGION = { x: 0, y: 0, w: W, h: H };
export function setRegion(r) {
  REGION = r;
}
/** Faint outline of the display area (only drawn while the demo chrome is visible). */
export function regionOutline(ctx, r, radius, color = 'rgba(255,255,255,0.10)') {
  ctx.save();
  rrect(ctx, r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1, radius);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.2;
  ctx.setLineDash([2, 6]);
  ctx.stroke();
  ctx.restore();
}
/**
 * Waveguides add light and cannot darken the world, so an 'additive' look keeps panels bright
 * and translucent: less dark backing (about 70% of the world's brightness shows through), a
 * light frost, and no dark drop shadow.
 */
let GLASS = { tintScale: 1, frost: 0, shadow: true };
/**
 * 'bright' (full-colour AR, P-44): luminous paper panels over a frosted copy of the world, with a
 * soft cool shadow so a white panel still separates from a bright room.
 */
export function setGlassStyle(look) {
  if (look === 'bright') GLASS = { bright: true, tintScale: 1, frost: 0, shadow: true };
  else GLASS = look === 'additive' ? { tintScale: 0.56, frost: 0.07, shadow: false } : { tintScale: 1, frost: 0, shadow: true };
}
export const isBright = () => !!GLASS.bright;

/** Fill for a solid bubble tail, matching the current glass look. */
export const tailFill = () => (GLASS.bright ? PAPER : GLASS.frost ? 'rgba(205,215,230,0.34)' : 'rgba(30,33,41,0.80)');

/** A colour pulled toward the ink, for small marks and text in a person's colour on paper. */
const deepCache = new Map();
export function deepen(hex, k = 0.38) {
  if (typeof hex !== 'string' || hex[0] !== '#' || hex.length !== 7) return hex;
  const key = hex + k;
  let out = deepCache.get(key);
  if (!out) {
    const n = parseInt(hex.slice(1), 16);
    const mix = (c, ink) => Math.round(c + (ink - c) * k);
    const r = mix((n >> 16) & 255, 0x16);
    const g = mix((n >> 8) & 255, 0x20);
    const b = mix(n & 255, 0x2e);
    out = `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`;
    deepCache.set(key, out);
  }
  return out;
}

/** 'rgba(r,g,b,a)' between two 'rgba(...)' strings (for a panel that changes colour). */
export function mixRgba(a, b, t) {
  const pa = a.match(/[\d.]+/g).map(Number);
  const pb = b.match(/[\d.]+/g).map(Number);
  const m = (i) => pa[i] + (pb[i] - pa[i]) * clamp(t);
  return `rgba(${Math.round(m(0))},${Math.round(m(1))},${Math.round(m(2))},${m(3).toFixed(3)})`;
}

/** Canvas pixels per design unit; shadow blur is in canvas pixels, so it is scaled by this. */
export let PX = 1;
export function setPixelScale(k) {
  PX = k;
}

/** The page's animation clock in seconds (performance.now plus any time settle() stepped ahead). */
let skewMs = 0;
let manualS = null;
export const nowS = () => manualS ?? (performance.now() + skewMs) / 1000;
/** Offline rendering: pin the animation clock to a value derived from film time (null = real time). */
export function setManualClock(s) {
  manualS = s;
}
export function addSkew(ms) {
  skewMs += ms;
}

/** Frame-rate independent smoothing factor for an exponential follow. */
export const follow = (dt, rate) => 1 - Math.exp(-dt * rate);

const hexCache = new Map();
export function hexA(hex, a) {
  let rgb = hexCache.get(hex);
  if (!rgb) {
    const n = parseInt(hex.slice(1), 16);
    rgb = `${(n >> 16) & 255},${(n >> 8) & 255},${n & 255}`;
    hexCache.set(hex, rgb);
  }
  return `rgba(${rgb},${clamp(a)})`;
}

export function rrect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, Math.max(0, Math.min(r, h / 2, w / 2)));
}

// ---------------------------------------------------------------- text measuring (cached)
const widthCache = new Map();
export function textW(ctx, text, fontStr) {
  const key = fontStr + '\u0000' + text;
  let w = widthCache.get(key);
  if (w === undefined) {
    ctx.font = fontStr;
    w = ctx.measureText(text).width;
    if (widthCache.size > 4000) widthCache.clear();
    widthCache.set(key, w);
  }
  return w;
}

/** Word-wrap by pixel width. Returns lines of text. */
export function wrapPx(ctx, text, maxW, fontStr) {
  const words = text.split(/\s+/).filter(Boolean);
  const lines = [];
  let cur = '';
  for (const wd of words) {
    const next = cur ? cur + ' ' + wd : wd;
    if (cur && textW(ctx, next, fontStr) > maxW) {
      lines.push(cur);
      cur = wd;
    } else cur = next;
  }
  if (cur) lines.push(cur);
  return lines;
}

/** Word-wrap by character count (the engine's bubble_chars), splitting very long words. */
export function wrapChars(text, maxChars) {
  const words = text.split(/\s+/).filter(Boolean);
  const lines = [];
  let cur = '';
  for (let wd of words) {
    while (wd.length > maxChars) {
      if (cur) { lines.push(cur); cur = ''; }
      lines.push(wd.slice(0, maxChars));
      wd = wd.slice(maxChars);
    }
    const next = cur ? cur + ' ' + wd : wd;
    if (cur && next.length > maxChars) {
      lines.push(cur);
      cur = wd;
    } else cur = next;
  }
  if (cur) lines.push(cur);
  return lines;
}

// ---------------------------------------------------------------- glass
/**
 * Frosted-glass panel. `blur` is a small, pre-blurred copy of the current video frame (or null).
 * o: { alpha, tint, glow (hex), border, shadow, radius }
 */
export function glass(ctx, blur, x, y, w, h, r, o = {}) {
  const a = o.alpha ?? 1;
  if (a <= 0.002 || w <= 0 || h <= 0) return;
  if (GLASS.bright) {
    brightGlass(ctx, blur, x, y, w, h, r, a, o);
    return;
  }
  ctx.save();
  ctx.globalAlpha *= a;
  if (o.shadow !== 0 && GLASS.shadow) {
    ctx.save();
    ctx.shadowColor = `rgba(0,0,0,${o.shadow ?? 0.3})`;
    ctx.shadowBlur = 26 * PX;
    ctx.shadowOffsetY = 8 * PX;
    rrect(ctx, x, y, w, h, r);
    ctx.fillStyle = 'rgba(0,0,0,0.28)';
    ctx.fill();
    ctx.restore();
  }
  ctx.save();
  rrect(ctx, x, y, w, h, r);
  ctx.clip();
  if (blur) ctx.drawImage(blur, 0, 0, blur.width, blur.height, 0, 0, W, H);
  ctx.save();
  ctx.globalAlpha *= GLASS.tintScale;
  ctx.fillStyle = o.tint ?? 'rgba(14,16,22,0.52)';
  ctx.fillRect(x, y, w, h);
  ctx.restore();
  if (GLASS.frost) {
    ctx.fillStyle = `rgba(235,245,255,${GLASS.frost})`;
    ctx.fillRect(x, y, w, h);
  }
  const g = ctx.createLinearGradient(0, y, 0, y + h);
  g.addColorStop(0, 'rgba(255,255,255,0.13)');
  g.addColorStop(0.45, 'rgba(255,255,255,0.03)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(x, y, w, h);
  if (o.glow) {
    const gg = ctx.createLinearGradient(x, 0, x + Math.min(w, 260), 0);
    gg.addColorStop(0, hexA(o.glow, 0.22));
    gg.addColorStop(1, hexA(o.glow, 0));
    ctx.fillStyle = gg;
    ctx.fillRect(x, y, w, h);
  }
  ctx.restore();
  if (o.border !== null) {
    rrect(ctx, x + 0.75, y + 0.75, w - 1.5, h - 1.5, r);
    ctx.strokeStyle = o.border ?? 'rgba(255,255,255,0.20)';
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
  ctx.restore();
}

/**
 * The Daylight panel's outline: a rounded rect, plus an optional pointer `notch` [base, tip,
 * base] joined to it as one shape (wound the same way, so the union fills with no seam).
 */
function panelPath(ctx, x, y, w, h, r, notch) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, Math.max(0, Math.min(r, h / 2, w / 2)));
  if (notch) {
    let [p, t, q] = notch;
    if ((t[0] - p[0]) * (q[1] - p[1]) - (t[1] - p[1]) * (q[0] - p[0]) < 0) [p, q] = [q, p];
    ctx.moveTo(p[0], p[1]);
    ctx.arcTo(t[0], t[1], q[0], q[1], 3.5);
    ctx.lineTo(q[0], q[1]);
    ctx.closePath();
  }
}

/**
 * The Daylight panel: a soft shadow, the frosted world, a paper wash (o.fill, default PAPER), a
 * sheen along the top, an optional wash of colour from the left (o.glow) and a crisp edge
 * (o.border; null for none). o.shadow scales the shadow (0 for none); o.notch adds a pointer.
 */
function brightGlass(ctx, blur, x, y, w, h, r, a, o) {
  ctx.save();
  ctx.globalAlpha *= a;
  const fill = o.fill ?? PAPER;
  const sh = o.shadow ?? 1;
  const notch = o.notch ?? null;
  if (sh > 0) {
    ctx.save();
    ctx.shadowColor = `rgba(20,34,58,${0.2 * sh})`;
    ctx.shadowBlur = 30 * PX;
    ctx.shadowOffsetY = 10 * PX;
    panelPath(ctx, x, y, w, h, r, notch);
    ctx.fillStyle = 'rgba(255,255,255,0.5)';
    ctx.fill();
    ctx.restore();
  }
  ctx.save();
  panelPath(ctx, x, y, w, h, r, notch);
  ctx.clip();
  if (blur) ctx.drawImage(blur, 0, 0, blur.width, blur.height, 0, 0, W, H);
  ctx.fillStyle = fill;
  ctx.fillRect(x - 24, y - 24, w + 48, h + 48); // the clip keeps it to the panel (and its notch)
  const g = ctx.createLinearGradient(0, y, 0, y + Math.min(h, 90));
  g.addColorStop(0, 'rgba(255,255,255,0.55)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(x, y, w, Math.min(h, 90));
  if (o.glow) {
    const gg = ctx.createLinearGradient(x, 0, x + Math.min(w, 300), 0);
    gg.addColorStop(0, hexA(o.glow, 0.2));
    gg.addColorStop(1, hexA(o.glow, 0));
    ctx.fillStyle = gg;
    ctx.fillRect(x, y, w, h);
  }
  ctx.restore();
  if (o.border !== null) {
    rrect(ctx, x + 0.75, y + 0.75, w - 1.5, h - 1.5, Math.max(0, r - 0.75));
    ctx.strokeStyle = o.border ?? 'rgba(255,255,255,0.95)';
    ctx.lineWidth = 1.5;
    ctx.stroke();
    if (!notch) {
      // a hairline edge so a white panel still reads against a white wall
      rrect(ctx, x - 0.5, y - 0.5, w + 1, h + 1, r + 0.5);
      ctx.strokeStyle = 'rgba(20,34,58,0.10)';
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  }
  ctx.restore();
}

// ---------------------------------------------------------------- icons (24-unit grid, stroked)
const P = (d) => new Path2D(d);
const ICONS = {
  bell: [P('M6 16.5V11a6 6 0 0 1 12 0v5.5l1.6 2.2H4.4z'), P('M10 21a2.1 2.1 0 0 0 4 0')],
  flame: [P('M12 2.8c.6 3.2 5.6 5.4 5.6 10.6a5.6 5.6 0 0 1-11.2 0c0-2.6 1.4-4.2 2.7-5.3.2 1.9 1.1 3.2 2.3 3.6-.4-3.3-.2-6 .6-8.9z')],
  co: [P('M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0'), P('M10.2 9.6a3 3 0 1 0 0 4.8'), P('M14.6 12m-1.9 0a1.9 2.4 0 1 0 3.8 0a1.9 2.4 0 1 0-3.8 0')],
  bike: [P('M5.8 17.5m-3.4 0a3.4 3.4 0 1 0 6.8 0a3.4 3.4 0 1 0-6.8 0'), P('M18.2 17.5m-3.4 0a3.4 3.4 0 1 0 6.8 0a3.4 3.4 0 1 0-6.8 0'), P('M5.8 17.5l4-7.2h5.4l3 7.2M9.8 10.3L8.4 7.6H6.6M15.2 10.3l1.2-3.2h2.2')],
  voice: [P('M9 8m-3.2 0a3.2 3.2 0 1 0 6.4 0a3.2 3.2 0 1 0-6.4 0'), P('M2.8 20.5c0-3.5 2.8-6.2 6.2-6.2s6.2 2.7 6.2 6.2'), P('M17.2 6.4c1.3 1.4 1.3 3.8 0 5.2M19.8 4.2c2.5 2.6 2.5 7 0 9.6')],
  truck: [P('M2 6.5h11.5v9.5H2z'), P('M13.5 9.5h4.2l3.3 3.4V16h-7.5'), P('M6.2 17.6m-1.9 0a1.9 1.9 0 1 0 3.8 0a1.9 1.9 0 1 0-3.8 0'), P('M17 17.6m-1.9 0a1.9 1.9 0 1 0 3.8 0a1.9 1.9 0 1 0-3.8 0')],
  lock: [P('M5.5 11h13a1.5 1.5 0 0 1 1.5 1.5v7a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 19.5v-7A1.5 1.5 0 0 1 5.5 11z'), P('M8 11V8a4 4 0 0 1 8 0v3')],
  check: [P('M5 12.6l4.3 4.3L19.2 7')],
  // cloud captions (P-48): a plain cloud outline
  cloud: [P('M7 18.5h10.2a3.8 3.8 0 0 0 .5-7.57A5.6 5.6 0 0 0 6.9 9.6 4.5 4.5 0 0 0 7 18.5z')],
  cross: [P('M6.5 6.5l11 11M17.5 6.5l-11 11')],
  globe: [P('M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0'), P('M3 12h18M12 3c3.2 3.3 3.2 14.7 0 18M12 3c-3.2 3.3-3.2 14.7 0 18')],
  userplus: [P('M9 8m-3.3 0a3.3 3.3 0 1 0 6.6 0a3.3 3.3 0 1 0-6.6 0'), P('M2.8 20.5c0-3.5 2.8-6.2 6.2-6.2s6.2 2.7 6.2 6.2'), P('M19 7.5v6M16 10.5h6')],
  wave: [P('M4 10v4M8 7v10M12 4v16M16 8v8M20 10.5v3')],
  pause: [P('M8.5 5.5v13M15.5 5.5v13')],
  warn: [P('M12 3.2l9.4 16.4H2.6z'), P('M12 9.5v4.8M12 17.2v.2')],
  sound: [P('M4 9.5h3.5L12 5.5v13l-4.5-4H4z'), P('M15.5 9a4.2 4.2 0 0 1 0 6M18.2 6.4a8 8 0 0 1 0 11.2')],
  trash: [P('M4.5 7h15M9.5 7V4.8h5V7M6.5 7l1 12.5h9l1-12.5')],
  // a door with its handle and two knock arcs beside it
  door: [P('M4.5 20.5V4.8a1.3 1.3 0 0 1 1.3-1.3h7.4a1.3 1.3 0 0 1 1.3 1.3v15.7M2.8 20.5h13.4'), P('M11.2 12.2v.3'), P('M18 9.2a4 4 0 0 1 0 5.6M20.6 7a7.2 7.2 0 0 1 0 10')],
  user: [P('M12 8.2m-3.6 0a3.6 3.6 0 1 0 7.2 0a3.6 3.6 0 1 0-7.2 0'), P('M5.2 20c0-3.8 3-6.6 6.8-6.6s6.8 2.8 6.8 6.6')],
};
export function icon(ctx, name, x, y, size, color, lw = 2) {
  const paths = ICONS[name] ?? ICONS.sound;
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(size / 24, size / 24);
  ctx.strokeStyle = color;
  ctx.lineWidth = lw;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  for (const p of paths) ctx.stroke(p);
  ctx.restore();
}

// ---------------------------------------------------------------- brand mark
export function logoMark(ctx, cx, cy, R, p = 1, a = 1) {
  if (a <= 0) return;
  ctx.save();
  ctx.globalAlpha *= a;
  const g = ctx.createLinearGradient(cx - R, cy - R, cx + R, cy + R);
  g.addColorStop(0, GLASS.bright ? TEAL : MINT);
  g.addColorStop(1, GLASS.bright ? BLUE : SKY);
  ctx.strokeStyle = g;
  ctx.fillStyle = g;
  ctx.lineWidth = R * 0.14;
  ctx.lineCap = 'round';
  const start = Math.PI * 0.72;
  const sweep = Math.PI * 1.86 * easeInOut(clamp(p / 0.7));
  ctx.beginPath();
  ctx.arc(cx, cy, R, start, start + sweep);
  ctx.stroke();
  const tailP = easeOut(clamp((p - 0.62) / 0.2));
  if (tailP > 0) {
    const bx = cx + Math.cos(start) * R;
    const by = cy + Math.sin(start) * R;
    ctx.beginPath();
    ctx.moveTo(bx, by);
    ctx.quadraticCurveTo(bx - R * 0.12, by + R * 0.3 * tailP, bx - R * 0.34 * tailP, by + R * 0.4 * tailP);
    ctx.stroke();
  }
  const hs = [0.42, 0.86, 0.58];
  for (let i = 0; i < 3; i++) {
    const bp = easeBack(clamp((p - 0.55 - i * 0.1) / 0.3));
    if (bp <= 0) continue;
    const bh = R * hs[i] * bp;
    const bw = R * 0.2;
    rrect(ctx, cx + (i - 1) * R * 0.38 - bw / 2, cy - bh / 2, bw, bh, bw / 2);
    ctx.fill();
  }
  ctx.restore();
}

/** Four little level bars that dance while someone is speaking. */
export function eqBars(ctx, x, y, color, t, active = 1, h = 16) {
  ctx.save();
  ctx.fillStyle = color;
  for (let i = 0; i < 4; i++) {
    const v = active * (0.35 + 0.65 * Math.abs(Math.sin(t * (7.3 + i * 2.1) + i * 1.7) * Math.sin(t * (3.1 + i) + i)));
    const bh = Math.max(3, h * v);
    rrect(ctx, x + i * 6, y - bh / 2, 3.4, bh, 1.7);
    ctx.fill();
  }
  ctx.restore();
}

/** Coloured person dot with a soft glow (on paper: a white rim and a gentle shadow instead). */
export function dot(ctx, x, y, r, color, glowPx = 12) {
  ctx.save();
  if (GLASS.bright) {
    ctx.shadowColor = 'rgba(20,34,58,0.28)';
    ctx.shadowBlur = 4 * PX;
    ctx.shadowOffsetY = 1 * PX;
    ctx.fillStyle = '#FFFFFF';
    ctx.beginPath();
    ctx.arc(x, y, r + 2.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowColor = 'transparent';
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = hexA(deepen(color, 0.5), 0.35);
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.restore();
    return;
  }
  ctx.shadowColor = color;
  ctx.shadowBlur = glowPx * PX;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

export function keycapWidth(ctx, label, size = 18) {
  return Math.max(size * 1.55, textW(ctx, label, font(650, size, FT)) + size * 0.9);
}

/** Small keycap, e.g. "Y". Returns its width. */
export function keycap(ctx, x, y, label, o = {}) {
  const size = o.size ?? 18;
  const f = font(650, size, FT);
  const h = size * 1.55;
  const w = keycapWidth(ctx, label, size);
  ctx.save();
  ctx.globalAlpha *= o.alpha ?? 1;
  rrect(ctx, x, y - h / 2, w, h, size * 0.38);
  ctx.fillStyle = o.fill ?? (GLASS.bright ? 'rgba(22,32,46,0.06)' : 'rgba(255,255,255,0.12)');
  ctx.fill();
  ctx.strokeStyle = o.stroke ?? (GLASS.bright ? 'rgba(22,32,46,0.22)' : 'rgba(255,255,255,0.32)');
  ctx.lineWidth = 1.2;
  ctx.stroke();
  ctx.font = f;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = o.color ?? (GLASS.bright ? INK_2 : '#FFFFFF');
  ctx.fillText(label, x + w / 2, y + 1);
  ctx.restore();
  return w;
}

/** Double chevron pointing left (d=-1) or right (d=1). */
export function chevrons(ctx, cx, cy, d, color, size = 16, lw = 4, n = 2, a = 1) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = lw;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  for (let i = 0; i < n; i++) {
    ctx.globalAlpha = a * (i ? 0.45 : 1);
    const bx = cx + d * i * size * 0.9;
    ctx.beginPath();
    ctx.moveTo(bx - d * size * 0.55, cy - size);
    ctx.lineTo(bx + d * size * 0.3, cy);
    ctx.lineTo(bx - d * size * 0.55, cy + size);
    ctx.stroke();
  }
  ctx.restore();
}

/** Arrow glyph drawn as vectors, pointing at `ang` radians (0 = right, -PI/2 = up). */
export function arrow(ctx, cx, cy, size, ang, color, lw = 3.2) {
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(ang);
  ctx.strokeStyle = color;
  ctx.lineWidth = lw;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  ctx.moveTo(-size * 0.5, 0);
  ctx.lineTo(size * 0.5, 0);
  ctx.moveTo(size * 0.12, -size * 0.36);
  ctx.lineTo(size * 0.5, 0);
  ctx.lineTo(size * 0.12, size * 0.36);
  ctx.stroke();
  ctx.restore();
}

/** Glow on the screen edge toward a side: 'left' | 'right' | 'behind'. */
export function edgeGlow(ctx, side, color, a, spread = 540) {
  if (a <= 0.002) return;
  let x;
  let y;
  const R = REGION;
  if (side === 'left') { x = R.x; y = R.y + R.h / 2; } else if (side === 'right') { x = R.x + R.w; y = R.y + R.h / 2; } else { x = R.x + R.w / 2; y = R.y + R.h; }
  ctx.save();
  const g = ctx.createRadialGradient(x, y, 0, x, y, spread);
  g.addColorStop(0, hexA(color, 0.42 * a));
  g.addColorStop(0.45, hexA(color, 0.12 * a));
  g.addColorStop(1, hexA(color, 0));
  ctx.fillStyle = g;
  ctx.fillRect(x - spread, y - spread, spread * 2, spread * 2);
  ctx.translate(x, y);
  if (side === 'left' || side === 'right') ctx.scale(0.2, 1.05);
  else ctx.scale(1.4, 0.2);
  const r = spread * 0.95;
  const gb = ctx.createRadialGradient(0, 0, 0, 0, 0, r);
  gb.addColorStop(0, hexA(color, 0.9 * a));
  gb.addColorStop(0.35, hexA(color, 0.4 * a));
  gb.addColorStop(1, hexA(color, 0));
  ctx.fillStyle = gb;
  ctx.fillRect(-r, -r, r * 2, r * 2);
  ctx.restore();
}

/** Expanding haptic ripple rings. */
export function ripples(ctx, x, y, r0, color, t, a = 1, n = 3, period = 1.1) {
  if (a <= 0.002) return;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2.2;
  for (let i = 0; i < n; i++) {
    const ph = ((t / period) + i / n) % 1;
    ctx.globalAlpha = a * (1 - ph) * 0.8;
    ctx.beginPath();
    ctx.arc(x, y, r0 + ph * r0 * 1.6, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.restore();
}

/** Corner brackets around a face (dashed while the face is unknown). */
export function faceBrackets(ctx, f, o) {
  const a = o.a ?? 1;
  if (a <= 0.002) return;
  const s = lerp(1.3, 1, easeOut(a)) * (o.scale ?? 1.3);
  const bw = f.w * s;
  const bh = f.h * s * 1.12;
  const x = f.cx - bw / 2;
  const y = f.cy - bh / 2;
  const L = Math.min(bw, bh) * 0.22;
  ctx.save();
  ctx.globalAlpha *= a;
  ctx.strokeStyle = o.color ?? '#FFFFFF';
  ctx.lineWidth = o.lw ?? 2.6;
  ctx.lineCap = 'round';
  if (o.dashed) {
    ctx.setLineDash([8, 7]);
    ctx.lineDashOffset = -(o.t ?? 0) * 20;
  }
  for (const [cx, cy, dx, dy] of [[x, y, 1, 1], [x + bw, y, -1, 1], [x, y + bh, 1, -1], [x + bw, y + bh, -1, -1]]) {
    ctx.beginPath();
    ctx.moveTo(cx, cy + dy * L);
    ctx.lineTo(cx, cy + dy * 8);
    ctx.quadraticCurveTo(cx, cy, cx + dx * 8, cy);
    ctx.lineTo(cx + dx * L, cy);
    ctx.stroke();
  }
  ctx.restore();
}

/**
 * Daylight focus marks: four solid, rounded corners around a face with a soft white halo, used
 * only while Attune is identifying someone (a name to confirm, a save in progress). They settle
 * in from a little wider as `a` rises; nothing dashes, marches or pulses.
 * o: { a, color, scale (box size vs the face, default 1.28), lw }
 */
export function focusMarks(ctx, f, o) {
  const a = o.a ?? 1;
  if (a <= 0.002) return;
  const s = lerp(1.12, 1, easeOut(a)) * (o.scale ?? 1.28);
  const bw = f.w * s;
  const bh = f.h * s * 1.12;
  const x = f.cx - bw / 2;
  const y = f.cy - bh / 2;
  const L = Math.min(bw, bh) * 0.2;
  const rad = Math.min(14, L * 0.6);
  const corners = [[x, y, 1, 1], [x + bw, y, -1, 1], [x, y + bh, 1, -1], [x + bw, y + bh, -1, -1]];
  const trace = () => {
    for (const [cx, cy, dx, dy] of corners) {
      ctx.beginPath();
      ctx.moveTo(cx, cy + dy * L);
      ctx.lineTo(cx, cy + dy * rad);
      ctx.quadraticCurveTo(cx, cy, cx + dx * rad, cy);
      ctx.lineTo(cx + dx * L, cy);
      ctx.stroke();
    }
  };
  ctx.save();
  ctx.globalAlpha *= a;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.strokeStyle = 'rgba(255,255,255,0.75)';
  ctx.lineWidth = (o.lw ?? 4) + 4;
  trace();
  ctx.strokeStyle = o.color ?? TEAL;
  ctx.lineWidth = o.lw ?? 4;
  trace();
  ctx.restore();
}

/** Pill made of parts: [{icon, color, bg} | {text, font, color, gap} | {key}] . Returns its rect. */
export function pill(ctx, blur, x, y, h, parts, o = {}) {
  const pad = h * 0.38;
  let w = pad;
  for (const p of parts) {
    if (p.icon) w += h * 0.62 + 12;
    else if (p.key) w += keycapWidth(ctx, p.key, h * 0.34) + (p.gap ?? 8);
    else w += textW(ctx, p.text, p.font) + (p.gap ?? 12);
  }
  w += pad - 12;
  const x0 = o.align === 'center' ? x - w / 2 : o.align === 'right' ? x - w : x;
  glass(ctx, blur, x0, y, w, h, h / 2, { alpha: o.a ?? 1, glow: o.glow, tint: o.tint, fill: o.fill, border: o.border });
  ctx.save();
  ctx.globalAlpha *= o.a ?? 1;
  let cx = x0 + pad;
  const xs = [];
  for (const p of parts) {
    xs.push(cx);
    if (p.icon) {
      const s = h * 0.62;
      if (p.bg) {
        ctx.fillStyle = p.bg;
        ctx.beginPath();
        ctx.arc(cx + s / 2, y + h / 2, s * 0.62, 0, Math.PI * 2);
        ctx.fill();
      }
      icon(ctx, p.icon, cx + s * 0.12, y + h / 2 - s * 0.38, s * 0.76, p.color, 2.2);
      cx += s + 12;
    } else if (p.key) {
      cx += keycap(ctx, cx, y + h / 2, p.key, { size: h * 0.34 }) + (p.gap ?? 8);
    } else {
      ctx.font = p.font;
      ctx.textBaseline = 'middle';
      ctx.fillStyle = p.color;
      ctx.fillText(p.text, cx, y + h / 2 + 1);
      cx += textW(ctx, p.text, p.font) + (p.gap ?? 12);
    }
  }
  ctx.restore();
  return { x: x0, y, w, h, xs };
}

// ---------------------------------------------------------------- calm motion
/** True when the viewer asked the OS for reduced motion: glides become cuts, words don't rise. */
export const REDUCED_MOTION = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * One step of a critically damped spring (exact solution, so any dt is stable and it never
 * overshoots or bounces). w is the stiffness in rad/s: it settles in about 4 / w seconds.
 * Returns [x, v].
 */
export function springStep(x, v, target, dt, w) {
  if (!(dt > 0)) return [x, v];
  const d = x - target;
  const e = Math.exp(-w * dt);
  const c = v + w * d;
  return [target + (d + c * dt) * e, (v - w * c * dt) * e];
}

/**
 * Prefix-stable line wrapping for live captions. Lines that are already laid out never reflow
 * when words are added: update() finds the first token that changed and re-wraps only from the
 * start of the line that holds it, so appending words touches just the last line, and a draft
 * revision touches only its own line onward. Tokens: { text, final, br } (br starts a new line).
 * Each token also gets animation times: born (appeared or changed) and firm (turned final).
 */
export function createLineWrap() {
  let toks = [];
  let meta = [];
  let lines = []; // [{ start, end }] token ranges
  let maxW = 0;
  let fontStr = '';
  let spaceW = 0;
  const st = {
    get lines() {
      return lines;
    },
    get tokens() {
      return toks;
    },
    get meta() {
      return meta;
    },
    /** Lay out `tokens` at width `w`; returns the number of lines. */
    update(ctx, tokens, w, f, anim) {
      let d = 0;
      if (w !== maxW || f !== fontStr) {
        lines = [];
        maxW = w;
        fontStr = f;
      } else {
        const n = Math.min(toks.length, tokens.length);
        while (d < n && toks[d].text === tokens[d].text && !!toks[d].br === !!tokens[d].br) d++;
        if (d === toks.length && d === tokens.length) {
          for (let i = 0; i < tokens.length; i++) firmUp(i, tokens[i], anim);
          toks = tokens;
          return lines.length;
        }
      }
      // keep every line that ends before the first change
      let L = lines.findIndex((ln) => d < ln.end);
      if (L < 0) L = Math.max(0, lines.length - 1);
      if (d === 0) L = 0;
      const from = lines[L]?.start ?? 0;
      lines = lines.slice(0, L);
      const next = [];
      for (let i = 0; i < tokens.length; i++) {
        const old = toks[i];
        const m = meta[i];
        if (i < d && m) next.push(m);
        else if (old && m && old.text === tokens[i].text) next.push(m);
        else next.push({ born: old ? anim - 0.1 : anim, firm: tokens[i].final ? anim : null, w: 0 });
      }
      meta = next;
      toks = tokens;
      const space = (spaceW = textW(ctx, ' ', f));
      let cur = null;
      for (let i = from; i < tokens.length; i++) {
        const tw = textW(ctx, tokens[i].text, f);
        meta[i].w = tw;
        if (!cur) cur = { start: i, end: i + 1, w: tw };
        else if (tokens[i].br || cur.w + space + tw > w) {
          lines.push(cur);
          cur = { start: i, end: i + 1, w: tw };
        } else {
          cur.end = i + 1;
          cur.w += space + tw;
        }
      }
      if (cur) lines.push(cur);
      for (let i = 0; i < from; i++) meta[i].w ||= textW(ctx, tokens[i].text, f);
      for (let i = 0; i < tokens.length; i++) firmUp(i, tokens[i], anim);
      return lines.length;
    },
    /** Forget the first k lines (they scrolled away); token indices shift down with them. */
    dropLines(k) {
      if (k <= 0 || !lines.length) return 0;
      k = Math.min(k, lines.length);
      const cut = lines[k - 1].end;
      toks = toks.slice(cut);
      meta = meta.slice(cut);
      lines = lines.slice(k).map((ln) => ({ ...ln, start: ln.start - cut, end: ln.end - cut }));
      return cut;
    },
    reset() {
      toks = [];
      meta = [];
      lines = [];
    },
    /** Calls fn(token, meta, x) for the words of line i, x measured from the line's left edge. */
    eachWord(i, fn) {
      const ln = lines[i];
      if (!ln) return;
      let x = 0;
      for (let k = ln.start; k < ln.end; k++) {
        fn(toks[k], meta[k], x, k);
        x += meta[k].w + spaceW;
      }
    },
  };
  function firmUp(i, t, anim) {
    const m = meta[i];
    if (!m) return;
    if (t.final && m.firm == null) m.firm = anim;
    else if (!t.final) m.firm = null;
  }
  return st;
}

/**
 * Scroll position for a wrapped caption window: the window shows `rows` lines ending at the
 * newest one; when a new line is added the text rolls up one line with a short ease.
 * Returns the fractional index of the top visible line.
 */
export function scrollTo(state, lineCount, rows, dt, rollUp = false) {
  // top-fill: the first line sits on the first row; roll-up: the newest line sits on the last row
  const top = rollUp ? lineCount - rows : Math.max(0, lineCount - rows);
  if (state.top == null || REDUCED_MOTION || top < state.top - 0.5) {
    state.top = top;
    state.v = 0;
  } else {
    [state.top, state.v] = springStep(state.top, state.v ?? 0, top, dt, 22);
    if (Math.abs(state.top - top) < 0.002) {
      state.top = top;
      state.v = 0;
    }
  }
  return state.top;
}
