/*
 * Console: the "Save this person?" card.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-29. Contracts: docs/contracts.md ("Save a person").
 *
 * After a double tap on the glasses the engine sends `save_request`; this card appears at the top
 * of the console with the person's face, a consent box they tick themselves, and Save / Cancel
 * (the same request the phone shows; whichever answers first wins). Save sends `enroll.start`
 * with request_id and consent_t; the card then shows the face and voice steps from
 * `enroll_progress` / `enroll_result`, and "Saved" at the end. Styles: ./save.css.
 */

import { h } from './ui.js';
import { createSaveState, statusLine } from '../shared/save_state.js';

function ensureStyles() {
  const href = new URL('./save.css', import.meta.url).href;
  if ([...document.querySelectorAll('link[rel="stylesheet"]')].some((l) => l.href === href)) return;
  document.head.append(h('link', { rel: 'stylesheet', href }));
}

/**
 * createSaveCard({ send, thumb }) -> { el, onMessage(msg) }. No toasts: the lens mounts these
 * panels hidden, and the glasses already say "Saved".
 * thumb(track_id) -> the console's last face thumbnail (base64 JPEG) for that track, if any.
 */
export function createSaveCard({ send, thumb = () => null }) {
  ensureStyles();
  const el = h('section', { class: 'atp-card atp-save', id: 'atp-save', hidden: true, 'aria-live': 'polite' });
  let tick = null;
  let lastPhase = null;
  let bars = null;
  const model = createSaveState(render);

  function bar(label) {
    const fill = h('i');
    const pct = h('small');
    const row = h('div', { class: 'atp-save-step' }, h('span', { class: 'atp-save-dot' }), h('strong', { text: label }), h('div', { class: 'atp-save-bar' }, fill), pct);
    return {
      row,
      update(part, active) {
        const state = part.ok === true ? 'ok' : part.ok === false ? 'fail' : active ? 'run' : 'idle';
        row.className = `atp-save-step ${state}`;
        fill.style.setProperty('--p', String(state === 'ok' ? 1 : part.fraction));
        pct.textContent = state === 'ok' ? 'Saved' : state === 'fail' ? 'Failed' : active ? `${Math.round(part.fraction * 100)}%` : 'Next';
      },
    };
  }

  function update(s) {
    bars.face.update(s.face, s.face.ok === null);
    bars.voice.update(s.voice, s.face.ok === true && s.voice.ok === null);
    bars.status.textContent = statusLine(s);
  }

  function render(s) {
    el.hidden = !s;
    const changed = s?.phase !== lastPhase;
    if (s && !changed && bars) {
      update(s);
      return;
    }
    if (s && !changed && s.phase === 'consent') return; // keep their tick and the countdown
    clearInterval(tick);
    tick = null;
    if (!s) {
      lastPhase = null;
      bars = null;
      return;
    }
    lastPhase = s.phase;
    el.dataset.phase = s.phase;
    const b64 = s.track_id != null ? thumb(s.track_id) : null;
    const face = b64 ? h('img', { class: 'atp-save-face', src: `data:image/jpeg;base64,${b64}`, alt: '' }) : h('span', { class: 'atp-save-face atp-save-initial', text: (s.name || '?').slice(0, 1).toUpperCase() });
    const title = s.phase === 'consent' ? `Save ${s.name}?` : s.phase === 'saving' ? `Saving ${s.name}` : s.phase === 'saved' ? `${s.name} saved` : s.phase === 'hint' ? (s.reason === 'already_saved' ? `${s.name || 'They'} ${s.name ? 'is' : 'are'} already saved` : 'Nobody to save yet') : 'Not saved';
    const head = h('header', { class: 'atp-save-head' }, face, h('div', {}, h('span', { class: 'atp-save-eyebrow', text: 'Double tap on the glasses' }), h('h3', { text: title })));
    bars = null;

    if (s.phase === 'consent') {
      const box = h('input', { type: 'checkbox', id: 'atp-save-consent', onchange: () => { ok.disabled = !box.checked; } });
      const consent = h('label', { class: 'atp-consent', for: 'atp-save-consent' }, box, h('span', { class: 'atp-check' }), h('span', { text: `${s.name} agrees to store a face and voice print on this laptop` }));
      const ok = h('button', { class: 'atp-btn atp-primary', type: 'button', id: 'atp-save-ok', disabled: true, text: `Save ${s.name}`, onclick: () => box.checked && model.consent(send) });
      const cancel = h('button', { class: 'atp-btn atp-ghost', type: 'button', id: 'atp-save-cancel', text: 'Cancel', onclick: () => model.cancel(send) });
      const count = h('span', { class: 'atp-save-count' });
      const setCount = () => { count.textContent = `${model.remaining()} s`; };
      setCount();
      tick = setInterval(setCount, 1000);
      el.replaceChildren(head, h('p', { class: 'atp-save-lead', text: `Only ${s.name} ticks this. Nothing is saved unless they agree; the phone shows the same request.` }), consent, h('div', { class: 'atp-row' }, cancel, ok, count));
      if (changed) el.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
      return;
    }

    const status = h('p', { class: `atp-save-status ${s.phase}` });
    const children = [head];
    if (s.phase === 'saving' || s.phase === 'saved' || (s.phase === 'failed' && s.face.ok !== null)) {
      bars = { face: bar('Face'), voice: bar('Voice'), status };
      children.push(h('div', { class: 'atp-save-steps' }, bars.face.row, bars.voice.row));
    }
    children.push(status);
    if (s.phase !== 'saving') children.push(h('div', { class: 'atp-row' }, h('button', { class: 'atp-btn atp-ghost', type: 'button', text: 'Close', onclick: () => model.dismiss() })));
    el.replaceChildren(...children);
    if (bars) update(s);
    else status.textContent = statusLine(s);
  }

  return { el, onMessage: (m) => model.apply(m) };
}
