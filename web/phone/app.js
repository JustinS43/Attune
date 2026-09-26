/*
 * Attune phone app.
 *
 * Section 4 - Pages, Engine & Demo. Contracts: docs/contracts.md (3, 4, 5, Pages additions).
 *
 * Connects to the engine through ../shared/ws.js with role 'phone'. While connected, every
 * screen shows live data (captions, people, name proposals, alerts, suggested replies, history)
 * and every control sends the matching command. Until it has ever connected, it runs the
 * built-in demo, labelled "Demo". Open with ?demo to stay in the demo without connecting.
 */

import { connect } from '../shared/ws.js';
import { createSaveSheet } from './save.js';
import {listContacts, saveContact, deleteContact, photoFromFile} from './contacts.js';

const app = document.querySelector('#app');
const content = document.querySelector('#screen-content');
const viewport = document.querySelector('.app-viewport');
const toast = document.querySelector('#toast');
const alertBanner = document.querySelector('#alert-banner');
const hint = document.querySelector('.desktop-hint');
const params = new URLSearchParams(location.search);

const DEFAULT_PRESETS = ['Nice to meet you', 'Can you repeat that?', 'One moment', 'Thank you', 'I read captions, go ahead'];
const ALERT_TEXT = { smoke: 'Smoke alarm', co: 'Carbon monoxide alarm', doorbell: 'Doorbell' };
const VOICE_NAME = { elevenlabs: 'ElevenLabs', kokoro: 'offline voice' };

// Optional Apricot Studio palette (Ryan's colorway); the default keeps the original palette.
const palette = params.get('palette');
if (palette === 'apricot') {
  app.classList.add('palette-apricot');
  document.body.classList.add('palette-apricot-preview');
  document.title = 'Attune · Apricot Studio';
}

const state = {
  screen: 'home', theme: 'light', paused: false, powered: true,
  features: { captions: true, names: true, alerts: true, translation: true },
  people: [
    { id: 'maya', name: 'Maya Chen', seen: 12, color: '', consent: true },
    { id: 'leo', name: 'Leo Martin', seen: 8, color: 'blue', consent: true }
  ],
  contacts: [],
  contactDraft: {name: '', photo: '', consent: false},
  editingContactId: null,
  proposal: 'Sam',
  history: [
    { speaker: 'Maya', text: 'We can meet by the entrance.', time: '2:14 PM', color: '' },
    { speaker: 'Leo', text: 'I can bring the notes.', time: '2:15 PM', color: 'violet' },
    { speaker: 'You', text: 'Sounds good, thank you.', time: '2:15 PM', color: 'blue' }
  ],
  draft: 'Nice to meet you, Sam!',
  voice: '', tone: 'natural',
  // live link
  live: false,        // has ever connected: live data replaces the demo
  connected: false,
  captions: [],       // live captions, newest last
  liveProposal: null, // {proposal_id, name}
  alert: null,        // {alert_id, kind, side}
  presets: [],
  suggestions: [],
  speaking: '',
  hwLink: null,
  sessionId: null,
  enroll: {phase: 'ready', name: '', consent: false, trackId: null, personId: null, photo: '', face: 'idle', voice: 'idle', reason: ''},
  faces: new Map()
};
let enrollmentFeed = null;
let portrait = '';
let portraitReady = false;
let latestFaceBoxes = new Map();
let frameBusy = false;
let lastPortraitFrameAt = 0;

function icon(name, className = 'icon') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', className);
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}

function el(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}

function brand() {
  const row = el('div', 'brand');
  const mark = el('span', 'brand-mark');
  mark.append(el('i'), el('i'));
  row.append(mark, el('span', '', 'attune'));
  return row;
}

function heading(title, subtitle, eyebrow) {
  if (eyebrow) content.append(el('p', 'eyebrow', eyebrow));
  content.append(el('h1', 'title', title));
  if (subtitle) content.append(el('p', 'subtitle', subtitle));
}

/** Connection pill: LIVE (connected), RECONNECTING (was live) or DEMO. */
function pill() {
  if (state.live && state.connected) return el('div', 'demo-pill live-pill', 'LIVE · CONNECTED TO ATTUNE');
  if (state.live) return el('div', 'demo-pill offline-pill', 'OFFLINE · RECONNECTING');
  return el('div', 'demo-pill', 'DEMO · NOT CONNECTED');
}

function button(label, className, action) {
  const node = el('button', className, label);
  node.type = 'button';
  node.dataset.action = action;
  return node;
}

function pressCard({title, sub, iconName, action, dot = false, dotClass = ''}) {
  const node = button('', 'press-card', action);
  node.textContent = '';
  node.append(dot ? el('span', `connected-dot ${dotClass}`) : iconBubble(iconName));
  const copy = el('span', 'grow');
  copy.append(el('span', 'card-title', title));
  if (sub) copy.append(el('span', 'card-sub', sub));
  node.append(copy, icon('arrow', 'chevron'));
  return node;
}

function iconBubble(name) {
  const node = el('span', 'icon-bubble');
  node.append(icon(name));
  return node;
}

function avatar(name, color = '') {
  return el('span', `avatar ${color}`, (name || '?').slice(0, 1).toUpperCase());
}

function contactAvatar(name, color, photo) {
  if (!photo) return avatar(name, color);
  const image = el('img', 'avatar contact-avatar');
  image.src = photo;
  image.alt = '';
  return image;
}

function previewLine(name, line, color) {
  const row = el('div', 'preview-line');
  const copy = el('div', 'grow');
  copy.append(el('strong', '', name), el('span', '', line));
  row.append(avatar(name, color), copy);
  return row;
}

// ------------------------------------------------------------------ live helpers
const COLORS = ['', 'blue', 'violet', 'amber'];
function colorFor(label) {
  const key = String(label || '').toLowerCase();
  if (key.startsWith('you')) return 'blue';
  let hash = 0;
  for (const ch of key) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return COLORS[hash % COLORS.length];
}

function speakerName(speaker) {
  if (!speaker) return 'Someone';
  if (speaker.kind === 'you') return 'You';
  if (speaker.kind === 'you_typed') return 'You (typed)';
  if (!state.features.names && speaker.kind !== 'you') return 'Someone';
  return speaker.label || 'Someone';
}

function captionText(c) {
  return state.features.translation && c.translation ? c.translation : c.text;
}

function timeNow() {
  return new Intl.DateTimeFormat('en-US', {hour: 'numeric', minute: '2-digit'}).format(new Date());
}

function greeting() {
  const hour = new Date().getHours();
  return hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
}

function glassesStatus() {
  if (state.live) {
    if (!state.connected) return {title: 'Reconnecting to Attune…', sub: 'Showing the last captions received', dot: true, dotClass: 'off'};
    if (state.paused) return {title: 'Glasses connected', sub: 'Recognition is paused', dot: true, dotClass: 'paused'};
    return {title: 'Glasses connected', sub: 'Captions are live', dot: true};
  }
  return {title: state.powered ? 'Glasses preview ready' : 'Glasses preview off', sub: state.powered ? (state.paused ? 'Recognition paused in this demo' : 'Demo captions, not connected') : 'Turn on in Settings to resume', dot: state.powered && !state.paused};
}

