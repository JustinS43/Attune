/*
 * Console panel (C)
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-09. Contracts: docs/contracts.md (3, 4, Pages additions).
 *
 * Enroll with live thumbnails and explicit consent, the people list (rename / delete), pause and
 * forget session, switches and languages, pattern tests with the rig link, the calibration steps,
 * the status strip with per-part health, and the event log with a Mark button.
 * Cloud captions (P-48): a "Cloud captions" cell in the status strip from the `cloud` message
 * (Off / Connecting / On · 420 ms / Fell back to local: network / Unavailable: credentials missing).
 */

import { h, section, clockText, dateText } from './ui.js';
import { createSaveCard } from './save.js';

const PATTERNS = ['T3', 'T4', 'BELL', 'NAME', 'OK', 'NO'];
const PATTERN_HINT = { T3: 'Smoke (3 pulses)', T4: 'CO (4 pulses)', BELL: 'Doorbell', NAME: 'Your name', OK: 'Confirm', NO: 'Reject' };
const SIDES = [['L', 'Left'], ['B', 'Both'], ['R', 'Right']];
const SWITCHES = [
  ['translation', 'Translation', 'Show English under other languages'],
  ['alerts', 'Sound alerts', 'Smoke, CO, doorbell and knock banners + buzz'],
  ['debug', 'Debug overlay', 'Boxes, scores and timings on the lens'],
];
const LANGS = [['en', 'English'], ['es', 'Spanish']];
const CAL_STEPS = [
  ['level', 'Level', 'Hold the rig level'],
  ['mic', 'Mic', 'Room noise floor'],
  ['you', 'You', 'Wearer reads a line'],
  ['other', 'Other', 'Someone else talks'],
  ['balance', 'Balance', 'Left / right sound'],
  ['claps', 'Claps', 'Audio-video sync'],
  ['noise', 'Noise', 'Quiet room check'],
  ['faces', 'Faces', '1, 2 and 3 m'],
];
const LOG_MAX = 300;

