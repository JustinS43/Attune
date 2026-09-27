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
    trackNames: new Map(), // track_id -> name confirmed for the session (name_proposal confirmed)
    toasts: [],
    lastStatus: new Map(), // track_id -> status (for "learned" toasts)
    replySeq: 0,
    sceneSeq: 0, // bumps on every scene message (the face filter steps once per message)
    epoch: 0, // bumps on reset(): the view builder drops its caption threads and face memory
    cloud: null, // cloud captions' state (P-48): { enabled, state, reason, latency_ms, ... } or null
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
        s.sceneSeq++;
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
          tSeen: s.clock, // last time the engine sent it, changed or not (stale-segment check)
          tFinal: msg.final ? prev?.tFinal ?? s.clock : null,
        });
        break;
      }
      case 'caption_retract': {
        // a segment the engine folded back into its utterance: the view builder drops it
        const prev = s.captions.get(msg.utt_id);
        if (prev) s.captions.set(msg.utt_id, { ...prev, retracted: true });
        break;
      }
      case 'reply_spoken': {
        const id = `reply-${++s.replySeq}`;
        s.captions.set(id, {
          utt_id: id, speaker: { kind: 'you_typed', label: 'You' }, text: String(msg.text ?? ''), final: true,
          lang: 'en', translation: null, tFirst: s.clock, tUpdate: s.clock, tSeen: s.clock, tFinal: s.clock,
        });
        break;
      }
      case 'name_proposal': {
        if (msg.proposal_id == null) break;
        const prev = s.proposals.get(msg.proposal_id);
        // a confirmed name is theirs from now on, even before the scene's label catches up
        if (msg.state === 'confirmed' && msg.track_id != null && msg.name) s.trackNames.set(msg.track_id, String(msg.name));
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
      case 'cloud':
        // cloud captions (P-48): the hub sends every change and the latest right after welcome
        s.cloud = {
          enabled: !!msg.enabled,
          state: typeof msg.state === 'string' ? msg.state : 'off',
          reason: typeof msg.reason === 'string' ? msg.reason : '',
          latencyMs: Number.isFinite(msg.latency_ms) ? msg.latency_ms : null,
        };
        break;
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
    s.epoch++;
    s.scene = { faces: [], offscreen: [], you_speaking: false };
    s.captions.clear();
    s.proposals.clear();
    s.alerts.clear();
    s.toasts = [];
    s.lastStatus.clear();
    s.trackNames.clear();
    if (!keepPeople) {
      s.people.clear();
      s.cloud = null; // a new source: its engine sends its own cloud state
    }
  }

  return { state: s, apply, prune, reset, colorOf, toast };
}

// ---------------------------------------------------------------- view model
const ALERT_META = {
  smoke: { label: 'Smoke alarm', icon: 'flame', level: 'urgent' },
  co: { label: 'CO alarm', icon: 'co', level: 'urgent' },
  doorbell: { label: 'Doorbell', icon: 'bell', level: 'attention' },
  knock: { label: 'Door knock', icon: 'door', level: 'attention' },
  bike: { label: 'Bike bell', icon: 'bike', level: 'info' },
  name: { label: 'Someone called you', icon: 'voice', level: 'attention' },
  vehicle: { label: 'Vehicle', icon: 'truck', level: 'attention' },
};
export const SIDE_TEXT = { left: 'On your left', right: 'On your right', behind: 'Behind you', none: 'Nearby' };

