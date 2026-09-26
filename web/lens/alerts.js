/*
 * Alert banners, name proposals, the paused state and the status pill (full-colour AR mode).
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-08.
 *
 * - Doorbell and other sounds: a glass chip at the top with the sound's icon, where it came
 *   from, an arrow toward `side` (L / R / B), an edge glow on that side and the A keycap.
 * - Smoke and CO: a large red card that pulses in the T3 rhythm, a red vignette and edge glow
 *   toward the source, and a haptic chip. Acknowledged alerts calm down, then fade.
 * - Name proposals for a face that is not in view: "Is this Sam?" with Y / N keycaps.
 * - Paused: the video dims (lens.css) and a pause chip explains that nothing is recognised.
 * - Calm while people talk: the status pill, sound chips and their arrows stand still. Only the
 *   smoke / CO card keeps its T3 flash, a deliberate safety signal.
 */

import {
  FD, FT, MINT, AMBER, RED, ACCENT, font, clamp, easeOut, hexA, rrect, textW, glass, icon, eqBars,
  logoMark, keycap, arrow, edgeGlow, ripples, pill, dot, PX, REGION, REDUCED_MOTION,
} from './hud.js';

const SIDE_WORD = { left: 'Left', right: 'Right', behind: 'Behind', none: 'Nearby' };
const SIDE_ANGLE = { left: Math.PI, right: 0, behind: Math.PI / 2, none: -Math.PI / 2 };

