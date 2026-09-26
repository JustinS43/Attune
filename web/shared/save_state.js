/*
 * "Save this person": the consent request and its progress, as seen by the phone and console.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-29. Contracts: docs/contracts.md ("Save a person").
 *
 * A double tap on the glasses (or D on the lens) makes the engine send `save_request` to every
 * page. The person being saved ticks consent on the phone or console, which sends
 * `enroll.start` {track_id, name, consent: true, consent_t, request_id}; Cancel sends
 * `save.cancel` {request_id}. The engine then reports `enroll_progress` (face, then voice) and
 * `enroll_result`, or ends the request with `save_cancel` {reason}. This module keeps that one
 * request's state; the pages draw it (web/phone/save.js, web/panels/save.js).
 *
 * Nothing here is stored: the state lives in memory and clears itself a few seconds after the
 * request ends.
 */

/** Why a save request ended, in words (contracts: SAVE_CANCEL_REASONS). */
export const CANCEL_TEXT = {
  declined: 'Not saved: they said no',
  timeout: 'Not saved: no answer in time',
  lost: 'Not saved: they left the view',
  replaced: 'Switched to someone else',
  cancelled: 'Not saved: Attune paused or the session was forgotten',
  no_name: 'Say their name first, or add them in the console',
  already_saved: 'Already saved on this laptop',
};

// how long each ending stays on screen (ms)
const LINGER = { saved: 4200, cancelled: 3200, hint: 4200, failed: 7000 };

/**
 * createSaveState(onChange) -> { get, apply, consent, cancel, dismiss, remaining }.
 * `get()` is null or {request_id, track_id, name, phase, face, voice, reason, fromTap}, where
 * phase is consent | saving | saved | failed | cancelled | hint, and face / voice are
 * {fraction, ok, hint} (ok: null while running, true or false once it has a result).
 */
export function createSaveState(onChange = () => {}) {
  let s = null;
  let timer = null;

  const part = () => ({ fraction: 0, ok: null, hint: '' });
  const changed = () => onChange(s);
  function end(phase, reason = '') {
    if (!s) return;
    s.phase = phase;
    s.reason = reason;
    clearTimeout(timer);
    const mine = s;
    timer = setTimeout(() => {
      if (s === mine) {
        s = null;
        changed();
      }
    }, LINGER[phase] ?? 3000);
    changed();
  }
  const sameTrack = (m) => s && m.track_id !== undefined && m.track_id !== null && m.track_id === s.track_id;

  /** Feed every engine message here; returns true when it was about the save request. */
  function apply(m) {
    switch (m?.type) {
      case 'save_request': {
        if (!m.request_id) return false;
        if (s && s.request_id === m.request_id) return true; // a repeat tap on the same person
        clearTimeout(timer);
        const window = Number(m.expires_t) - Number(m.t);
        s = {
          request_id: m.request_id,
          track_id: m.track_id,
          name: m.name || 'them',
          phase: 'consent',
          face: part(),
          voice: part(),
          reason: '',
          deadline: Date.now() + (Number.isFinite(window) && window > 0 ? window : 60) * 1000,
        };
        changed();
        return true;
      }
      case 'save_cancel': {
        if (m.request_id == null) {
          // nobody could be saved (no name yet, or already saved): a short hint, unless a save
          // is already running
          if (s && (s.phase === 'consent' || s.phase === 'saving')) return true;
          clearTimeout(timer);
          s = { request_id: null, track_id: null, name: m.name || '', phase: 'hint', face: part(), voice: part(), reason: m.reason };
          end('hint', m.reason);
          return true;
        }
        if (!s || s.request_id !== m.request_id || s.phase !== 'consent') return !!s && s.request_id === m.request_id;
        end('cancelled', m.reason);
        return true;
      }
      case 'enroll_progress': {
        if (!sameTrack(m) || !['consent', 'saving'].includes(s.phase)) return false;
        s.phase = 'saving'; // consent may have come from the other page
        const p = m.part === 'voice' ? s.voice : s.face;
        p.fraction = Math.max(p.fraction, Math.min(1, Number(m.fraction) || 0));
        p.hint = m.hint || '';
        if (m.part === 'voice') s.face = { ...s.face, ok: true, fraction: 1, hint: '' };
        changed();
        return true;
      }
      case 'enroll_result': {
        if (!sameTrack(m) || !['consent', 'saving'].includes(s.phase)) return false;
        const p = m.part === 'voice' ? s.voice : s.face;
        p.ok = !!m.ok;
        p.hint = '';
        if (m.ok) p.fraction = 1;
        // their face prints are kept once the face part succeeds: a voice failure after that
        // still leaves them saved (face only), which is what the glasses say too
        if (!m.ok && m.part === 'voice' && s.face.ok === true) end('saved', m.reason || 'not enough speech');
        else if (!m.ok) end('failed', m.reason || 'Try again');
        else if (m.part === 'voice') end('saved');
        else {
          s.phase = 'saving';
          changed();
        }
        return true;
      }
      case 'forgotten':
        dismiss();
        return false;
      default:
        return false;
    }
  }

  /** The person ticked consent: start their enrollment. */
  function consent(send) {
    if (!s || s.phase !== 'consent') return;
    send('enroll.start', { track_id: s.track_id, name: s.name, consent: true, consent_t: Date.now() / 1000, request_id: s.request_id });
    s.phase = 'saving';
    changed();
  }

  /** Cancel a waiting request (they said no), or close an ended one. */
  function cancel(send) {
    if (s?.phase === 'consent') {
      send('save.cancel', { request_id: s.request_id });
      end('cancelled', 'declined');
    } else if (s && s.phase !== 'saving') {
      dismiss();
    }
  }

  function dismiss() {
    clearTimeout(timer);
    if (!s) return;
    s = null;
    changed();
  }

  /** Whole seconds left to answer (0 once it has run out; the engine then cancels it). */
  const remaining = () => (s?.deadline ? Math.max(0, Math.ceil((s.deadline - Date.now()) / 1000)) : 0);

  return { get: () => s, apply, consent, cancel, dismiss, remaining };
}

/** One line for where the save is up to. */
export function statusLine(s) {
  if (!s) return '';
  const n = s.name;
  switch (s.phase) {
    case 'consent':
      return `Waiting for ${n}'s OK`;
    case 'saving':
      if (s.face.ok !== true) return s.face.hint ? `Face: ${s.face.hint}` : `Learning ${n}'s face: look at the glasses`;
      return s.voice.hint && s.voice.hint !== 'keep talking' ? `Voice: ${s.voice.hint}` : `Learning ${n}'s voice: keep talking`;
    case 'saved':
      return s.voice.ok === false ? `${n} saved with their face · voice later (${s.reason})` : `${n} saved on this laptop`;
    case 'failed':
      return `Not saved: ${s.reason}`;
    case 'cancelled':
      return CANCEL_TEXT[s.reason] || 'Not saved';
    case 'hint':
      return CANCEL_TEXT[s.reason] || 'Nobody to save';
    default:
      return '';
  }
}
