/*
 * Alert banners, name proposals, the paused state and the status pill (full-colour AR mode).
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-08, P-44 (Daylight look).
 *
 * - Doorbell, door knock and other sounds: a bright paper chip at the top with the sound's icon
 *   on a coloured disc, where it came from, an arrow toward `side` (L / R / B), a soft edge glow
 *   on that side and the A keycap.
 * - Smoke and CO: a large coral card that pulses in the T3 rhythm, a red vignette and edge glow
 *   toward the source, and a haptic chip. Acknowledged alerts turn green, calm down, then fade.
 * - Name proposals for a face that is not in view: "Is this Sam?" with Y / N keycaps.
 * - Paused: a pause chip explains that nothing is recognised.
 * - Cloud captions (P-48): while the wearer has them on, a small calm chip beside the status pill
 *   reads "Cloud captions on" (with "· local captions" while they have fallen back). It never
 *   flashes or pulses and uses no alert colour.
 * - Calm while people talk: the status pill, sound chips and their arrows stand still. Only the
 *   smoke / CO card keeps its T3 flash, a deliberate safety signal.
 * - Nothing jumps: when a toast or an alert arrives or leaves, the others glide to their new
 *   places (critically damped, about 0.25 s) instead of snapping.
 */

import {
  FD, FT, font, clamp, easeOut, hexA, rrect, textW, glass, icon, eqBars,
  logoMark, keycap, arrow, edgeGlow, ripples, pill, dot, PX, REGION, REDUCED_MOTION, springStep,
  INK, INK_2, INK_3, TEAL, SUN, CORAL, LEAF, BLUE, deepen,
} from './hud.js';

const SIDE_WORD = { left: 'Left', right: 'Right', behind: 'Behind', none: 'Nearby' };
const SIDE_ANGLE = { left: Math.PI, right: 0, behind: Math.PI / 2, none: -Math.PI / 2 };
const MOVE_W = 16; // stack glide stiffness (rad/s): about 0.25 s
/** Semantic colours on paper: the lens model's alert colours mapped to the Daylight accents. */
const TONE = {
  '#FF4D4F': CORAL, '#FFC857': SUN, '#FF9F43': '#F28A2E', '#7CC8FF': '#3A95F0', '#8FF3E0': TEAL, '#7CF5D6': TEAL,
  // A-40 everyday sounds, deepened so a white icon reads on paper
  '#FF5A36': '#E5452A', '#FF4D8D': '#E03A78', '#B48CFF': '#8A5CF0', '#FF9EC7': '#EC6FA8',
  '#E8B070': '#C98A3E', '#6EE7A8': '#2FB36E', '#5ED4F5': '#1FA9D6',
};
const tone = (c) => TONE[c] ?? c;
/** White reads on the deeper accents; ink reads better on sunny yellow. */
const iconOn = (c) => (c === SUN ? INK : '#FFFFFF');

/** T3 smoke-alarm rhythm: three beeps (0.5 s on, 0.5 s off) then 1.5 s silence. Returns 0..1. */
export function t3Flash(age) {
  const lt = ((age % 4) + 4) % 4;
  const beep = lt < 0.5 || (lt >= 1 && lt < 1.5) || (lt >= 2 && lt < 2.5);
  const bl = lt % 1;
  return beep ? 0.55 + 0.45 * Math.cos((bl / 0.5) * Math.PI * 0.5) : Math.max(0, 0.35 - (bl - 0.5) * 0.7);
}

/**
 * Where each stacked item (toast, alert, proposal) sits: it glides to its target y; a new one
 * starts right there. Keep one per glasses mode instance (createStack), so offline renders
 * start clean.
 */
export function createStack() {
  const items = new Map();
  return {
    y(key, target, dt, anim) {
      let s = items.get(key);
      if (!s || REDUCED_MOTION) {
        s = { y: target, v: 0 };
        items.set(key, s);
      } else [s.y, s.v] = springStep(s.y, s.v, target, dt, MOVE_W);
      s.seen = anim;
      return s.y;
    },
    sweep(anim) {
      for (const [k, s] of items) if (anim - s.seen > 1) items.delete(k);
    },
  };
}
const fallbackStack = createStack();