// ------------------------------------------------------------------ screens
function renderHome() {
  content.append(brand(), pill(), el('h1', 'hero-title', greeting()));
  const status = glassesStatus();
  content.append(pressCard({...status, action: 'settings'}));
  if (state.live && state.liveProposal) content.append(proposalCard());
  const feature = el('section', 'feature-card');
  const head = el('div', 'feature-head');
  head.append(icon('wave'), el('span', '', 'Live conversation'));
  const lines = el('div', 'conversation-preview');
  lines.id = 'home-preview';
  if (state.live) {
    fillHomePreview(lines);
  } else if (state.powered && !state.paused && state.features.captions) {
    lines.append(previewLine('Maya', 'That sounds like a great idea.', 'blue'), previewLine('Leo', 'I agree, let’s go with that.'));
  } else {
    lines.append(el('div', 'empty', state.paused ? 'Recognition is paused.' : 'Captions are unavailable in this preview.'));
  }
  const open = button('Open live view', 'primary full', 'live');
  open.prepend(icon('wave'));
  feature.append(head, lines, open);
  content.append(feature);
  const shortcuts = el('div', 'shortcuts');
  for (const item of [{name:'Speak for me',iconName:'mic',action:'speak'}, {name:'Conversations',iconName:'clock',action:'history'}, {name:'People',iconName:'people',action:'people'}, {name:'Glasses settings',iconName:'settings',action:'settings'}]) {
    const card = button('', 'press-card shortcut', item.action);
    card.textContent = '';
    card.append(iconBubble(item.iconName));
    const label = el('span', 'shortcut-label');
    label.append(el('span', '', item.name), icon('arrow', 'chevron'));
    card.append(label);
    shortcuts.append(card);
  }
  content.append(shortcuts);
}

function fillHomePreview(lines) {
  lines.replaceChildren();
  const recent = state.captions.filter(c => c.final !== false).slice(-2);
  if (state.paused) lines.append(el('div', 'empty', 'Recognition is paused.'));
  else if (!state.features.captions) lines.append(el('div', 'empty', 'Captions are hidden on this phone.'));
  else if (!recent.length) lines.append(el('div', 'empty', 'Waiting for someone to speak…'));
  else for (const c of recent) lines.append(previewLine(speakerName(c.speaker), captionText(c), colorFor(speakerName(c.speaker))));
}

function back(to = 'home') {
  const node = button('Back', 'back', to);
  node.prepend(icon('back'));
  return node;
}

function proposalCard() {
  const name = state.live ? state.liveProposal.name : state.proposal;
  const card = el('div', 'card person-card proposal-card');
  const top = el('div', 'person-top');
  const copy = el('div', 'grow');
  copy.append(el('div', 'card-title', `${name}?`), el('div', 'card-sub', 'Name heard in conversation · session only'));
  top.append(avatar(name, 'amber'), copy);
  const actions = el('div', 'person-actions');
  actions.append(button('Confirm name', 'primary', 'confirm-proposal'), button(`Not ${name}`, 'outline', 'reject-proposal'));
  card.append(top, actions);
  return card;
}

function renderPeople() {
  content.append(back(), brand());
  heading('People', 'Your familiar faces, all in one place');
  if (state.live) content.append(pill());
  const add = el('div', 'contact-actions');
  add.append(button('Remember someone', 'primary full', 'enroll'));
  content.append(add);
  content.append(el('h2', 'section-title', 'Contacts'));
  const list = el('div', 'people-list');
  const enrolled = state.people.map(person => ({...person, contact: state.contacts.find(item => item.personId === person.id)}));
  const uploaded = state.contacts.filter(item => !item.personId).map(item => ({id: item.id, name: item.name, contact: item, photoOnly: true}));
  const entries = [...enrolled, ...uploaded].sort((a, b) => a.name.localeCompare(b.name));
  if (!entries.length) list.append(el('div', 'card empty', 'No contacts yet. Invite someone to save their face.'));
  for (const person of entries) {
    const card = el('div', 'card person-card');
    const top = el('div', 'person-top');
    const copy = el('div', 'grow');
    let sub = person.photoOnly ? 'Photo contact · Recognition not set up' : `Seen ${person.seen} times · Saved with consent`;
    if (state.live && !person.photoOnly) {
      const parts = [person.has_face ? 'Face' : '', person.has_voice ? 'Voice' : ''].filter(Boolean).join(' + ') || 'Name only';
      sub = `${parts} · Saved with consent${person.consent_t ? ` ${formatDate(person.consent_t)}` : ''}`;
    }
    copy.append(el('div', 'card-title', person.name), el('div', 'card-sub', sub));
    top.append(contactAvatar(person.name, person.color, person.contact?.photo), copy);
    if (state.editingContactId === person.id) {
      const editor = el('div', 'contact-editor');
      const label = el('label', 'enroll-label', 'Name');
      const input = el('input', 'enroll-name');
      input.id = 'contact-rename'; input.type = 'text'; input.maxLength = 60; input.value = person.name;
      label.append(input);
      const actions = el('div', 'person-actions');
      const save = button('Save name', 'primary', 'save-rename'); save.dataset.personId = person.id;
      if (person.photoOnly) save.dataset.contactOnly = 'true';
      actions.append(save, button('Cancel', 'outline', 'cancel-rename'));
      editor.append(label, actions);
      card.append(top, editor);
    } else {
      const actions = el('div', 'person-actions');
      const rename = button('Rename', 'outline', 'rename-person');
      const remove = button('Remove', 'outline', 'remove-person');
      rename.dataset.personId = person.id;
      remove.dataset.personId = person.id;
      if (person.photoOnly) { rename.dataset.contactOnly = 'true'; remove.dataset.contactOnly = 'true'; }
      rename.setAttribute('aria-label', `Rename ${person.name}`);
      remove.setAttribute('aria-label', `Remove ${person.name}`);
      actions.append(rename, remove);
      card.append(top, actions);
    }
    list.append(card);
  }
  content.append(list);
  content.append(el('h2', 'section-title', 'This session'));
  const hasProposal = state.live ? !!state.liveProposal : !!state.proposal;
  if (hasProposal) content.append(proposalCard());
  else content.append(el('div', 'card empty', 'No unconfirmed session names.'));
  content.append(el('p', 'note', 'Photo contacts stay on this device. Face recognition is added only after the person agrees and completes live face enrollment.'));
}

function renderNewContact() {
  const draft = state.contactDraft;
  content.append(back('people'), brand());
  heading('New contact', 'Add a photo and name to your list.', 'Keep someone close');
  const photo = el('div', 'enroll-photo card contact-photo');
  if (draft.photo) {
    const image = el('img', 'enroll-preview'); image.src = draft.photo; image.alt = 'Selected contact photo';
    photo.append(image); photo.classList.add('has-image');
  } else {
    const placeholder = el('div', 'enroll-photo-placeholder');
    placeholder.append(icon('camera'), el('span', '', 'Choose a clear photo showing their whole face'));
    photo.append(placeholder);
  }
  content.append(photo);
  const form = el('div', 'enroll-form card');
  const fileLabel = el('label', 'enroll-label', 'Photo');
  const file = el('input', 'contact-file');
  file.id = 'contact-file'; file.type = 'file'; file.accept = 'image/*';
  fileLabel.append(file);
  const nameLabel = el('label', 'enroll-label', 'Name');
  const name = el('input', 'enroll-name');
  name.id = 'contact-name'; name.type = 'text'; name.maxLength = 60; name.placeholder = 'Their name'; name.value = draft.name;
  nameLabel.append(name);
  const consent = el('label', 'enroll-consent');
  const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.id = 'contact-consent'; checkbox.checked = draft.consent;
  consent.append(checkbox, el('span', '', 'I agree to save my photo and name on this device. Ask the person in the photo to tick this themselves.'));
  const save = button('Save contact', 'primary full', 'save-contact');
  save.id = 'save-contact'; save.disabled = !draft.photo || !draft.name.trim() || !draft.consent;
  form.append(fileLabel, nameLabel, consent, save);
  content.append(form, el('p', 'note', 'A photo contact appears in your list. Attune will not recognize them until they complete Remember Me in person.'));
}

