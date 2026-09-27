/* Stable captions for the full-colour lens: fixed slots and face name tags. */

import { FD, FT, ACCENT, REGION, REDUCED_MOTION, clamp, font, glass, textW, faceBrackets, createLineWrap } from './hud.js';

const NAME_FONT = font(620, 24, FD);
const HEADER_FONT = font(620, 21, FD);
const PRIMARY_FONT = font(450, 31, FT);
const SECONDARY_FONT = font(450, 22, FT);
const FOCUS_HOLD = 0.55;
const UNRESOLVED_HOLD = 2.2;
const area = () => REGION;

function slot(which) {
  const r = area();
  const mid = r.x + r.w / 2;
  if (which === 'center') return {x: mid - 340, y: r.y + r.h - 210, w: 680, h: 150};
  if (which === 'left') return {x: r.x + 18, y: r.y + r.h - 186, w: 300, h: 126};
  return {x: r.x + r.w - 318, y: r.y + r.h - 186, w: 300, h: 126};
}

function sideOf(b) {
  if (b.side === 'left' || b.side === 'right') return b.side;
  if (b.face) return b.face.cx < area().x + area().w / 2 ? 'left' : 'right';
  return b.dir?.side === 'left' || b.dir?.side === 'right' ? b.dir.side : 'behind';
}

function fitText(ctx, label, width) {
  if (textW(ctx, label, ctx.font) <= width) return label;
  let text = label;
  while (text.length > 1 && textW(ctx, `${text}…`, ctx.font) > width) text = text.slice(0, -1);
  return `${text}…`;
}