// ---------------------------------------------------------------- status pill (top-left of the display)
// The speech glyph is still: it rises or settles (about 0.2 s) only when speech starts or stops,
// held through pauses shorter than 0.6 s, so a busy room does not make it flicker.
const statusSpeech = { on: 0, at: null, t: null };
function speechLevel(on, anim) {
  const s = statusSpeech;
  if (on) s.at = anim;
  const want = s.at != null && anim - s.at >= 0 && anim - s.at < 0.6 ? 1 : 0;
  const dt = s.t == null ? 1 : clamp(anim - s.t, 0, 0.1);
  s.t = anim;
  s.on = REDUCED_MOTION ? want : s.on + (want - s.on) * (1 - Math.exp(-dt * 16));
  return s.on;
}

export function drawStatus(ctx, blur, view, anim, a = 1) {
  const x = REGION.x + 26;
  const y = REGION.y + 22;
  const h = 54;
  const labels = { listening: 'Listening', paused: 'Paused', alert: 'Sound alert', connecting: 'Connecting…' };
  const colors = { listening: LEAF, paused: INK_3, alert: view.activeAlert?.level === 'urgent' ? CORAL : SUN, connecting: SUN };
  const label = labels[view.status];
  const lf = font(560, 19, FT);
  const labelW = Math.max(textW(ctx, 'Listening', lf), textW(ctx, label, lf));
  const barsX = x + 180 + labelW + 14;
  const lockX = barsX + 40;
  const w = lockX + 18 + 22 - x;
  glass(ctx, blur, x, y, w, h, h / 2, { alpha: a * 0.97 });
  ctx.save();
  ctx.globalAlpha *= a;
  logoMark(ctx, x + 30, y + h / 2, 13.5, 1, 1);
  ctx.font = font(680, 23, FD);
  ctx.textBaseline = 'middle';
  ctx.fillStyle = INK;
  ctx.fillText('Attune', x + 54, y + h / 2 + 1);
  ctx.fillStyle = 'rgba(22,32,46,0.14)';
  ctx.fillRect(x + 146, y + 15, 1.5, h - 30);
  if (view.status === 'paused') {
    ctx.fillStyle = INK_3;
    ctx.fillRect(x + 161, y + h / 2 - 6, 3.5, 12);
    ctx.fillRect(x + 168, y + h / 2 - 6, 3.5, 12);
  } else {
    // the status dot sits on a soft halo of its own colour
    ctx.fillStyle = hexA(colors[view.status], 0.2);
    ctx.beginPath();
    ctx.arc(x + 166, y + h / 2, 9, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = colors[view.status];
    ctx.beginPath();
    ctx.arc(x + 166, y + h / 2, 5, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.font = lf;
  ctx.fillStyle = INK_2;
  ctx.fillText(label, x + 180, y + h / 2 + 1);
  const speech = speechLevel(view.status !== 'paused' && !!view.speaking, anim);
  eqBars(ctx, barsX, y + h / 2, INK_3, 0.6, view.status === 'paused' ? 0 : 0.25 + 0.75 * speech, 16);
  icon(ctx, 'lock', lockX, y + h / 2 - 9, 18, INK_3, 2.2);
  ctx.restore();
  if (view.cloud?.on) drawCloudChip(ctx, blur, x + w + 12, y + (h - CLOUD_H) / 2, view.cloud, a);
}

// ---------------------------------------------------------------- cloud captions chip (P-48)
const CLOUD_H = 40;
const CLOUD_FONT = font(600, 17, FT);
const CLOUD_SUB = font(520, 16, FT);
/** Parts reused every frame: only the second one's alpha changes with the state. */
const cloudOn = [
  { icon: 'cloud', color: BLUE, gap: 10 },
  { text: 'Cloud captions on', font: CLOUD_FONT, color: INK_2, gap: 12 },
];
const cloudLocal = [...cloudOn, { text: '· local captions', font: CLOUD_SUB, color: INK_3, gap: 12 }];

function drawCloudChip(ctx, blur, x, y, cloud, a = 1) {
  pill(ctx, blur, x, y, CLOUD_H, cloud.local ? cloudLocal : cloudOn, { a: a * 0.97 });
}

// ---------------------------------------------------------------- alerts
/** A round chip with an arrow toward `side`. Still, unless `nudge` (the smoke / CO card only). */
function directionChip(ctx, cx, cy, r, side, color, anim, a = 1, nudge = false, onColor = false) {
  ctx.save();
  ctx.globalAlpha *= a;
  ctx.fillStyle = onColor ? 'rgba(255,255,255,0.22)' : hexA(color, 0.14);
  ctx.strokeStyle = onColor ? 'rgba(255,255,255,0.7)' : hexA(deepen(color, 0.2), 0.5);
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  const ang = SIDE_ANGLE[side] ?? -Math.PI / 2;
  const off = nudge ? Math.sin(anim * 6) * r * 0.1 : 0;
  arrow(ctx, cx + Math.cos(ang) * off, cy + Math.sin(ang) * off, r * 1.05, ang, onColor ? '#FFFFFF' : deepen(color, 0.3), r * 0.13);
  ctx.restore();
}

function urgentCard(ctx, blur, al, y, anim, dim) {
  const a = al.alpha * dim;
  const flash = al.acked ? 0 : t3Flash(al.age);
  const color = al.acked ? LEAF : CORAL;
  if (!al.acked) {
    ctx.save();
    const R = REGION;
    const cx = R.x + R.w / 2;
    const cy = R.y + R.h / 2;
    const g = ctx.createRadialGradient(cx, cy, R.h * 0.42, cx, cy, R.w * 0.62);
    g.addColorStop(0, 'rgba(255,40,50,0)');
    g.addColorStop(1, `rgba(255,40,50,${0.5 * a * flash})`);
    ctx.fillStyle = g;
    ctx.fillRect(R.x, R.y, R.w, R.h);
    ctx.restore();
    edgeGlow(ctx, al.side, CORAL, a * (0.5 + 0.5 * flash), 640);
  }
  const w = 780;
  const h = 150;
  const x = REGION.x + REGION.w / 2 - w / 2;
  const yy = y + (1 - easeOut(clamp(al.age / 0.3))) * -30;
  // a solid coral (or, acknowledged, green) card: the one panel that is not paper, on purpose
  ctx.save();
  ctx.globalAlpha *= a;
  ctx.save();
  ctx.shadowColor = hexA(color, 0.45 + 0.35 * flash);
  ctx.shadowBlur = (30 + 26 * flash) * PX;
  ctx.shadowOffsetY = 10 * PX;
  rrect(ctx, x, yy, w, h, 36);
  const g = ctx.createLinearGradient(x, yy, x + w, yy + h);
  if (al.acked) {
    g.addColorStop(0, '#2BC47C');
    g.addColorStop(1, '#159A5B');
  } else {
    g.addColorStop(0, '#FF6A4D');
    g.addColorStop(1, '#E8363A');
  }
  ctx.fillStyle = g;
  ctx.fill();
  ctx.restore();
  rrect(ctx, x, yy, w, h, 36);
  const sheen = ctx.createLinearGradient(0, yy, 0, yy + h);
  sheen.addColorStop(0, 'rgba(255,255,255,0.22)');
  sheen.addColorStop(0.5, 'rgba(255,255,255,0)');
  ctx.fillStyle = sheen;
  ctx.fill();
  ctx.strokeStyle = `rgba(255,255,255,${0.35 + 0.45 * flash})`;
  ctx.lineWidth = 2;
  ctx.stroke();
  const icx = x + 82;
  const icy = yy + h / 2;
  if (!al.acked) ripples(ctx, icx, icy, 40, '#FFFFFF', anim, 0.8, 3, 1.0);
  ctx.fillStyle = '#FFFFFF';
  ctx.beginPath();
  ctx.arc(icx, icy, 40, 0, Math.PI * 2);
  ctx.fill();
  icon(ctx, al.acked ? 'check' : al.icon, icx - 22, icy - 23, 44, color, 2.6);
  ctx.textBaseline = 'middle';
  ctx.font = font(740, 46, FD);
  ctx.fillStyle = '#FFFFFF';
  ctx.fillText(al.label, x + 150, yy + 56);
  ctx.font = font(520, 25, FT);
  ctx.fillStyle = 'rgba(255,255,255,0.9)';
  ctx.fillText(al.acked ? al.ackText : al.detail, x + 152, yy + 102);
  // direction block
  directionChip(ctx, x + w - 78, yy + h / 2 - 12, 32, al.side, color, anim, 1, !al.acked, true); // safety: keeps moving
  ctx.font = font(650, 18, FT);
  ctx.textAlign = 'center';
  ctx.fillStyle = '#FFFFFF';
  ctx.fillText(SIDE_WORD[al.side] ?? '', x + w - 78, yy + h / 2 + 40);
  ctx.textAlign = 'left';
  ctx.restore();
  // haptic + acknowledge chip
  const parts = al.acked
    ? [{ icon: 'check', color: '#FFFFFF', bg: LEAF }, { text: al.ackText, font: font(600, 20, FT), color: INK }]
    : [
      { icon: 'wave', color: '#FFFFFF', bg: CORAL },
      { text: 'Haptic alert on', font: font(600, 20, FT), color: INK, gap: 18 },
      { key: 'A', gap: 8 },
      { text: 'acknowledge', font: font(480, 19, FT), color: INK_3 },
    ];
  pill(ctx, blur, REGION.x + REGION.w / 2, yy + h + 18, 46, parts, { a: a * clamp((al.age - 0.35) / 0.3), align: 'center' });
  return { x, y: yy, w, h: h + 70 };
}

function chipAlert(ctx, blur, al, y, anim, dim) {
  const a = al.alpha * dim;
  const color = al.acked ? LEAF : tone(al.color);
  if (!al.acked && !al.watch) edgeGlow(ctx, al.side, color, a * 0.5, 560); // steady
  const h = al.watch ? 56 : 72;
  const parts = [
    { icon: al.acked ? 'check' : al.icon, color: al.acked ? '#FFFFFF' : iconOn(color), bg: color },
    { text: al.watch ? `Watching for ${al.label.toLowerCase()}` : al.label, font: font(680, al.watch ? 24 : 30, FD), color: INK },
  ];
  if (!al.watch) parts.push({ text: al.acked ? al.ackText : al.detail, font: font(480, 24, FT), color: INK_2 });
  if (al.count > 1 && !al.acked) parts.push({ text: `×${al.count}`, font: font(720, 24, FT), color: deepen(color, 0.3) });
  let slot = -1;
  if (!al.acked && !al.watch) {
    slot = parts.length;
    parts.push({ text: ' ', font: font(400, 24, FT), color: 'rgba(0,0,0,0)', gap: 52 });
    parts.push({ key: 'A', gap: 12 });
  }
  const yy = y + (1 - easeOut(clamp(al.age / 0.3))) * -24;
  const r = pill(ctx, blur, REGION.x + REGION.w / 2, yy, h, parts, { a, align: 'center', glow: color });
  if (slot >= 0) {
    directionChip(ctx, (r.xs[slot] + r.xs[slot + 1] - 12) / 2 + 2, yy + h / 2, 21, al.side, color, anim, a);
    if (al.age < 1.4) ripples(ctx, r.x + h * 0.38 + h * 0.31, yy + h / 2, 24, color, al.age, a * (1 - al.age / 1.4), 2, 0.7);
  }
  return { ...r, h: h + 8 };
}

/** Draw every active alert; returns the rects they cover (obstacles for the bubbles). */
export function drawAlerts(ctx, blur, view, anim, topY = REGION.y + 88, stack = fallbackStack) {
  const dim = view.paused ? 0.6 : 1;
  const dt = view.dt ?? 1 / 60;
  const rects = [];
  let y = topY;
  for (const al of view.alerts) {
    const urgent = al.level === 'urgent' && !al.watch;
    const at = stack.y(`a${al.id}`, y, dt, anim);
    const r = urgent ? urgentCard(ctx, blur, al, at + 14, anim, dim) : chipAlert(ctx, blur, al, at, anim, dim);
    rects.push(r);
    y += (urgent ? 14 : 0) + r.h + 16;
  }
  // proposals whose face is not in view
  for (const p of view.proposals) {
    const confirmed = p.state === 'confirmed';
    const parts = p.state === 'proposed'
      ? [
        { icon: 'userplus', color: '#FFFFFF', bg: TEAL },
        { text: `Is this ${p.name}?`, font: font(660, 26, FD), color: INK, gap: 18 },
        { key: 'Y', gap: 6 }, { text: 'yes', font: font(480, 20, FT), color: INK_3, gap: 14 },
        { key: 'N', gap: 6 }, { text: 'no', font: font(480, 20, FT), color: INK_3 },
      ]
      : [
        { icon: confirmed ? 'check' : 'cross', color: '#FFFFFF', bg: confirmed ? LEAF : INK_3 },
        { text: confirmed ? `${p.name} added to your people` : `Not ${p.name}`, font: font(660, 24, FD), color: INK },
      ];
    const at = stack.y(`p${p.id}`, y, dt, anim);
    const r = pill(ctx, blur, REGION.x + REGION.w / 2, at, 58, parts, { a: p.alpha, align: 'center', glow: confirmed ? LEAF : TEAL });
    rects.push(r);
    y += 74;
  }
  stack.sweep(anim);
  return rects;
}

// ---------------------------------------------------------------- toasts (top centre)
export function drawToasts(ctx, blur, view, anim, topY = REGION.y + 22, stack = fallbackStack) {
  const dt = view.dt ?? 1 / 60;
  let y = topY;
  const rects = [];
  for (const t of view.toasts) {
    const a = clamp(t.age / 0.35) * clamp((3.4 - t.age) / 0.4);
    if (a <= 0) continue;
    let parts;
    if (t.kind === 'learned') {
      // a person's own colour on the disc, with an ink icon (their pastel is too light for white)
      parts = [
        { icon: 'userplus', color: INK, bg: t.color },
        { text: `${t.text} ${t.enrolled ? 'saved to your people' : 'added to your people'}`, font: font(660, 24, FD), color: INK },
      ];
      if (t.relation) parts.push({ text: `·  ${t.relation}`, font: font(480, 21, FT), color: INK_2 });
    } else {
      const bg = t.color ? tone(t.color) : LEAF;
      parts = [{ icon: t.icon ?? 'check', color: iconOn(bg), bg }, { text: t.text, font: font(640, 23, FD), color: INK }];
    }
    const at = stack.y(`t${t.t}${t.text}`, y, dt, anim);
    ctx.save();
    ctx.translate(0, (1 - easeOut(a)) * -18);
    rects.push(pill(ctx, blur, REGION.x + REGION.w / 2, at, 56, parts, { a, align: 'center', glow: t.kind === 'learned' ? t.color : LEAF }));
    ctx.restore();
    y += 66;
  }
  return rects;
}

// ---------------------------------------------------------------- paused chip
export function drawPaused(ctx, blur, view, anim) {
  if (!view.paused) return;
  const a = clamp(view.pausedAge / 0.3);
  const y = REGION.y + REGION.h / 2 - 36;
  const parts = [
    { icon: 'pause', color: '#FFFFFF', bg: INK_3 },
    { text: 'Paused', font: font(700, 30, FD), color: INK, gap: 14 },
    { text: 'Nothing is being recognised', font: font(480, 23, FT), color: INK_2, gap: 22 },
    { key: 'P', gap: 6 },
    { text: 'resume', font: font(480, 20, FT), color: INK_3 },
  ];
  pill(ctx, blur, REGION.x + REGION.w / 2, y, 72, parts, { a, align: 'center' });
}

export { dot, rrect, keycap };