function enrollLine(name) {
  return `I'm ${name}. It's nice to meet you, and I'm looking forward to our conversation.`;
}

function renderEnroll() {
  const enrollment = state.enroll;
  content.append(brand());
  heading('Remember me', 'Save your face so Attune can recognize you. Voice is optional.', 'A familiar face, a familiar voice');
  content.append(pill());

  const steps = el('div', 'enroll-steps');
  for (const [number, label] of [['1', 'Photo'], ['2', 'Consent'], ['3', 'Voice']]) {
    const step = el('span', `enroll-step${Number(number) <= (enrollment.phase === 'ready' || enrollment.phase === 'captured' ? 1 : enrollment.phase === 'face' ? 2 : 3) ? ' current' : ''}`);
    step.append(el('b', '', number), label);
    steps.append(step);
  }
  content.append(steps);

  const photo = el('div', 'enroll-photo card');
  const preview = el('img', 'enroll-preview');
  preview.id = 'enroll-preview';
  preview.alt = 'Full face portrait from the Attune camera';
  if (enrollment.photo || portrait) preview.src = enrollment.photo || portrait;
  const placeholder = el('div', 'enroll-photo-placeholder');
  placeholder.append(icon('camera'), el('span', '', state.live ? 'Move back until your whole face is in view…' : 'Connect to Attune to take a photo'));
  photo.append(preview, placeholder, el('span', 'enroll-camera-label', enrollment.photo ? 'PHOTO CAPTURED' : 'LIVE CAMERA PREVIEW'));
  photo.classList.toggle('has-image', !!preview.src);
  content.append(photo);

  if (enrollment.phase === 'ready' && state.faces.size > 1) {
    const choices = el('div', 'enroll-faces');
    for (const [trackId, face] of state.faces) {
      const choice = button('', `enroll-face${enrollment.trackId === trackId ? ' selected' : ''}`, 'select-face');
      choice.dataset.trackId = trackId;
      const image = el('img'); image.src = face.photo; image.alt = `Face ${trackId}`;
      choice.append(image);
      choices.append(choice);
    }
    content.append(el('p', 'note', 'More than one face is in view. Tap your own face.'), choices);
  }

  if (enrollment.phase === 'ready' || enrollment.phase === 'captured') {
    const shutter = button(enrollment.photo ? 'Retake photo' : 'Take my photo', enrollment.photo ? 'outline full' : 'primary full', 'take-photo');
    shutter.disabled = !state.live || !state.connected || !portraitReady;
    shutter.prepend(icon('camera'));
    content.append(shutter);
    content.append(el('p', 'note', 'Step back and face the Attune camera. The portrait includes your whole face and shoulders; recognition collects several views after you agree to save.'));
    if (enrollment.photo) {
      const form = el('div', 'enroll-form card');
      const label = el('label', 'enroll-label', 'Your name');
      const input = el('input', 'enroll-name');
      input.id = 'enroll-name'; input.type = 'text'; input.maxLength = 40;
      input.autocomplete = 'name'; input.placeholder = 'What should Attune call you?';
      input.value = enrollment.name;
      label.append(input);
      const consent = el('label', 'enroll-consent');
      const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.id = 'enroll-consent'; checkbox.checked = enrollment.consent;
      consent.append(checkbox, el('span', '', 'I agree to save my face print and, if I complete the voice step, my voice print on this laptop. I can delete them from People.'));
      const start = button('Save my face', 'primary full', 'start-enroll');
      start.id = 'start-enroll';
      start.disabled = !enrollment.name.trim() || !enrollment.consent || !state.connected;
      form.append(label, consent, start);
      content.append(form);
    }
  } else {
    const progress = el('div', 'enroll-progress card');
    const face = el('div', `enroll-progress-row ${enrollment.face}`);
    face.append(el('b', '', enrollment.face === 'ok' ? '✓' : '1'), el('span', '', enrollment.face === 'ok' ? 'Face saved' : enrollment.face === 'fail' ? `Face needs another try: ${enrollment.reason}` : 'Saving several views of your face…'));
    const voice = el('div', `enroll-progress-row ${enrollment.voice}`);
    voice.append(el('b', '', enrollment.voice === 'ok' ? '✓' : '2'), el('span', '', enrollment.voice === 'ok' ? 'Voice saved' : enrollment.voice === 'skipped' ? 'Voice skipped for now' : enrollment.face === 'ok' ? 'Speak clearly for at least five seconds' : 'Voice comes next'));
    progress.append(face, voice);
    content.append(progress);
    if (enrollment.phase === 'voice') {
      content.append(el('p', 'eyebrow enroll-prompt-label', 'Say this aloud near the Attune microphone'));
      content.append(el('div', 'enroll-prompt card', enrollLine(enrollment.name)));
      content.append(el('p', 'note', 'Keep your face in view and speak naturally. If the voice step stays open, say another short sentence.'));
      content.append(button('Skip voice for now', 'outline full enroll-skip', 'skip-voice'));
    } else if (enrollment.voice === 'skipped') {
      content.append(el('p', 'note', 'Your face is saved. Voice recognition is not set up yet.'));
    }
    if (enrollment.phase === 'done') {
      content.append(button('View saved people', 'primary full', 'people'), button('Enroll another person', 'outline full enroll-again', 'enroll-again'));
    } else if (enrollment.phase === 'error') {
      content.append(button('Try again', 'primary full', 'enroll-again'));
    }
  }
  content.append(el('div', 'info-card enroll-privacy', 'Attune saves consented face and optional voice prints on the laptop. Your contact photo stays on this device; the spoken recording is not kept.'));
  content.append(button(`View contacts (${state.people.length + state.contacts.filter(item => !item.personId).length})`, 'outline full enroll-contacts', 'people'));
}

function updateEnrollPreview() {
  if (state.screen !== 'enroll' || state.enroll.photo) return;
  const preview = document.querySelector('#enroll-preview');
  if (!preview) return;
  if (portrait) preview.src = portrait;
  else preview.removeAttribute('src');
  preview.parentElement.classList.toggle('has-image', !!portrait);
  const shutter = document.querySelector('[data-action="take-photo"]');
  if (shutter) shutter.disabled = !portraitReady || !state.connected;
}

async function updatePortrait(frame) {
  if (frameBusy || state.enroll.photo || state.screen !== 'enroll') return;
  if (performance.now() - lastPortraitFrameAt < 125) return;
  lastPortraitFrameAt = performance.now();
  const trackId = state.enroll.trackId ?? state.faces.keys().next().value;
  const box = latestFaceBoxes.get(trackId);
  if (!box) { portrait = ''; portraitReady = false; updateEnrollPreview(); return; }
  frameBusy = true;
  try {
    const bitmap = await createImageBitmap(frame.blob);
    try {
      const [x, y, w, h] = box;
      const height = Math.max(w * 2, h * 2.25);
      const width = height * .75;
      const left = x + w / 2 - width / 2;
      const top = y - h * .55;
      if (left < 0 || top < 0 || left + width > bitmap.width || top + height > bitmap.height) {
        portraitReady = false; portrait = ''; updateEnrollPreview(); return;
      }
      const canvas = document.createElement('canvas');
      canvas.width = 360; canvas.height = 480;
      canvas.getContext('2d').drawImage(bitmap, left, top, width, height, 0, 0, 360, 480);
      portrait = canvas.toDataURL('image/jpeg', .84);
      portraitReady = true;
      updateEnrollPreview();
    } finally {
      bitmap.close();
    }
  } catch {
    portraitReady = false;
  } finally {
    frameBusy = false;
  }
}

