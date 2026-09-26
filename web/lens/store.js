/*
 * The lens model: one store fed with contract messages (docs/contracts.md sections 3 and
 * "Pages integration additions"), whichever source produced them. The Live source feeds it
 * from the engine WebSocket; the Film source replays the launch film's script as the same
 * messages. Every glasses mode renders from buildView(), so both sources share one code path.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-06, P-07, P-08.
 */

import { W, H, K, clamp, follow, wrapChars, nowS } from './hud.js';

export const DEFAULT_CONFIG = { bubble_chars: 42, bubble_lines: 2, bubble_fade_s: 4 };

const PALETTE = ['#FF8FA3', '#FFB86B', '#C4A7FF', '#7CC8FF', '#86E3B0', '#FFD36E', '#5FE0D8', '#B8F07A'];
export const NEUTRAL = '#E6EAF0';

function hash(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** 'L' | 'left' -> 'left'; 'R' -> 'right'; 'B' | 'behind' -> 'behind'; else 'none'. */
export function normSide(side) {
  const s = String(side ?? '').toLowerCase();
  if (s === 'l' || s === 'left') return 'left';
  if (s === 'r' || s === 'right') return 'right';
  if (s === 'b' || s === 'behind' || s === 'back' || s === 'rear') return 'behind';
  return 'none';
}

const NAMED = new Set(['named', 'enrolled']);

/** 'en-US' -> 'en'; missing or undetermined ('und', 'unk', '') counts as the wearer's language. */
export function normLang(lang) {
  const l = String(lang ?? '').toLowerCase().split(/[-_]/)[0];
  return !l || l === 'und' || l === 'unk' || l === 'xx' || l === 'auto' ? 'en' : l;
}

export function createStore() {
  const s = {
    clock: 0, // source clock (engine/wall seconds for Live, film seconds for Film)
    connected: false,
    paused: false,
    tPaused: -1e9, // wall time of the last pause change
    sessionId: null,
    config: { ...DEFAULT_CONFIG },
    scene: { faces: [], offscreen: [], you_speaking: false },
    captions: new Map(),
    proposals: new Map(),
    alerts: new Map(),
    people: new Map(), // person_id -> { name, color, relation }
    toasts: [],
    lastStatus: new Map(), // track_id -> status (for "learned" toasts)
    replySeq: 0,
  };

  const wall = nowS; // appear animations run on wall time, even with the film paused

  function toast(kind, text, o = {}) {
    s.toasts.push({ kind, text, t: wall(), ...o });
    if (s.toasts.length > 4) s.toasts.shift();
  }

  function colorOf(personId, trackId, status) {
    if (personId != null) {
      const p = s.people.get(String(personId));
      if (p?.color) return p.color;
      return PALETTE[hash(String(personId)) % PALETTE.length];
    }
    if (NAMED.has(status)) return PALETTE[hash(`t${trackId}`) % PALETTE.length];
    return NEUTRAL;
  }

  const apply = (msg) => {
    if (!msg || typeof msg !== 'object') return;
    switch (msg.type) {
      case 'welcome':
        s.sessionId = msg.session_id ?? null;
        if (typeof msg.paused === 'boolean') s.paused = msg.paused;
        if (msg.config) {
          for (const k of Object.keys(DEFAULT_CONFIG)) {
            if (Number.isFinite(msg.config[k]) && msg.config[k] > 0) s.config[k] = msg.config[k];
          }
        }
        break;
      case 'paused':
        if (s.paused !== !!msg.paused) s.tPaused = wall();
        s.paused = !!msg.paused;
        break;
      case 'scene': {
        const faces = Array.isArray(msg.faces) ? msg.faces : [];
        for (const f of faces) {
          const prev = s.lastStatus.get(f.track_id);
          if (prev && !NAMED.has(prev) && NAMED.has(f.status)) {
            const p = f.person_id != null ? s.people.get(String(f.person_id)) : null;
            toast('learned', f.label, { color: colorOf(f.person_id, f.track_id, f.status), relation: p?.relation, enrolled: f.status === 'enrolled' });
          }
          s.lastStatus.set(f.track_id, f.status);
        }
        s.scene = {
          faces,
          offscreen: Array.isArray(msg.offscreen) ? msg.offscreen : [],
          you_speaking: !!msg.you_speaking,
        };
        break;
      }
      case 'caption': {
        if (msg.utt_id == null) break;
        const prev = s.captions.get(msg.utt_id);
        const changed = !prev || prev.text !== msg.text || prev.final !== !!msg.final || prev.translation !== msg.translation;
        s.captions.set(msg.utt_id, {
          utt_id: msg.utt_id,
          speaker: msg.speaker ?? { kind: 'someone', label: 'Someone' },
          text: String(msg.text ?? ''),
          final: !!msg.final,
          lang: normLang(msg.lang),
          translation: msg.translation || null,
          words: msg.words,
          tFirst: prev?.tFirst ?? s.clock,
          tUpdate: changed ? s.clock : prev.tUpdate,
          tFinal: msg.final ? prev?.tFinal ?? s.clock : null,
        });
        break;
      }
      case 'reply_spoken': {
        const id = `reply-${++s.replySeq}`;
        s.captions.set(id, {
          utt_id: id, speaker: { kind: 'you_typed', label: 'You' }, text: String(msg.text ?? ''), final: true,
          lang: 'en', translation: null, tFirst: s.clock, tUpdate: s.clock, tFinal: s.clock,
        });
        break;
      }
      case 'name_proposal': {
        if (msg.proposal_id == null) break;
        const prev = s.proposals.get(msg.proposal_id);
        s.proposals.set(msg.proposal_id, {
          ...msg,
          tStart: prev?.tStart ?? s.clock,
          wStart: prev?.wStart ?? wall(),
          tState: prev?.state === msg.state ? prev.tState : s.clock,
        });
        break;
      }
      case 'alert': {
        if (msg.alert_id == null) break;
        const prev = s.alerts.get(msg.alert_id);
        const st = msg.state || 'start';
        if (!prev && st === 'clear') break; // joined after it ended: nothing to show
        s.alerts.set(msg.alert_id, {
          ...msg,
          side: normSide(msg.side),
          state: st,
          tStart: prev?.tStart ?? s.clock,
          wStart: prev?.wStart ?? wall(),
          tUpdate: s.clock,
          count: Number.isFinite(msg.count) ? msg.count : prev?.count ?? 1, // optional repeat count
          tAck: st === 'acknowledged' ? prev?.tAck ?? s.clock : prev?.tAck ?? null,
          tClear: st === 'clear' ? prev?.tClear ?? s.clock : null,
        });
        break;
      }
      case 'people': {
        const list = Array.isArray(msg.people) ? msg.people : Array.isArray(msg.list) ? msg.list : [];
        for (const p of list) {
          if (p?.person_id == null) continue;
          const cur = s.people.get(String(p.person_id)) ?? {};
          s.people.set(String(p.person_id), { ...cur, name: p.name ?? cur.name, color: p.color ?? cur.color, relation: p.relation ?? cur.relation });
        }
        break;
      }
      default:
        break;
    }
  };

  /** Drop captions, alerts and proposals that finished fading. */
  function prune() {
    const fade = s.config.bubble_fade_s;
    for (const [id, c] of s.captions) {
      if (s.clock - c.tUpdate > fade + 3 || s.clock < c.tFirst - 1) s.captions.delete(id);
    }
    for (const [id, a] of s.alerts) {
      if ((a.tClear != null && s.clock - a.tClear > 1.5) || (a.tAck != null && s.clock - a.tAck > 4) || s.clock < a.tStart - 1) s.alerts.delete(id);
    }
    for (const [id, p] of s.proposals) {
      if ((p.state !== 'proposed' && s.clock - p.tState > 3) || s.clock < p.tStart - 1) s.proposals.delete(id);
    }
    const now = wall();
    s.toasts = s.toasts.filter((t) => now - t.t < 3.6);
  }

  /** Clear everything session-bound (scene cut in the film, "forget session", source switch). */
  function reset({ keepPeople = true } = {}) {
    s.scene = { faces: [], offscreen: [], you_speaking: false };
    s.captions.clear();
    s.proposals.clear();
    s.alerts.clear();
    s.toasts = [];
    s.lastStatus.clear();
    if (!keepPeople) s.people.clear();
  }

  return { state: s, apply, prune, reset, colorOf, toast };
}

// ---------------------------------------------------------------- view model
const ALERT_META = {
  smoke: { label: 'Smoke alarm', icon: 'flame', level: 'urgent' },
  co: { label: 'CO alarm', icon: 'co', level: 'urgent' },
  doorbell: { label: 'Doorbell', icon: 'bell', level: 'attention' },
  bike: { label: 'Bike bell', icon: 'bike', level: 'info' },
  name: { label: 'Someone called you', icon: 'voice', level: 'attention' },
  vehicle: { label: 'Vehicle', icon: 'truck', level: 'attention' },
};
export const SIDE_TEXT = { left: 'On your left', right: 'On your right', behind: 'Behind you', none: 'Nearby' };

/**
 * Builds the per-frame view model. Holds the face smoothing state, so keep one per page.
 * view = { clock, faces[], bubbles[], offscreen[], lower, you, alerts[], proposals[], status, feed[] ... }
 */
export function createViewBuilder(store) {
  const smooth = new Map(); // track_id -> { cx, cy, w, h, mx, my, seen }

  return function build(dt, anim, sourceKind) {
    const s = store.state;
    const clock = s.clock;
    const cfg = s.config;
    const fade = cfg.bubble_fade_s;

    // ---- faces (1920x1080 design space, smoothed)
    const faces = [];
    const byTrack = new Map();
    const k = follow(dt, sourceKind === 'film' ? 22 : 12);
    const seenNow = new Set();
    for (const f of s.paused ? [] : s.scene.faces) {
      if (!Array.isArray(f.box) || f.box.length < 4) continue;
      const [bx, by, bw, bh] = f.box;
      const tgt = {
        cx: (bx + bw / 2) * K, cy: (by + bh / 2) * K, w: bw * K, h: bh * K,
        mx: Array.isArray(f.mouth) ? f.mouth[0] * K : (bx + bw / 2) * K,
        my: Array.isArray(f.mouth) ? f.mouth[1] * K : (by + bh * 0.78) * K,
      };
      let sm = smooth.get(f.track_id);
      if (!sm || anim - sm.seen > 1.5) {
        sm = { ...tgt, born: anim, seen: anim };
        smooth.set(f.track_id, sm);
      } else {
        for (const key of ['cx', 'cy', 'w', 'h', 'mx', 'my']) sm[key] += (tgt[key] - sm[key]) * k;
        sm.seen = anim;
      }
      seenNow.add(f.track_id);
      const person = f.person_id != null ? s.people.get(String(f.person_id)) : null;
      let proposal = null;
      for (const p of s.proposals.values()) if (p.track_id === f.track_id) proposal = { ...p, age: clock - p.tState };
      const status = f.status || 'unknown';
      const face = {
        key: `t${f.track_id}`,
        track_id: f.track_id,
        person_id: f.person_id ?? null,
        cx: sm.cx, cy: sm.cy, w: sm.w, h: sm.h, mx: sm.mx, my: sm.my,
        top: sm.cy - sm.h * 0.72, // top of the head, a little above the face box
        born: sm.born,
        tiny: f.box[2] < 40,
        label: f.label || (NAMED.has(status) ? 'Someone you know' : 'Someone new'),
        status,
        known: NAMED.has(status),
        relation: person?.relation ?? null,
        color: store.colorOf(f.person_id, f.track_id, status),
        isSpeaker: !!f.is_speaker,
        dashed: !!f.dashed,
        lip: clamp(Number(f.lip_score) || 0),
        proposal,
      };
      faces.push(face);
      byTrack.set(f.track_id, face);
    }
    for (const [id, sm] of smooth) if (!seenNow.has(id) && anim - sm.seen > 2) smooth.delete(id);

    // ---- captions grouped by speaker: each speaker shows its latest utterance
    const groups = new Map();
    const caps = [...s.captions.values()].sort((a, b) => a.tUpdate - b.tUpdate);
    for (const c of caps) {
      const sp = c.speaker || {};
      const kind = sp.kind || 'someone';
      let key;
      if ((kind === 'face' || kind === 'probable_face') && sp.track_id != null) key = `t${sp.track_id}`;
      else if (kind === 'offscreen') key = `o${sp.person_id ?? sp.label ?? normSide(sp.side)}`;
      else if (kind === 'you' || kind === 'you_typed') key = 'you';
      else key = 'someone';
      groups.set(key, c);
    }

    const offMap = new Map();
    for (const o of s.paused ? [] : s.scene.offscreen) {
      const key = `o${o.person_id ?? o.label ?? normSide(o.side)}`;
      offMap.set(key, {
        key, name: o.label || 'Someone', side: normSide(o.side), color: store.colorOf(o.person_id, null, o.person_id != null ? 'named' : 'unknown'),
        person_id: o.person_id ?? null, bubble: null,
      });
    }

    const bubbles = [];
    let lower = null;
    let you = null;
    const feed = [];
    for (const [key, c] of groups) {
      const tRef = c.final ? c.tFinal : c.tUpdate;
      const age = clock - tRef;
      const alpha = age < fade ? 1 : clamp(1 - (age - fade) / 0.5);
      if (alpha <= 0 || !c.text) continue;
      const sp = c.speaker || {};
      const translated = !!c.translation && c.lang !== 'en';
      const primary = translated ? c.translation : c.text;
      let all = wrapChars(primary, cfg.bubble_chars);
      const cut = all.length > cfg.bubble_lines;
      if (cut) all = all.slice(-cfg.bubble_lines);
      const b = {
        key, utt_id: c.utt_id, kind: sp.kind || 'someone', text: primary, lines: all, cut, tFirst: c.tFirst,
        orig: translated ? c.text : null, lang: c.lang, translated, pending: c.lang !== 'en' && !c.translation,
        final: c.final, alpha, tUpdate: c.tUpdate, age,
        speaking: !c.final && clock - c.tUpdate < 1.2,
        dashed: sp.kind === 'probable_face',
        name: sp.label || 'Someone', color: NEUTRAL, face: null, side: normSide(sp.side), relation: null,
      };
      if (key.startsWith('t')) {
        const face = byTrack.get(sp.track_id);
        if (face) {
          b.face = face;
          b.name = face.proposal?.state === 'proposed' ? `${face.proposal.name}?` : face.label;
          b.color = face.color;
          b.relation = face.relation;
          b.dashed = b.dashed || face.dashed;
          b.speaking = b.speaking || face.isSpeaker;
          b.known = face.known;
          bubbles.push(b);
        } else {
          const ok = [...offMap.values()].find((o) => sp.person_id != null && o.person_id === sp.person_id);
          if (ok) { ok.bubble = b; b.color = ok.color; b.side = ok.side; } else { b.color = store.colorOf(sp.person_id, sp.track_id, sp.person_id != null ? 'named' : 'unknown'); lower = b; }
        }
      } else if (key.startsWith('o')) {
        let o = offMap.get(key);
        if (!o) {
          o = { key, name: sp.label || 'Someone', side: normSide(sp.side), color: store.colorOf(sp.person_id, null, sp.person_id != null ? 'named' : 'unknown'), person_id: sp.person_id ?? null, bubble: null };
          offMap.set(key, o);
        }
        b.color = o.color;
        b.side = o.side;
        b.speaking = b.speaking || !c.final;
        o.bubble = b;
      } else if (key === 'you') {
        b.name = 'You';
        b.typed = sp.kind === 'you_typed';
        b.color = '#FFFFFF';
        you = b;
      } else {
        b.color = NEUTRAL;
        lower = b;
      }
      feed.push(b);
    }
    feed.sort((a, b) => b.tUpdate - a.tUpdate || a.final - b.final || b.tFirst - a.tFirst);

    // direction of every caption relative to the wearer (for the mono and corner displays)
    for (const b of feed) {
      if (b.face) {
        const dx = (b.face.cx - W / 2) / (W / 2);
        const dy = (b.face.cy - H / 2) / (H / 2);
        b.dir = { dx, dy, side: dx < -0.33 ? 'left' : dx > 0.33 ? 'right' : 'ahead' };
      } else if (b.side === 'left' || b.side === 'right' || b.side === 'behind') {
        b.dir = { dx: b.side === 'left' ? -1.2 : b.side === 'right' ? 1.2 : 0, dy: b.side === 'behind' ? 1.2 : 0, side: b.side, off: true };
      } else b.dir = null;
    }

    // ---- alerts
    const alerts = [];
    for (const a of s.alerts.values()) {
      const meta = ALERT_META[a.kind] ?? { label: a.kind ? a.kind[0].toUpperCase() + a.kind.slice(1) : 'Sound', icon: 'sound', level: 'attention' };
      let alpha = clamp((anim - a.wStart) / 0.25);
      if (a.tClear != null) alpha *= clamp(1 - (clock - a.tClear) / 0.4);
      if (a.tAck != null) alpha *= clamp(1 - (clock - a.tAck - 1.6) / 0.5);
      if (alpha <= 0) continue;
      const level = a.level === 'urgent' || a.level === 'info' || a.level === 'attention' ? a.level : meta.level;
      alerts.push({
        id: a.alert_id, kind: a.kind, icon: meta.icon, level,
        label: a.label || meta.label, detail: a.detail || SIDE_TEXT[a.side], side: a.side,
        color: level === 'urgent' ? '#FF4D4F' : level === 'info' ? '#7CC8FF' : a.kind === 'vehicle' ? '#FF9F43' : '#FFC857',
        state: a.state, acked: a.tAck != null, watch: a.state === 'watch', count: Math.max(1, a.count),
        tStart: a.tStart, tAck: a.tAck, tUpdate: a.tUpdate, alpha, age: anim - a.wStart,
      });
    }
    alerts.sort((a, b) => (b.level === 'urgent') - (a.level === 'urgent') || b.tStart - a.tStart);
    const activeAlert = alerts.find((a) => !a.acked && !a.watch && a.state !== 'clear') ?? null;

    // ---- proposals not attached to a visible face
    const proposals = [];
    for (const p of s.proposals.values()) {
      const shown = p.state === 'proposed' || clock - p.tState < 1.4;
      if (!shown) continue;
      const item = {
        id: p.proposal_id, name: p.name, state: p.state, track_id: p.track_id, age: clock - p.tState,
        alpha: p.state === 'proposed' ? clamp((anim - p.wStart) / 0.3) : clamp(1 - (clock - p.tState - 1.0) / 0.4),
      };
      if (!byTrack.has(p.track_id)) proposals.push(item);
    }
    const pendingProposal = [...s.proposals.values()].filter((p) => p.state === 'proposed').sort((a, b) => b.tStart - a.tStart)[0] ?? null;

    const speaking = faces.some((f) => f.isSpeaker) || feed.some((b) => b.speaking) || s.scene.you_speaking;
    let status = 'listening';
    if (sourceKind === 'live' && !s.connected) status = 'connecting';
    else if (s.paused) status = 'paused';
    else if (activeAlert) status = 'alert';

    return {
      clock, anim, dt, sourceKind, config: cfg, paused: s.paused, pausedAge: anim - s.tPaused,
      connected: s.connected, status, speaking,
      faces, bubbles, offscreen: [...offMap.values()], lower, you, feed,
      alerts, activeAlert, proposals, pendingProposal,
      toasts: s.toasts.map((t) => ({ ...t, age: anim - t.t })),
    };
  };
}
