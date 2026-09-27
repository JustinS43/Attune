/*
 * Phone: save a person at the laptop (the enrollment station).
 *
 * Section 4 - Pages (built by the enrollment stream). TODO: P-35. Contracts: docs/contracts.md
 * ("Enrollment station").
 *
 * The glasses camera and mic are for the world outside; people are saved at the laptop's own
 * camera and mic. When the engine says so (`welcome.config.enroll.source === "station"`):
 *  - the "Save this person?" sheet's Save sends `enroll.station` {action: start} instead of
 *    `enroll.start`, and the Enroll tab starts a save at the laptop (no glasses photo);
 *  - this screen then covers the phone and follows `enroll_state`: "Look at the laptop camera"
 *    with a live mirrored preview, an oval to line up with and one hint at a time; "That isn't
 *    the person you were looking at" (Try again / Save as someone new); "Read this sentence"
 *    with a level meter; then Saved, or what went wrong and what to do.
 * The preview frames and the meter come only to the phone that started the save and are
 * never stored. Keyboard: Tab stays inside, Escape cancels, focus goes to each new heading;
 * hints are announced politely. Styles: ./station.css.
 */

const FACE_HINTS = {
  'look at the laptop camera': 'Look at the laptop camera',
  'move to the middle': 'Move to the middle of the picture',
  'come closer': 'Come a little closer',
  'move back a little': 'Move back a little',
  'face the camera': 'Face the camera',
  'more light': 'More light on your face, please',
  'hold still': 'Hold still for a moment',
  'stay in view': 'Stay in view',
  'one person at a time': 'One person at a time, please',
};
const VOICE_HINTS = {
  'a bit softer': 'A bit softer, please',
  'too noisy': 'It is noisy here: lean in closer to the laptop',
  'speak up': 'Speak up a little',
  'read the sentence aloud': 'Read the sentence aloud',
  'keep talking': 'Keep reading',
};
const ENDED = {
  cancelled: 'Cancelled.',
  paused: 'Attune was paused, so nothing more was saved.',
  timeout: 'Nobody answered in time.',
  replaced: 'Another save started.',
  stopped: 'Attune stopped.',
  error: 'Something went wrong on the laptop.',
  voice_skipped: 'Voice skipped for now.',
  'consent is required': 'Only the person being saved can tick consent.',
};
const ACTIVE = ['pending', 'opening', 'face', 'mismatch', 'face_failed', 'saving', 'voice', 'voice_failed', 'glasses'];
const FINAL = ['done', 'cancelled', 'fallback'];