function syncEnrollmentFeed() {
  if (state.screen === 'enroll' && state.live && state.connected && !enrollmentFeed) {
    enrollmentFeed = connect({role: 'console', frames: true, onFrame: updatePortrait, onMessage(msg) {
      if (msg.type === 'scene') {
        latestFaceBoxes = new Map((msg.faces || []).filter(face => Number.isInteger(face.track_id) && Array.isArray(face.box)).map(face => [face.track_id, face.box]));
        return;
      }
      if (msg.type !== 'thumbnails') return;
      state.faces = new Map((msg.thumbnails || []).filter(face => Number.isInteger(face.track_id) && face.jpeg_b64).map(face => [face.track_id, {photo: `data:image/jpeg;base64,${face.jpeg_b64}`} ]));
      if (state.enroll.trackId !== null && !state.faces.has(state.enroll.trackId) && !state.enroll.photo) state.enroll.trackId = null;
      if (state.screen === 'enroll') {
        if (state.faces.size > 1 && state.enroll.phase === 'ready') refresh();
        else updateEnrollPreview();
      }
    }});
  } else if ((state.screen !== 'enroll' || !state.connected) && enrollmentFeed) {
    enrollmentFeed.close(); enrollmentFeed = null; state.faces.clear(); latestFaceBoxes.clear(); portrait = ''; portraitReady = false; lastPortraitFrameAt = 0;
  }
}

function formatDate(value) {
  const date = new Date(typeof value === 'number' || /^\d+(?:\.\d+)?$/.test(String(value)) ? Number(value) * 1000 : value);
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('en-US', {month: 'short', day: 'numeric'}).format(date);
}

function timelineCard(rows, title, subtitle) {
  const timeline = el('div', 'card timeline');
  const header = el('div', 'timeline-head');
  header.append(el('span', '', title), el('small', '', subtitle));
  timeline.append(header);
  rows.forEach((item, index) => {
    const row = el('div', 'timeline-row');
    const rail = el('div', 'timeline-rail');
    rail.append(el('span', `timeline-dot ${item.color}`));
    if (index < rows.length - 1) rail.append(el('span', 'timeline-stem'));
    const text = el('div');
    const name = el('span', `timeline-name ${item.color}`, item.speaker);
    text.append(name, el('span', 'timeline-time', item.time), el('p', 'timeline-text', item.text));
    if (item.translation) text.append(el('p', 'timeline-trans', item.translation));
    row.append(rail, text);
    timeline.append(row);
  });
  return timeline;
}

function drawHistory(query = '') {
  const target = document.querySelector('#history-results');
  if (!target) return;
  if (state.live) return drawLiveHistory(target, query);
  target.replaceChildren();
  const rows = state.history.filter(item => `${item.speaker} ${item.text}`.toLowerCase().includes(query.trim().toLowerCase()));
  if (!rows.length) {
    target.append(el('div', 'card empty', query ? 'No conversations match your search.' : 'No conversation in this session.'));
    return;
  }
  target.append(timelineCard(rows, 'Today', '· Demo'));
}

// History API (contracts section 5), same origin as the engine
function apiBase() {
  const host = params.get('engine');
  return host ? `${location.protocol === 'https:' ? 'https' : 'http'}://${host}` : '';
}

