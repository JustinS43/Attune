/*
 * Film source: plays the launch film's footage in the browser and turns the film's script
 * (timeline.json + face tracks) into the SAME contract messages the engine sends - `scene`,
 * `caption`, `name_proposal`, `alert`, `paused` - so every glasses mode renders the film and
 * the live engine through one code path.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-06.
 *
 * Assets (default /data/reels/film/, override with ?assets=): <clip>.mp4, tracks_<clip>.json
 * (normalized cx/cy/w/h per frame, from the film's tools/track.py), timeline.json. If the
 * timeline can't be fetched, the copy next to this file is used.
 *
 * Commands work like the engine's: name.answer (Y/N) and alert.ack (A) change what the script
 * emits next, pause.toggle stops recognition, session.forget clears the answers.
 */

import { createFilmPlayer } from './player.js';
import { createQuickStart } from './quickstart.js';

// which face tracks belong to which person (several ids = the same person re-acquired later)
const PEOPLE_TRACKS = {
  dinner_group: { mark: [0], jess: [2, 1] },
  mom_talk: { mom: [0] },
  grandpa: { grandpa: [0] },
  cafe_friends: { mia: [1, 2], andre: [0] },
  cafe_newperson: { leo: [0] },
  mom_toast: { mom: [0] },
};
// tracked faces that are not in the script: strangers with a description from the fixed word lists
const STRANGERS = {
  cafe_newperson: [{ track: 1, id: 90, label: 'Person in blue top' }],
};
// how the film's people are recognised over time (descriptions use only the contract word lists)
const SCRIPT = {
  mom: { unknownLabel: 'Person in black top', enrolledAt: { qs_mom: 20.1 } },
  grandpa: { unknownLabel: 'Person in red sweater', enrolledAt: { qs_grandpa: 22.9 } },
  andre: { unknownLabel: 'Person in red hat', proposeAt: 28.2, confirmAt: 33.2 },
};
const PROBABLE = new Set(['mark1']); // lines attributed without a clear lip signal (dashed tail)
const SEGMENT_LABELS = {
  open: 'Christmas dinner',
  dinner: 'Christmas dinner',
  qs_mom: 'Quick start · Mom',
  qs_grandpa: 'Quick start · Grandpa',
  cafe: 'Diner · meeting someone new',
  translate: 'Diner · Spanish → English',
  doorbell: 'Home · doorbell',
  alarm: 'Kitchen · smoke alarm',
  street: 'Street · sounds around you',
  finale: 'Christmas toast',
};
const FIRST_START = 6.6; // skip the film's silent opening

const dirToSide = (d) => {
  const a = ((d % 360) + 360) % 360;
  if (a >= 150 && a <= 210) return 'B';
  return a < 180 ? 'R' : 'L';
};