function el(tag, className = '', text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function btn(label, className, onClick) {
  const node = el('button', className, label);
  node.type = 'button';
  node.addEventListener('click', onClick);
  return node;
}

const cap = (text) => (text ? text.charAt(0).toUpperCase() + text.slice(1) : '');

/**
 * createStation({ host, send, onStarted }) -> {
 *   configure(enrollConfig), enabled(), onMessage(msg), route(name, args), start(name),
 *   renderStart(container, {connected}), isOpen() }.
 * host: the phone screen the overlay covers; send(name, args): the engine link;
 * onStarted(): called when a save sheet's consent became a station save (to close that sheet).
 */
export function createStation({ host, send, onStarted = () => {} }) {
  let config = { source: 'glasses', sentence: '' };
  const draft = { name: '', consent: false };
  // the save on screen: {session, phase, name, trackId, requestId, consentT, face, voice, ...}
  let s = null;
  let lastFocus = null;
  let pendingTimer = null;
  let liveAt = 0;
  let liveText = '';
  let clearForm = () => {}; // empties the Enroll tab's form once a save has started

  const overlay = el('div', 'st-overlay');
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-labelledby', 'st-title');
  overlay.hidden = true;
  const live = el('p', 'st-sr');
  live.setAttribute('aria-live', 'polite');
  live.setAttribute('role', 'status');
  host.append(overlay, live);

  // a modal screen: its keys work wherever focus is while it is open
  document.addEventListener('keydown', (event) => {
    if (overlay.hidden) return;
    if (event.key === 'Escape' && s && ACTIVE.includes(s.phase)) {
      event.preventDefault();
      cancel();
    } else if (event.key === 'Tab') {
      const items = [...overlay.querySelectorAll('button:not([disabled]), [href], input, [tabindex="0"]')].filter((n) => n.offsetParent !== null);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (!overlay.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });

  function announce(text, force = false) {
    const now = Date.now();
    if (!text || (text === liveText && !force) || (!force && now - liveAt < 2500)) return;
    liveText = text;
    liveAt = now;
    live.textContent = text;
  }

  function control(action) {
    if (!s?.session) return;
    send('enroll.station', { action, session_id: s.session });
  }

  function cancel() {
    if (!s) return;
    if (s.phase === 'pending' || !s.session) {
      close();
      return;
    }
    control('cancel');
  }

  function open(next) {
    if (overlay.hidden) lastFocus = document.activeElement;
    s = next;
    overlay.hidden = false;
    host.classList.add('st-open');
    draw(true);
  }

  function close() {
    clearTimeout(pendingTimer);
    s = null;
    overlay.hidden = true;
    overlay.replaceChildren();
    host.classList.remove('st-open');
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
    lastFocus = null;
  }

  const blankPart = () => ({ fraction: 0, ok: null });

  /** Start a save and show "Getting the laptop camera ready" until the engine answers. */
  function begin({ name, trackId = null, requestId = null, consentT }) {
    open({
      session: null, phase: 'pending', name, trackId, requestId, consentT,
      face: blankPart(), voice: blankPart(), reason: '', message: '', sentence: config.sentence, needS: 5,
      hint: '', level: null, preview: null,
    });
    clearTimeout(pendingTimer);
    pendingTimer = setTimeout(() => {
      if (s && s.phase === 'pending') {
        s.phase = 'cancelled';
        s.reason = 'no_answer';
        draw(true);
      }
    }, 8000);
  }

  // ------------------------------------------------------------------ engine messages
  function onState(m) {
    if (!m.session_id) return;
    if (s && s.session && m.session_id !== s.session) {
      // an older save ending (it was replaced by ours): not news
      if (FINAL.includes(m.phase)) return;
    }
    if (!s || (s.session && s.session !== m.session_id) || (!s.session && s.phase !== 'pending')) {
      if (FINAL.includes(m.phase) && !s) return; // an ending for a save we never showed
      open({
        session: m.session_id, phase: m.phase, name: m.name || '', trackId: m.track_id ?? null,
        requestId: m.request_id ?? null, consentT: null, face: blankPart(), voice: blankPart(),
        reason: '', message: '', sentence: m.sentence || config.sentence, needS: m.need_s || 5,
        hint: '', level: null, preview: null,
      });
    }
    clearTimeout(pendingTimer);
    const changed = s.phase !== m.phase || s.session !== m.session_id;
    s.session = m.session_id;
    s.phase = m.phase;
    s.name = m.name || s.name;
    s.reason = m.reason || '';
    s.message = m.message || '';
    s.sentence = m.sentence || s.sentence || config.sentence;
    s.needS = Number(m.need_s) || s.needS;
    s.personId = m.person_id ?? s.personId;
    if (m.face_ok) s.face = { fraction: 1, ok: true };
    if (m.voice_ok) s.voice = { fraction: 1, ok: true };
    if (m.phase === 'voice') s.face = { fraction: 1, ok: true };
    if (m.phase === 'mismatch') s.score = m.score;
    if (m.phase === 'face') s.shared = !!m.shared;
    if (changed || m.phase === 'face_failed' || m.phase === 'voice_failed') {
      s.hint = '';
      draw(true);
    }
  }

  function onProgress(m) {
    if (!s) return;
    if (s.phase === 'glasses') {
      if (m.track_id !== s.trackId || m.source === 'station') return;
    } else if (!s.session || m.session_id !== s.session) {
      return;
    }
    const part = m.part === 'voice' ? s.voice : s.face;
    part.fraction = Math.max(part.fraction, Math.min(1, Number(m.fraction) || 0));
    if (m.part === 'voice') s.face = { fraction: 1, ok: true };
    if (s.phase === 'glasses') {
      s.hint = m.hint || '';
      draw(false);
    } else {
      updateBars();
    }
  }

  function onResult(m) {
    if (!s || s.phase !== 'glasses' || m.track_id !== s.trackId) return;
    const part = m.part === 'voice' ? s.voice : s.face;
    part.ok = !!m.ok;
    if (m.ok) part.fraction = 1;
    if (m.part === 'face' && !m.ok) {
      s.phase = 'cancelled';
      s.reason = m.reason || 'error';
      draw(true);
    } else if (m.part === 'voice') {
      s.phase = 'done';
      if (!m.ok) s.reason = 'voice_later';
      draw(true);
    } else {
      draw(false);
    }
  }

  function onPreview(m) {
    if (!s || m.session_id !== s.session || s.phase !== 'face') return;
    s.preview = m;
    const img = overlay.querySelector('.st-img');
    if (!img) return;
    img.src = `data:image/jpeg;base64,${m.jpeg_b64}`;
    const cam = overlay.querySelector('.st-cam');
    cam.classList.add('has-image');
    cam.classList.toggle('ok', !!m.ok);
    const box = overlay.querySelector('.st-box');
    if (Array.isArray(m.face)) {
      const [x, y, w, h] = m.face;
      box.hidden = false;
      box.style.setProperty('left', `${x * 100}%`); // CSSOM: allowed by the page's CSP
      box.style.setProperty('top', `${y * 100}%`);
      box.style.setProperty('width', `${w * 100}%`);
      box.style.setProperty('height', `${h * 100}%`);
    } else {
      box.hidden = true;
    }
    setHint(m.hint ? FACE_HINTS[m.hint] || cap(m.hint) : m.ok ? 'Good. Hold still while Attune learns your face' : '');
  }

  function onLevel(m) {
    if (!s || m.session_id !== s.session || s.phase !== 'voice') return;
    s.level = m;
    const meter = overlay.querySelector('.st-meter');
    if (!meter) return;
    const pct = Math.round(Math.max(0, Math.min(1, Number(m.level) || 0)) * 100);
    meter.querySelector('i').style.setProperty('--level', String(pct / 100));
    meter.setAttribute('aria-valuenow', String(pct));
    meter.setAttribute('aria-valuetext', m.clipping ? 'too loud' : m.speech ? 'hearing you' : 'quiet');
    meter.classList.toggle('loud', !!m.clipping || m.hint === 'a bit softer');
    meter.classList.toggle('speech', !!m.speech);
    const text = m.hint ? VOICE_HINTS[m.hint] || cap(m.hint) : m.speech ? 'Good. Keep reading' : '';
    setHint(text);
    const voiced = Number(m.voiced_s) || 0;
    const need = Number(m.need_s) || s.needS;
    s.voice.fraction = Math.max(s.voice.fraction, Math.min(1, voiced / need));
    updateBars();
  }

  function setHint(text) {
    const hint = overlay.querySelector('.st-hint');
    if (!hint || hint.textContent === text) return;
    hint.textContent = text;
    s.hint = text;
    announce(text);
  }

  function updateBars() {
    for (const [key, part] of [['face', s.face], ['voice', s.voice]]) {
      const bar = overlay.querySelector(`.st-progress[data-part="${key}"]`);
      if (!bar) continue;
      const pct = Math.round((part.ok ? 1 : part.fraction) * 100);
      bar.querySelector('i').style.setProperty('--p', String(pct / 100));
      bar.setAttribute('aria-valuenow', String(pct));
      const label = bar.parentElement.querySelector('.st-progress-label small');
      if (label) label.textContent = key === 'voice' && !part.ok ? `${(part.fraction * s.needS).toFixed(1)} of ${s.needS} s` : `${pct}%`;
    }
  }

  // ------------------------------------------------------------------ drawing
  function progress(part, label) {
    const wrap = el('div', 'st-progress-wrap');
    const top = el('div', 'st-progress-label');
    top.append(el('span', '', label), el('small', '', ''));
    const bar = el('div', 'st-progress');
    bar.dataset.part = part;
    bar.setAttribute('role', 'progressbar');
    bar.setAttribute('aria-label', `${label} progress`);
    bar.setAttribute('aria-valuemin', '0');
    bar.setAttribute('aria-valuemax', '100');
    bar.append(el('i'));
    wrap.append(top, bar);
    return wrap;
  }

  function header(eyebrow, title) {
    const head = el('div', 'st-head');
    head.append(el('p', 'eyebrow', eyebrow));
    const h = el('h2', 'st-title', title);
    h.id = 'st-title';
    h.tabIndex = -1;
    head.append(h);
    return head;
  }

  function steps(current) {
    const row = el('ol', 'st-steps');
    row.setAttribute('aria-label', 'Steps');
    [['face', 'Face'], ['voice', 'Voice'], ['done', 'Saved']].forEach(([key, label], i) => {
      const item = el('li', `st-step${key === current ? ' current' : ''}${
        (key === 'face' && s.face.ok) || (key === 'voice' && s.voice.ok) || (key === 'done' && s.phase === 'done') ? ' ok' : ''}`);
      item.append(el('b', '', String(i + 1)), el('span', '', label));
      if (key === current) item.setAttribute('aria-current', 'step');
      row.append(item);
    });
    return row;
  }

  function draw(phaseChanged) {
    if (!s) return;
    overlay.dataset.phase = s.phase;
    const body = [];
    const actions = el('div', 'st-actions');
    const n = s.name || 'them';
    let title = '';
    let eyebrow = 'Save at the laptop';
    switch (s.phase) {
      case 'pending':
      case 'opening': {
        title = 'Getting the laptop camera ready';
        body.push(steps('face'), el('p', 'st-lead', `${n}, please sit in front of the laptop. Its camera light comes on while it learns your face and goes off right after.`));
        const spin = el('div', 'st-spinner');
        spin.setAttribute('aria-hidden', 'true');
        body.push(spin);
        actions.append(btn('Cancel', 'outline full', cancel));
        break;
      }
      case 'face': {
        title = 'Look at the laptop camera';
        const cam = el('div', 'st-cam');
        const mirror = el('div', 'st-mirror');
        const img = el('img', 'st-img');
        img.alt = 'Live picture from the laptop camera';
        const box = el('div', 'st-box');
        box.hidden = true;
        mirror.append(img, box);
        const oval = el('div', 'st-oval');
        oval.setAttribute('aria-hidden', 'true');
        const tag = el('span', 'st-cam-label', s.shared ? 'LAPTOP CAMERA · SHARED' : 'LAPTOP CAMERA');
        cam.append(mirror, oval, tag, el('span', 'st-cam-wait', 'Waiting for the picture…'));
        const hint = el('p', 'st-hint', s.hint || '');
        body.push(steps('face'), cam, hint, progress('face', 'Face'));
        body.push(el('p', 'st-note', 'Keep your face inside the oval. The picture stays on this phone screen only; Attune keeps a face print, never the photo.'));
        actions.append(btn('Cancel', 'outline full', cancel));
        break;
      }
      case 'mismatch':
        eyebrow = 'Check who is being saved';
        title = "That isn't the person you were looking at";
        body.push(el('p', 'st-lead', `The face at the laptop doesn't match the face the glasses picked for ${n}. If ${n} is at the laptop, try again. If someone else is saving themselves, save them as someone new.`));
        actions.append(
          btn('Try again', 'primary full', () => control('retry')),
          btn('Save as someone new', 'outline full', () => control('new_person')),
          btn('Cancel', 'st-link', cancel),
        );
        break;
      case 'face_failed':
        title = "We couldn't see your face clearly";
        body.push(steps('face'), el('p', 'st-lead', `${cap(FACE_HINTS[s.reason] || s.reason || 'Try again')}. Sit facing the laptop with light on your face, then try again.`));
        actions.append(btn('Try again', 'primary full', () => control('retry')), btn('Cancel', 'outline full', cancel));
        break;
      case 'saving':
        title = 'Saving your face';
        body.push(steps('face'), progress('face', 'Face'), el('p', 'st-lead', 'One moment…'));
        break;
      case 'voice': {
        title = 'Read this sentence';
        const quote = el('blockquote', 'st-sentence', s.sentence || config.sentence || '');
        const meter = el('div', 'st-meter');
        meter.setAttribute('role', 'meter');
        meter.setAttribute('aria-label', 'Microphone level');
        meter.setAttribute('aria-valuemin', '0');
        meter.setAttribute('aria-valuemax', '100');
        meter.setAttribute('aria-valuenow', '0');
        meter.append(el('i'));
        const hint = el('p', 'st-hint', s.hint || '');
        body.push(steps('voice'), el('p', 'st-lead', 'Read it aloud in your normal voice, facing the laptop. The laptop microphone is listening.'), quote, meter, hint, progress('voice', 'Voice'));
        body.push(el('p', 'st-note', 'Attune keeps a voice print, never the recording.'));
        actions.append(btn('Skip voice for now', 'outline full', () => control('skip_voice')), btn('Cancel', 'st-link', cancel));
        break;
      }
      case 'voice_failed':
        title = "We didn't hear enough";
        body.push(steps('voice'), el('p', 'st-lead', `${cap(s.reason || 'not enough speech')}. Your face is saved. Try the sentence again, or skip the voice for now.`));
        actions.append(btn('Try again', 'primary full', () => control('retry')), btn('Skip voice for now', 'outline full', () => control('skip_voice')));
        break;
      case 'glasses':
        eyebrow = 'Save at the glasses';
        title = s.face.ok ? `Now ${n} speaks` : `Look at the glasses, ${n}`;
        body.push(progress('face', 'Face'), progress('voice', 'Voice'), el('p', 'st-hint', s.hint ? cap(s.hint) : ''));
        break;
      case 'done': {
        eyebrow = 'Saved';
        title = `${n} is saved`;
        const list = el('ul', 'st-summary');
        list.append(el('li', 'ok', 'Face saved'));
        list.append(el('li', s.voice.ok ? 'ok' : 'later', s.voice.ok ? 'Voice saved' : 'Voice not saved yet: add it later from Enroll'));
        body.push(steps('done'), list, el('p', 'st-lead', `The glasses will recognize ${n} from now on.`));
        actions.append(btn('Done', 'primary full', close));
        break;
      }
      case 'fallback':
        eyebrow = 'Laptop camera unavailable';
        title = "Can't use the laptop camera";
        body.push(el('p', 'st-lead', s.message || (s.reason === 'station_off' ? 'Saving at the laptop is turned off.' : 'The laptop camera is not available.')));
        if (s.trackId !== null && s.trackId !== undefined && s.consentT) {
          body.push(el('p', 'st-note', `You can save ${n} with the glasses camera instead: ${n} looks at the glasses and then talks for a few seconds.`));
          actions.append(btn('Save with the glasses instead', 'primary full', useGlasses));
        }
        actions.append(btn('Close', s.trackId !== null && s.consentT ? 'outline full' : 'primary full', close));
        break;
      default: {
        eyebrow = 'Not saved';
        title = s.reason === 'no_answer' ? "The laptop didn't answer" : 'Nothing was saved';
        const why = s.reason === 'no_answer' ? 'Check that Attune is running on the laptop, then try again.' : ENDED[s.reason] || cap(s.reason) || 'Cancelled.';
        body.push(el('p', 'st-lead', s.face.ok ? `${n}'s face is saved. ${why}` : why));
        actions.append(btn('Close', 'primary full', close));
      }
    }
    if (!phaseChanged && s.phase === 'glasses') {
      overlay.querySelector('.st-title').textContent = title;
      const hint = overlay.querySelector('.st-hint');
      if (hint) hint.textContent = s.hint ? cap(s.hint) : '';
      updateBars();
      return;
    }
    const scroller = el('div', 'st-scroll');
    scroller.append(header(eyebrow, title), ...body, actions);
    overlay.replaceChildren(scroller);
    updateBars();
    if (phaseChanged) overlay.querySelector('#st-title')?.focus();
  }

  function useGlasses() {
    if (!s || s.trackId === null || !s.consentT) return;
    send('enroll.start', { track_id: s.trackId, name: s.name, consent: true, consent_t: s.consentT, request_id: s.requestId });
    s.phase = 'glasses';
    s.session = null;
    s.face = blankPart();
    s.voice = blankPart();
    draw(true);
  }

  // ------------------------------------------------------------------ Enroll tab
  function renderStart(container, { connected }) {
    const card = el('div', 'st-start card');
    card.append(el('p', 'st-start-lead', 'Sit in front of the laptop. Its own camera and microphone save your face and voice; the glasses camera stays on the room.'));
    const label = el('label', 'enroll-label', 'Your name');
    const input = el('input', 'enroll-name');
    input.id = 'station-name';
    input.type = 'text';
    input.maxLength = 40;
    input.autocomplete = 'off';
    input.value = draft.name;
    label.append(input);
    const consent = el('label', 'st-consent');
    const box = el('input');
    box.type = 'checkbox';
    box.id = 'station-consent';
    box.checked = draft.consent;
    consent.append(box, el('span', '', 'I agree to save my face print and voice print on this laptop. I tick this myself, and I can delete them from People.'));
    const go = el('button', 'primary full', 'Start at the laptop');
    go.type = 'button';
    go.id = 'station-start';
    const sync = () => { go.disabled = !draft.name.trim() || !draft.consent || !connected; };
    input.addEventListener('input', () => { draft.name = input.value; sync(); });
    box.addEventListener('change', () => { draft.consent = box.checked; sync(); });
    go.addEventListener('click', () => {
      if (!draft.name.trim() || !draft.consent || !connected) return;
      start(draft.name.trim());
    });
    sync();
    clearForm = () => {
      input.value = '';
      box.checked = false;
      sync();
    };
    card.append(label, consent, go);
    const steps3 = el('ol', 'st-howto');
    for (const line of ['Look at the laptop camera and line your face up in the oval.', 'Read one sentence aloud for the laptop microphone.', 'Done: the glasses recognize you from then on.']) steps3.append(el('li', '', line));
    container.append(card, steps3, el('p', 'note', connected ? 'Only prints are kept, never photos or recordings.' : 'Connect to Attune to save someone.'));
  }

  function start(name) {
    const consentT = Date.now() / 1000;
    send('enroll.station', { action: 'start', name, consent: true, consent_t: consentT });
    draft.name = '';
    draft.consent = false; // the next person ticks it again themselves
    clearForm();
    begin({ name, consentT });
  }

  return {
    configure(enroll) {
      config = { source: enroll?.source || 'glasses', sentence: enroll?.sentence || '' };
    },
    enabled: () => config.source === 'station',
    prefill: name => { draft.name = name || ''; draft.consent = false; },
    currentSessionId: () => s?.session || null,
    isOpen: () => !overlay.hidden,
    renderStart,
    start,
    /** The save sheet's send: its consent becomes a station save when the station is on. */
    route(name, args) {
      if (name === 'enroll.start' && config.source === 'station' && args?.consent === true) {
        send('enroll.station', { action: 'start', ...args });
        setTimeout(onStarted, 0); // after the sheet has finished its own consent step
        begin({ name: args.name, trackId: args.track_id ?? null, requestId: args.request_id ?? null, consentT: args.consent_t });
        return;
      }
      send(name, args);
    },
    /** Every engine message; returns true when it was the station's. */
    onMessage(msg) {
      switch (msg?.type) {
        case 'enroll_state': onState(msg); return true;
        case 'enroll_preview': onPreview(msg); return true;
        case 'enroll_level': onLevel(msg); return true;
        case 'enroll_mismatch': return true; // the state that comes with it draws the screen
        case 'enroll_progress': onProgress(msg); return false;
        case 'enroll_result': onResult(msg); return false;
        default: return false;
      }
    },
  };
}