// ---------------------------------------------------------------- caption timing and stability
// Caption guidance (DCMP Captioning Key, BBC subtitle guidelines): keep a caption up long enough
// to read at about 160-180 words a minute, and never move text the viewer is reading.
const READ_WPM = 170;
const READ_BASE_S = 0.8;
/** Seconds needed to read `words` words (only what fits on screen counts). */
export const readTime = (words) => READ_BASE_S + Math.min(words, 18) / (READ_WPM / 60);
const FLIP_HOLD_S = 0.6; // a new speaker for an utterance must persist this long before its text moves
const TRACK_HOLD_S = 1.5; // a face the tracker lost keeps its place (and its bubble) this long
const MAX_BUBBLES = 3; // more than this and the oldest fade once they have been read
const SEG_RE = /^(.*)\.(\d+)$/; // "<utt_id>.<n>": the n-th speaker segment of one utterance
// A line in another language waits for its translation (contracts: 0.5-1.2 s after its final)
// small and dimmed; if none has come this long after the final (translation switched off, or the
// job failed), the original takes the main line after all.
const TRANSLATE_WAIT_S = 3.5;
/** Whether an utterance snapshot is still waiting for its English translation. */
const awaitingTranslation = (u, clock) => u.lang !== 'en' && !u.translation && !(u.final && u.tFinal != null && clock - u.tFinal > TRANSLATE_WAIT_S);
/** The language a resolved utterance shows in on the main line. */
const shownLang = (u) => (u.lang === 'en' || u.translation ? 'en' : u.lang);

// One Euro filter (Casiez, Roussel & Vogel, CHI 2012): heavy smoothing while a face is still,
// little lag while it moves. Values in design px, time in seconds.
function euro(minCutoff, beta) {
  return { x: null, dx: 0, minCutoff, beta };
}
const euroAlpha = (cutoff, dt) => 1 / (1 + 1 / (2 * Math.PI * cutoff * dt));
function euroStep(f, x, dt) {
  if (f.x == null || !(dt > 0)) {
    f.x = x;
    f.dx = 0;
    return x;
  }
  f.dx += euroAlpha(1, dt) * ((x - f.x) / dt - f.dx);
  f.x += euroAlpha(f.minCutoff + f.beta * Math.abs(f.dx), dt) * (x - f.x);
  return f.x;
}
const FACE_KEYS = ['cx', 'cy', 'w', 'h', 'mx', 'my'];
const faceFilters = () => ({
  cx: euro(0.9, 0.012), cy: euro(0.9, 0.012), w: euro(0.4, 0.004), h: euro(0.4, 0.004), mx: euro(1.5, 0.02), my: euro(1.5, 0.02),
});

/** Left / ahead / right with hysteresis, so an arrow never flickers at a boundary. */
function stickySide(prev, dx) {
  if (dx < -0.38) return 'left';
  if (dx > 0.38) return 'right';
  if (Math.abs(dx) < 0.28) return 'ahead';
  return prev ?? (dx < -0.33 ? 'left' : dx > 0.33 ? 'right' : 'ahead');
}
function stickyUp(prev, dy) {
  if (dy < -0.36) return true;
  if (dy > -0.24) return false;
  return prev ?? dy < -0.3;
}

/**
 * Builds the per-frame view model. Holds the face filters, caption threads and direction state,
 * so keep one per page (lens.js makes a fresh one for offline rendering).
 * view = { clock, faces[], bubbles[], offscreen[], lower, you, alerts[], proposals[], status, feed[] ... }
 * view.cloud = { on, state, reason, latencyMs, local }: cloud captions (P-48), shown only from a
 * connected live engine; `local` is true while they have fallen back to local captions.
 *
 * Captions become one thread per speaker: an utterance's ".n" segments and the speaker's next
 * utterances join that speaker's running text, so a bubble or a line fills in instead of being
 * replaced. A speaker attribution that flips for less than FLIP_HOLD_S does not move the text.
 */