/** T3 smoke-alarm rhythm: three beeps (0.5 s on, 0.5 s off) then 1.5 s silence. Returns 0..1. */
export function t3Flash(age) {
  const lt = ((age % 4) + 4) % 4;
  const beep = lt < 0.5 || (lt >= 1 && lt < 1.5) || (lt >= 2 && lt < 2.5);
  const bl = lt % 1;
  return beep ? 0.55 + 0.45 * Math.cos((bl / 0.5) * Math.PI * 0.5) : Math.max(0, 0.35 - (bl - 0.5) * 0.7);
}

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
  const colors = { listening: MINT, paused: 'rgba(255,255,255,0.55)', alert: view.activeAlert?.level === 'urgent' ? RED : AMBER, connecting: AMBER };
  const label = labels[view.status];
  const lf = font(500, 19, FT);
  const labelW = Math.max(textW(ctx, 'Listening', lf), textW(ctx, label, lf));
  const barsX = x + 180 + labelW + 14;
  const lockX = barsX + 40;
  const w = lockX + 18 + 22 - x;
  glass(ctx, blur, x, y, w, h, h / 2, { alpha: a * 0.96, tint: 'rgba(14,16,22,0.44)' });
  ctx.save();
  ctx.globalAlpha *= a;
  logoMark(ctx, x + 30, y + h / 2, 13.5, 1, 1);
  ctx.font = font(640, 23, FD);
  ctx.textBaseline = 'middle';
  ctx.fillStyle = '#FFFFFF';
  ctx.fillText('Attune', x + 54, y + h / 2 + 1);
  ctx.fillStyle = 'rgba(255,255,255,0.22)';
  ctx.fillRect(x + 146, y + 15, 1.5, h - 30);
  if (view.status === 'paused') {
    ctx.fillStyle = 'rgba(255,255,255,0.7)';
    ctx.fillRect(x + 161, y + h / 2 - 6, 3.5, 12);
    ctx.fillRect(x + 168, y + h / 2 - 6, 3.5, 12);
  } else {
    ctx.fillStyle = colors[view.status];
    ctx.beginPath();
    ctx.arc(x + 166, y + h / 2, 5, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.font = lf;
  ctx.fillStyle = 'rgba(255,255,255,0.86)';
  ctx.fillText(label, x + 180, y + h / 2 + 1);
  const speech = speechLevel(view.status !== 'paused' && !!view.speaking, anim);
  eqBars(ctx, barsX, y + h / 2, 'rgba(255,255,255,0.8)', 0.6, view.status === 'paused' ? 0 : 0.25 + 0.75 * speech, 16);
  icon(ctx, 'lock', lockX, y + h / 2 - 9, 18, 'rgba(255,255,255,0.7)', 2.2);
  ctx.restore();
}

// ---------------------------------------------------------------- alerts
/** A round chip with an arrow toward `side`. Still, unless `nudge` (the smoke / CO card only). */
function directionChip(ctx, cx, cy, r, side, color, anim, a = 1, nudge = false) {
  ctx.save();
  ctx.globalAlpha *= a;
  ctx.fillStyle = hexA(color, 0.16);
  ctx.strokeStyle = hexA(color, 0.6);
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  const ang = SIDE_ANGLE[side] ?? -Math.PI / 2;
  const off = nudge ? Math.sin(anim * 6) * r * 0.1 : 0;
  arrow(ctx, cx + Math.cos(ang) * off, cy + Math.sin(ang) * off, r * 1.05, ang, color, r * 0.13);
  ctx.restore();
}

function urgentCard(ctx, blur, al, y, anim, dim) {
  const a = al.alpha * dim;
  const flash = al.acked ? 0 : t3Flash(al.age);
  const color = al.acked ? MINT : al.color;
  if (!al.acked) {
    ctx.save();
    const R = REGION;
    const cx = R.x + R.w / 2;
    const cy = R.y + R.h / 2;
    const g = ctx.createRadialGradient(cx, cy, R.h * 0.42, cx, cy, R.w * 0.62);
    g.addColorStop(0, 'rgba(255,40,50,0)');
    g.addColorStop(1, `rgba(255,40,50,${0.55 * a * flash})`);
    ctx.fillStyle = g;
    ctx.fillRect(R.x, R.y, R.w, R.h);
    ctx.restore();
    edgeGlow(ctx, al.side, RED, a * (0.5 + 0.5 * flash), 640);
  }
  const w = 780;
  const h = 150;
  const x = REGION.x + REGION.w / 2 - w / 2;
  const yy = y + (1 - easeOut(clamp(al.age / 0.3))) * -30;
  glass(ctx, blur, x, yy, w, h, 36, {
    alpha: a, glow: color, tint: al.acked ? 'rgba(12,20,18,0.58)' : 'rgba(28,10,12,0.6)',
    border: hexA(color, al.acked ? 0.45 : 0.35 + 0.5 * flash),
  });
  ctx.save();
  ctx.globalAlpha *= a;
  const icx = x + 82;
  const icy = yy + h / 2;
  if (!al.acked) ripples(ctx, icx, icy, 40, RED, anim, 1, 3, 1.0);
  ctx.shadowColor = color;
  ctx.shadowBlur = 26 * flash * PX;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(icx, icy, 40, 0, Math.PI * 2);
  ctx.fill();
  ctx.shadowBlur = 0;
  icon(ctx, al.acked ? 'check' : al.icon, icx - 22, icy - 23, 44, al.acked ? '#0B1A17' : '#FFFFFF', 2.4);
  ctx.textBaseline = 'middle';
  ctx.font = font(720, 46, FD);
  ctx.fillStyle = '#FFFFFF';
  ctx.fillText(al.label, x + 150, yy + 56);
  ctx.font = font(480, 25, FT);
  ctx.fillStyle = 'rgba(255,255,255,0.8)';
  ctx.fillText(al.acked ? 'Acknowledged' : al.detail, x + 152, yy + 102);
  // direction block
  directionChip(ctx, x + w - 78, yy + h / 2 - 12, 32, al.side, color, anim, 1, !al.acked); // safety: keeps moving
  ctx.font = font(600, 18, FT);
  ctx.textAlign = 'center';
  ctx.fillStyle = hexA(color, 0.95);
  ctx.fillText(SIDE_WORD[al.side] ?? '', x + w - 78, yy + h / 2 + 40);
  ctx.textAlign = 'left';
  ctx.restore();
  // haptic + acknowledge chip
  const parts = al.acked
    ? [{ icon: 'check', color: MINT }, { text: 'Acknowledged', font: font(560, 20, FT), color: 'rgba(255,255,255,0.9)' }]
    : [
      { icon: 'wave', color: RED },
      { text: 'Haptic alert on', font: font(560, 20, FT), color: 'rgba(255,255,255,0.9)', gap: 18 },
      { key: 'A', gap: 8 },
      { text: 'acknowledge', font: font(450, 19, FT), color: 'rgba(255,255,255,0.62)' },
    ];
  pill(ctx, blur, REGION.x + REGION.w / 2, yy + h + 18, 46, parts, { a: a * clamp((al.age - 0.35) / 0.3), align: 'center' });
  return { x, y: yy, w, h: h + 70 };
}

function chipAlert(ctx, blur, al, y, anim, dim) {
  const a = al.alpha * dim;
  const color = al.acked ? MINT : al.color;
  if (!al.acked && !al.watch) edgeGlow(ctx, al.side, color, a * 0.6, 560); // steady
  const h = al.watch ? 56 : 72;
  const parts = [
    { icon: al.acked ? 'check' : al.icon, color: '#141414', bg: color },
    { text: al.watch ? `Watching for ${al.label.toLowerCase()}` : al.label, font: font(660, al.watch ? 24 : 30, FD), color: '#FFFFFF' },
  ];
  if (!al.watch) parts.push({ text: al.acked ? 'Acknowledged' : al.detail, font: font(450, 24, FT), color: 'rgba(255,255,255,0.68)' });
  if (al.count > 1 && !al.acked) parts.push({ text: `×${al.count}`, font: font(700, 24, FT), color });
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
export function drawAlerts(ctx, blur, view, anim, topY = REGION.y + 88) {
  const dim = view.paused ? 0.6 : 1;
  const rects = [];
  let y = topY;
  for (const al of view.alerts) {
    const r = al.level === 'urgent' && !al.watch ? urgentCard(ctx, blur, al, y + 14, anim, dim) : chipAlert(ctx, blur, al, y, anim, dim);
    rects.push(r);
    y = r.y + r.h + 16;
  }
  // proposals whose face is not in view
  for (const p of view.proposals) {
    const confirmed = p.state === 'confirmed';
    const parts = p.state === 'proposed'
      ? [
        { icon: 'userplus', color: '#0B1A17', bg: ACCENT },
        { text: `Is this ${p.name}?`, font: font(620, 26, FD), color: '#FFFFFF', gap: 18 },
        { key: 'Y', gap: 6 }, { text: 'yes', font: font(450, 20, FT), color: 'rgba(255,255,255,0.6)', gap: 14 },
        { key: 'N', gap: 6 }, { text: 'no', font: font(450, 20, FT), color: 'rgba(255,255,255,0.6)' },
      ]
      : [
        { icon: confirmed ? 'check' : 'cross', color: '#0B1A17', bg: confirmed ? MINT : 'rgba(255,255,255,0.7)' },
        { text: confirmed ? `${p.name} added to your people` : `Not ${p.name}`, font: font(620, 24, FD), color: '#FFFFFF' },
      ];
    const r = pill(ctx, blur, REGION.x + REGION.w / 2, y, 58, parts, { a: p.alpha, align: 'center', glow: ACCENT });
    rects.push(r);
    y += 74;
  }
  return rects;
}

// ---------------------------------------------------------------- toasts (top centre)
export function drawToasts(ctx, blur, view, anim, topY = REGION.y + 22) {
  let y = topY;
  const rects = [];
  for (const t of view.toasts) {
    const a = clamp(t.age / 0.35) * clamp((3.4 - t.age) / 0.4);
    if (a <= 0) continue;
    let parts;
    if (t.kind === 'learned') {
      parts = [
        { icon: 'userplus', color: '#0B1A17', bg: t.color },
        { text: `${t.text} ${t.enrolled ? 'saved to your people' : 'added to your people'}`, font: font(620, 24, FD), color: '#FFFFFF' },
      ];
      if (t.relation) parts.push({ text: `·  ${t.relation}`, font: font(430, 21, FT), color: 'rgba(255,255,255,0.62)' });
    } else {
      parts = [{ icon: t.icon ?? 'check', color: '#0B1A17', bg: t.color ?? MINT }, { text: t.text, font: font(600, 23, FD), color: '#FFFFFF' }];
    }
    ctx.save();
    ctx.translate(0, (1 - easeOut(a)) * -18);
    rects.push(pill(ctx, blur, REGION.x + REGION.w / 2, y, 56, parts, { a, align: 'center', glow: t.color ?? MINT }));
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
    { icon: 'pause', color: '#FFFFFF', bg: 'rgba(255,255,255,0.18)' },
    { text: 'Paused', font: font(680, 30, FD), color: '#FFFFFF', gap: 14 },
    { text: 'Nothing is being recognised', font: font(450, 23, FT), color: 'rgba(255,255,255,0.7)', gap: 22 },
    { key: 'P', gap: 6 },
    { text: 'resume', font: font(450, 20, FT), color: 'rgba(255,255,255,0.6)' },
  ];
  pill(ctx, blur, REGION.x + REGION.w / 2, y, 72, parts, { a, align: 'center' });
}

export { dot, rrect, keycap };
