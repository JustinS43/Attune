/* Full-colour focused layout: the main voice below center, other voices by side. */

import { W, H, FD, FT, font, clamp, glass, setRegion, setGlassStyle, regionOutline, INK, INK_2, PAPER, MIST, deepen } from '../hud.js';
import { drawAlerts, drawStatus, drawToasts, drawPaused, createStack } from '../alerts.js';
import { ORION_REGION } from './color.js';
import { focusedSlots } from './focused-layout.mjs';

const BODY = font(450, 30, FT);
const BODY_SMALL = font(430, 22, FT);
const NAME = font(680, 25, FD);
const NAME_SMALL = font(650, 18, FD);

function lines(ctx, value, width, maxRows) {
  const words = String(value || '').split(/\s+/).filter(Boolean);
  const rows = [];
  let line = '';
  for (const word of words) {
    const next = line ? `${line} ${word}` : word;
    if (line && ctx.measureText(next).width > width) {
      rows.push(line);
      line = word;
    } else line = next;
  }
  if (line) rows.push(line);
  if (rows.length <= maxRows) return rows;
  const visible = rows.slice(-maxRows);
  while (visible[0] && ctx.measureText(`…${visible[0]}`).width > width) {
    visible[0] = visible[0].slice(1);
  }
  visible[0] = `…${visible[0]}`;
  return visible;
}

function drawBubble(ctx, slot, blur, labelsOn) {
  const b = slot.bubble;
  const main = slot.size === 'main';
  const showName = !labelsOn || !b.face || b.face.ghost;
  const pad = main ? 26 : 16;
  const color = deepen(b.color || '#8AA6BA', 0.25);
  ctx.save();
  ctx.globalAlpha *= clamp(b.alpha) * (main ? 1 : 0.88);
  glass(ctx, blur, slot.x, slot.y, slot.w, slot.h, main ? 24 : 18, {
    fill: b.known ? PAPER : MIST,
    glow: main ? b.color : null,
  });
  ctx.fillStyle = color;
  ctx.fillRect(slot.x + pad, slot.y + (main ? 18 : 13), main ? 35 : 25, 4);
  let textY = slot.y + (main ? 47 : 34);
  if (showName) {
    ctx.font = main ? NAME : NAME_SMALL;
    ctx.textBaseline = 'top';
    ctx.fillStyle = color;
    ctx.fillText(b.name || 'Someone', slot.x + pad, textY, slot.w - 2 * pad);
    textY += main ? 40 : 25;
  }
  ctx.font = main ? BODY : BODY_SMALL;
  ctx.textBaseline = 'top';
  ctx.fillStyle = INK;
  const text = b.pending ? b.orig : b.text;
  const rowH = main ? 39 : 28;
  const maxRows = Math.max(1, Math.floor((slot.y + slot.h - pad - textY) / rowH));
  for (const row of lines(ctx, text, slot.w - pad * 2, maxRows)) {
    ctx.fillText(row, slot.x + pad, textY, slot.w - pad * 2);
    textY += rowH;
  }
  ctx.restore();
}

function drawFaceLabel(ctx, face, blur, region) {
  if (face.ghost || face.tiny) return;
  const label = face.proposal?.state === 'proposed' ? `${face.proposal.name}?` : face.label;
  ctx.font = NAME_SMALL;
  const w = Math.min(260, ctx.measureText(label).width + 34);
  const x = clamp(face.cx - w / 2, region.x + 12, region.x + region.w - w - 12);
  const y = Math.max(region.y + 12, face.top - 52);
  ctx.save();
  glass(ctx, blur, x, y, w, 38, 14, { fill: face.known ? PAPER : MIST });
  ctx.textBaseline = 'middle';
  ctx.fillStyle = INK_2;
  ctx.fillText(label, x + 17, y + 20, w - 34);
  ctx.restore();
}

function drawYou(ctx, slot, blur) {
  const b = slot.bubble;
  const pad = 18;
  ctx.save();
  ctx.globalAlpha *= clamp(b.alpha);
  glass(ctx, blur, slot.x, slot.y, slot.w, slot.h, 18, { fill: PAPER });
  ctx.font = NAME_SMALL;
  ctx.textBaseline = 'middle';
  ctx.fillStyle = INK_2;
  ctx.fillText('You', slot.x + pad, slot.y + slot.h / 2);
  ctx.font = BODY_SMALL;
  ctx.fillStyle = INK;
  ctx.fillText(b.text || b.orig || '', slot.x + pad + 58, slot.y + slot.h / 2, slot.w - 2 * pad - 58);
  ctx.restore();
}

export function createFocusedMode() {
  const stack = createStack();
  return {
    id: 'focused',
    name: 'Focused captions',
    device: 'Binocular · bottom-third layout',
    blur: true,
    region: ORION_REGION,
    render(ctx, view, env) {
      const region = ORION_REGION;
      setRegion(region);
      setGlassStyle('bright');
      ctx.save();
      ctx.beginPath();
      ctx.rect(region.x, region.y, region.w, region.h);
      ctx.clip();
      if (env.labelsOn) for (const face of view.faces) drawFaceLabel(ctx, face, env.blur, region);
      const slots = focusedSlots(view.feed, region, W, view.you);
      if (slots.center) drawBubble(ctx, slots.center, env.blur, env.labelsOn);
      for (const slot of slots.sides) drawBubble(ctx, slot, env.blur, env.labelsOn);
      if (slots.you) drawYou(ctx, slots.you, env.blur);
      view.save?.drawColor(ctx, env, null);
      const toastRects = drawToasts(ctx, env.blur, view, env.anim, region.y + 88, stack);
      drawAlerts(ctx, env.blur, view, env.anim, region.y + 88 + toastRects.length * 66, stack);
      drawPaused(ctx, env.blur, view, env.anim);
      drawStatus(ctx, env.blur, view, env.anim);
      ctx.restore();
      if (env.chrome) regionOutline(ctx, region, 40);
      setGlassStyle('solid');
      setRegion({ x: 0, y: 0, w: W, h: H });
    },
  };
}
