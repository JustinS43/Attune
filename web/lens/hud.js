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

export const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
export const lerp = (a, b, t) => a + (b - a) * t;
export const easeOut = (t) => 1 - Math.pow(1 - clamp(t), 3);
export const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
export const easeBack = (t, s = 1.4) => 1 + (s + 1) * Math.pow(t - 1, 3) + s * Math.pow(t - 1, 2);
export const font = (w, px, fam = FT) => `${w} ${px}px ${fam}`;
/** Canvas pixels per design unit; shadow blur is in canvas pixels, so it is scaled by this. */
export let PX = 1;
export function setPixelScale(k) {
  PX = k;
}

/** The page's animation clock in seconds (performance.now plus any time settle() stepped ahead). */
let skewMs = 0;
export const nowS = () => (performance.now() + skewMs) / 1000;
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
  ctx.save();
  ctx.globalAlpha *= a;
  if (o.shadow !== 0) {
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
  ctx.fillStyle = o.tint ?? 'rgba(14,16,22,0.52)';
  ctx.fillRect(x, y, w, h);
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
  cross: [P('M6.5 6.5l11 11M17.5 6.5l-11 11')],
  globe: [P('M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0'), P('M3 12h18M12 3c3.2 3.3 3.2 14.7 0 18M12 3c-3.2 3.3-3.2 14.7 0 18')],
  userplus: [P('M9 8m-3.3 0a3.3 3.3 0 1 0 6.6 0a3.3 3.3 0 1 0-6.6 0'), P('M2.8 20.5c0-3.5 2.8-6.2 6.2-6.2s6.2 2.7 6.2 6.2'), P('M19 7.5v6M16 10.5h6')],
  wave: [P('M4 10v4M8 7v10M12 4v16M16 8v8M20 10.5v3')],
  pause: [P('M8.5 5.5v13M15.5 5.5v13')],
  warn: [P('M12 3.2l9.4 16.4H2.6z'), P('M12 9.5v4.8M12 17.2v.2')],
  sound: [P('M4 9.5h3.5L12 5.5v13l-4.5-4H4z'), P('M15.5 9a4.2 4.2 0 0 1 0 6M18.2 6.4a8 8 0 0 1 0 11.2')],
  trash: [P('M4.5 7h15M9.5 7V4.8h5V7M6.5 7l1 12.5h9l1-12.5')],
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
  g.addColorStop(0, MINT);
  g.addColorStop(1, SKY);
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

/** Coloured person dot with a soft glow. */
export function dot(ctx, x, y, r, color, glowPx = 12) {
  ctx.save();
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
  ctx.fillStyle = o.fill ?? 'rgba(255,255,255,0.12)';
  ctx.fill();
  ctx.strokeStyle = o.stroke ?? 'rgba(255,255,255,0.32)';
  ctx.lineWidth = 1.2;
  ctx.stroke();
  ctx.font = f;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = o.color ?? '#FFFFFF';
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
  if (side === 'left') { x = 0; y = H / 2; } else if (side === 'right') { x = W; y = H / 2; } else { x = W / 2; y = H; }
  ctx.save();
  const g = ctx.createRadialGradient(x, y, 0, x, y, spread);
  g.addColorStop(0, hexA(color, 0.42 * a));
  g.addColorStop(0.45, hexA(color, 0.12 * a));
  g.addColorStop(1, hexA(color, 0));
  ctx.fillStyle = g;
  ctx.fillRect(x - spread, y - spread, spread * 2, spread * 2);
  ctx.translate(x, y);
  if (x === 0 || x === W) ctx.scale(0.2, 1.05);
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
  glass(ctx, blur, x0, y, w, h, h / 2, { alpha: o.a ?? 1, glow: o.glow, tint: o.tint, border: o.border });
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