async function fetchJSON(url) {
  const r = await fetch(url, { cache: 'force-cache' });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

export async function createFilmSource({ assets, layer, emit, reset, setClock }) {
  const base = assets.endsWith('/') ? assets : `${assets}/`;
  let TL;
  let timelineFrom = base;
  try {
    TL = await fetchJSON(`${base}timeline.json`);
  } catch {
    TL = await fetchJSON(new URL('./timeline.json', import.meta.url).href);
    timelineFrom = 'fallback';
  }

  const tracks = {};
  await Promise.all(Object.keys(PEOPLE_TRACKS).map(async (clip) => {
    try {
      tracks[clip] = await fetchJSON(`${base}tracks_${clip}.json`);
    } catch {
      tracks[clip] = null;
    }
  }));

  const personIds = Object.keys(TL.people).filter((p) => p !== 'you');
  const trackIdOf = (pid) => personIds.indexOf(pid) + 1;
  // Quick Start (P-29): saving Mom and Grandpa, as the save flow's messages (quickstart.js)
  const quickStart = createQuickStart(TL, (sc) => Object.keys(PEOPLE_TRACKS[sc.clip] ?? {})[0], trackIdOf);

  // ---- segments: film scenes with footage, contiguous shots of one clip merged
  const segs = [];
  for (const sc of TL.scenes) {
    if (!sc.clip) continue;
    const prev = segs[segs.length - 1];
    if (prev && prev.clip === sc.clip && Math.abs(prev.in + (prev.t1 - prev.t0) - sc.in) < 0.05 && Math.abs(prev.t1 - sc.t0) < 0.05) {
      prev.t1 = sc.t1;
      continue;
    }
    segs.push({ id: sc.id, clip: sc.clip, t0: sc.t0, t1: sc.t1, in: sc.in, label: SEGMENT_LABELS[sc.id] ?? sc.id });
  }
  const dinner = segs.find((s) => s.clip === 'dinner_group');
  if (dinner) dinner.start = FIRST_START;
  for (const s of segs) s.start ??= s.t0;

  // ---- footage: two <video> elements painted into a canvas, with a watchdog (player.js)
  const player = createFilmPlayer({ layer, base });

  let cur = 0;
  let playing = true;
  let active = false;
  let recogPaused = false;
  let filmT = segs[0]?.start ?? 0;
  const answers = new Map(); // proposal_id -> { accept, t }
  const acks = new Map(); // alert_id -> t
  const sent = new Map(); // dedupe key -> json
  let lastNudge = 0;

  function send(key, msg) {
    const j = JSON.stringify(msg);
    if (sent.get(key) === j) return;
    sent.set(key, j);
    emit(msg);
  }

  function go(i, t = null) {
    cur = ((i % segs.length) + segs.length) % segs.length;
    const seg = segs[cur];
    const start = t ?? seg.start;
    // show this clip (holding the last frame until it has a picture) and cue the next one
    // in the spare element so the cut to it is instant
    const next = segs[(cur + 1) % segs.length];
    player.cue(seg.clip, seg.in + (start - seg.t0), { clip: next.clip, t: next.in + (next.start - next.t0) });
    player.setPlaying(playing && active);
    filmT = start;
    setClock(start);
    sent.clear();
    for (const [id, a] of answers) if (a.t > start) answers.delete(id);
    for (const [id, a] of acks) if (a > start) acks.delete(id);
    if (cur === 0) {
      answers.clear();
      acks.clear();
    }
    reset();
    emit({ type: 'paused', paused: recogPaused });
  }

  // ---- face tracks
  function sample(tr, f) {
    if (f < tr.start || f > tr.end) return null;
    const k = f - tr.start;
    const k0 = Math.floor(k);
    const k1 = Math.min(k0 + 1, tr.end - tr.start);
    const u = k - k0;
    const g = (a) => (a ? a[k0] + (a[k1] - a[k0]) * u : undefined);
    return { cx: g(tr.cx), cy: g(tr.cy), w: g(tr.w), h: g(tr.h), mx: g(tr.mx), my: g(tr.my) };
  }
  function faceFromIds(clip, ids, f) {
    const data = tracks[clip];
    if (!data) return null;
    const trs = ids.map((i) => data.tracks[i]).filter(Boolean);
    for (const tr of trs) {
      const s = sample(tr, f);
      if (s) return s;
    }
    const sorted = [...trs].sort((a, b) => a.start - b.start);
    for (let i = 0; i < sorted.length - 1; i++) {
      const a = sorted[i];
      const b = sorted[i + 1];
      if (f > a.end && f < b.start && b.start - a.end < 45) {
        const sa = sample(a, a.end);
        const sb = sample(b, b.start);
        const u = (f - a.end) / (b.start - a.end);
        const o = {};
        for (const key of Object.keys(sa)) o[key] = sa[key] === undefined ? undefined : sa[key] + (sb[key] - sa[key]) * u;
        return o;
      }
    }
    return null;
  }
  const OFFS = [-0.24, -0.16, -0.08, 0, 0.08, 0.16, 0.24];
  const WTS = OFFS.map((o) => Math.exp(-0.5 * (o / 0.12) ** 2));
  /** Face at clip time `ct`, smoothed over +-0.24 s like the film renderer. */
  function smoothFace(clip, ids, ct, seg) {
    const data = tracks[clip];
    if (!data) return null;
    const lo = seg.in;
    const hi = seg.in + (seg.t1 - seg.t0) - 1e-3;
    let sw = 0;
    let center = null;
    const acc = { cx: 0, cy: 0, w: 0, h: 0, mx: 0, my: 0 };
    OFFS.forEach((o, i) => {
      const tt = Math.min(hi, Math.max(lo, ct + o));
      const s = faceFromIds(clip, ids, tt * data.fps);
      if (!s) return;
      if (o === 0) center = s;
      for (const key in acc) acc[key] += (s[key] ?? (key === 'mx' ? s.cx : s.cy + s.h * 0.3)) * WTS[i];
      sw += WTS[i];
    });
    if (!center || sw < 1.2) return null;
    for (const key in acc) acc[key] /= sw;
    return acc;
  }
  const toBox = (s) => [(s.cx - s.w / 2) * 1280, (s.cy - s.h / 2) * 720, s.w * 1280, s.h * 720];

  function sceneIdAt(t) {
    return (TL.scenes.find((s) => t >= s.t0 && t < s.t1) ?? TL.scenes[0]).id;
  }

  function personState(pid, sceneId, t) {
    const p = TL.people[pid];
    const sc = SCRIPT[pid];
    if (pid === 'andre' && sc) {
      const ans = answers.get('p-andre');
      if (ans && !ans.accept) return { status: 'unknown', label: sc.unknownLabel };
      const confirmT = ans?.accept ? Math.min(ans.t, sc.confirmAt) : sc.confirmAt;
      if (t >= confirmT) return { status: 'named', label: p.name };
      if (t >= sc.proposeAt) return { status: 'proposed', label: sc.unknownLabel };
      return { status: 'unknown', label: sc.unknownLabel };
    }
    const at = sc?.enrolledAt?.[sceneId];
    if (at != null && t < at) return { status: 'unknown', label: sc.unknownLabel };
    return { status: 'enrolled', label: p.name };
  }

  /** Index of the segment that shows film time t (the last one started, in a gap between them). */
  function segAt(t) {
    let k = segs.findIndex((s) => t >= s.t0 && t < s.t1);
    if (k < 0) {
      k = 0;
      for (let i = 0; i < segs.length; i++) if (segs[i].t0 <= t) k = i;
    }
    return k;
  }

  // ---- per-frame emission
  // tick() follows the playing video; tick(at) is offline rendering: film time is `at` exactly,
  // whatever the video shows (the caller seeks the picture separately with frameAt).
  function tick(at) {
    if (!segs.length) return;
    let seg = segs[cur];
    player.update();
    const offline = at != null;
    // until the new clip has a picture, the old frame holds and nothing is drawn over it
    if (!offline && !player.ready) return;
    const v = player.video;
    const segEnd = seg.in + (seg.t1 - seg.t0);
    if (!offline && active && playing && (v.ended || v.currentTime >= segEnd - 0.03)) {
      go(cur + 1);
      seg = segs[cur];
      return;
    }
    // browsers may leave a clip paused after a seek or a blocked autoplay: nudge it
    if (!offline && active && playing && v.paused && v.readyState >= 2 && performance.now() - lastNudge > 500) {
      lastNudge = performance.now();
      v.play().catch(() => {});
    }
    const media = offline ? seg.in + (at - seg.t0) : v.presented ?? v.currentTime;
    filmT = Math.min(seg.t1, Math.max(seg.t0, seg.t0 + (media - seg.in)));
    const t = filmT;
    setClock(t);
    const ct = seg.in + (t - seg.t0);
    const sceneId = sceneIdAt(t);

    const lines = TL.speech.filter((s) => s.t0 >= seg.t0 && s.t0 < seg.t1);
    const speakingNow = (pid) => lines.find((s) => s.who === pid && t >= s.t0 && t <= s.t1 + 0.1);

    // faces
    const faces = [];
    const faceOf = {};
    if (!recogPaused) {
      for (const [pid, ids] of Object.entries(PEOPLE_TRACKS[seg.clip] ?? {})) {
        const s = smoothFace(seg.clip, ids, ct, seg);
        if (!s) continue;
        const st = personState(pid, sceneId, t);
        const line = speakingNow(pid);
        const face = {
          track_id: trackIdOf(pid),
          box: toBox(s),
          mouth: [s.mx * 1280, s.my * 720],
          label: st.label,
          status: st.status,
          lip_score: line ? 0.62 + 0.3 * Math.abs(Math.sin(t * 9)) : 0.04,
          is_speaker: !!line && !PROBABLE.has(line.id),
          dashed: !!line && PROBABLE.has(line.id),
          person_id: st.status === 'unknown' || st.status === 'proposed' ? null : pid,
        };
        faces.push(face);
        faceOf[pid] = face;
      }
      for (const sg of STRANGERS[seg.clip] ?? []) {
        const s = smoothFace(seg.clip, [sg.track], ct, seg);
        if (!s) continue;
        faces.push({ track_id: sg.id, box: toBox(s), label: sg.label, status: 'unknown', lip_score: 0.02, is_speaker: false, dashed: false, person_id: null });
      }
    }
    const offscreen = [];
    let youSpeaking = false;
    if (!recogPaused) {
      for (const s of lines) {
        if (s.offscreen && t >= s.t0 - 0.3 && t <= s.t1 + 1.6) {
          offscreen.push({ person_id: s.who, label: TL.people[s.who].name, side: s.offscreen.side });
        }
        if (s.who === 'you' && t >= s.t0 && t <= s.t1) youSpeaking = true;
      }
    }
    emit({ type: 'scene', frame_no: Math.round(ct * 30), t, faces, offscreen, you_speaking: youSpeaking });

    // captions: words stream in over the line, final at its end; Spanish gets its translation after
    if (!recogPaused) {
      for (const s of lines) {
        if (t < s.t0) continue;
        const es = !!s.orig;
        const srcText = es ? s.orig : s.text;
        const words = srcText.split(' ');
        const dur = s.t1 - s.t0;
        const draftEnd = es ? s.t0 + dur * 0.55 : s.t1;
        const n = Math.max(1, Math.ceil(words.length * Math.min(1, (t - s.t0) / Math.max(0.2, draftEnd - s.t0 - 0.1))));
        const final = t >= draftEnd;
        let speaker;
        if (s.who === 'you') speaker = { kind: 'you', track_id: null, person_id: null, label: 'You', side: 'none' };
        else if (s.offscreen) speaker = { kind: 'offscreen', track_id: null, person_id: s.who, label: TL.people[s.who].name, side: s.offscreen.side };
        else {
          const f = faceOf[s.who];
          speaker = {
            kind: PROBABLE.has(s.id) ? 'probable_face' : f ? 'face' : 'someone',
            track_id: f ? f.track_id : null,
            person_id: f?.person_id ?? null,
            label: f ? f.label : TL.people[s.who].name,
            side: 'none',
          };
        }
        const msg = {
          type: 'caption', utt_id: s.id, speaker, text: words.slice(0, n).join(' '), final,
          lang: es ? (s.lang || 'es').toLowerCase() : 'en',
          words: words.slice(0, n).map((w, i) => [w, s.t0 + (i / words.length) * dur, s.t0 + ((i + 1) / words.length) * dur]),
        };
        if (es && t >= draftEnd + 0.35) msg.translation = s.text;
        const { words: _w, ...key } = msg;
        const j = JSON.stringify(key);
        if (sent.get(`cap:${s.id}`) !== j) {
          sent.set(`cap:${s.id}`, j);
          emit(msg);
        }
      }

      // name proposal (the diner: Mia introduces Andre; you say his name back)
      const andre = faceOf.andre;
      if (andre && SCRIPT.andre && t >= SCRIPT.andre.proposeAt) {
        const ans = answers.get('p-andre');
        const st = personState('andre', sceneId, t).status;
        const state = ans && !ans.accept ? 'rejected' : st === 'named' ? 'confirmed' : 'proposed';
        send('prop:andre', { type: 'name_proposal', proposal_id: 'p-andre', track_id: andre.track_id, name: 'Andre', state, expires_t: SCRIPT.andre.confirmAt });
      }
      for (const [key, msg] of quickStart(sceneId, t)) send(key, msg);
    }

    // sound alerts (they keep working while recognition is paused: safety first)
    for (const al of TL.alerts) {
      if (al.t0 < seg.t0 || al.t0 >= seg.t1 || t < al.t0) continue;
      const ack = acks.get(al.id);
      let state = 'start';
      let count = 1;
      if (t >= al.t1 || (ack != null && t >= ack + 2)) state = 'clear';
      else if (ack != null) state = 'acknowledged';
      else if (al.count_at && t >= al.count_at) {
        state = 'update';
        count = 2;
      }
      send(`al:${al.id}`, {
        type: 'alert', alert_id: al.id, kind: al.kind, side: dirToSide(al.dir), confidence: 0.94, state,
        label: al.label, detail: al.detail, level: al.level, count,
      });
    }
  }

  // ---- public
  return {
    kind: 'film',
    timelineFrom,
    get clock() {
      return filmT;
    },
    get info() {
      const seg = segs[cur];
      return {
        index: cur, count: segs.length, label: seg?.label ?? '', playing, error: player.error,
        progress: seg ? (filmT - seg.t0) / (seg.t1 - seg.t0) : 0,
        t: filmT,
      };
    },
    /** What the glass panels blur: the canvas holding the film picture. */
    get blurSource() {
      return player.frameSource;
    },
    /** The footage player (two elements, canvas, watchdog stats) for debugging and tests. */
    player,
    start(t) {
      active = true;
      layer.classList.add('film-on');
      let i = 0;
      if (Number.isFinite(t)) {
        const k = segs.findIndex((s) => t >= s.t0 && t < s.t1);
        if (k >= 0) i = k;
        go(i, k >= 0 ? t : null);
      } else go(cur, filmT);
      // announce the cast like the engine's people list, with the film's colours and relations
      emit({ type: 'welcome', session_id: 'film', paused: recogPaused, config: { bubble_chars: 42, bubble_lines: 2, bubble_fade_s: 4 } });
      emit({ type: 'people', people: personIds.map((pid) => ({ person_id: pid, name: TL.people[pid].name, color: TL.people[pid].color, relation: TL.people[pid].relation })) });
      sent.clear();
    },
    stop() {
      active = false;
      layer.classList.remove('film-on');
      player.release(); // frees both video decoders while Live runs
    },
    tick,
    get playing() {
      return playing;
    },
    togglePlay() {
      playing = !playing;
      player.setPlaying(playing && active);
      return playing;
    },
    next() {
      go(cur + 1);
    },
    prev() {
      const seg = segs[cur];
      go(filmT - seg.start > 2 ? cur : cur - 1);
    },
    seek(t) {
      const k = segs.findIndex((s) => t >= s.t0 && t < s.t1);
      if (k >= 0) go(k, t);
    },
    /** Offline rendering: the segment index for film time t, and a segment's time range. */
    segmentAt: segAt,
    segmentRange(k) {
      const s = segs[k];
      return { t0: s.t0, t1: s.t1, start: s.start };
    },
    get segment() {
      return cur;
    },
    /** Offline rendering: stop playback and restart the script at film time t (clears the HUD state). */
    offlineStart(t) {
      if (playing) {
        playing = false;
        player.setPlaying(false);
      }
      const k = segAt(t);
      go(k, Math.min(segs[k].t1, Math.max(segs[k].t0, t)));
    },
    /** Offline rendering: show exactly the video frame for film time t; resolves once it is painted. */
    async frameAt(t, timeoutMs = 8000) {
      const seg = segs[cur];
      const tt = Math.min(seg.t1 - 0.001, Math.max(seg.t0, t));
      const ct = seg.in + (tt - seg.t0) + 0.001; // a hair past the frame's start, never the previous frame
      const next = segs[(cur + 1) % segs.length];
      player.cue(seg.clip, ct, { clip: next.clip, t: next.in + (next.start - next.t0) });
      const t0 = performance.now();
      for (;;) {
        const v = player.video;
        player.update();
        if (player.ready && v && !v.seeking && v.readyState >= 2 && Math.abs(v.currentTime - ct) < 0.02 && !v.dirty) return true;
        if (performance.now() - t0 > timeoutMs) return false;
        await new Promise((r) => setTimeout(r, 15));
      }
    },
    /** Same commands as the engine link (docs/contracts.md section 4). */
    send(name, args = {}) {
      if (name === 'name.answer' && args.proposal_id != null) answers.set(args.proposal_id, { accept: !!args.accept, t: filmT });
      else if ((name === 'alert.ack' || name === 'alert.snooze') && args.alert_id != null) acks.set(args.alert_id, filmT);
      else if (name === 'pause.toggle') {
        recogPaused = !recogPaused;
        emit({ type: 'paused', paused: recogPaused });
      } else if (name === 'session.forget') {
        answers.clear();
        acks.clear();
        sent.clear();
      }
    },
  };
}
