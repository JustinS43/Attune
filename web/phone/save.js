/*
 * Phone: the "Save this person?" consent sheet.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-29. Contracts: docs/contracts.md ("Save a person").
 *
 * When the wearer double taps the glasses, the engine sends `save_request` and this sheet slides
 * up over whatever screen is open. The wearer hands the phone to the person; only they tick
 * "Sam agrees to store a face and voice print on this laptop" and press Save, which sends
 * `enroll.start` with their consent time. Cancel (or no answer in 60 s) saves nothing. While
 * Attune learns their face and then their voice the sheet shows both steps, then "Saved".
 * Styles: ./save.css.
 */

import { createSaveState, statusLine } from '../shared/save_state.js';

function el(tag, className = '', text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * createSaveSheet({ host, send }) -> { onMessage(msg), state }.
 * host: the phone screen element the sheet covers; send(name, args): the engine link.
 */
export function createSaveSheet({ host, send }) {
  const sheet = el('div', 'save-sheet');
  sheet.setAttribute('role', 'dialog');
  sheet.setAttribute('aria-modal', 'true');
  sheet.setAttribute('aria-labelledby', 'save-sheet-title');
  sheet.hidden = true;
  const scrim = el('div', 'save-scrim');
  scrim.hidden = true;
  host.append(scrim, sheet);

  let tick = null;
  let lastPhase = null;
  const model = createSaveState(render);

  /** One step row (Face or Voice), updated in place as progress arrives. */
  function makeStep(label) {
    const row = el('div', 'save-step idle');
    const dot = el('b');
    const body = el('div', 'grow');
    const top = el('div', 'save-step-top');
    const pct = el('small');
    top.append(el('span', '', label), pct);
    const bar = el('div', 'save-bar');
    const fill = el('i');
    bar.append(fill);
    body.append(top, bar);
    row.append(dot, body);
    let last = '';
    return {
      row,
      update(part, active) {
        const state = part.ok === true ? 'ok' : part.ok === false ? 'fail' : active ? 'run' : 'idle';
        if (state !== last) {
          row.className = `save-step ${state}`;
          dot.textContent = state === 'ok' ? '✓' : state === 'fail' ? '!' : '';
          last = state;
        }
        pct.textContent = state === 'ok' ? 'Saved' : state === 'fail' ? 'Failed' : active ? `${Math.round(part.fraction * 100)}%` : 'Next';
        fill.style.setProperty('--p', String(state === 'ok' ? 1 : part.fraction)); // CSSOM: allowed by the page's CSP
      },
    };
  }
  let steps = null; // {face, voice, status} while the steps are on screen

  function render(s) {
    if (s && s.phase === lastPhase && s.phase === 'consent') return; // keep their tick and the countdown
    clearInterval(tick);
    tick = null;
    const open = !!s;
    sheet.hidden = !open;
    scrim.hidden = !open;
    sheet.classList.toggle('show', open);
    if (!open) {
      lastPhase = null;
      return;
    }
    // a short buzz, once the page may vibrate (browsers block it before the first touch)
    const mayBuzz = navigator.vibrate && navigator.userActivation?.hasBeenActive !== false;
    if (s.phase !== lastPhase && mayBuzz && (s.phase === 'consent' || s.phase === 'saved')) navigator.vibrate(s.phase === 'saved' ? [60, 60, 60] : 40);
    const phaseChanged = s.phase !== lastPhase;
    lastPhase = s.phase;
    if (!phaseChanged && s.phase === 'saving' && steps) {
      updateSteps(s);
      return;
    }
    sheet.dataset.phase = s.phase;
    sheet.replaceChildren(el('div', 'save-grip'));

    const head = el('div', 'save-head');
    const avatar = el('div', 'save-avatar', (s.name || '?').slice(0, 1).toUpperCase());
    const titles = el('div', 'grow');
    const title = el('h2', 'save-title', s.phase === 'consent' ? `Save ${s.name}?` : s.phase === 'saved' ? `${s.name} is saved` : s.phase === 'hint' ? (s.reason === 'already_saved' ? `${s.name || 'They'} ${s.name ? 'is' : 'are'} already saved` : 'Nobody to save yet') : s.phase === 'saving' ? `Saving ${s.name}` : 'Not saved');
    title.id = 'save-sheet-title';
    titles.append(el('p', 'eyebrow', 'Double tap on the glasses'), title);
    head.append(avatar, titles);
    sheet.append(head);

    if (s.phase === 'consent') {
      sheet.append(el('p', 'save-lead', `Hand the phone to ${s.name}. Only they should tick this.`));
      const label = el('label', 'save-consent');
      const box = el('input');
      box.type = 'checkbox';
      box.id = 'save-consent';
      label.append(box, el('span', '', `${s.name} agrees to store a face and voice print on this laptop`));
      const buttons = el('div', 'save-actions');
      const cancel = el('button', 'outline', 'Cancel');
      cancel.type = 'button';
      cancel.id = 'save-cancel';
      const ok = el('button', 'primary', `Save ${s.name}`);
      ok.type = 'button';
      ok.id = 'save-ok';
      ok.disabled = true;
      box.addEventListener('change', () => { ok.disabled = !box.checked; });
      ok.addEventListener('click', () => { if (box.checked) model.consent(send); });
      cancel.addEventListener('click', () => model.cancel(send));
      buttons.append(cancel, ok);
      const count = el('p', 'save-count');
      const setCount = () => {
        const left = model.remaining();
        count.textContent = left > 0 ? `Nothing is saved unless they agree · closes in ${left} s` : 'Closing…';
      };
      setCount();
      tick = setInterval(setCount, 1000);
      sheet.append(label, buttons, count, el('p', 'save-privacy', 'Attune keeps a face print and a voice print, never the photo or the recording. Delete them any time under People.'));
      if (phaseChanged) setTimeout(() => box.focus({ preventScroll: true }), 60);
      return;
    }

    steps = null;
    const status = el('p', `save-status ${s.phase}`);
    status.setAttribute('role', 'status');
    if (s.phase === 'saving' || s.phase === 'saved' || (s.phase === 'failed' && s.face.ok !== null)) {
      steps = { face: makeStep('Face'), voice: makeStep('Voice'), status };
      const box = el('div', 'save-steps');
      box.append(steps.face.row, steps.voice.row);
      sheet.append(box);
    }
    sheet.append(status);
    if (steps) updateSteps(s);
    else status.textContent = statusLine(s);
    if (s.phase !== 'saving') {
      const close = el('button', s.phase === 'saved' ? 'primary full' : 'outline full', s.phase === 'saved' ? 'Done' : 'Close');
      close.type = 'button';
      close.addEventListener('click', () => model.dismiss());
      sheet.append(close);
    }
  }

  function updateSteps(s) {
    steps.face.update(s.face, s.face.ok === null);
    steps.voice.update(s.voice, s.face.ok === true && s.voice.ok === null);
    steps.status.textContent = statusLine(s);
  }

  return {
    state: model.get,
    /** Close the sheet (the enrollment station took the save over, P-35). */
    dismiss: () => model.dismiss(),
    /** Every engine message; returns true when it was about a save request. */
    onMessage: (msg) => model.apply(msg),
  };
}