export function createConsole(ctx) {
  const { send, toast, confirm } = ctx;
  const st = {
    thumbs: new Map(), // track_id -> jpeg_b64 (last seen)
    visible: new Set(),
    selected: null,
    enroll: null, // {track_id, name, person_id, face, voice, reason}
    people: [],
    busy: new Set(), // person_ids being deleted
    switches: { translation: true, alerts: true, debug: false },
    langs: new Set(['en', 'es']),
    calActive: null,
    log: [],
  };

  // ---------------------------------------------------------------- status strip
  const statusGrid = h('div', { class: 'atp-stats' });
  const partsRow = h('div', { class: 'atp-parts' });
  const statusCard = h('section', { class: 'atp-card atp-status-card', id: 'atp-status' }, statusGrid, partsRow);

  function stat(label, value, tone = '') {
    return h('div', { class: `atp-stat ${tone}` }, h('span', { class: 'atp-stat-label', text: label }), h('span', { class: 'atp-stat-value', text: value }));
  }
  function meter(label, level) {
    const pct = Number.isFinite(level) ? Math.round(Math.max(0, Math.min(1, (level + 60) / 60)) * 100) : 0;
    return h('div', { class: 'atp-stat' }, h('span', { class: 'atp-stat-label', text: label }),
      h('span', { class: 'atp-meter', role: 'meter', 'aria-label': label, 'aria-valuenow': String(pct), 'aria-valuemin': '0', 'aria-valuemax': '100' }, h('i', { style: { width: `${pct}%` } })));
  }
  function arduinoFlag(link) {
    if (!link) return ['Offline', 'bad'];
    return link.connected ? ['Connected', 'ok'] : ['Offline', 'bad'];
  }

  function ollamaFlag(llm) {
    if (!llm || !llm.ok) return ['Unavailable', 'bad'];
    return llm.warm ? ['Ready', 'ok'] : ['Starting', 'warn'];
  }

  // cloud captions (P-48): one cell that follows the `cloud` message, kept across status ticks
  const cloudLabel = h('span', { class: 'atp-stat-label', text: 'Cloud captions' });
  const cloudValue = h('span', { class: 'atp-stat-value', text: '—' });
  const cloudStat = h('div', { class: 'atp-stat atp-stat-wide', id: 'atp-cloud' }, cloudLabel, cloudValue);
  const CLOUD_REASON = { error: 'Google error' };
  function cloudText(c) {
    if (!c) return ['—', ''];
    const reason = CLOUD_REASON[c.reason] || c.reason || '';
    if (!c.enabled || c.state === 'off') return ['Off', ''];
    if (c.state === 'connecting') return ['Connecting', ''];
    if (c.state === 'on') return [Number.isFinite(c.latency_ms) ? `On · ${Math.round(c.latency_ms)} ms` : 'On', 'ok'];
    if (c.state === 'fallback') return [`Fell back to local${reason ? `: ${reason}` : ''}`, 'warn'];
    if (c.state === 'unavailable') return [`Unavailable${reason ? `: ${reason}` : ''}`, 'warn'];
    if (c.state === 'paused') return ['Paused', ''];
    return [String(c.state || '—'), ''];
  }
  function renderCloud(c) {
    const [text, tone] = cloudText(c);
    cloudStat.className = `atp-stat atp-stat-wide ${tone}`;
    cloudValue.textContent = text;
    cloudStat.title = `Cloud captions: ${text}`;
  }

  function renderStatus(s) {
    const delay = typeof s.caption_delay === 'number' ? `${s.caption_delay.toFixed(2)} s` : '—';
    const delayTone = typeof s.caption_delay === 'number' ? (s.caption_delay <= 1 ? 'ok' : s.caption_delay <= 1.5 ? 'warn' : 'bad') : '';
    const fps = typeof s.fps === 'number' ? s.fps.toFixed(0) : '—';
    const [ard, ardTone] = arduinoFlag(s.arduino);
    const [oll, ollTone] = ollamaFlag(s.ollama);
    statusGrid.replaceChildren(
      stat('FPS', fps, typeof s.fps === 'number' ? (s.fps >= 24 ? 'ok' : 'warn') : ''),
      stat('Caption delay', delay, delayTone),
      stat('GPU mem', typeof s.gpu_mem_gb === 'number' ? `${s.gpu_mem_gb.toFixed(1)} GB` : '—', typeof s.gpu_mem_gb === 'number' && s.gpu_mem_gb > 11 ? 'warn' : ''),
      stat('Arduino', ard, ardTone),
      stat('Ollama', oll, ollTone),
      stat('Power', s.on_battery == null ? '—' : s.on_battery ? 'On battery' : 'Plugged in', s.on_battery == null ? '' : s.on_battery ? 'bad' : 'ok'),
      meter('Mic level', s.mic_level),
      cloudStat,
    );
    const parts = Array.isArray(s.parts) ? s.parts : Object.entries(s.parts || {}).map(([part, v]) => (typeof v === 'object' && v ? { part, ...v } : { part, ok: !!v }));
    partsRow.replaceChildren(...parts.map((p) => h('span', { class: `atp-part ${p.ok && !p.stale ? 'ok' : 'bad'}`, title: p.stale ? 'status is stale' : p.detail || (p.ok ? 'healthy' : 'not running') }, h('i'), p.part)));
    if (!parts.length) partsRow.append(h('span', { class: 'atp-muted', text: 'Waiting for part health…' }));
    const cal = parts.find((p) => p.part === 'calibration');
    if (cal) calStatus.textContent = cal.detail || (cal.ok ? 'Calibration ready' : 'Calibration not ready');
  }
  statusGrid.append(h('span', { class: 'atp-muted', text: 'Waiting for engine status…' }));

  // ---------------------------------------------------------------- enroll
  const thumbGrid = h('div', { class: 'atp-thumbs', role: 'listbox', 'aria-label': 'Faces in view' });
  const nameInput = h('input', { class: 'atp-input', type: 'text', placeholder: 'Their name, e.g. Maya', maxlength: '40', autocomplete: 'off', 'aria-label': 'Name to enroll', oninput: () => renderEnrollForm() });
  const consentBox = h('input', { type: 'checkbox', id: 'atp-consent', onchange: () => renderEnrollForm() });
  const consentText = h('span');
  const consentRow = h('label', { class: 'atp-consent', for: 'atp-consent' }, consentBox, h('span', { class: 'atp-check' }), consentText);
  const enrollBtn = h('button', { class: 'atp-btn atp-primary atp-wide', type: 'button', onclick: startEnroll });
  const progress = h('div', { class: 'atp-progress', hidden: true });
  const enrollCard = section('Enroll a person', { hint: 'E', id: 'atp-enroll' },
    h('p', { class: 'atp-help', text: 'Pick their face, type their name, and let them tick consent themselves.' }),
    thumbGrid, nameInput, consentRow, enrollBtn, progress);

  let thumbSig = '';
  function renderThumbs() {
    const ids = [...st.visible];
    if (st.selected !== null && !st.visible.has(st.selected) && st.thumbs.has(st.selected)) ids.push(st.selected);
    // thumbnails arrive about once a second: rebuild only when the faces or the choice change
    const sig = JSON.stringify([ids, st.selected, [...st.visible]]);
    if (sig === thumbSig) {
      for (const img of thumbGrid.querySelectorAll('img')) {
        const src = `data:image/jpeg;base64,${st.thumbs.get(Number(img.dataset.track)) ?? st.thumbs.get(img.dataset.track)}`;
        if (img.src !== src) img.src = src;
      }
      return;
    }
    thumbSig = sig;
    const hadFocus = thumbGrid.contains(document.activeElement) ? document.activeElement.dataset.track : null;
    thumbGrid.replaceChildren();
    if (!ids.length) {
      thumbGrid.append(h('div', { class: 'atp-empty', text: 'No faces in view. Ask them to look at the glasses.' }));
      return;
    }
    for (const id of ids) {
      const b64 = st.thumbs.get(id);
      const selected = id === st.selected;
      thumbGrid.append(h('button', {
        class: `atp-thumb${selected ? ' selected' : ''}${st.visible.has(id) ? '' : ' gone'}`, type: 'button', role: 'option', 'aria-selected': String(selected), dataset: { track: String(id) },
        title: st.visible.has(id) ? `Track ${id}` : `Track ${id} (left the view)`,
        onclick: () => { st.selected = selected ? null : id; renderThumbs(); renderEnrollForm(); if (!selected) nameInput.focus(); },
      }, b64 ? h('img', { src: `data:image/jpeg;base64,${b64}`, alt: `Face ${id}`, dataset: { track: String(id) } }) : h('span', { class: 'atp-thumb-ph' }), h('span', { class: 'atp-thumb-id', text: `#${id}` })));
    }
    if (hadFocus) thumbGrid.querySelector(`[data-track="${CSS.escape(hadFocus)}"]`)?.focus();
  }

  function renderEnrollForm() {
    const name = nameInput.value.trim();
    consentText.textContent = `${name || 'This person'} agreed to store a face and voice print on this laptop`;
    const running = st.enroll && !st.enroll.done;
    enrollBtn.disabled = running || st.selected === null || !name || !consentBox.checked;
    enrollBtn.textContent = running ? 'Enrolling…' : st.selected === null ? 'Pick a face first' : !name ? 'Type their name' : !consentBox.checked ? 'Waiting for consent' : `Enroll ${name}`;
  }

  function startEnroll() {
    const name = nameInput.value.trim();
    if (st.selected === null || !name || !consentBox.checked) return;
    st.enroll = { track_id: st.selected, name, person_id: null, face: 'wait', voice: 'idle', reason: '' };
    send('enroll.start', { track_id: st.selected, name, consent: true, consent_t: Date.now() / 1000 });
    renderProgress();
    renderEnrollForm();
  }

  function renderProgress() {
    const e = st.enroll;
    progress.hidden = !e;
    if (!e) return;
    const step = (label, state, sub) => h('div', { class: `atp-step ${state}` }, h('i'), h('div', {}, h('strong', { text: label }), h('span', { text: sub })));
    const faceSub = { wait: 'Hold still, facing the camera…', ok: 'Face saved', fail: e.faceReason || 'Try again' }[e.face];
    const voiceSub = { idle: 'Next: they talk for 5 seconds', wait: `Ask ${e.name} to say a sentence, e.g. "I'm ${e.name}, I'm on the team"`, ok: 'Voice saved', fail: e.voiceReason || 'Try again' }[e.voice];
    const children = [h('div', { class: 'atp-progress-title', text: e.done && e.face === 'ok' && e.voice === 'ok' ? `${e.name} saved` : `Enrolling ${e.name}` }), step('Face', e.face, faceSub), step('Voice', e.voice, voiceSub)];
    if (e.face === 'fail' || e.voice === 'fail') {
      children.push(h('div', { class: 'atp-row' },
        h('button', { class: 'atp-btn atp-ghost', type: 'button', text: 'Cancel', onclick: () => { st.enroll = null; renderProgress(); renderEnrollForm(); } }),
        h('button', { class: 'atp-btn atp-primary', type: 'button', text: 'Try again', onclick: () => { st.selected = e.track_id; startEnroll(); } })));
    }
    progress.replaceChildren(...children);
  }

  function onEnrollResult(r) {
    const e = st.enroll;
    if (!e) return;
    if (r.track_id !== undefined && r.track_id !== null && r.track_id !== e.track_id) return;
    if (r.person_id) e.person_id = r.person_id;
    if (r.part === 'face') {
      e.face = r.ok ? 'ok' : 'fail';
      e.faceReason = r.reason;
      if (r.ok) e.voice = 'wait';
      else if (r.reason) toast(`Face: ${r.reason}`, 'warn');
    } else if (r.part === 'voice') {
      e.voice = r.ok ? 'ok' : 'fail';
      e.voiceReason = r.reason;
      if (!r.ok && r.reason) toast(`Voice: ${r.reason}`, 'warn');
    }
    if (e.face === 'ok' && e.voice === 'ok') {
      e.done = true;
      toast(`${e.name} saved`, 'ok');
      setTimeout(() => {
        if (st.enroll === e) {
          st.enroll = null;
          st.selected = null;
          nameInput.value = '';
          consentBox.checked = false;
          renderThumbs();
          renderProgress();
          renderEnrollForm();
        }
      }, 3500);
    }
    renderProgress();
    renderEnrollForm();
  }

  // ---------------------------------------------------------------- people
  const peopleList = h('ul', { class: 'atp-people' });
  const peopleCard = section('People', { hint: 'saved with consent', id: 'atp-people' }, peopleList);

  function renderPeople() {
    if (st.renaming) return; // an engine update mid-rename would drop the typed name
    peopleList.replaceChildren();
    if (!st.people.length) {
      peopleList.append(h('li', { class: 'atp-empty', text: 'Nobody saved yet. Enroll someone above; strangers stay session-only.' }));
      return;
    }
    for (const p of st.people) {
      const busy = st.busy.has(p.person_id);
      const nameEl = h('strong', { text: p.name || 'Unnamed' });
      const row = h('li', { class: `atp-person${busy ? ' busy' : ''}` },
        h('span', { class: 'atp-avatar', text: (p.name || '?').slice(0, 1).toUpperCase() }),
        h('div', { class: 'atp-grow' }, nameEl,
          h('div', { class: 'atp-badges' },
            h('span', { class: `atp-badge ${p.has_face ? 'on' : ''}`, text: 'Face' }),
            h('span', { class: `atp-badge ${p.has_voice ? 'on' : ''}`, text: 'Voice' }),
            h('span', { class: 'atp-muted', text: p.consent_t ? `Consent ${dateText(p.consent_t)}` : 'Consent date unknown' }))),
        h('button', { class: 'atp-icon-btn', type: 'button', title: `Rename ${p.name}`, 'aria-label': `Rename ${p.name}`, disabled: busy, onclick: () => beginRename(row, p) }, '✎'),
        h('button', { class: 'atp-icon-btn atp-danger-text', type: 'button', title: `Delete ${p.name}`, 'aria-label': `Delete ${p.name}`, disabled: busy, onclick: () => removePerson(p) }, '🗑'));
      peopleList.append(row);
    }
  }

  function beginRename(row, p) {
    const input = h('input', { class: 'atp-input atp-inline', type: 'text', value: p.name || '', maxlength: '40', 'aria-label': `New name for ${p.name}` });
    let finished = false;
    const finish = (save) => {
      if (finished) return;
      finished = true;
      st.renaming = false;
      const name = input.value.trim();
      if (save && name && name !== p.name) {
        send('person.rename', { person_id: p.person_id, name });
        p.name = name;
        toast(`Renamed to ${name}`, 'ok');
      }
      renderPeople();
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') finish(true);
      if (e.key === 'Escape') { e.stopPropagation(); finish(false); }
    });
    input.addEventListener('blur', () => finish(true));
    st.renaming = true;
    row.querySelector('.atp-grow strong').replaceWith(input);
    input.focus();
    input.select();
  }

  async function removePerson(p) {
    const ok = await confirm({ title: `Delete ${p.name}?`, body: `This removes ${p.name}'s face print, voice print and every saved file for them from this laptop. It can't be undone.`, confirm: 'Delete everything', danger: true });
    if (!ok) return;
    st.busy.add(p.person_id);
    send('person.delete', { person_id: p.person_id });
    renderPeople();
  }

  function onPersonChanged(m) {
    const i = st.people.findIndex((p) => p.person_id === m.person_id);
    if (m.action === 'deleted') {
      if (i >= 0) st.people.splice(i, 1);
      st.busy.delete(m.person_id);
      toast(`${m.name || 'Person'} deleted`, 'ok');
    } else if (m.action === 'renamed' && i >= 0) st.people[i].name = m.name;
    else if (m.action === 'enrolled' && i < 0) st.people.push({ person_id: m.person_id, name: m.name, consent_t: new Date().toISOString(), has_face: true, has_voice: false });
    renderPeople();
  }

  // ---------------------------------------------------------------- session + switches
  const pauseBtn = h('button', { class: 'atp-btn atp-ghost', type: 'button', onclick: () => send('pause.toggle') });
  const forgetBtn = h('button', { class: 'atp-btn atp-danger-ghost', type: 'button', text: 'Forget this session', onclick: forgetSession });
  const switchRows = SWITCHES.map(([key, label, sub]) => {
    const btn = h('button', { class: 'atp-switch', type: 'button', role: 'switch', 'aria-label': label, onclick: () => {
      st.switches[key] = !st.switches[key];
      send('switch.set', { key, value: st.switches[key] });
      renderSwitches();
    } });
    return { key, btn, row: h('div', { class: 'atp-switch-row' }, h('div', { class: 'atp-grow' }, h('strong', { text: label }), h('span', { class: 'atp-muted', text: sub })), btn) };
  });
  const langRow = h('div', { class: 'atp-chips', role: 'group', 'aria-label': 'Languages' });
  const sessionCard = section('Session', { id: 'atp-session' },
    h('div', { class: 'atp-row' }, pauseBtn, forgetBtn),
    h('div', { class: 'atp-switches' }, switchRows.map((s) => s.row)),
    h('div', { class: 'atp-lang' }, h('span', { class: 'atp-label', text: 'Languages heard' }), langRow));

  function renderSwitches() {
    for (const s of switchRows) {
      s.btn.setAttribute('aria-checked', String(!!st.switches[s.key]));
      s.btn.classList.toggle('on', !!st.switches[s.key]);
    }
    langRow.replaceChildren(...LANGS.map(([code, label]) => h('button', {
      class: `atp-chip${st.langs.has(code) ? ' on' : ''}`, type: 'button', 'aria-pressed': String(st.langs.has(code)),
      onclick: () => {
        if (st.langs.has(code) && st.langs.size === 1) return toast('Keep at least one language', 'warn');
        st.langs.has(code) ? st.langs.delete(code) : st.langs.add(code);
        send('languages.set', { langs: LANGS.map(([c]) => c).filter((c) => st.langs.has(c)) });
        renderSwitches();
      },
    }, h('b', { text: code.toUpperCase() }), label)));
  }

  function renderPaused() {
    pauseBtn.textContent = ctx.state.paused ? '▶  Resume recognition' : '❚❚  Pause recognition';
    pauseBtn.classList.toggle('atp-warn-ghost', !!ctx.state.paused);
  }

  async function forgetSession() {
    const ok = await confirm({ title: 'Forget this session?', body: 'Strangers, session names and this session\'s captions are wiped everywhere. People who consented stay saved.', confirm: 'Forget session', danger: true });
    if (!ok) return;
    send('session.forget');
    toast('Session forgotten', 'ok');
  }

  // ---------------------------------------------------------------- rig patterns
  const linkLine = h('div', { class: 'atp-link' });
  const patternGrid = h('div', { class: 'atp-patterns' },
    PATTERNS.map((name) => h('div', { class: 'atp-pattern' },
      h('div', { class: 'atp-pattern-name' }, h('strong', { text: name }), h('span', { text: PATTERN_HINT[name] })),
      h('div', { class: 'atp-seg' }, SIDES.map(([side, label]) => h('button', { type: 'button', title: `${name} ${label}`, text: side, onclick: (e) => {
        send('pattern.test', { name, side });
        e.currentTarget.classList.remove('flash');
        void e.currentTarget.offsetWidth;
        e.currentTarget.classList.add('flash');
      } }))))));
  const rigCard = section('Light and buzz tests', { id: 'atp-rig' }, linkLine, patternGrid);

  function renderLink() {
    const l = ctx.state.hwLink;
    linkLine.className = `atp-link ${l?.connected ? 'ok' : 'bad'}`;
    linkLine.replaceChildren(h('i'), l?.connected
      ? h('span', {}, h('strong', { text: 'Rig connected' }), ` · firmware ${l.firmware || '?'} · driver ${l.driver || '?'}`)
      : h('span', {}, h('strong', { text: 'Rig not connected' }), ' · tests are sent but nothing will buzz'));
  }

  // ---------------------------------------------------------------- calibration
  const calStatus = h('p', { class: 'atp-help', text: 'Run each step in order, then Save it.' });
  const calGrid = h('ol', { class: 'atp-cal' });
  const calCard = section('Calibration', { id: 'atp-cal' }, calStatus, calGrid);

  function renderCal() {
    calGrid.replaceChildren(...CAL_STEPS.map(([step, label, sub], i) => {
      const active = st.calActive === step;
      const run = h('button', { class: 'atp-btn atp-small atp-ghost', type: 'button', text: active ? 'Running' : 'Run', onclick: () => {
        st.calActive = step;
        send('calibrate.step', { step });
        renderCal();
      } });
      const save = h('button', { class: 'atp-btn atp-small atp-primary', type: 'button', text: 'Save', disabled: !active, onclick: () => {
        const measurements = step === 'level' ? { confirmed: true } : step === 'faces' ? { distances_checked: [1, 2, 3] } : {};
        send('calibrate.step', { step: `finish:${step}`, measurements });
        st.calActive = null;
        toast(`${label} saved`, 'ok');
        renderCal();
      } });
      return h('li', { class: `atp-cal-step${active ? ' active' : ''}` }, h('span', { class: 'atp-cal-n', text: String(i + 1) }), h('div', { class: 'atp-grow' }, h('strong', { text: label }), h('span', { class: 'atp-muted', text: sub })), run, save);
    }));
  }

  // ---------------------------------------------------------------- event log
  const logList = h('ol', { class: 'atp-log', 'aria-live': 'off' });
  const noteInput = h('input', { class: 'atp-input', type: 'text', placeholder: 'Note (optional)', maxlength: '120', 'aria-label': 'Mark note', onkeydown: (e) => { if (e.key === 'Enter') mark(); } });
  const logCard = section('Event log', { id: 'atp-log' },
    h('div', { class: 'atp-row' }, noteInput, h('button', { class: 'atp-btn atp-amber', type: 'button', text: '⚑ Mark', onclick: mark })), logList);

  function mark() {
    const note = noteInput.value.trim();
    send('mark', { note });
    noteInput.value = '';
    toast(note ? `Marked: ${note}` : 'Marked', 'ok', 1800);
  }

  function addLog(m) {
    st.log.unshift(m);
    if (st.log.length > LOG_MAX) st.log.length = LOG_MAX;
    const item = h('li', {}, h('time', { text: clockText(m.t) }), h('span', { text: m.text }));
    logList.prepend(item);
    while (logList.children.length > LOG_MAX) logList.lastChild.remove();
    empty.remove();
  }
  const empty = h('li', { class: 'atp-empty', text: 'Events from every part show up here.' });
  logList.append(empty);

  // ---------------------------------------------------------------- layout
  const jump = h('nav', { class: 'atp-jump', 'aria-label': 'Console sections' },
    [['Enroll', enrollCard], ['People', peopleCard], ['Session', sessionCard], ['Rig', rigCard], ['Calibrate', calCard], ['Log', logCard]]
      .map(([label, card]) => h('button', { type: 'button', text: label, onclick: () => card.scrollIntoView({ behavior: 'smooth', block: 'start' }) })));
  const saveCard = createSaveCard({ send, thumb: (id) => st.thumbs.get(id) }); // P-29: double tap -> consent
  const el = h('div', { class: 'atp-view atp-console' }, statusCard, saveCard.el, jump, enrollCard, peopleCard, sessionCard, rigCard, calCard, logCard);

  renderThumbs();
  renderEnrollForm();
  renderPeople();
  renderSwitches();
  renderPaused();
  renderLink();
  renderCal();

  return {
    el,
    title: 'Console',
    focusEnroll() {
      enrollCard.scrollIntoView({ block: 'start' });
      (st.selected === null ? thumbGrid.querySelector('button') || nameInput : nameInput).focus();
    },
    onMessage(m) {
      saveCard.onMessage(m);
      switch (m.type) {
        case 'status': renderStatus(m); break;
        case 'thumbnails': {
          const list = m.thumbnails || m.list || m.items || [];
          st.visible = new Set(list.map((t) => t.track_id));
          for (const t of list) st.thumbs.set(t.track_id, t.jpeg_b64);
          if (st.thumbs.size > 60) for (const id of st.thumbs.keys()) { if (!st.visible.has(id) && id !== st.selected) st.thumbs.delete(id); }
          renderThumbs();
          break;
        }
        case 'people': st.people = (m.people || m.list || m.items || []).slice(); for (const id of st.busy) if (!st.people.some((p) => p.person_id === id)) st.busy.delete(id); renderPeople(); break;
        case 'person_changed': onPersonChanged(m); break;
        case 'enroll_result': onEnrollResult(m); break;
        case 'event_log': addLog(m); break;
        case 'paused': case 'welcome': renderPaused(); break;
        case 'hw_link': renderLink(); break;
        case 'cloud': renderCloud(m); if (!cloudStat.isConnected) statusGrid.append(cloudStat); break;
        default: break;
      }
    },
  };
}