export function createViewBuilder(store) {
  const cloudView = { on: false, state: 'off', reason: '', latencyMs: null, local: false }; // reused
  const faceMem = new Map(); // face key -> { filt, flt, sm, face, born, seen, trackId, personId }
  const alias = new Map(); // track_id -> face key (a re-found person keeps their key)
  const owners = new Map(); // utt_id -> { key, cand, since }
  const threads = new Map(); // speaker key -> thread
  const uttThread = new Map(); // utt_id -> speaker key of the thread holding it
  const dirs = new Map(); // key -> { side, up }
  const famMem = new Map(); // utterance id -> its segments, for view.utterances
  const retired = new Set(); // utterances that have faded from view.utterances
  let threadSeq = 0;
  let lastSeq = -1;
  let lastSceneClock = null;
  let epoch = store.state.epoch;

  const keyOfTrack = (tid) => alias.get(tid) ?? `t${tid}`;
  function speakerKey(sp) {
    const kind = sp.kind || 'someone';
    if ((kind === 'face' || kind === 'probable_face') && sp.track_id != null) return keyOfTrack(sp.track_id);
    if (kind === 'offscreen') return `o${sp.person_id ?? sp.label ?? normSide(sp.side)}`;
    if (kind === 'you' || kind === 'you_typed') return 'you';
    return 'someone';
  }
  function dropUtt(id) {
    const k = uttThread.get(id);
    const th = k != null ? threads.get(k) : null;
    if (th) {
      th.utts.delete(id);
      if (!th.utts.size) threads.delete(k);
    }
    uttThread.delete(id);
  }

  return function build(dt, anim, sourceKind) {
    const s = store.state;
    const clock = s.clock;
    const cfg = s.config;
    const fade = cfg.bubble_fade_s;
    if (s.epoch !== epoch) {
      // scene cut, source switch or "forget session": nothing carries over
      epoch = s.epoch;
      faceMem.clear();
      alias.clear();
      owners.clear();
      threads.clear();
      uttThread.clear();
      dirs.clear();
      famMem.clear();
      retired.clear();
    }

    // ---- faces (1920x1080 design space): One Euro filtered per scene message, eased per frame
    const faces = [];
    const byKey = new Map();
    const newScene = s.sceneSeq !== lastSeq;
    const sdt = newScene && lastSceneClock != null ? clamp(clock - lastSceneClock, 1 / 120, 0.3) : 0;
    if (newScene) {
      lastSeq = s.sceneSeq;
      lastSceneClock = clock;
    }
    const kDisp = follow(dt, sourceKind === 'film' ? 30 : 18);
    const rawFaces = s.paused ? [] : s.scene.faces.filter((f) => Array.isArray(f.box) && f.box.length >= 4);
    const presentTracks = new Set(rawFaces.map((f) => f.track_id));
    const usedKeys = new Set();
    for (const f of rawFaces) {
      const [bx, by, bw, bh] = f.box;
      let key = alias.get(f.track_id);
      if (!key || usedKeys.has(key)) {
        key = `t${f.track_id}`;
        // the tracker lost someone and found them again under a new id: keep their card
        if (f.person_id != null) {
          for (const [k, m] of faceMem) {
            if (m.personId === f.person_id && !presentTracks.has(m.trackId) && !usedKeys.has(k) && anim - m.seen < TRACK_HOLD_S) {
              key = k;
              break;
            }
          }
        }
        alias.set(f.track_id, key);
      }
      usedKeys.add(key);
      const raw = {
        cx: (bx + bw / 2) * K, cy: (by + bh / 2) * K, w: bw * K, h: bh * K,
        mx: Array.isArray(f.mouth) ? f.mouth[0] * K : (bx + bw / 2) * K,
        my: Array.isArray(f.mouth) ? f.mouth[1] * K : (by + bh * 0.78) * K,
      };
      let m = faceMem.get(key);
      if (!m || anim - m.seen > TRACK_HOLD_S) {
        m = { filt: faceFilters(), flt: { ...raw }, sm: { ...raw }, born: anim, seen: anim };
        for (const k of FACE_KEYS) euroStep(m.filt[k], raw[k], 0);
        faceMem.set(key, m);
      } else if (newScene) {
        for (const k of FACE_KEYS) m.flt[k] = euroStep(m.filt[k], raw[k], sdt);
      }
      for (const k of FACE_KEYS) m.sm[k] += (m.flt[k] - m.sm[k]) * kDisp;
      m.seen = anim;
      m.trackId = f.track_id;
      if (f.person_id != null) m.personId = f.person_id;
      const sm = m.sm;
      const person = f.person_id != null ? s.people.get(String(f.person_id)) : null;
      let proposal = null;
      for (const p of s.proposals.values()) if (p.track_id === f.track_id) proposal = { ...p, age: clock - p.tState };
      let status = f.status || 'unknown';
      let label = f.label || (NAMED.has(status) ? 'Someone you know' : 'Someone new');
      let named = null;
      if (!NAMED.has(status) && s.trackNames.has(f.track_id)) {
        // their name was confirmed a moment ago: use it now, not the description
        label = s.trackNames.get(f.track_id);
        status = 'named';
        for (const p of s.people.values()) if (p.name === label) named = p;
      }
      const face = {
        key,
        track_id: f.track_id,
        person_id: f.person_id ?? null,
        cx: sm.cx, cy: sm.cy, w: sm.w, h: sm.h, mx: sm.mx, my: sm.my,
        top: sm.cy - sm.h * 0.72, // top of the head, a little above the face box
        born: m.born,
        tiny: f.box[2] < 40,
        ghost: false,
        label,
        status,
        known: NAMED.has(status),
        relation: person?.relation ?? named?.relation ?? null,
        color: named?.color ?? store.colorOf(f.person_id, f.track_id, status),
        isSpeaker: !!f.is_speaker,
        dashed: !!f.dashed,
        lip: clamp(Number(f.lip_score) || 0),
        proposal,
      };
      m.face = face;
      faces.push(face);
      byKey.set(key, face);
    }
    // faces the tracker lost a moment ago hold their last place (no brackets, no voice)
    for (const [key, m] of faceMem) {
      if (usedKeys.has(key)) continue;
      if (!s.paused && m.face && anim - m.seen <= TRACK_HOLD_S) {
        const g = { ...m.face, ghost: true, isSpeaker: false, lip: 0, proposal: null };
        faces.push(g);
        byKey.set(key, g);
      } else if (anim - m.seen > 4 || anim < m.seen - 1) {
        faceMem.delete(key);
      }
    }
    for (const [tid, key] of alias) if (!faceMem.has(key)) alias.delete(tid);

    // ---- captions -> one thread per speaker
    const caps = [...s.captions.values()];
    const famSeen = new Map();
    const famFirst = new Map();
    const baseOf = (id) => SEG_RE.exec(String(id))?.[1] ?? String(id);
    const segOf = (id) => Number(SEG_RE.exec(String(id))?.[2] ?? 0);
    for (const c of caps) {
      const b = baseOf(c.utt_id);
      famSeen.set(b, Math.max(famSeen.get(b) ?? -1e9, c.tSeen ?? c.tUpdate));
      famFirst.set(b, Math.min(famFirst.get(b) ?? 1e9, c.tFirst));
    }
    const staleNow = [];
    for (const c of caps) {
      // a draft segment the engine has since folded back into its utterance is stale: drop it
      const stale = c.retracted || (!c.final && SEG_RE.test(String(c.utt_id)) && famSeen.get(baseOf(c.utt_id)) - (c.tSeen ?? c.tUpdate) > 0.3);
      if (stale) {
        dropUtt(c.utt_id);
        staleNow.push(c.utt_id);
        continue;
      }
      const rk = speakerKey(c.speaker || {});
      let o = owners.get(c.utt_id);
      if (!o) {
        // a new ".n" segment starts with its utterance's speaker: the engine splits a sentence
        // when the attribution wavers, and a brief waver must not move words to another bubble
        const fam = SEG_RE.test(String(c.utt_id)) ? owners.get(baseOf(c.utt_id)) : null;
        o = fam && fam.key !== rk && fam.key !== 'someone' ? { key: fam.key, cand: rk, since: clock } : { key: rk, cand: null, since: 0 };
        owners.set(c.utt_id, o);
      } else if (rk === o.key || rk === 'someone') o.cand = null; // losing the speaker moves nothing
      else if (o.key === 'someone') {
        o.key = rk; // an unplaced voice found its face: take it at once
        o.cand = null;
      } else if (o.cand !== rk) {
        o.cand = rk;
        o.since = clock;
      } else if (clock - o.since >= FLIP_HOLD_S) {
        o.key = rk;
        o.cand = null;
      }
      const prevKey = uttThread.get(c.utt_id);
      if (prevKey != null && prevKey !== o.key) dropUtt(c.utt_id);
      let th = threads.get(o.key);
      if (!th) {
        th = { id: ++threadSeq, key: o.key, utts: new Map(), tFirst: c.tFirst, tLast: c.tUpdate, speaker: c.speaker || {}, face: null };
        threads.set(o.key, th);
      }
      const order = [famFirst.get(baseOf(c.utt_id)), segOf(c.utt_id)];
      const snap = th.utts.get(c.utt_id);
      if (!snap || snap.src !== c) {
        th.utts.set(c.utt_id, {
          src: c, id: c.utt_id, text: c.text, translation: c.translation, lang: c.lang, final: c.final,
          tFirst: c.tFirst, tUpdate: c.tUpdate, tFinal: c.tFinal, order,
        });
      } else snap.order = order;
      uttThread.set(c.utt_id, o.key);
      th.tFirst = Math.min(th.tFirst, c.tFirst);
      if (c.tUpdate >= th.tLast) th.speaker = c.speaker || {};
      th.tLast = Math.max(th.tLast, c.tUpdate);
    }
    for (const [id] of owners) if (!s.captions.has(id) && !uttThread.has(id)) owners.delete(id);
    if (retired.size > 500) for (const id of retired) if (!s.captions.has(id) && ![...uttThread.keys()].some((u) => baseOf(u) === id)) retired.delete(id);

    const offMap = new Map();
    for (const o of s.paused ? [] : s.scene.offscreen) {
      const key = `o${o.person_id ?? o.label ?? normSide(o.side)}`;
      offMap.set(key, {
        key, name: o.label || 'Someone', side: normSide(o.side), color: store.colorOf(o.person_id, null, o.person_id != null ? 'named' : 'unknown'),
        person_id: o.person_id ?? null, bubble: null,
      });
    }

    // ---- threads -> bubbles, docked speakers, the lower caption and the You bar
    const bubbles = [];
    let lower = null;
    let you = null;
    const feed = [];
    const alive = [];
    for (const [key, th] of threads) {
      if (clock < th.tFirst - 1) {
        for (const id of th.utts.keys()) uttThread.delete(id);
        threads.delete(key); // the film jumped back before it began
        continue;
      }
      const utts = [...th.utts.values()].sort((a, b) => a.order[0] - b.order[0] || a.order[1] - b.order[1]);
      const latest = utts.reduce((a, u) => (u.tFirst >= a.tFirst ? u : a), utts[0]);
      // the main line never mixes languages: a line still waiting for its translation stays off
      // it (shown small and dimmed as `pending`), and only lines in the newest shown language join
      const waiting = utts.filter((u) => awaitingTranslation(u, clock));
      const resolved = utts.filter((u) => !waiting.includes(u));
      const lastResolved = resolved.reduce((a, u) => (!a || u.tFirst >= a.tFirst ? u : a), null);
      const lang = lastResolved ? shownLang(lastResolved) : 'en';
      const tokens = [];
      for (const u of utts) {
        const tr = !!u.translation && u.lang !== 'en';
        const wait = waiting.includes(u);
        // u.tokens: this utterance's own line (the compact displays give each utterance its own)
        u.tokens = wait ? [] : String(tr ? u.translation : u.text).split(/\s+/).filter(Boolean).map((w) => ({ text: w, final: u.final }));
        u.pending = wait && u.text ? u.text : null;
        if (!wait && shownLang(u) === lang) tokens.push(...u.tokens);
      }
      const pendingText = waiting.map((u) => u.text).filter(Boolean).join(' ');
      if (!tokens.length && !pendingText) continue;
      const age = clock - th.tLast;
      const read = readTime(tokens.length + (pendingText ? pendingText.split(/\s+/).length : 0));
      const hold = Math.max(fade, read);
      alive.push({ key, th, utts, latest, lastResolved, pendingText, tokens, age, read, alpha: age < hold ? 1 : clamp(1 - (age - hold) / 0.5) });
    }
    // no more than MAX_BUBBLES at once: the oldest ones leave early, once they have been read
    const ranked = alive.filter((x) => x.key !== 'you').sort((a, b) => b.th.tLast - a.th.tLast);
    ranked.forEach((x, i) => {
      if (i >= MAX_BUBBLES) x.alpha = Math.min(x.alpha, clamp(1 - (x.age - x.read) / 0.5));
    });
    const current = ranked.find((x) => x.alpha > 0)?.th.id ?? null;
    for (const x of alive) {
      const { key, th, latest, lastResolved, pendingText, tokens, age, alpha } = x;
      x.b = null;
      if (alpha <= 0) {
        for (const id of th.utts.keys()) uttThread.delete(id);
        threads.delete(key);
        continue;
      }
      const sp = th.speaker || {};
      const pending = !!pendingText;
      // the language tag and the small original line: the line waiting for its translation, else
      // the newest translated one
      const translated = !pending && !!lastResolved?.translation && lastResolved.lang !== 'en';
      const tagLang = pending ? [...x.utts].reverse().find((u) => u.pending)?.lang ?? latest.lang : (lastResolved ?? latest).lang;
      const text = tokens.map((t) => t.text).join(' ');
      let lines = wrapChars(text, cfg.bubble_chars);
      const cut = lines.length > cfg.bubble_lines;
      if (cut) lines = lines.slice(-cfg.bubble_lines);
      const b = {
        key, id: th.id, utt_id: latest.id, kind: sp.kind || 'someone', text, tokens, lines, cut, tFirst: th.tFirst,
        orig: pending ? pendingText : translated ? lastResolved.text : null, lang: tagLang, translated, pending,
        final: latest.final, alpha, tUpdate: th.tLast, age, current: th.id === current,
        speaking: !latest.final && clock - th.tLast < 1.2,
        dashed: sp.kind === 'probable_face',
        name: sp.label || 'Someone', color: NEUTRAL, face: null, side: normSide(sp.side), relation: null,
      };
      x.b = b;
      if (key.startsWith('t')) {
        let face = byKey.get(key);
        if (face && !face.ghost) {
          th.face = face;
          th.faceSeen = clock;
        } else if (!face && th.face) {
          if (clock - (th.faceSeen ?? clock) <= TRACK_HOLD_S) face = { ...th.face, ghost: true, isSpeaker: false, lip: 0, proposal: null }; // hold its place
          else {
            // Their face has left the view: keep its identity and exit direction for the
            // bottom caption, instead of leaving text over another person.
            const lk = `o~${key}`;
            const side = th.face.cx < 960 ? 'left' : 'right';
            offMap.set(lk, { key: lk, name: th.face.label, side, color: th.face.color, person_id: th.face.person_id, bubble: b });
            Object.assign(b, { name: th.face.label, color: th.face.color, side, known: th.face.known, relation: th.face.relation });
            feed.push(b);
            continue;
          }
        }
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
        b.speaking = b.speaking || !latest.final;
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
    // Off-screen speech shares one stable bottom caption. Its arrow carries the direction;
    // a visible speaker still uses the bubble attached to their face above.
    for (const o of offMap.values()) {
      if (o.bubble && (!lower || o.bubble.tUpdate > lower.tUpdate)) lower = o.bubble;
    }
    feed.sort((a, b) => b.tUpdate - a.tUpdate || a.final - b.final || b.tFirst - a.tFirst);

    // utterances in the order they were spoken, each with all of its ".n" segments in order: the
    // unit for head-locked displays, where words must stay put even if their speaker changes.
    // Each utterance has its own reading time and, once it has faded, never comes back.
    for (const x of alive) {
      if (!x.b) continue;
      for (const u of x.utts) {
        const id = baseOf(u.id);
        if (retired.has(id)) continue;
        let f = famMem.get(id);
        if (!f) famMem.set(id, (f = { id, segs: new Map(), tFirst: u.tFirst, tUpdate: u.tUpdate }));
        f.segs.set(segOf(u.id), { tokens: u.tokens, pending: u.pending, lang: u.lang, final: u.final, b: x.b, own: u.id === id });
        f.tFirst = Math.min(f.tFirst, u.tFirst);
        f.tUpdate = Math.max(f.tUpdate, u.tUpdate);
      }
    }
    for (const id of staleNow) famMem.get(baseOf(id))?.segs.delete(segOf(id));
    const utterances = [];
    for (const [id, f] of famMem) {
      const segs = [...f.segs.entries()].sort((p, q) => p[0] - q[0]).map((e) => e[1]);
      const tokens = segs.flatMap((g) => g.tokens);
      const pending = segs.map((g) => g.pending).filter(Boolean).join(' ');
      const age = clock - f.tUpdate;
      const hold = Math.max(fade, readTime(tokens.length + (pending ? pending.split(/\s+/).length : 0)));
      const alpha = age < hold ? 1 : clamp(1 - (age - hold) / 0.5);
      if ((!tokens.length && !pending) || alpha <= 0 || clock < f.tFirst - 1) {
        famMem.delete(id);
        retired.add(id);
        continue;
      }
      // its speaker: whoever holds most of its words (ties go to the utterance's own segment)
      const words = new Map();
      for (const g of segs) words.set(g.b.key, (words.get(g.b.key) ?? 0) + g.tokens.length + (g.pending ? 0.25 : 0) + (g.own ? 0.5 : 0));
      const key = [...words.entries()].sort((p, q) => q[1] - p[1])[0][0];
      const b = segs.filter((g) => g.b.key === key).pop().b;
      const lang = segs.find((g) => g.pending)?.lang ?? null;
      utterances.push({ id, b, key, tokens, pending: pending || null, lang, tFirst: f.tFirst, tUpdate: f.tUpdate, alpha, final: segs.every((g) => g.final) });
    }
    utterances.sort((a, b) => a.tFirst - b.tFirst);
    // the newest line still waiting for its translation, for the compact displays' small line
    const waitingUtt = [...utterances].reverse().find((u) => u.pending);
    const translating = waitingUtt ? { text: waitingUtt.pending, lang: waitingUtt.lang ?? waitingUtt.b.lang, name: waitingUtt.b.name, key: waitingUtt.key, alpha: waitingUtt.alpha } : null;

    // direction of every caption relative to the wearer (mono and corner), with hysteresis
    for (const b of feed) {
      if (b.face) {
        const dx = (b.face.cx - W / 2) / (W / 2);
        const dy = (b.face.cy - H / 2) / (H / 2);
        const prev = dirs.get(b.key);
        const d = { side: stickySide(prev?.side, dx), up: stickyUp(prev?.up, dy) };
        dirs.set(b.key, d);
        b.dir = { dx, dy, side: d.side, up: d.up };
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
      if (!faces.some((f) => !f.ghost && f.track_id === p.track_id)) proposals.push(item);
    }
    const pendingProposal = [...s.proposals.values()].filter((p) => p.state === 'proposed').sort((a, b) => b.tStart - a.tStart)[0] ?? null;

    const speaking = faces.some((f) => f.isSpeaker) || feed.some((b) => b.speaking) || s.scene.you_speaking;
    let status = 'listening';
    if (sourceKind === 'live' && !s.connected) status = 'connecting';
    else if (s.paused) status = 'paused';
    else if (activeAlert) status = 'alert';

    const cl = sourceKind === 'live' && s.connected ? s.cloud : null;
    cloudView.on = !!cl?.enabled;
    cloudView.state = cl ? cl.state : 'off';
    cloudView.reason = cl ? cl.reason : '';
    cloudView.latencyMs = cl ? cl.latencyMs : null;
    cloudView.local = cloudView.on && (cloudView.state === 'fallback' || cloudView.state === 'unavailable');

    return {
      clock, anim, dt, sourceKind, config: cfg, paused: s.paused, pausedAge: anim - s.tPaused,
      connected: s.connected, status, speaking,
      faces, bubbles, offscreen: [...offMap.values()], lower, you, feed, utterances, translating,
      alerts, activeAlert, proposals, pendingProposal, cloud: cloudView,
      toasts: s.toasts.map((t) => ({ ...t, age: anim - t.t })),
    };
  };
}