async function historyGet(path) {
  try {
    const res = await fetch(`${apiBase()}/api/history/${path}`, {headers: {Accept: 'application/json'}});
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

function rowTime(t) {
  const date = typeof t === 'number' && t > 1e9 ? new Date(t * 1000) : typeof t === 'string' ? new Date(t) : null;
  return date && !Number.isNaN(date.getTime()) ? new Intl.DateTimeFormat('en-US', {hour: 'numeric', minute: '2-digit'}).format(date) : '';
}

function apiRow(r) {
  const speaker = r.kind === 'alert' ? 'Sound alert' : r.speaker_label || 'Someone';
  return {speaker, text: r.text || '', translation: r.translation && r.translation !== r.text ? r.translation : '', time: rowTime(r.t), color: r.kind === 'alert' ? 'amber' : colorFor(speaker)};
}

let historyToken = 0;
async function drawLiveHistory(target, query) {
  const token = ++historyToken;
  const q = query.trim();
  let rows = null;
  let title = 'Latest session';
  let subtitle = '· Saved on the laptop';
  if (q) {
    rows = await historyGet(`search?${new URLSearchParams({q})}`);
    title = 'Search results';
    subtitle = Array.isArray(rows) ? `· ${rows.length} found` : '';
  } else {
    const sessions = await historyGet('sessions');
    if (Array.isArray(sessions) && sessions.length) {
      const latest = sessions.slice().sort((a, b) => (Number(b.started_t) || 0) - (Number(a.started_t) || 0))[0];
      rows = await historyGet(`sessions/${encodeURIComponent(latest.session_id)}`);
      title = latest.ended_t ? 'Last session' : 'This session';
      subtitle = `· ${latest.lines ?? (rows?.length || 0)} lines`;
    } else if (Array.isArray(sessions)) rows = [];
  }
  if (token !== historyToken || !target.isConnected) return;
  target.replaceChildren();
  if (!Array.isArray(rows)) {
    // no history API yet: fall back to this session's captions
    const local = state.captions.filter(c => c.final !== false && (!q || `${speakerName(c.speaker)} ${c.text} ${c.translation || ''}`.toLowerCase().includes(q.toLowerCase())))
      .map(c => ({speaker: speakerName(c.speaker), text: c.text, translation: c.translation && c.translation !== c.text ? c.translation : '', time: c.time, color: colorFor(speakerName(c.speaker))}));
    if (!local.length) target.append(el('div', 'card empty', q ? 'No lines match your search.' : 'History starts when the engine runs. Captions from this session will appear here.'));
    else target.append(timelineCard(local.slice(-40), 'This session', '· On this phone'));
    return;
  }
  if (!rows.length) {
    target.append(el('div', 'card empty', q ? 'No conversations match your search.' : 'No conversation saved yet.'));
    return;
  }
  target.append(timelineCard(rows.slice(-60).map(apiRow), title, subtitle));
}

function renderHistory() {
  content.append(brand());
  heading('Conversations', 'Catch up on what you missed');
  if (state.live) content.append(pill());
  const search = el('label', 'search-wrap');
  search.append(icon('search'));
  const input = el('input');
  input.type = 'search';
  input.placeholder = 'Search conversations';
  input.setAttribute('aria-label', 'Search conversations');
  input.id = 'history-search';
  search.append(input);
  content.append(search, el('div', '', ''));
  content.lastChild.id = 'history-results';
  if (state.live) document.querySelector('#history-results')?.append(el('div', 'card empty', 'Loading history…'));
  drawHistory();
  const info = el('div', 'info-card');
  info.append(icon('lock'), el('span', '', state.live ? 'History stays on the laptop and is deleted after 24 hours. Audio and video are never saved.' : 'Demo data stays in this tab and disappears when it closes. Engine history is deleted after 24 hours.'));
  content.append(info, button('Forget this session', 'outline full', 'forget'));
}

function renderSpeak() {
  content.append(brand());
  heading('Speak for me', state.live ? 'Type a message and the laptop says it aloud.' : 'Type a message and let Attune say it aloud.');
  if (state.live) content.append(pill());
  const area = el('textarea', 'compose');
  area.id = 'speak-text';
  area.maxLength = 280;
  area.placeholder = 'Type what you want to say…';
  area.value = state.draft;
  area.setAttribute('aria-label', 'Message to speak');
  content.append(area);
  if (!state.live) {
    const selects = el('div', 'form-row');
    const voice = el('div', 'select-wrap');
    const voiceSelect = el('select');
    voiceSelect.id = 'voice-select';
    voiceSelect.setAttribute('aria-label', 'Voice');
    const localVoices = getLocalVoices();
    if (localVoices.length) {
      for (const availableVoice of localVoices) {
        const option = el('option', '', availableVoice.name);
        option.value = availableVoice.voiceURI;
        voiceSelect.append(option);
      }
      if (!localVoices.some(item => item.voiceURI === state.voice)) state.voice = localVoices[0].voiceURI;
      voiceSelect.value = state.voice;
    } else {
      voiceSelect.append(el('option', '', 'No offline voice found'));
      voiceSelect.disabled = true;
    }
    voice.append(voiceSelect);
    const tone = el('div', 'select-wrap');
    const toneSelect = el('select');
    toneSelect.id = 'tone-select';
    toneSelect.setAttribute('aria-label', 'Speech style');
    for (const [value, label] of [['natural','Natural'], ['warm','Warm'], ['clear','Clear']]) {
      const option = el('option', '', label); option.value = value; toneSelect.append(option);
    }
    toneSelect.value = state.tone;
    tone.append(toneSelect);
    selects.append(voice, tone);
    content.append(selects);
  }
  const speak = button(state.speaking ? 'Speaking…' : 'Speak aloud', 'primary full', 'speak-now');
  speak.id = 'speak-button';
  speak.prepend(icon('volume'));
  if (state.live) speak.classList.add('speak-live');
  content.append(speak);
  if (state.live) {
    content.append(el('h2', 'section-title', 'Suggested replies'));
    const suggestions = el('div', 'quick-list');
    suggestions.id = 'suggestion-list';
    fillSuggestions(suggestions);
    content.append(suggestions);
  }
  content.append(el('h2', 'section-title', state.live ? 'Presets' : 'Quick replies'));
  const replies = el('div', 'quick-list');
  const presets = state.live ? (state.presets.length ? state.presets : DEFAULT_PRESETS) : ['Can you repeat that?', 'One moment, please.', 'Thank you!'];
  for (const text of presets) {
    const reply = button(text, 'quick-reply', 'quick-reply');
    reply.dataset.source = 'preset';
    replies.append(reply);
  }
  content.append(replies, el('p', 'note', state.live
    ? 'Tap a reply to say it at once. Only the text you send goes to ElevenLabs; if it is slow, the laptop speaks with its offline voice.'
    : 'Spoken replies appear in this preview’s conversation history. They are never saved to disk.'));
}

let suggestionsAt = 0;
function fillSuggestions(list) {
  suggestionsAt = performance.now();
  list.replaceChildren();
  const options = state.suggestions.filter(Boolean);
  if (!options.length) list.append(el('div', 'card empty compact', 'Suggestions appear after a few lines of conversation.'));
  for (const text of options) {
    const reply = button(text, 'quick-reply suggestion', 'quick-reply');
    reply.dataset.source = 'suggestion';
    list.append(reply);
  }
}

function settingToggle(label, sub, key, iconName) {
  const row = button('', 'setting-row', 'toggle-feature');
  row.dataset.feature = key;
  row.setAttribute('role', 'switch');
  row.setAttribute('aria-checked', String(state.features[key]));
  row.setAttribute('aria-label', label);
  const copy = el('div', 'grow');
  copy.append(el('div', 'card-title', label));
  if (sub) copy.append(el('div', 'card-sub', sub));
  row.append(iconBubble(iconName), copy, el('span', `switch ${state.features[key] ? 'on' : ''}`));
  return row;
}

function renderSettings() {
  content.append(brand());
  heading('Glasses', state.live ? 'Controls for the glasses and your privacy' : 'Your preview controls and privacy settings');
  content.append(pill());
  if (state.live) {
    const link = state.hwLink;
    content.append(pressCard({
      title: state.connected ? 'Connected to Attune' : 'Reconnecting to Attune…',
      sub: !state.connected ? 'Commands are sent when the link is back' : state.paused ? 'Recognition is paused' : link?.connected ? `Captions live · rig firmware ${link.firmware || '?'}` : 'Captions live · light-and-buzz rig not connected',
      action: 'pause', dot: true, dotClass: !state.connected ? 'off' : state.paused ? 'paused' : ''
    }));
  } else {
    content.append(pressCard({title: state.powered ? 'Simulator connected' : 'Simulator off', sub: state.powered ? (state.paused ? 'Recognition is paused' : 'Camera and captions preview ready') : 'Camera and captions preview stopped', action: 'toggle-power', dot: state.powered && !state.paused}));
  }
  content.append(el('h2', 'setting-label', 'Appearance'));
  const mode = el('div', 'segmented');
  for (const theme of ['light','dark']) {
    const choice = button(theme === 'light' ? 'Light mode' : 'Dark mode', theme === state.theme ? 'selected' : '', 'theme');
    choice.dataset.theme = theme;
    choice.setAttribute('aria-pressed', String(theme === state.theme));
    mode.append(choice);
  }
  content.append(mode, el('h2', 'setting-label', 'Live features'));
  const features = el('div', 'card setting-group');
  features.append(
    settingToggle('Captions', state.live ? 'On this phone' : '', 'captions', 'wave'),
    settingToggle('Name labels', state.live ? 'On this phone' : '', 'names', 'people'),
    settingToggle('Sound alerts', state.live ? 'Smoke, CO and doorbell, on every screen' : '', 'alerts', 'volume'),
    settingToggle('Translation', state.live ? 'English under Spanish, on every screen' : '', 'translation', 'wave'));
  content.append(features, el('h2', 'setting-label', 'Privacy'));
  const privacy = el('div', 'card setting-group');
  const people = pressCard({title:'Known people', sub:`${state.people.length + state.contacts.filter(item => !item.personId).length} contacts`, iconName:'people', action:'people'});
  const history = pressCard({title:'Conversation history', sub: state.live ? 'On the laptop · deleted after 24 hours' : 'Demo memory only · engine limit 24 hours', iconName:'clock', action:'history'});
  people.className = 'setting-row'; history.className = 'setting-row';
  privacy.append(people, history);
  content.append(privacy);
  const actions = el('div', 'action-stack');
  const pause = button(state.paused ? 'Resume recognition' : 'Pause recognition', 'outline full', 'pause');
  pause.prepend(icon('pause'));
  const on = state.live ? !state.paused : state.powered;
  const power = button(on ? 'Turn off glasses' : 'Turn on glasses', on ? 'danger full' : 'primary full', 'toggle-power');
  power.prepend(icon('power'));
  actions.append(pause, power, button('Forget this session', 'outline full', 'forget'));
  content.append(actions, el('p', 'subtle-center', state.live
    ? 'Turning off pauses all recognition on the laptop. Nothing leaves the laptop except text you type to speak.'
    : 'Demo mode: these controls only change this preview. Open the page from the Attune laptop to go live.'));
}

function renderLive() {
  content.append(back(), brand());
  heading('Live conversation', state.live ? 'Captions from the glasses, newest at the bottom' : 'A preview of captions from the glasses');
  content.append(pill());
  const feed = el('div', 'live-feed');
  feed.id = 'live-feed';
  content.append(feed);
  fillLiveFeed(feed);
}

function fillLiveFeed(feed) {
  feed.replaceChildren();
  if (!state.live) {
    if (!state.powered || state.paused || !state.features.captions) {
      feed.append(el('div', 'card empty', state.paused ? 'Recognition is paused. Resume it in Settings.' : 'Captions are off in this preview.'));
      return;
    }
    for (const [name, line] of [['Maya', 'That sounds like a great idea.'], ['Leo', 'I agree, let’s go with that.']]) {
      const card = el('div', 'card live-caption');
      card.append(el('strong', '', state.features.names ? name : 'Someone'), el('p', '', line));
      feed.append(card);
    }
    feed.append(el('p', 'note', 'Demo captions for layout. No microphone or camera is active.'));
    return;
  }
  if (state.paused) feed.append(el('div', 'card empty', 'Recognition is paused. Resume it in Settings.'));
  if (!state.features.captions) {
    feed.append(el('div', 'card empty', 'Captions are hidden on this phone. Turn them on in Settings.'));
    return;
  }
  if (!state.captions.length && !state.paused) feed.append(el('div', 'card empty', 'Waiting for someone to speak…'));
  for (const c of state.captions.slice(-12)) {
    const name = speakerName(c.speaker);
    const card = el('div', `card live-caption${c.final === false ? ' draft' : ''}${name.startsWith('You') ? ' mine' : ''}`);
    const head = el('div', 'live-head');
    const who = el('strong', `live-name ${colorFor(name)}`, name);
    head.append(who);
    if (c.lang && c.lang !== 'en') head.append(el('span', 'lang-tag', c.lang.toUpperCase()));
    if (c.speaker?.kind === 'offscreen') head.append(el('span', 'side-tag', c.speaker.side === 'left' ? '← off screen' : c.speaker.side === 'right' ? 'off screen →' : 'off screen'));
    head.append(el('span', 'timeline-time', c.time));
    card.append(head, el('p', '', captionText(c)));
    if (state.features.translation && c.translation && c.translation !== c.text) card.append(el('p', 'orig', c.text));
    feed.append(card);
  }
}

function render() {
  content.replaceChildren();
  app.dataset.theme = state.theme;
  app.dataset.link = state.live ? (state.connected ? 'live' : 'offline') : 'demo';
  document.querySelector('meta[name="theme-color"]').content = state.theme === 'dark' ? '#081a21' : '#f8f6f2';
  document.querySelectorAll('.tab').forEach(tab => {
    const active = tab.dataset.nav === state.screen || (state.screen === 'people' || state.screen === 'live') && tab.dataset.nav === 'home';
    tab.classList.toggle('active', active);
    if (active) tab.setAttribute('aria-current', 'page'); else tab.removeAttribute('aria-current');
  });
  ({home:renderHome, people:renderPeople, 'contact-new':renderNewContact, history:renderHistory, speak:renderSpeak, enroll:renderEnroll, settings:renderSettings, live:renderLive})[state.screen]();
  syncEnrollmentFeed();
  renderHint();
  viewport.scrollTop = 0;
}

/** Re-render in place for live updates, keeping scroll position and any text being typed. */
function refresh() {
  const active = document.activeElement;
  if (active && content.contains(active) && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) {
    renderHint();
    return; // never yank a field the user is typing in; the next navigation picks the change up
  }
  const top = viewport.scrollTop;
  if (state.screen === 'speak') state.draft = document.querySelector('#speak-text')?.value ?? state.draft;
  render();
  viewport.scrollTop = top;
}

function renderHint() {
  hint.replaceChildren();
  const dot = el('span', `hint-dot ${state.live ? (state.connected ? '' : 'off') : 'demo'}`);
  const label = state.live ? (state.connected ? 'LIVE · Connected' : 'OFFLINE · Reconnecting…') : 'DEMO · Not connected';
  hint.append(dot, ' ATTUNE PHONE ', el('span', 'hint-sep', '/'), label);
}

let toastTimer;
function showToast(message) {
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 3100);
}

function navigate(screen) {
  if (state.screen === 'speak') state.draft = document.querySelector('#speak-text')?.value ?? state.draft;
  state.screen = screen;
  render();
}

// ------------------------------------------------------------------ speaking
function speak(text = document.querySelector('#speak-text')?.value.trim() || '', source = 'typed') {
  text = text.trim();
  if (!text) return showToast('Type a message first.');
  if (text.length > 280) return showToast('Keep replies under 280 characters.');
  if (state.live) {
    link.send('speak', {text, source});
    state.speaking = text;
    if (source === 'typed') {
      state.draft = '';
      const area = document.querySelector('#speak-text');
      if (area) area.value = '';
    }
    updateSpeakButton();
    showToast(state.connected ? `Sending “${text}”` : 'Offline: it will be spoken when the link is back.');
    clearTimeout(speakTimer);
    speakTimer = setTimeout(() => { state.speaking = ''; updateSpeakButton(); }, 12000);
    return;
  }
  if (!('speechSynthesis' in window)) return showToast('Speech is unavailable in this browser.');
  const voices = getLocalVoices();
  if (!voices.length) return showToast('No offline English voice is available on this computer.');
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.voice = voices.find(voice => voice.voiceURI === state.voice) ?? voices[0];
  utterance.rate = state.tone === 'clear' ? .88 : state.tone === 'warm' ? .94 : 1;
  utterance.pitch = state.tone === 'warm' ? .92 : 1;
  utterance.onstart = () => showToast('Speaking with an offline device voice.');
  utterance.onend = () => {
    state.history.push({speaker:'You',text,time:timeNow(),color:'blue'});
    if (state.screen === 'history') render();
  };
  utterance.onerror = () => showToast('Speech could not be played.');
  window.speechSynthesis.speak(utterance);
  state.draft = text;
}
let speakTimer;

function updateSpeakButton() {
  const btn = document.querySelector('#speak-button');
  if (!btn) return;
  btn.lastChild.textContent = state.speaking ? 'Speaking…' : 'Speak aloud';
  btn.classList.toggle('busy', !!state.speaking);
}

function getLocalVoices() {
  if (!('speechSynthesis' in window)) return [];
  return window.speechSynthesis.getVoices().filter(voice => voice.localService && voice.lang.toLowerCase().startsWith('en'));
}

// ------------------------------------------------------------------ alerts
function showAlert() {
  const a = state.alert;
  alertBanner.replaceChildren();
  alertBanner.classList.toggle('show', !!a);
  if (!a) return;
  const side = a.side === 'left' ? 'on your left' : a.side === 'right' ? 'on your right' : 'nearby';
  const copy = el('div', 'grow');
  copy.append(el('strong', '', ALERT_TEXT[a.kind] || 'Sound alert'), el('span', '', `${side}${a.state === 'watch' ? ' · watching' : ''}`));
  const ack = button('Got it', 'alert-ack', 'alert-ack');
  alertBanner.append(el('span', `alert-icon ${a.kind === 'doorbell' ? 'bell' : ''}`, a.kind === 'doorbell' ? '🔔' : '!'), copy, ack);
}

// ------------------------------------------------------------------ engine link
function goLive() {
  if (state.live) return;
  state.live = true;
  state.people = [];
  state.proposal = '';
  state.history = [];
  state.draft = '';
  state.paused = false;
  state.powered = true;
}

function onMessage(msg) {
  saveSheet.onMessage(msg); // P-29: the "Save this person?" sheet over any screen
  switch (msg.type) {
    case 'welcome':
      state.sessionId = msg.session_id ?? state.sessionId;
      state.paused = !!msg.paused;
      if (Array.isArray(msg.config?.presets)) state.presets = msg.config.presets;
      refresh();
      break;
    case 'paused':
      state.paused = !!msg.paused;
      refresh();
      showToast(state.paused ? 'Recognition paused.' : 'Recognition resumed.');
      break;
    case 'caption': {
      const i = state.captions.findIndex(c => c.utt_id === msg.utt_id);
      const entry = {...msg, time: i >= 0 ? state.captions[i].time : timeNow()};
      if (i >= 0) state.captions[i] = entry; else state.captions.push(entry);
      if (state.captions.length > 80) state.captions.splice(0, state.captions.length - 80);
      if (state.screen === 'live') {
        const feed = document.querySelector('#live-feed');
        if (feed) {
          const atBottom = viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight - 40;
          fillLiveFeed(feed);
          if (atBottom) viewport.scrollTop = viewport.scrollHeight;
        }
      } else if (state.screen === 'home') {
        const preview = document.querySelector('#home-preview');
        if (preview) fillHomePreview(preview);
      }
      break;
    }
    case 'people':
      state.people = (msg.people || msg.list || msg.items || []).map(p => ({id: p.person_id, name: p.name, consent_t: p.consent_t, has_face: p.has_face, has_voice: p.has_voice, color: colorFor(p.name), consent: true}));
      if (['people', 'settings', 'enroll'].includes(state.screen)) refresh();
      break;
    case 'enroll_result': {
      const e = state.enroll;
      if (!['face', 'voice'].includes(e.phase) || (msg.track_id != null && msg.track_id !== e.trackId)) break;
      if (msg.part === 'face') {
        e.face = msg.ok ? 'ok' : 'fail';
        e.reason = msg.reason || 'Try again';
        e.phase = msg.ok ? 'voice' : 'error';
        e.voice = msg.ok ? 'wait' : 'idle';
        e.personId = msg.ok ? msg.person_id : null;
        if (msg.ok && msg.person_id && e.photo) {
          const contact = {id: `person:${msg.person_id}`, personId: msg.person_id, name: e.name.trim(), photo: e.photo, consentT: Date.now() / 1000};
          saveContact(contact).then(() => { state.contacts = [...state.contacts.filter(item => item.id !== contact.id), contact]; if (state.screen === 'people') refresh(); })
            .catch(() => showToast('Recognition was saved, but this device could not save the contact photo.'));
        }
      } else if (msg.part === 'voice' && e.face === 'ok') {
        e.voice = msg.ok ? 'ok' : e.voice === 'skipped' ? 'skipped' : 'fail';
        e.reason = msg.reason || 'Try speaking again';
        e.phase = msg.ok || e.phase === 'done' ? 'done' : 'error';
        if (msg.ok) {
          const person = state.people.find(p => p.id === msg.person_id);
          if (person) person.has_voice = true;
        }
      }
      if (state.screen === 'enroll') refresh();
      if (msg.part === 'voice' && msg.ok) showToast(`${e.name} is saved with face and voice recognition.`);
      break;
    }
    case 'person_changed': {
      const i = state.people.findIndex(p => p.id === msg.person_id);
      if (msg.action === 'deleted' && i >= 0) state.people.splice(i, 1);
      else if (msg.action === 'renamed' && i >= 0) state.people[i].name = msg.name;
      else if (msg.action === 'enrolled' && i < 0) {
        state.people.push({id: msg.person_id, name: msg.name, has_face: true, has_voice: false, color: colorFor(msg.name), consent: true});
        showToast(`${msg.name}'s face is saved. Finish the voice step.`);
      }
      if (msg.action === 'deleted') {
        const id = `person:${msg.person_id}`;
        state.contacts = state.contacts.filter(item => item.id !== id);
        deleteContact(id).catch(() => showToast('Could not remove the local contact photo.'));
      }
      if (['people', 'settings', 'enroll'].includes(state.screen)) refresh();
      break;
    }
    case 'name_proposal':
      if (msg.state === 'proposed') {
        state.liveProposal = {proposal_id: msg.proposal_id, name: msg.name};
        showToast(`Heard a name: ${msg.name}? Confirm or reject it.`);
      } else if (state.liveProposal?.proposal_id === msg.proposal_id) {
        state.liveProposal = null;
        if (msg.state === 'confirmed') showToast(`${msg.name} confirmed for this session.`);
      }
      if (['home', 'people'].includes(state.screen)) refresh();
      break;
    case 'alert':
      if (!state.features.alerts) break;
      if (['start', 'update', 'watch'].includes(msg.state)) state.alert = {alert_id: msg.alert_id, kind: msg.kind, side: msg.side, state: msg.state};
      else if (state.alert?.alert_id === msg.alert_id) state.alert = null;
      showAlert();
      if (msg.state === 'start' && navigator.vibrate) navigator.vibrate([200, 100, 200]);
      break;
    case 'reply_suggestions':
      if (JSON.stringify(msg.options) === JSON.stringify(state.suggestions)) break;
      state.suggestions = Array.isArray(msg.options) ? msg.options : [];
      if (state.screen === 'speak') {
        const list = document.querySelector('#suggestion-list');
        if (list) fillSuggestions(list);
      }
      break;
    case 'reply_spoken':
      if (state.speaking && state.speaking.trim() === String(msg.text || '').trim()) {
        state.speaking = '';
        clearTimeout(speakTimer);
        updateSpeakButton();
        showToast(`Spoken aloud with ${VOICE_NAME[msg.voice] || msg.voice}.`);
      }
      break;
    case 'hw_link':
      state.hwLink = {connected: !!msg.connected, firmware: msg.firmware, driver: msg.driver};
      if (state.screen === 'settings') refresh();
      break;
    default:
      break;
  }
}

const saveSheet = createSaveSheet({host: app, send: (name, args) => link.send(name, args)});
const noopLink = {send() {}, connected: false};
const link = params.has('demo') ? noopLink : connect({
  role: 'phone',
  frames: false,
  onMessage,
  onState(up) {
    state.connected = up;
    if (up) {
      const first = !state.live;
      goLive();
      if (first) render(); else refresh();
      showToast(first ? 'Connected to Attune. Showing live captions.' : 'Reconnected to Attune.');
    } else if (state.live) {
      refresh();
    }
    syncEnrollmentFeed();
  }
});

// ------------------------------------------------------------------ controls
async function confirmAction(message) {
  return window.confirm(message);
}

document.addEventListener('click', async event => {
  const control = event.target.closest('[data-action], [data-nav]');
  if (!control) return;
  const action = control.dataset.action ?? control.dataset.nav;
  if (['home','people','contact-new','history','speak','enroll','settings','live'].includes(action)) return navigate(action);
  if (action === 'select-face') { state.enroll.trackId = Number(control.dataset.trackId); portrait = ''; portraitReady = false; refresh(); return; }
  if (action === 'take-photo') {
    if (state.enroll.photo) {
      state.enroll.photo = '';
      state.enroll.phase = 'ready';
      portrait = ''; portraitReady = false;
      render(); return;
    }
    if (!portraitReady || !portrait) return showToast('Move back until your whole face is visible.');
    state.enroll.trackId = state.enroll.trackId ?? [...state.faces.keys()][0];
    state.enroll.photo = portrait;
    state.enroll.phase = 'captured';
    render(); return;
  }
  if (action === 'start-enroll') {
    const e = state.enroll;
    if (!state.live || !state.connected || !e.photo || !e.name.trim() || !e.consent || !state.faces.has(e.trackId)) return showToast('Keep your face in view, add your name, and tick consent.');
    e.phase = 'face'; e.face = 'wait'; e.voice = 'idle';
    link.send('enroll.start', {track_id: e.trackId, name: e.name.trim(), consent: true, consent_t: Date.now() / 1000});
    render(); return;
  }
  if (action === 'skip-voice') {
    const e = state.enroll;
    if (e.phase !== 'voice' || e.face !== 'ok' || !e.personId) return;
    e.phase = 'done'; e.voice = 'skipped';
    render(); showToast(`${e.name}'s face is saved without voice.`); return;
  }
  if (action === 'enroll-again') { state.enroll = {phase: 'ready', name: '', consent: false, trackId: null, personId: null, photo: '', face: 'idle', voice: 'idle', reason: ''}; portrait = ''; portraitReady = false; render(); return; }
  if (action === 'save-contact') {
    const draft = state.contactDraft;
    if (!draft.photo || !draft.name.trim() || !draft.consent) return showToast('Add a photo, name, and their consent first.');
    const contact = {id: crypto.randomUUID(), personId: null, name: draft.name.trim(), photo: draft.photo, consentT: Date.now() / 1000};
    try {
      await saveContact(contact);
      state.contacts.push(contact);
      state.contactDraft = {name: '', photo: '', consent: false};
      navigate('people'); showToast(`${contact.name} was added to your contacts.`);
    } catch { showToast('This device could not save the contact.'); }
    return;
  }
  if (action === 'theme') { state.theme = control.dataset.theme; render(); return; }
  if (action === 'toggle-feature') {
    const key = control.dataset.feature;
    state.features[key] = !state.features[key];
    if (state.live && (key === 'alerts' || key === 'translation')) link.send('switch.set', {key, value: state.features[key]});
    if (key === 'alerts' && !state.features.alerts) { state.alert = null; showAlert(); }
    refresh();
    return;
  }
  if (action === 'pause') {
    if (state.live) { link.send('pause.toggle'); return; }
    state.paused = !state.paused; render(); showToast(state.paused ? 'Recognition paused in the demo.' : 'Recognition resumed in the demo.'); return;
  }
  if (action === 'toggle-power') {
    if (state.live) {
      if (!state.paused && !(await confirmAction('Turn off the glasses? All recognition on the laptop pauses until you turn them back on.'))) return;
      link.send('pause.toggle');
      return;
    }
    state.powered = !state.powered; if (!state.powered) state.paused = false; render(); showToast(state.powered ? 'Glasses preview on.' : 'Glasses preview off.'); return;
  }
  if (action === 'confirm-proposal' || action === 'reject-proposal') {
    const accept = action === 'confirm-proposal';
    if (state.live) {
      if (!state.liveProposal) return;
      link.send('name.answer', {proposal_id: state.liveProposal.proposal_id, accept});
      if (!accept) showToast('Name proposal dismissed.');
      state.liveProposal = null;
      refresh();
      return;
    }
    state.proposal = ''; render(); showToast(accept ? 'Name confirmed for this session only.' : 'Name proposal dismissed.'); return;
  }
  if (action === 'rename-person') {
    state.editingContactId = control.dataset.personId;
    refresh();
    document.querySelector('#contact-rename')?.focus();
    return;
  }
  if (action === 'cancel-rename') {
    state.editingContactId = null;
    refresh();
    return;
  }
  if (action === 'save-rename') {
    const name = document.querySelector('#contact-rename')?.value.trim();
    if (!name || name.length > 60) return showToast('Enter a name under 60 characters.');
    if (control.dataset.contactOnly) {
      const contact = state.contacts.find(item => item.id === control.dataset.personId);
      if (!contact) return;
      try { await saveContact({...contact, name}); contact.name = name; }
      catch { return showToast('Could not rename this contact.'); }
    } else {
      const person = state.people.find(item => item.id === control.dataset.personId);
      if (!person) return;
      if (state.live) link.send('person.rename', {person_id: person.id, name});
      person.name = name;
      const contact = state.contacts.find(item => item.personId === person.id);
      if (contact) { contact.name = name; saveContact(contact).catch(() => showToast('Could not update the local contact photo.')); }
    }
    state.editingContactId = null;
    refresh();
    showToast('Name updated.');
    return;
  }
  if (action === 'remove-person') {
    if (control.dataset.contactOnly) {
      const contact = state.contacts.find(item => item.id === control.dataset.personId);
      if (!contact) return;
      if (await confirmAction(`Remove ${contact.name} and their photo from this device?`)) {
        try { await deleteContact(contact.id); state.contacts = state.contacts.filter(item => item.id !== contact.id); render(); showToast('Contact removed.'); }
        catch { showToast('Could not remove this contact.'); }
      }
      return;
    }
    const person = state.people.find(item => item.id === control.dataset.personId);
    if (!person) return;
    const message = state.live ? `Delete ${person.name}? Their face print, voice print and every saved file are removed from the laptop.` : `Remove ${person.name} from this preview?`;
    if (await confirmAction(message)) {
      if (state.live) { link.send('person.delete', {person_id: person.id}); showToast(`Deleting ${person.name}…`); return; }
      state.people = state.people.filter(item => item.id !== person.id); render(); showToast('Person removed from preview.');
    }
    return;
  }
  if (action === 'forget') {
    const message = state.live ? 'Forget this session? Strangers, session names and this session’s captions are wiped on the laptop. People who consented stay saved.' : 'Forget this session? The preview conversation and session names will disappear.';
    if (await confirmAction(message)) {
      if (state.live) { link.send('session.forget'); state.captions = []; state.liveProposal = null; }
      state.history = []; state.proposal = ''; window.speechSynthesis?.cancel(); render(); showToast('Session forgotten.');
    }
    return;
  }
  if (action === 'quick-reply') {
    // a suggestion that changed under the finger must not be spoken by accident
    if (state.live && control.dataset.source === 'suggestion' && performance.now() - suggestionsAt < 700) return showToast('Suggestions just changed. Tap again.');
    if (state.live) return speak(control.textContent, control.dataset.source || 'preset');
    state.draft = control.textContent; document.querySelector('#speak-text').value = state.draft; document.querySelector('#speak-text').focus(); return;
  }
  if (action === 'speak-now') speak();
  if (action === 'alert-ack') {
    if (state.alert && state.live) link.send('alert.ack', {alert_id: state.alert.alert_id});
    state.alert = null; showAlert();
  }
});

let searchTimer;
document.addEventListener('input', event => {
  if (event.target.id === 'history-search') {
    clearTimeout(searchTimer);
    const value = event.target.value;
    searchTimer = setTimeout(() => drawHistory(value), state.live ? 300 : 0);
  }
  if (event.target.id === 'speak-text') state.draft = event.target.value;
  if (event.target.id === 'enroll-name') {
    state.enroll.name = event.target.value;
    const start = document.querySelector('#start-enroll');
    if (start) start.disabled = !state.enroll.name.trim() || !state.enroll.consent || !state.connected;
  }
  if (event.target.id === 'contact-name') {
    state.contactDraft.name = event.target.value;
    const save = document.querySelector('#save-contact');
    if (save) save.disabled = !state.contactDraft.photo || !state.contactDraft.name.trim() || !state.contactDraft.consent;
  }
});
document.addEventListener('keydown', event => {
  if (event.target.id === 'speak-text' && event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    speak();
  }
});
document.addEventListener('change', async event => {
  if (event.target.id === 'enroll-consent') {
    state.enroll.consent = event.target.checked;
    const start = document.querySelector('#start-enroll');
    if (start) start.disabled = !state.enroll.name.trim() || !state.enroll.consent || !state.connected;
  }
  if (event.target.id === 'contact-consent') {
    state.contactDraft.consent = event.target.checked;
    const save = document.querySelector('#save-contact');
    if (save) save.disabled = !state.contactDraft.photo || !state.contactDraft.name.trim() || !state.contactDraft.consent;
  }
  if (event.target.id === 'contact-file') {
    try { state.contactDraft.photo = await photoFromFile(event.target.files?.[0]); render(); }
    catch (error) { showToast(error.message || 'Could not read that photo.'); }
  }
  if (event.target.id === 'voice-select') state.voice = event.target.value;
  if (event.target.id === 'tone-select') state.tone = event.target.value;
});
if ('speechSynthesis' in window) {
  window.speechSynthesis.addEventListener('voiceschanged', () => {
    if (state.screen === 'speak' && !state.live) render();
  });
}

render();
listContacts().then(contacts => {
  state.contacts = contacts;
  if (['people', 'settings'].includes(state.screen)) refresh();
}).catch(() => showToast('Contacts are unavailable in this browser.'));