export function createBubbleLayer() {
  const cards = new Map(); // face key -> tag, also used by the save animation
  const wraps = new Map(); // stable line wrap per speaker
  const unresolved = new Map(); // an offscreen sound with no usable transcript
  const visible = [];
  let focus = null;
  let candidate = null;
  let candidateSince = 0;
  let lastPrimary = null;

  function drawTag(ctx, blur, c) {
    if (c.a < 0.01) return;
    glass(ctx, blur, c.x, c.y, c.w, c.h, 20, {alpha: c.a * 0.9, glow: c.color});
    ctx.save();
    ctx.globalAlpha *= c.a;
    ctx.font = NAME_FONT;
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#FFFFFF';
    ctx.fillText(fitText(ctx, c.label, c.w - 32), c.x + 16, c.y + c.h / 2);
    ctx.restore();
    visible.push({...c});
  }

  function updateTag(ctx, blur, face, now, dim) {
    const label = face.proposal?.state === 'proposed' ? `${face.proposal.name}?` : face.label;
    ctx.font = NAME_FONT;
    const r = area();
    const w = Math.min(290, Math.max(112, textW(ctx, label, NAME_FONT) + 34));
    const x = clamp(face.cx - w / 2, r.x + 12, r.x + r.w - w - 12);
    const y = clamp(face.top - 66, r.y + 86, r.y + r.h - 300);
    let c = cards.get(face.key);
    if (!c) c = {key: face.key, kind: 'tag', x, y, w, h: 46, a: 0, seen: now};
    if (Math.abs(x - c.x) > 9) c.x = x;
    if (Math.abs(y - c.y) > 9) c.y = y;
    c.w = w;
    c.label = label;
    c.color = face.proposal?.state === 'proposed' ? ACCENT : face.color;
    c.seen = now;
    c.a = REDUCED_MOTION ? dim : Math.min(dim, c.a + 0.18);
    cards.set(face.key, c);
    drawTag(ctx, blur, c);
  }

  function drawCaption(ctx, blur, b, which, now, dim) {
    const r = slot(which);
    const primary = which === 'center';
    const alpha = b.alpha * dim * (primary ? 1 : 0.68);
    if (alpha <= 0.01) return;
    glass(ctx, blur, r.x, r.y, r.w, r.h, 21, {alpha, glow: b.color});
    ctx.save();
    ctx.globalAlpha *= alpha;
    const offscreen = !b.face && b.kind !== 'you' && b.kind !== 'you_typed';
    const self = b.kind === 'you' || b.kind === 'you_typed';
    let bodyY = r.y + (primary ? 35 : 30);
    if (offscreen || self) {
      const side = sideOf(b);
      const arrow = offscreen ? side === 'left' ? '← ' : side === 'right' ? '→ ' : side === 'behind' ? '↓ ' : '' : '';
      ctx.font = HEADER_FONT;
      ctx.textBaseline = 'middle';
      ctx.fillStyle = b.color || '#FFFFFF';
      ctx.fillText(fitText(ctx, `${arrow}${self ? 'You' : b.name || 'Someone'}`, r.w - 40), r.x + 20, r.y + 27);
      bodyY += 25;
    }
    const f = primary ? PRIMARY_FONT : SECONDARY_FONT;
    let wrap = wraps.get(b.key);
    if (!wrap || wrap.id !== b.id) {
      wrap = {id: b.id, lines: createLineWrap()};
      wraps.set(b.key, wrap);
    }
    wrap.lines.update(ctx, b.tokens, r.w - 40, f, now);
    const start = Math.max(0, wrap.lines.lines.length - 2);
    ctx.font = f;
    ctx.textBaseline = 'middle';
    for (let row = start; row < wrap.lines.lines.length; row++) {
      wrap.lines.eachWord(row, (word, meta, dx) => {
        ctx.fillStyle = word.final ? '#FFFFFF' : 'rgba(255,255,255,0.72)';
        ctx.fillText(word.text, r.x + 20 + dx, bodyY + (row - start) * (primary ? 38 : 29));
      });
    }
    if (!b.tokens.length && b.orig) {
      ctx.fillStyle = 'rgba(255,255,255,0.7)';
      ctx.fillText(fitText(ctx, b.orig, r.w - 40), r.x + 20, bodyY);
    }
    ctx.restore();
    visible.push({...r, a: alpha, kind: 'bubble', key: b.key});
  }

  function drawUnresolved(ctx, blur, item, side, now, dim) {
    const alpha = clamp((UNRESOLVED_HOLD + 0.5 - (now - item.first)) / 0.5) * 0.4 * dim;
    if (alpha <= 0) return;
    const r = slot(side);
    glass(ctx, blur, r.x, r.y, r.w, r.h, 21, {alpha});
    ctx.save();
    ctx.globalAlpha *= alpha;
    ctx.fillStyle = '#FFFFFF';
    ctx.font = HEADER_FONT;
    ctx.textBaseline = 'middle';
    ctx.fillText(`${side === 'left' ? '←' : side === 'right' ? '→' : '↓'} ${item.name || 'Someone'}`, r.x + 20, r.y + 30, r.w - 40);
    ctx.font = SECONDARY_FONT;
    ctx.fillText('Listening…', r.x + 20, r.y + 68);
    ctx.restore();
  }

  function render(ctx, view, env) {
    const now = env.anim;
    const dim = view.paused ? 0.3 : 1;
    visible.length = 0;
    for (const face of view.faces) {
      if (face.tiny || face.ghost) continue;
      if (face.proposal?.state === 'proposed') faceBrackets(ctx, face, {a: 0.85 * dim, color: ACCENT, dashed: true, t: 0, scale: 1.3});
      if (view.config.name_labels !== false && (face.known || face.proposal?.state === 'proposed')) updateTag(ctx, env.blur, face, now, dim);
    }
    for (const [key, c] of cards) {
      if (now - c.seen > 1.2 || view.config.name_labels === false) {
        c.a = Math.max(0, c.a - 0.12);
        if (c.a <= 0.01) cards.delete(key);
        else drawTag(ctx, env.blur, c);
      }
    }
    const feed = view.feed.filter((b) => b.alpha > 0.01 && (b.tokens?.length || b.orig));
    const newest = feed.find((b) => b.current) || feed[0] || null;
    if (!newest) { focus = null; candidate = null; }
    else if (!focus || !feed.some((b) => b.key === focus)) { focus = newest.key; candidate = null; }
    else if (newest.key !== focus) {
      if (candidate !== newest.key) { candidate = newest.key; candidateSince = now; }
      else if (now - candidateSince >= FOCUS_HOLD) { focus = candidate; candidate = null; }
    } else candidate = null;
    let primary = feed.find((b) => b.key === focus) || newest;
    if (primary) lastPrimary = {bubble: primary, seen: now};
    else if (lastPrimary && now - lastPrimary.seen < 0.8) {
      primary = {...lastPrimary.bubble, alpha: lastPrimary.bubble.alpha * clamp((0.8 - (now - lastPrimary.seen)) / 0.35)};
    }
    if (primary) drawCaption(ctx, env.blur, primary, 'center', now, dim);
    const occupied = new Set();
    for (const b of feed) {
      if (b === primary || occupied.size >= 2) continue;
      let side = sideOf(b);
      if (side === 'behind') side = occupied.has('left') ? 'right' : 'left';
      if (occupied.has(side)) continue;
      occupied.add(side);
      drawCaption(ctx, env.blur, b, side, now, dim);
    }
    const active = new Set();
    for (const o of view.offscreen) {
      if (o.bubble || !['left', 'right', 'behind'].includes(o.side)) continue;
      active.add(o.key);
      const prior = unresolved.get(o.key);
      unresolved.set(o.key, {first: prior?.first ?? now, last: now, name: o.name, side: o.side});
    }
    for (const [key, item] of unresolved) {
      if (now - item.last > 3 || now < item.first - 1) { unresolved.delete(key); continue; }
      if (active.has(key) && !occupied.has(item.side) && !primary && item.side !== 'behind') drawUnresolved(ctx, env.blur, item, item.side, now, dim);
    }
    for (const key of wraps.keys()) if (!feed.some((b) => b.key === key) && primary?.key !== key) wraps.delete(key);
    return visible;
  }

  const debug = () => visible.map((rect) => ({...rect}));
  return {render, debug, debugDocks: () => [], cards};
}
