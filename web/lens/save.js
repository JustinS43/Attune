/*
 * "Save this person" on the glasses: the double-tap flow in every glasses mode.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-29. Contracts: docs/contracts.md ("Save a person").
 *
 * A double tap on the side of the glasses (key D, or Y twice quickly) asks the engine to save the
 * person in front of the wearer. The engine answers with `save_request` (the phone asks that
 * person for consent) or a `save_cancel` hint ("Say their name first"); once they consent,
 * vision and audio report `enroll_progress` (face crops, then voice seconds) and `enroll_result`.
 * This module follows those messages (from Live or the Film's Quick Start) and draws the flow:
 *
 *   waiting for their OK -> scanning face -> learning voice -> saved (or not saved, and why)
 *
 * - Colour (Meta Orion class): soft rounded brackets and a thin progress ring around the face, a
 *   slow scan sweep, then a circular voice meter around the ring while they talk; a card beside
 *   them (never over a face; it takes its own slot with the same rules as the bubbles and is an
 *   obstacle for them) forms into a profile card and then shrinks into their name tag.
 * - Mono (Even Realities G1 band): green light only, on the band's fourth line: "SAVING SAM" with
 *   a segmented bar and a scan line, a voice meter of green bars, then "✓ SAM SAVED · FACE ·
 *   VOICE" with a short glow pulse; a chevron points toward them.
 * - Corner (Meta Ray-Ban Display square): a compact card in the square's clear middle: a
 *   progress ring around an initial avatar, the steps Face → Voice → Saved with ticks, and an
 *   arrow toward them (one line on the smaller Google Glass placement).
 *
 * Every animation eases in 250 ms or less, holds still with prefers-reduced-motion, and is
 * derived from the animation clock and the messages alone, so attuneLens.renderAt(t) draws the
 * same frame every time. The modes call view.save.drawColor / drawMono / drawCorner from inside
 * their own clip, so nothing leaves a display's region.
 */

import { onKey } from '../shared/keys.js';
import {
  W, H, FD, FT, MINT, AMBER, PX, REGION, REDUCED_MOTION,
  clamp, lerp, easeOut, easeBack, font, hexA, rrect, textW, glass, icon, follow, springStep, nowS, chevrons, arrow,
} from './hud.js';

const RM = REDUCED_MOTION;
const G = '#28FF46'; // the mono waveguide's green (modes/mono.js)
const WHITE = '#FFFFFF';
const HOLD_S = 2.6; // the saved card stays this long, then shrinks into the name tag
const SHRINK_S = 0.45;
const END_S = { hint: 2.8, cancelled: 2.4, failed: 3.4 };
const APPEAR_S = 0.25;
const WAIT_TEXT_AFTER = 0.45; // a request answered within this never flashes "waiting"
const CANCEL_TEXT = {
  declined: (n) => `${n} said no · nothing saved`,
  timeout: () => 'No answer · nothing saved',
  lost: (n) => `${n} left the view`,
  replaced: () => 'Saving someone else instead',
  cancelled: () => 'Stopped · nothing saved',
};
const HINT_TEXT = {
  no_name: ['Say their name first', 'or add them in the console'],
  already_saved: (n) => [`${n || 'They'} ${n ? 'is' : 'are'} already saved`, 'Recognised every time'],
};
const cap1 = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
/** `text`, cut with an ellipsis to fit maxW in the given font (a longer name never spills). */
function fitText(ctx, text, fnt, maxW) {
  ctx.save();
  ctx.font = fnt;
  let out = text;
  if (ctx.measureText(out).width > maxW) {
    while (out.length > 1 && ctx.measureText(`${out}…`).width > maxW) out = out.slice(0, -1);
    out = `${out.trimEnd()}…`;
  }
  ctx.restore();
  return out;
}
const initial = (name) => (String(name || '?').trim()[0] || '?').toUpperCase();

