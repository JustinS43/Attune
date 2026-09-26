/*
 * The film's Quick Start (about 15-25 s) as the save flow's own contract messages.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-29. Contracts: docs/contracts.md ("Save a person").
 *
 * In the launch film the wearer adds Mom, then Grandpa. The Film source replays that as the
 * same messages the engine sends after a double tap and the person's consent on the phone -
 * `save_request`, `enroll_progress` (face, then voice) and `enroll_result` - so every glasses
 * mode draws it with web/lens/save.js, exactly like a live save. The timing comes from
 * timeline.json alone: each "qs_*" scene's ui_scan cue is the face scan (start and duration),
 * its first ui_success the face result, the person's speech in that scene the voice sample, and
 * the next ui_success (or the first ui_tick) the voice result; the saved card then holds until
 * just before the scene cuts. Everything is a function of film time, so renderAt(t) is exact.
 */

const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const q = (x) => Math.round(x * 40) / 40; // progress in 1/40 steps: fewer messages, same look

/**
 * personOf(scene) -> the person id this Quick Start scene saves; trackIdOf(pid) -> their track id.
 * Returns messages(sceneId, t) -> [[dedupeKey, message], ...] for film time t.
 */
export function createQuickStart(TL, personOf, trackIdOf) {
  const plans = new Map();
  for (const sc of TL.scenes ?? []) {
    if (!String(sc.id).startsWith('qs')) continue;
    const pid = personOf(sc);
    if (!pid || !TL.people?.[pid]) continue;
    const cues = (TL.cues ?? []).filter((c) => c.t >= sc.t0 && c.t < sc.t1);
    const scan = cues.find((c) => c.type === 'ui_scan');
    const oks = cues.filter((c) => c.type === 'ui_success').map((c) => c.t);
    const ticks = cues.filter((c) => c.type === 'ui_tick').map((c) => c.t);
    if (!scan || !oks.length) continue;
    const faceOk = oks[0];
    const line = (TL.speech ?? []).find((s) => s.who === pid && s.t0 >= faceOk - 0.5 && s.t0 < sc.t1);
    const voiceOk = oks[1] ?? ticks[0] ?? faceOk + 0.4;
    const voiceFrom = Math.min(line ? line.t0 : faceOk + 0.05, voiceOk - 0.1);
    plans.set(sc.id, {
      pid,
      name: TL.people[pid].name,
      t0: sc.t0,
      scanT: scan.t,
      scanDur: Math.max(0.2, scan.dur ?? 1),
      faceOk,
      voiceFrom: Math.max(faceOk, voiceFrom),
      voiceOk,
      // the saved card holds until just before the cut, then shrinks into the name tag
      hold: clamp(sc.t1 - voiceOk - 0.9, 0.6, 2.6),
    });
  }

  return function messages(sceneId, t) {
    const p = plans.get(sceneId);
    if (!p || t < p.t0) return [];
    const track = trackIdOf(p.pid);
    const id = `qs-${p.pid}`;
    const out = [[`qs:req:${id}`, { type: 'save_request', request_id: id, track_id: track, name: p.name, t: p.t0, expires_t: p.t0 + 60, hold_s: p.hold }]];
    if (t >= p.scanT) {
      const fraction = t >= p.faceOk ? 1 : q(clamp((t - p.scanT) / Math.min(p.scanDur, p.faceOk - p.scanT)));
      out.push([`qs:face:${id}`, { type: 'enroll_progress', track_id: track, person_id: null, part: 'face', fraction, hint: '' }]);
    }
    if (t >= p.faceOk) {
      out.push([`qs:faceok:${id}`, { type: 'enroll_result', person_id: p.pid, part: 'face', ok: true, reason: '', track_id: track }]);
    }
    if (t >= p.voiceFrom && t > p.faceOk) {
      const fraction = t >= p.voiceOk ? 1 : q(clamp((t - p.voiceFrom) / (p.voiceOk - p.voiceFrom)));
      out.push([`qs:voice:${id}`, { type: 'enroll_progress', track_id: track, person_id: p.pid, part: 'voice', fraction, hint: '' }]);
    }
    if (t >= p.voiceOk) {
      out.push([`qs:voiceok:${id}`, { type: 'enroll_result', person_id: p.pid, part: 'voice', ok: true, reason: '', track_id: track }]);
    }
    return out;
  };
}