// small icons of our own (24-unit grid, stroked like hud.js icon())
const FACE_ICON = [new Path2D('M12 12m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0'), new Path2D('M9 10.5v.3M15 10.5v.3M9 15c1.7 1.4 4.3 1.4 6 0')];
const PHONE_ICON = [new Path2D('M8 2.8h8a1.6 1.6 0 0 1 1.6 1.6v15.2a1.6 1.6 0 0 1-1.6 1.6H8a1.6 1.6 0 0 1-1.6-1.6V4.4A1.6 1.6 0 0 1 8 2.8z'), new Path2D('M11 18h2')];
function ownIcon(ctx, paths, x, y, size, color, lw = 2) {
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

/** Deterministic "speech energy" for meters: smooth pseudo-random bars from the animation clock. */
function energy(anim, i, level) {
  if (RM) return 0.35 * level + 0.15;
  const v = Math.abs(Math.sin(anim * (6.3 + (i % 5) * 1.7) + i * 1.3) * Math.sin(anim * (2.9 + (i % 3)) + i * 0.7));
  return clamp(level * (0.25 + 0.75 * v));
}

/** Where someone is, for the arrow and chevrons: 'left' | 'right' | 'ahead'. */
function sideOf(face) {
  if (!face) return null;
  const dx = (face.cx - W / 2) / (W / 2);
  return dx < -0.3 ? 'left' : dx > 0.3 ? 'right' : 'ahead';
}

const overlapArea = (a, b) => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
/** A face's keep-out box (same shape as bubbles.js): forehead to chin, with a margin. */
const keepOut = (f, pad = 14) => ({ x: f.cx - f.w * 0.55 - pad, y: f.cy - f.h * 0.62 - pad, w: f.w * 1.1 + pad * 2, h: f.h * 1.22 + pad * 2 });

/**
 * Create the save flow for a lens store. It wraps store.apply / store.reset so every message the
 * lens receives (Live or Film) and every reset (scene cut, source switch) reaches it.
 * opts: { send(name, args), source() -> 'live' | 'film' }
 */
export function createSaveFlow(store, { send, source } = {}) {
  let flow = null;
  let lastView = null;
  let seq = 0;

  const origApply = store.apply;
  store.apply = (msg) => {
    origApply(msg);
    apply(msg);
  };
  const origReset = store.reset;
  store.reset = (...a) => {
    origReset(...a);
    reset();
  };

  function reset() {
    flow = null;
    card.slot = null;
    card.pos = null;
    lastObstacles = [];
  }

  function newFlow(o) {
    const now = nowS();
    flow = {
      id: o.id, track: o.track ?? null, name: o.name || '', born: now, phase: o.phase, since: now,
      face: { target: 0, shown: 0, done: false, tDone: null, hint: '' },
      voice: { target: 0, shown: 0, done: false, tDone: null, hint: '', failed: '', tPulse: -1e9 },
      reason: o.reason ?? '', expires: o.expires ?? 60, hold: o.hold ?? HOLD_S, end: null, lastFace: null,
      sim: o.sim ?? null, seq: ++seq,
    };
    card.slot = null;
    card.pos = null;
    return flow;
  }

  function setPhase(p, reason = '') {
    if (!flow || flow.phase === p) return;
    flow.phase = p;
    flow.since = nowS();
    if (reason) flow.reason = reason;
    if (p === 'saved' || p === 'failed' || p === 'cancelled' || p === 'hint') flow.end = flow.since;
  }

  const running = () => flow && (flow.phase === 'waiting' || flow.phase === 'face' || flow.phase === 'voice');

  // ------------------------------------------------------------------ messages
  function apply(msg) {
    if (!msg || typeof msg !== 'object') return;
    switch (msg.type) {
      case 'save_request': {
        if (flow?.id === msg.request_id && running()) {
          flow.name = msg.name || flow.name;
          break;
        }
        const exp = Number.isFinite(msg.expires_t) && Number.isFinite(msg.t) ? msg.expires_t - msg.t : 60;
        newFlow({ id: msg.request_id, track: msg.track_id, name: msg.name, phase: 'waiting', expires: exp, hold: Number.isFinite(msg.hold_s) ? msg.hold_s : HOLD_S });
        break;
      }
      case 'save_cancel': {
        if (msg.request_id == null) {
          if (running() && flow.phase !== 'waiting') break; // an enrollment is running: leave it be
          newFlow({ id: `hint-${seq + 1}`, track: msg.track_id, name: msg.name, phase: 'hint', reason: msg.reason || 'no_name' });
          flow.end = flow.born;
        } else if (flow?.id === msg.request_id && flow.phase === 'waiting') {
          setPhase('cancelled', msg.reason || 'cancelled');
        }
        break;
      }
      case 'enroll_progress': {
        const part = msg.part === 'voice' ? 'voice' : 'face';
        if (!running() || (flow.track != null && msg.track_id != null && flow.track !== msg.track_id)) {
          if (running()) break; // one save at a time; the engine enrolls one person at a time too
          // an enrollment started from the console or the phone: animate it too
          newFlow({ id: `enroll-${msg.track_id}`, track: msg.track_id, name: msg.name || '', phase: part });
        }
        if (flow.track == null) flow.track = msg.track_id;
        const s = flow[part];
        s.target = Math.max(s.target, clamp(Number(msg.fraction) || 0)); // rings never run backwards
        s.hint = msg.hint || '';
        if (part === 'voice') {
          s.tPulse = nowS();
          if (!flow.face.done) Object.assign(flow.face, { done: true, target: 1, tDone: nowS() });
        }
        if (flow.phase === 'waiting' || (part === 'voice' && flow.phase === 'face')) setPhase(part);
        break;
      }
      case 'enroll_result': {
        if (!running()) break;
        if (flow.track != null && msg.track_id != null && msg.track_id !== flow.track) break;
        if (msg.part === 'face') {
          if (msg.ok) {
            Object.assign(flow.face, { done: true, target: 1, tDone: flow.face.tDone ?? nowS(), hint: '' });
            setPhase('voice');
          } else setPhase('failed', msg.reason || 'try again');
        } else if (msg.part === 'voice') {
          if (msg.ok) {
            Object.assign(flow.voice, { done: true, target: 1, tDone: nowS() });
            if (!flow.face.done) Object.assign(flow.face, { done: true, target: 1, tDone: nowS() });
            setPhase('saved');
          } else if (flow.face.done) {
            flow.voice.failed = msg.reason || 'not enough speech';
            setPhase('saved');
          } else setPhase('failed', msg.reason || 'try again');
        }
        break;
      }
      default:
        break;
    }
  }

  // ------------------------------------------------------------------ keys: D, or Y twice
  function trigger() {
    if ((source?.() ?? 'live') === 'film') {
      simulate();
      return;
    }
    send?.('save.start', {});
  }
  /**
   * The Film has no engine: D plays the same flow locally on the person the engine would pick
   * (a name proposal, else the named speaker, else the most prominent named face).
   */
  function simulate() {
    const v = lastView;
    if (!v) return;
    let face = null;
    const prop = v.faces.find((f) => f.proposal && (f.proposal.state === 'proposed' || (f.proposal.state === 'confirmed' && f.proposal.age < 5)));
    if (prop) face = prop;
    face ??= v.faces.find((f) => f.known && f.status === 'named' && f.isSpeaker);
    face ??= v.faces.filter((f) => f.known && f.status === 'named').sort((a, b) => b.w * b.h - a.w * a.h)[0];
    if (!face) {
      const saved = v.faces.find((f) => f.status === 'enrolled');
      newFlow({ id: `hint-${seq + 1}`, phase: 'hint', reason: saved ? 'already_saved' : 'no_name', name: saved?.label ?? '' });
      flow.end = flow.born;
      return;
    }
    const name = face.proposal?.name ?? face.label;
    if (face.proposal?.state === 'proposed') send?.('name.answer', { proposal_id: face.proposal.proposal_id, accept: true });
    newFlow({ id: `sim-${seq + 1}`, track: face.track_id, name, phase: 'waiting', sim: { t0: nowS() } });
  }
  function stepSim(now) {
    const s = flow?.sim;
    if (!s || !running()) return;
    const t = now - s.t0;
    if (t >= 1.2 && flow.phase === 'waiting') setPhase('face');
    if (flow.phase === 'face') flow.face.target = clamp((t - 1.2) / 1.9);
    if (t >= 3.1 && flow.phase === 'face') {
      Object.assign(flow.face, { done: true, target: 1, tDone: now });
      setPhase('voice');
    }
    if (flow.phase === 'voice') {
      flow.voice.target = clamp((t - 3.3) / 2.4);
      flow.voice.tPulse = now;
    }
    if (t >= 5.7 && flow.phase === 'voice') {
      Object.assign(flow.voice, { done: true, target: 1, tDone: now });
      setPhase('saved');
    }
  }
  let lastY = -1e9;
  onKey('D', () => {
    trigger();
    return true;
  }, 'Save this person (double tap on the glasses; or Y twice)');
  onKey('Y', () => {
    const now = performance.now();
    const second = now - lastY < 450;
    lastY = second ? -1e9 : now;
    if (!second) return false; // the first Y is an ordinary yes
    trigger();
    return true;
  }, '', { priority: 20 });

  // ------------------------------------------------------------------ per frame
  /** Advance the flow and hang view.save on the view model (null when nothing to show). */
  function decorate(view, dt) {
    lastView = view;
    const now = nowS();
    stepSim(now);
    if (!flow) {
      view.save = null;
      return;
    }
    const f = flow;
    // ends: saved -> hold -> shrink; others fade after a while; stale requests give up
    const age = now - f.since;
    if (f.phase === 'saved' && age > f.hold + SHRINK_S + 0.05) flow = null;
    else if (END_S[f.phase] && age > END_S[f.phase]) flow = null;
    else if (f.phase === 'waiting' && age > f.expires + 3) flow = null;
    else if ((f.phase === 'face' || f.phase === 'voice') && now - Math.max(f.since, f.voice.tPulse) > 90) flow = null;
    if (!flow) {
      view.save = null;
      return;
    }
    // smooth the rings (a ring never runs backwards)
    for (const part of [f.face, f.voice]) {
      const k = RM ? 1 : follow(dt, 9);
      part.shown = Math.max(part.shown, part.shown + (part.target - part.shown) * k);
    }
    const face = f.track != null ? view.faces.find((q) => q.track_id === f.track && !q.ghost) ?? null : null;
    if (face) {
      f.lastFace = { cx: face.cx, cy: face.cy, w: face.w, h: face.h, top: face.top, key: face.key, seen: now };
      if (!f.name && face.known) f.name = face.label;
    }
    // their colour and relation: from the people list when it knows them, else their face
    if (f.name && !f.person) {
      for (const p of store.state.people.values()) if (p.name === f.name) f.person = p;
    }
    f.color = f.person?.color ?? (face?.known ? face.color : f.color);
    f.relation = f.person?.relation ?? face?.relation ?? f.relation;
    // the store's own "X saved" toast would say it twice: this card says it
    if (f.name && store.state.toasts.length) {
      store.state.toasts = store.state.toasts.filter((t) => !(t.kind === 'learned' && t.text === f.name));
    }
    const running = f.phase === 'waiting' || f.phase === 'face' || f.phase === 'voice';
    view.save = {
      phase: f.phase, name: f.name, track_id: f.track, running,
      band: true, // mono: the save line takes the band's fourth line
      obstacles: lastObstacles,
      drawColor: (ctx, env, layer) => drawColor(ctx, view, env, layer, face),
      drawMono: (ctx, box) => drawMono(ctx, view, box, face),
      drawCorner: (ctx, spec, env) => drawCorner(ctx, view, spec, env, face),
    };
  }

  // ------------------------------------------------------------------ shared wording
  function statusText(f) {
    const n = f.name || 'them';
    const now = nowS();
    switch (f.phase) {
      case 'waiting':
        return now - f.since < WAIT_TEXT_AFTER ? 'Getting ready…' : `Waiting for ${n}'s OK`;
      case 'face':
        return f.face.hint ? `${cap1(f.face.hint)} · scanning face` : 'Scanning face…';
      case 'voice':
        if (!f.face.done) return 'Scanning face…';
        return f.voice.shown > 0.02 ? 'Learning voice · keep talking' : `Learning voice · ask ${n} to say hi`;
      case 'saved':
        return f.voice.failed ? 'Face saved · voice later' : 'Saved on this laptop';
      case 'failed':
        return `Not saved · ${f.reason}`;
      case 'cancelled':
        return (CANCEL_TEXT[f.reason] ?? CANCEL_TEXT.cancelled)(n);
      default:
        return '';
    }
  }
  const hintLines = (f) => (f.reason === 'already_saved' ? HINT_TEXT.already_saved(f.name) : HINT_TEXT.no_name);
  /** 0..1 appear, and 0..1 fade at the end (saved: after the shrink). */
  function alphaOf(f) {
    const now = nowS();
    const appear = RM ? 1 : easeOut((now - f.born) / APPEAR_S);
    let out = 1;
    if (f.end != null) {
      const age = now - f.end;
      if (f.phase === 'saved') out = 1;
      else out = clamp((END_S[f.phase] - age) / 0.25);
    }
    return appear * out;
  }
  /** Steps as { face, voice, saved } states: 'todo' | 'now' | 'done' | 'fail'. */
  function steps(f) {
    const s = { face: 'todo', voice: 'todo', saved: 'todo' };
    if (f.phase === 'face' || (f.phase === 'voice' && !f.face.done)) s.face = 'now';
    if (f.face.done) s.face = 'done';
    if (f.phase === 'voice' && f.face.done) s.voice = 'now';
    if (f.voice.done) s.voice = 'done';
    if (f.voice.failed) s.voice = 'fail';
    if (f.phase === 'saved') s.saved = 'done';
    if (f.phase === 'failed') s[f.face.done ? 'voice' : 'face'] = 'fail';
    return s;
  }
  const overall = (f) => (f.phase === 'saved' ? 1 : 0.5 * f.face.shown + 0.5 * f.voice.shown);
  /** A tick's pop: 0..1 over 0.22 s after `t0` (a little overshoot), 1 with reduced motion. */
  const pop = (t0) => (t0 == null ? 0 : RM ? 1 : clamp(easeBack(clamp((nowS() - t0) / 0.22)), 0, 1.15));

  // ================================================================== Colour (Orion class)
  const card = { slot: null, badSince: null, pos: null, vel: { x: 0, y: 0 } };
  let lastObstacles = [];
  const CARD_W = 372;
  const CARD_H = 142;

  /** The ring is a face-shaped oval: its width stays inside bubbles' keep-out box (0.55 w + 14),
   *  so a name tag beside the face never sits on it. */
  function ringRadii(face) {
    const rx = face.w * 0.55 + 10;
    return { rx, ry: Math.max(face.h * 0.64, rx * 1.12) };
  }

  function slotRect(slot, face, w, h) {
    const R = REGION;
    const { rx, ry } = ringRadii(face);
    const minY = R.y + 24;
    const maxY = R.y + R.h - 150; // clear of the You bar
    let x;
    let y;
    if (slot === 'left' || slot === 'right') {
      x = slot === 'right' ? face.cx + rx + 26 : face.cx - rx - 26 - w;
      y = clamp(face.cy - h / 2, minY, Math.max(minY, maxY - h));
    } else {
      x = clamp(face.cx - w / 2, R.x + 24, R.x + R.w - 24 - w);
      y = slot === 'below' ? face.cy + ry + 22 : face.cy - ry - 22 - h;
    }
    return { x, y, w, h, slot };
  }

  function fitCost(r, face, view, layer) {
    const R = REGION;
    let cost = 0;
    cost += (Math.max(0, R.x + 24 - r.x) + Math.max(0, r.x + r.w - (R.x + R.w - 24)) + Math.max(0, R.y + 24 - r.y) + Math.max(0, r.y + r.h - (R.y + R.h - 150))) * 400;
    const { rx, ry } = ringRadii(face);
    cost += overlapArea(r, { x: face.cx - rx, y: face.cy - ry, w: rx * 2, h: ry * 2 }) * 2;
    for (const q of view.faces) if (!q.tiny && q.key !== face.key) cost += overlapArea(r, keepOut(q)) * 2;
    for (const c of layer?.debug?.() ?? []) if (c.a > 0.05) cost += overlapArea(r, c) * 1;
    cost += overlapArea(r, { x: R.x + 14, y: R.y + 12, w: 440, h: 78 }) * 3; // the status pill
    return cost;
  }

  /** The card's slot: chosen once, kept while it fits, moved after 0.7 s of not fitting. */
  function placeCard(face, view, layer, dt) {
    const order = face.cx > REGION.x + REGION.w / 2 ? ['left', 'right', 'below', 'above'] : ['right', 'left', 'below', 'above'];
    const now = nowS();
    let target = card.slot ? slotRect(card.slot, face, CARD_W, CARD_H) : null;
    const bad = !target || fitCost(target, face, view, layer) >= 1;
    if (bad) card.badSince ??= now;
    else card.badSince = null;
    if (!target || (bad && now - card.badSince > 0.7)) {
      let best = null;
      for (const slot of order) {
        const r = slotRect(slot, face, CARD_W, CARD_H);
        const cost = fitCost(r, face, view, layer);
        if (cost < 1) {
          best = { r, cost };
          break;
        }
        if (!best || cost < best.cost) best = { r, cost };
      }
      card.slot = best.r.slot;
      card.badSince = null;
      target = best.r;
    }
    if (!card.pos || RM) {
      card.pos = { x: target.x, y: target.y };
      card.vel = { x: 0, y: 0 };
    } else {
      [card.pos.x, card.vel.x] = springStep(card.pos.x, card.vel.x, target.x, dt, 16);
      [card.pos.y, card.vel.y] = springStep(card.pos.y, card.vel.y, target.y, dt, 16);
    }
    return { x: card.pos.x, y: card.pos.y, w: CARD_W, h: CARD_H };
  }

  function drawFaceRing(ctx, f, face, a, anim) {
    const now = nowS();
    const { rx, ry } = ringRadii(face);
    const faceDoneAge = f.face.tDone != null ? now - f.face.tDone : -1;
    const savedAge = f.phase === 'saved' ? now - f.since : -1;
    // everything around the face fades once they are saved (the card carries on)
    const fa = a * (savedAge >= 0 ? clamp(1 - savedAge / 0.6) : 1);
    if (fa <= 0.01) return;
    ctx.save();
    ctx.globalAlpha *= fa;
    // brackets: soft rounded corners around the face (where bubbles.js draws a stranger's)
    const bw = face.w * 1.3;
    const bh = face.h * 1.3 * 1.12;
    const x = face.cx - bw / 2;
    const y = face.cy - bh / 2;
    const L = Math.min(bw, bh) * 0.2;
    const col = f.phase === 'waiting' ? WHITE : MINT;
    ctx.strokeStyle = hexA(col, f.phase === 'waiting' ? 0.75 : 0.95);
    ctx.lineWidth = 2.6;
    ctx.lineCap = 'round';
    if (f.phase === 'waiting') {
      ctx.setLineDash([7, 7]);
      ctx.lineDashOffset = RM ? 0 : -anim * 14;
    }
    for (const [cx, cy, dx, dy] of [[x, y, 1, 1], [x + bw, y, -1, 1], [x, y + bh, 1, -1], [x + bw, y + bh, -1, -1]]) {
      ctx.beginPath();
      ctx.moveTo(cx, cy + dy * L);
      ctx.lineTo(cx, cy + dy * 12);
      ctx.quadraticCurveTo(cx, cy, cx + dx * 12, cy);
      ctx.lineTo(cx + dx * L, cy);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    // scan sweep: a soft band gliding down the face while crops arrive
    if (f.phase === 'face' || (f.phase === 'voice' && !f.face.done)) {
      ctx.save();
      rrect(ctx, x, y, bw, bh, 18);
      ctx.clip();
      if (RM) {
        ctx.fillStyle = hexA(MINT, 0.07);
        ctx.fillRect(x, y, bw, bh);
      } else {
        const p = (anim / 1.6) % 1;
        const sy = y - 30 + p * (bh + 60);
        const g = ctx.createLinearGradient(0, sy - 40, 0, sy);
        g.addColorStop(0, hexA(MINT, 0));
        g.addColorStop(1, hexA(MINT, 0.16));
        ctx.fillStyle = g;
        ctx.fillRect(x, sy - 40, bw, 40);
        ctx.fillStyle = hexA(MINT, 0.5);
        ctx.fillRect(x + 10, sy - 1, bw - 20, 1.6);
      }
      ctx.restore();
    }
    // thin progress ring: face crops, then a circular voice meter
    ctx.lineCap = 'round';
    ctx.strokeStyle = 'rgba(255,255,255,0.16)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.ellipse(face.cx, face.cy, rx, ry, 0, 0, Math.PI * 2);
    ctx.stroke();
    const facePart = f.face.done ? 1 : f.face.shown;
    if (facePart > 0.002) {
      ctx.save();
      ctx.shadowColor = hexA(MINT, 0.8);
      ctx.shadowBlur = 10 * PX;
      ctx.strokeStyle = MINT;
      ctx.lineWidth = 3.4;
      ctx.beginPath();
      ctx.ellipse(face.cx, face.cy, rx, ry, 0, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * facePart);
      ctx.stroke();
      // the arc's leading dot
      if (!f.face.done) {
        const ang = -Math.PI / 2 + Math.PI * 2 * facePart;
        ctx.fillStyle = WHITE;
        ctx.beginPath();
        ctx.arc(face.cx + Math.cos(ang) * rx, face.cy + Math.sin(ang) * ry, 4.2, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }
    // face captured: one soft ripple and a tick badge on the ring
    if (faceDoneAge >= 0) {
      if (!RM && faceDoneAge < 0.6) {
        const p = faceDoneAge / 0.6;
        ctx.save();
        ctx.strokeStyle = hexA(MINT, 0.55 * (1 - p));
        ctx.lineWidth = 2.4;
        ctx.beginPath();
        ctx.ellipse(face.cx, face.cy, rx + 4 + p * 26, ry + 4 + p * 26, 0, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }
      const k = pop(f.face.tDone);
      // on the upper side away from the display's centre, where bubbles rarely sit
      const bang = face.cx > REGION.x + REGION.w / 2 ? -Math.PI / 4 : (-3 * Math.PI) / 4;
      const bx = face.cx + Math.cos(bang) * rx;
      const by = face.cy + Math.sin(bang) * ry;
      ctx.save();
      ctx.translate(bx, by);
      ctx.scale(k, k);
      ctx.fillStyle = MINT;
      ctx.beginPath();
      ctx.arc(0, 0, 15, 0, Math.PI * 2);
      ctx.fill();
      icon(ctx, 'check', -9, -9, 18, '#0B1A17', 2.8);
      ctx.restore();
    }
    // voice: short radial bars around the lower half of the ring, lit clockwise as seconds of
    // their voice arrive, dancing while they talk
    if (f.phase === 'voice' && f.face.done) {
      const n = 26;
      const talking = clamp(1 - (now - f.voice.tPulse) / 1.2) * 0.7 + (face.isSpeaker ? 0.3 : 0);
      for (let i = 0; i < n; i++) {
        const u = i / (n - 1);
        const ang = Math.PI * 0.12 + u * Math.PI * 0.76; // bottom arc, left to right under the chin
        const lit = u <= f.voice.shown;
        const len = 5 + 16 * energy(anim, i, lit ? 0.35 + 0.65 * talking : 0.2);
        const c = Math.cos(ang);
        const s = Math.sin(ang);
        ctx.strokeStyle = lit ? MINT : 'rgba(255,255,255,0.28)';
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.moveTo(face.cx + c * (rx + 7), face.cy + s * (ry + 7));
        ctx.lineTo(face.cx + c * (rx + 7 + len), face.cy + s * (ry + 7 + len));
        ctx.stroke();
      }
    }
    ctx.restore();
  }

  function drawStepChips(ctx, f, x, y, anim) {
    const st = steps(f);
    const items = [['face', 'Face', FACE_ICON], ['voice', 'Voice', null], ['saved', 'Saved', null]];
    let cx = x;
    for (const [key, label, ic] of items) {
      const s = st[key];
      const on = s !== 'todo';
      const col = s === 'fail' ? AMBER : s === 'done' ? MINT : on ? WHITE : 'rgba(255,255,255,0.45)';
      const w = key === 'voice' && s === 'now' ? 136 : 98;
      ctx.save();
      rrect(ctx, cx, y, w, 34, 17);
      ctx.fillStyle = s === 'done' ? hexA(MINT, 0.16) : s === 'fail' ? hexA(AMBER, 0.16) : on ? 'rgba(255,255,255,0.10)' : 'rgba(255,255,255,0.04)';
      ctx.fill();
      ctx.strokeStyle = hexA(s === 'fail' ? AMBER : s === 'done' ? MINT : WHITE, on ? 0.45 : 0.16);
      ctx.lineWidth = 1.3;
      ctx.stroke();
      const ix = cx + 11;
      const iy = y + 17;
      if (s === 'done') {
        const k = pop(key === 'face' ? f.face.tDone : key === 'voice' ? f.voice.tDone : f.since);
        ctx.save();
        ctx.translate(ix + 7, iy);
        ctx.scale(k, k);
        icon(ctx, 'check', -8, -8, 16, MINT, 2.6);
        ctx.restore();
      } else if (s === 'fail') icon(ctx, 'cross', ix - 1, iy - 8, 16, AMBER, 2.4);
      else if (s === 'now' && key === 'face') {
        ctx.strokeStyle = 'rgba(255,255,255,0.25)';
        ctx.lineWidth = 2.4;
        ctx.beginPath();
        ctx.arc(ix + 7, iy, 7, 0, Math.PI * 2);
        ctx.stroke();
        ctx.strokeStyle = MINT;
        ctx.beginPath();
        ctx.arc(ix + 7, iy, 7, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.max(0.04, f.face.shown));
        ctx.stroke();
      } else if (ic) ownIcon(ctx, ic, ix - 1, iy - 8, 16, col, 2);
      else if (key === 'voice') icon(ctx, 'wave', ix - 1, iy - 8, 16, col, 2);
      else icon(ctx, 'lock', ix - 1, iy - 8, 16, col, 2);
      ctx.font = font(620, 17, FT);
      ctx.textBaseline = 'middle';
      ctx.fillStyle = col;
      ctx.fillText(label, cx + 32, iy + 1);
      if (key === 'voice' && s === 'now') {
        // a live equaliser while they talk; the lit bars are the seconds collected
        const talking = clamp(1 - (nowS() - f.voice.tPulse) / 1.2);
        for (let i = 0; i < 7; i++) {
          const litBar = i / 7 < f.voice.shown + 0.02;
          const bh = 4 + 16 * energy(anim, i + 3, litBar ? 0.4 + 0.6 * talking : 0.18);
          ctx.fillStyle = litBar ? MINT : 'rgba(255,255,255,0.35)';
          rrect(ctx, cx + 80 + i * 7, iy - bh / 2, 3.6, bh, 1.8);
          ctx.fill();
        }
      }
      ctx.restore();
      cx += w + 10;
    }
  }

  function drawAvatar(ctx, f, cx, cy, r, color, anim) {
    // progress ring around the initial
    ctx.save();
    ctx.lineCap = 'round';
    ctx.strokeStyle = 'rgba(255,255,255,0.18)';
    ctx.lineWidth = 3.2;
    ctx.beginPath();
    ctx.arc(cx, cy, r + 6, 0, Math.PI * 2);
    ctx.stroke();
    const p = overall(f);
    if (p > 0.005) {
      ctx.strokeStyle = f.phase === 'failed' ? AMBER : MINT;
      ctx.beginPath();
      ctx.arc(cx, cy, r + 6, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * p);
      ctx.stroke();
    }
    ctx.fillStyle = hexA(color, 0.9);
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.font = font(700, r * 1.05, FD);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#0B1A17';
    ctx.fillText(initial(f.name), cx, cy + 1.5);
    ctx.restore();
    // waiting: the phone is asking them (a slow breathing dot on the ring)
    if (f.phase === 'waiting' && !RM) {
      const ang = -Math.PI / 2 + anim * 2.2;
      ctx.save();
      ctx.fillStyle = WHITE;
      ctx.globalAlpha *= 0.85;
      ctx.beginPath();
      ctx.arc(cx + Math.cos(ang) * (r + 6), cy + Math.sin(ang) * (r + 6), 3.4, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
  }

  function drawCardBody(ctx, f, r, blur, color, anim) {
    const saved = f.phase === 'saved';
    const warn = f.phase === 'failed' || f.phase === 'cancelled';
    glass(ctx, blur, r.x, r.y, r.w, r.h, 26, { glow: warn ? AMBER : color, border: hexA(warn ? AMBER : saved ? MINT : WHITE, saved ? 0.45 : 0.22) });
    const ax = r.x + 50;
    const ay = r.y + 46;
    drawAvatar(ctx, f, ax, ay, 25, color, anim);
    ctx.save();
    ctx.textBaseline = 'middle';
    ctx.font = font(680, 29, FD);
    ctx.fillStyle = WHITE;
    const name = f.name || 'This person';
    ctx.fillText(name, r.x + 94, r.y + 32);
    let nx = r.x + 94 + textW(ctx, name, font(680, 29, FD)) + 10;
    if (saved && f.relation) {
      ctx.font = font(500, 19, FT);
      ctx.fillStyle = 'rgba(255,255,255,0.62)';
      ctx.fillText(`· ${f.relation}`, nx, r.y + 34);
      nx += textW(ctx, `· ${f.relation}`, font(500, 19, FT)) + 8;
    }
    const sub = statusText(f);
    ctx.font = font(480, 19, FT);
    ctx.fillStyle = warn ? AMBER : f.face.hint && f.phase === 'face' ? AMBER : saved ? MINT : 'rgba(255,255,255,0.72)';
    let subX = r.x + 94;
    if (f.phase === 'waiting' && nowS() - f.since >= WAIT_TEXT_AFTER) {
      ownIcon(ctx, PHONE_ICON, subX - 2, r.y + 53, 18, 'rgba(255,255,255,0.72)', 1.8);
      subX += 20;
    } else if (saved) {
      icon(ctx, 'lock', subX - 2, r.y + 53, 17, MINT, 2);
      subX += 21;
    }
    ctx.fillText(fitText(ctx, sub, ctx.font, r.x + r.w - 18 - subX), subX, r.y + 63);
    ctx.restore();
    if (f.phase === 'waiting') {
      // their consent is the only way on: say so, calmly
      ctx.save();
      ctx.font = font(450, 17, FT);
      ctx.textBaseline = 'middle';
      ctx.fillStyle = 'rgba(255,255,255,0.55)';
      const line = f.name ? `Nothing is saved until ${f.name} agrees` : 'Nothing is saved until they agree';
      ctx.fillText(ctx.measureText(line).width <= r.w - 48 ? line : 'Nothing is saved until they agree', r.x + 24, r.y + 110);
      ctx.restore();
    } else if (f.phase === 'cancelled') {
      ctx.save();
      ctx.font = font(450, 17, FT);
      ctx.textBaseline = 'middle';
      ctx.fillStyle = 'rgba(255,255,255,0.55)';
      ctx.fillText('Nothing was stored', r.x + 24, r.y + 110);
      ctx.restore();
    } else {
      drawStepChips(ctx, f, r.x + 22, r.y + 92, anim);
    }
  }

  function drawHintPill(ctx, f, blur, a) {
    const [l1, l2] = hintLines(f);
    const R = REGION;
    const f1 = font(640, 25, FD);
    const f2 = font(450, 20, FT);
    const w = Math.max(textW(ctx, l1, f1), textW(ctx, l2, f2)) + 120;
    const h = 84;
    const x = R.x + R.w / 2 - w / 2;
    const y = R.y + 104;
    ctx.save();
    ctx.globalAlpha *= a;
    ctx.translate(0, RM ? 0 : (1 - clamp((nowS() - f.born) / APPEAR_S)) * -14);
    glass(ctx, blur, x, y, w, h, 26, { glow: AMBER });
    ctx.fillStyle = hexA(AMBER, 0.95);
    ctx.beginPath();
    ctx.arc(x + 44, y + h / 2, 22, 0, Math.PI * 2);
    ctx.fill();
    icon(ctx, 'userplus', x + 32, y + h / 2 - 12, 24, '#1A1406', 2.2);
    ctx.textBaseline = 'middle';
    ctx.font = f1;
    ctx.fillStyle = WHITE;
    ctx.fillText(l1, x + 80, y + 30);
    ctx.font = f2;
    ctx.fillStyle = 'rgba(255,255,255,0.66)';
    ctx.fillText(l2, x + 80, y + 58);
    ctx.restore();
    return { x, y, w, h };
  }

  function drawColor(ctx, view, env, layer, face) {
    const f = flow;
    if (!f) return;
    const a = alphaOf(f);
    const anim = env.anim;
    const dt = view.dt ?? 1 / 60;
    if (a <= 0.005) {
      lastObstacles = [];
      return;
    }
    if (f.phase === 'hint') {
      lastObstacles = [drawHintPill(ctx, f, env.blur, a)];
      return;
    }
    const where = face ?? (f.lastFace && nowS() - f.lastFace.seen < 1.2 ? f.lastFace : null);
    const color = f.color || MINT;
    if (face && f.phase !== 'cancelled' && f.phase !== 'failed') drawFaceRing(ctx, f, face, a, anim);
    if (!where) {
      lastObstacles = [];
      return;
    }
    const base = placeCard({ ...where, key: where.key, tiny: false }, view, layer, dt);
    lastObstacles = [{ x: base.x - 10, y: base.y - 6, w: base.w + 20, h: base.h + 12 }];
    // appear: a small rise; saved: hold, then shrink into their name tag
    const now = nowS();
    let k = RM ? 1 : lerp(0.94, 1, easeOut((now - f.born) / APPEAR_S));
    let cx = base.x + base.w / 2;
    let cy = base.y + base.h / 2;
    let alpha = a;
    if (f.phase === 'saved') {
      const s = clamp((now - f.since - f.hold) / SHRINK_S);
      if (s > 0) {
        const tag = face ? layer?.cards?.get?.(face.key) : null;
        const tx = tag && tag.w > 0 ? tag.x + tag.w / 2 : where.cx;
        const ty = tag && tag.w > 0 ? tag.y + tag.h / 2 : where.top - 40;
        const tw = tag && tag.w > 0 ? tag.w : 180;
        const e = RM ? 1 : easeOut(s);
        k *= lerp(1, tw / base.w, e);
        cx = lerp(cx, tx, e);
        cy = lerp(cy, ty, e);
        alpha *= 1 - clamp((s - 0.35) / 0.65);
        lastObstacles = [];
      }
      // a brief brightening of the border as it saves
      if (!RM && now - f.since < 0.5) k *= 1 + 0.025 * Math.sin((Math.PI * (now - f.since)) / 0.5);
    }
    if (alpha <= 0.005) return;
    ctx.save();
    ctx.globalAlpha *= alpha;
    ctx.translate(cx, cy);
    ctx.scale(k, k);
    ctx.translate(-base.w / 2, -base.h / 2);
    drawCardBody(ctx, f, { x: 0, y: 0, w: base.w, h: base.h }, null, color, anim);
    ctx.restore();
  }

  // ================================================================== Mono (G1 band)
  function monoText(ctx, str, x, y, f, a = 1, align = 'left', spacing = 2) {
    ctx.font = f;
    ctx.textAlign = align;
    ctx.letterSpacing = `${spacing}px`;
    ctx.globalAlpha = a;
    ctx.fillStyle = G;
    ctx.fillText(str, x, y);
    const w = ctx.measureText(str).width;
    ctx.letterSpacing = '0px';
    ctx.textAlign = 'left';
    return w;
  }

  /** box: { x, y (baseline), w, vw } in the band's own 640x200 pixels. */
  function drawMono(ctx, view, box, face) {
    const f = flow;
    if (!f) return;
    const a = alphaOf(f);
    if (a <= 0.005) return;
    const anim = view.anim ?? nowS();
    const now = nowS();
    const { x, y, w } = box;
    const right = x + w;
    const FH = font(680, 22, FT);
    const FS = font(560, 16, FT);
    const NAME = (f.name || 'them').toUpperCase();
    ctx.save();
    ctx.shadowColor = hexA(G, 0.9);
    ctx.shadowBlur = 3.5 * PX;
    const appear = RM ? 1 : easeOut((now - f.born) / APPEAR_S);
    ctx.translate(0, (1 - appear) * 6);
    const A = a;
    ctx.globalAlpha = A;
    if (f.phase === 'hint') {
      const [l1] = hintLines(f);
      icon(ctx, 'userplus', x - 2, y - 19, 22, G, 2.3);
      monoText(ctx, l1.toUpperCase(), x + 30, y, FH, A);
      monoText(ctx, 'OR USE THE CONSOLE', right, y, FS, 0.7 * A, 'right');
    } else if (f.phase === 'waiting') {
      monoText(ctx, `SAVE ${NAME}?`, x, y, FH, A);
      const dots = RM ? 3 : 1 + (Math.floor(anim * 2.5) % 3);
      const late = now - f.since >= WAIT_TEXT_AFTER;
      if (late) monoText(ctx, `WAITING FOR THEIR OK${'.'.repeat(dots)}`, right, y, FS, 0.75 * A, 'right');
    } else if (f.phase === 'face' || (f.phase === 'voice' && !f.face.done)) {
      const lw = monoText(ctx, `SAVING ${NAME}`, x, y, FH, A);
      if (f.face.hint) monoText(ctx, f.face.hint.toUpperCase(), x + lw + 14, y, FS, 0.75 * A);
      // segmented bar: eight segments light up as face crops arrive, a scan line sweeps across
      const n = 8;
      const sw = 14;
      const gap = 5;
      const bx = right - n * (sw + gap) + gap;
      const by = y - 15;
      const lit = f.face.shown * n;
      for (let i = 0; i < n; i++) {
        const v = clamp(lit - i);
        ctx.globalAlpha = A * (0.3 + 0.7 * v);
        if (v > 0.02) {
          ctx.fillStyle = G;
          ctx.fillRect(bx + i * (sw + gap), by, sw, 14);
        } else {
          ctx.strokeStyle = G;
          ctx.lineWidth = 1.3;
          ctx.strokeRect(bx + i * (sw + gap) + 0.5, by + 0.5, sw - 1, 13);
        }
      }
      if (!RM) {
        const p = (anim / 1.1) % 1;
        const sx = bx - 8 + p * (n * (sw + gap) + 8);
        ctx.save();
        const g = ctx.createLinearGradient(sx - 36, 0, sx, 0);
        g.addColorStop(0, hexA(G, 0));
        g.addColorStop(1, hexA(G, 0.55));
        ctx.globalAlpha = A;
        ctx.fillStyle = g;
        ctx.fillRect(sx - 36, by - 4, 36, 22);
        ctx.fillStyle = G;
        ctx.fillRect(sx - 1, by - 5, 2, 24);
        ctx.restore();
      }
    } else if (f.phase === 'voice') {
      const lw = monoText(ctx, 'LEARNING VOICE', x, y, FH, A);
      monoText(ctx, NAME, x + lw + 12, y, FS, 0.7 * A);
      // a voice meter of green bars; the lit ones are the seconds collected
      const n = 14;
      const bw = 5;
      const gap = 5;
      const bx = right - n * (bw + gap) + gap;
      const talking = clamp(1 - (now - f.voice.tPulse) / 1.2);
      for (let i = 0; i < n; i++) {
        const litBar = i / n < f.voice.shown + 0.01;
        const bh = 4 + 20 * energy(anim, i, litBar ? 0.35 + 0.65 * talking : 0.15);
        ctx.globalAlpha = A * (litBar ? 1 : 0.35);
        ctx.fillStyle = G;
        ctx.fillRect(bx + i * (bw + gap), y - 7 - bh / 2, bw, bh);
      }
    } else if (f.phase === 'saved') {
      const age = now - f.since;
      const pulse = RM ? 0 : Math.sin(Math.PI * clamp(age / 0.7));
      ctx.shadowBlur = 3.5 * PX * (1 + 2.6 * pulse);
      const fade = clamp((f.hold + SHRINK_S - age) / 0.4);
      const parts = [`${NAME} SAVED`, 'FACE', f.voice.failed ? 'VOICE LATER' : 'VOICE'];
      const tx = x + 30;
      ctx.globalAlpha = A * fade;
      icon(ctx, 'check', x - 2, y - 20, 22, G, 2.6);
      let cx = tx;
      parts.forEach((p, i) => {
        if (i) cx += monoText(ctx, ' · ', cx, y, FS, 0.7 * A * fade, 'left', 1);
        cx += monoText(ctx, p, cx, y, i ? FS : FH, A * fade * (i ? 0.85 : 1)) + 2;
      });
    } else {
      // failed or cancelled
      ctx.globalAlpha = A;
      icon(ctx, 'cross', x - 2, y - 19, 20, G, 2.4);
      const why = f.phase === 'failed' ? f.reason : { declined: 'THEY SAID NO', timeout: 'NO ANSWER', lost: 'OUT OF VIEW', replaced: 'SOMEONE ELSE', cancelled: 'STOPPED' }[f.reason] ?? f.reason;
      const lw = monoText(ctx, 'NOT SAVED', x + 28, y, FH, A);
      monoText(ctx, `· ${String(why).toUpperCase()}`, x + 28 + lw + 10, y, FS, 0.8 * A);
    }
    // a chevron toward them, at the end of the line (they may be off to one side)
    const side = sideOf(face ?? f.lastFace);
    if (side === 'left' || side === 'right') {
      ctx.globalAlpha = 1;
      const nud = RM ? 0 : Math.sin(anim * 5) * 1.5;
      chevrons(ctx, side === 'left' ? 22 + nud : box.vw - 22 - nud, y - 8, side === 'left' ? -1 : 1, G, 9, 2.6, 1, A * 0.9);
    }
    ctx.restore();
  }

  // ================================================================== Corner (Ray-Ban Display square)
  function lit(ctx, str, x, y, f, color = WHITE, a = 1, align = 'left') {
    ctx.save();
    ctx.globalAlpha *= a;
    ctx.font = f;
    ctx.textAlign = align;
    ctx.textBaseline = 'middle';
    ctx.fillStyle = color;
    ctx.shadowColor = hexA(color === WHITE ? '#DDEBFF' : color, 0.55);
    ctx.shadowBlur = 5 * PX;
    ctx.fillText(str, x, y);
    ctx.restore();
  }

  function cornerArrow(ctx, cx, cy, r, face, color, anim) {
    const side = sideOf(face);
    if (!side) return;
    const up = face && face.cy < H * 0.36;
    const ang = side === 'ahead' ? -Math.PI / 2 : side === 'left' ? (up ? (-3 * Math.PI) / 4 : Math.PI) : up ? -Math.PI / 4 : 0;
    const nud = RM ? 0 : Math.sin(anim * 5) * 1.6;
    ctx.save();
    ctx.strokeStyle = hexA(color, 0.7);
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();
    arrow(ctx, cx + Math.cos(ang) * nud, cy + Math.sin(ang) * nud, r * 1.1, ang, color, Math.max(2, r * 0.16));
    ctx.restore();
  }

  /** spec: { rect, k, compact, labelY } from modes/corner.js (drawn inside its clip). */
  function drawCorner(ctx, view, spec, env, face) {
    const f = flow;
    if (!f) return;
    const a = alphaOf(f);
    if (a <= 0.005) return;
    const anim = env.anim;
    const now = nowS();
    const { x: DX, y: DY, w: DW } = spec.rect;
    const k = spec.k ?? 1;
    const PAD = 15 * k;
    const color = f.color || MINT;
    const where = face ?? f.lastFace;
    const warn = f.phase === 'failed' || f.phase === 'cancelled' || f.phase === 'hint';
    let fade = 1;
    if (f.phase === 'saved') fade = clamp((f.hold + SHRINK_S - (now - f.since)) / 0.35);
    ctx.save();
    ctx.globalAlpha *= a * fade;
    const rise = RM ? 0 : (1 - easeOut((now - f.born) / APPEAR_S)) * 6 * k;
    ctx.translate(0, rise);
    if (spec.compact) {
      // Google Glass placement: one line between the status line and the captions
      const y = DY + 50 * k;
      const r = 10 * k;
      const cx = DX + PAD + r;
      ctx.lineCap = 'round';
      ctx.strokeStyle = 'rgba(255,255,255,0.25)';
      ctx.lineWidth = 2.4 * k;
      ctx.beginPath();
      ctx.arc(cx, y, r, 0, Math.PI * 2);
      ctx.stroke();
      ctx.strokeStyle = warn ? AMBER : MINT;
      ctx.beginPath();
      ctx.arc(cx, y, r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * (warn ? 1 : overall(f)));
      ctx.stroke();
      if (f.phase === 'saved') icon(ctx, 'check', cx - 7 * k, y - 7 * k, 14 * k, MINT, 2.4);
      const text = f.phase === 'hint' ? hintLines(f)[0] : `${f.name || 'Them'} · ${statusText(f)}`;
      const tf = font(600, 13.5 * k, FT);
      lit(ctx, fitText(ctx, text, tf, DX + DW - PAD - (cx + r + 8 * k) - (where ? 22 * k : 0)), cx + r + 8 * k, y + 1, tf, warn ? AMBER : WHITE, 0.92);
      if (where && f.phase !== 'hint') cornerArrow(ctx, DX + DW - PAD - 9 * k, y, 9 * k, where, color, anim);
      ctx.restore();
      return;
    }
    // Ray-Ban Display square: a compact card in the clear middle, above the caption rows
    const top = DY + 42 * k;
    const bottom = spec.labelY - 26 * k;
    const cx = DX + PAD + 38 * k;
    const cy = top + (bottom - top) / 2 - 8 * k;
    // brightness headroom behind the card (a simulation aid, see modes/corner.js)
    ctx.save();
    ctx.filter = `blur(${10 * PX}px)`;
    ctx.fillStyle = 'rgba(0,0,0,0.26)';
    ctx.fillRect(DX + 6, top - 4 * k, DW - 12, bottom - top + 8 * k);
    ctx.restore();
    if (f.phase === 'hint') {
      const [l1, l2] = hintLines(f);
      ctx.fillStyle = hexA(AMBER, 0.95);
      ctx.beginPath();
      ctx.arc(cx, cy, 26 * k, 0, Math.PI * 2);
      ctx.fill();
      icon(ctx, 'userplus', cx - 13 * k, cy - 13 * k, 26 * k, '#1A1406', 2.2);
      lit(ctx, l1, cx + 40 * k, cy - 10 * k, font(640, 17 * k, FD));
      lit(ctx, l2, cx + 40 * k, cy + 14 * k, font(520, 13 * k, FT), WHITE, 0.7);
      ctx.restore();
      return;
    }
    // ring around an initial avatar
    const r = 24 * k;
    ctx.lineCap = 'round';
    ctx.strokeStyle = 'rgba(255,255,255,0.22)';
    ctx.lineWidth = 3.4 * k;
    ctx.beginPath();
    ctx.arc(cx, cy, r + 7 * k, 0, Math.PI * 2);
    ctx.stroke();
    const p = warn ? 1 : overall(f);
    if (p > 0.005) {
      ctx.save();
      ctx.shadowColor = hexA(warn ? AMBER : MINT, 0.7);
      ctx.shadowBlur = 8 * PX;
      ctx.strokeStyle = warn ? AMBER : MINT;
      ctx.beginPath();
      ctx.arc(cx, cy, r + 7 * k, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * p);
      ctx.stroke();
      ctx.restore();
    }
    if (f.phase === 'waiting' && !RM) {
      const ang = -Math.PI / 2 + anim * 2.2;
      ctx.fillStyle = WHITE;
      ctx.beginPath();
      ctx.arc(cx + Math.cos(ang) * (r + 7 * k), cy + Math.sin(ang) * (r + 7 * k), 3 * k, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = hexA(color, 0.88);
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.save();
    ctx.font = font(700, 24 * k, FD);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#0B1A17';
    ctx.fillText(initial(f.name), cx, cy + 1);
    ctx.restore();
    if (f.phase === 'saved') {
      const kk = pop(f.since);
      ctx.save();
      ctx.translate(cx + r * 0.78, cy + r * 0.78);
      ctx.scale(kk, kk);
      ctx.fillStyle = MINT;
      ctx.beginPath();
      ctx.arc(0, 0, 10 * k, 0, Math.PI * 2);
      ctx.fill();
      icon(ctx, 'check', -7 * k, -7 * k, 14 * k, '#0B1A17', 2.6);
      ctx.restore();
    }
    // name, the steps Face → Voice → Saved, and a status line
    const tx = cx + r + 20 * k;
    lit(ctx, f.name || 'This person', tx, cy - 22 * k, font(660, 19 * k, FD));
    const st = steps(f);
    let sx = tx;
    const labels = [['face', 'Face'], ['voice', 'Voice'], ['saved', 'Saved']];
    labels.forEach(([key, label], i) => {
      const s = st[key];
      const col = s === 'done' ? MINT : s === 'fail' ? AMBER : s === 'now' ? WHITE : 'rgba(255,255,255,0.45)';
      if (s === 'done') {
        const kk = pop(key === 'face' ? f.face.tDone : key === 'voice' ? f.voice.tDone : f.since);
        ctx.save();
        ctx.translate(sx + 6 * k, cy + 3 * k);
        ctx.scale(kk, kk);
        icon(ctx, 'check', -6 * k, -6 * k, 12 * k, MINT, 2.4);
        ctx.restore();
        sx += 15 * k;
      } else if (s === 'fail') {
        icon(ctx, 'cross', sx, cy - 3 * k, 12 * k, AMBER, 2.2);
        sx += 15 * k;
      }
      const f13 = font(s === 'now' ? 650 : 560, 13 * k, FT);
      lit(ctx, label, sx, cy + 4 * k, f13, col, s === 'now' && !RM ? 0.75 + 0.25 * Math.sin(anim * 4) : 1);
      sx += textW(ctx, label, f13);
      if (i < labels.length - 1) {
        lit(ctx, '→', sx + 5 * k, cy + 3 * k, font(500, 12 * k, FT), WHITE, 0.4);
        sx += 20 * k;
      }
    });
    // a thin bar under the steps: the current part's progress (face crops or voice seconds)
    const barX = tx;
    const barW = DX + DW - PAD - tx;
    const barY = cy + 17 * k;
    const part = f.phase === 'voice' && f.face.done ? f.voice.shown : f.face.done ? 1 : f.face.shown;
    if (f.phase === 'face' || f.phase === 'voice') {
      ctx.fillStyle = 'rgba(255,255,255,0.18)';
      rrect(ctx, barX, barY, barW, 3 * k, 1.5 * k);
      ctx.fill();
      ctx.fillStyle = MINT;
      rrect(ctx, barX, barY, Math.max(3 * k, barW * part), 3 * k, 1.5 * k);
      ctx.fill();
      if (f.phase === 'voice' && f.face.done) {
        // tiny live equaliser at the bar's end while they talk
        const talking = clamp(1 - (now - f.voice.tPulse) / 1.2);
        for (let i = 0; i < 4; i++) {
          const bh = 3 * k + 9 * k * energy(anim, i, 0.3 + 0.7 * talking);
          ctx.fillStyle = MINT;
          rrect(ctx, barX + barW - 26 * k + i * 6 * k, barY - 7 * k - bh / 2, 3 * k, bh, 1.5 * k);
          ctx.fill();
        }
      }
    }
    const subF = font(520, 12.5 * k, FT);
    const sub = fitText(ctx, statusText(f), subF, DX + DW - PAD - tx);
    lit(ctx, sub, tx, cy + 33 * k, subF, warn || (f.phase === 'face' && f.face.hint) ? AMBER : f.phase === 'saved' ? MINT : WHITE, 0.85);
    // arrow toward them
    if (where) cornerArrow(ctx, DX + DW - PAD - 12 * k, cy - 24 * k, 11 * k, where, color, anim);
    ctx.restore();
  }

  return {
    apply, reset, decorate,
    /** The current flow (for tests and measurements). */
    debug: () => (flow ? { phase: flow.phase, name: flow.name, track: flow.track, face: flow.face.shown, voice: flow.voice.shown, slot: card.slot } : null),
    /** Start the flow as a double tap would (the same as key D). */
    trigger,
  };
}
