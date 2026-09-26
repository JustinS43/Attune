/* Attune phone preview. All demo content stays in memory in this page. */
const app = document.querySelector('#app');
const content = document.querySelector('#screen-content');
const viewport = document.querySelector('.app-viewport');
const toast = document.querySelector('#toast');
const state = {
  screen: 'home', theme: 'light', paused: false, powered: true,
  features: { captions: true, names: true, alerts: true },
  people: [
    { id: 'maya', name: 'Maya Chen', seen: 12, color: '', consent: true },
    { id: 'leo', name: 'Leo Martin', seen: 8, color: 'blue', consent: true }
  ],
  proposal: 'Sam',
  history: [
    { speaker: 'Maya', text: 'We can meet by the entrance.', time: '2:14 PM', color: '' },
    { speaker: 'Leo', text: 'I can bring the notes.', time: '2:15 PM', color: 'violet' },
    { speaker: 'You', text: 'Sounds good, thank you.', time: '2:15 PM', color: 'blue' }
  ],
  draft: 'Nice to meet you, Sam!',
  voice: '', tone: 'natural'
};

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

function pill() { return el('div', 'demo-pill', 'INTERACTIVE PREVIEW'); }

function button(label, className, action) {
  const node = el('button', className, label);
  node.type = 'button';
  node.dataset.action = action;
  return node;
}

function pressCard({title, sub, iconName, action, dot = false}) {
  const node = button('', 'press-card', action);
  node.textContent = '';
  node.append(dot ? el('span', 'connected-dot') : iconBubble(iconName));
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
  return el('span', `avatar ${color}`, name.slice(0, 1).toUpperCase());
}

function previewLine(name, line, color) {
  const row = el('div', 'preview-line');
  const copy = el('div', 'grow');
  copy.append(el('strong', '', name), el('span', '', line));
  row.append(avatar(name, color), copy);
  return row;
}

function renderHome() {
  content.append(brand(), pill(), el('h1', 'hero-title', 'Good afternoon'));
  content.append(pressCard({title: state.powered ? 'Glasses preview ready' : 'Glasses preview off', sub: state.powered ? (state.paused ? 'Recognition paused in this demo' : 'Simulated captions are live') : 'Turn on in Settings to resume', action: 'settings', dot: state.powered && !state.paused}));
  const feature = el('section', 'feature-card');
  const head = el('div', 'feature-head');
  head.append(icon('wave'), el('span', '', 'Live conversation'));
  const lines = el('div', 'conversation-preview');
  if (state.powered && !state.paused && state.features.captions) {
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

function back(to = 'home') {
  const node = button('Back', 'back', to);
  node.prepend(icon('back'));
  return node;
}

function renderPeople() {
  content.append(back(), brand());
  heading('People', 'Familiar voices, easier to follow');
  content.append(el('h2', 'section-title', 'Frequently seen'));
  const list = el('div', 'people-list');
  for (const person of state.people) {
    const card = el('div', 'card person-card');
    const top = el('div', 'person-top');
    const copy = el('div', 'grow');
    copy.append(el('div', 'card-title', person.name), el('div', 'card-sub', `Seen ${person.seen} times · Saved with consent`));
    top.append(avatar(person.name, person.color), copy);
    const actions = el('div', 'person-actions');
    const rename = button('Rename', 'outline', 'rename-person');
    const remove = button('Remove', 'outline', 'remove-person');
    rename.dataset.personId = person.id;
    remove.dataset.personId = person.id;
    actions.append(rename, remove);
    card.append(top, actions);
    list.append(card);
  }
  content.append(list);
  content.append(el('h2', 'section-title', 'This session'));
  if (state.proposal) {
    const card = el('div', 'card person-card');
    const top = el('div', 'person-top');
    const copy = el('div', 'grow');
    copy.append(el('div', 'card-title', `${state.proposal}?`), el('div', 'card-sub', 'Name heard in conversation · session only'));
    top.append(avatar(state.proposal, 'amber'), copy);
    const actions = el('div', 'person-actions');
    actions.append(button('Confirm name', 'primary', 'confirm-proposal'), button(`Not ${state.proposal}`, 'outline', 'reject-proposal'));
    card.append(top, actions);
    content.append(card);
  } else content.append(el('div', 'card empty', 'No unconfirmed session names.'));
  content.append(el('p', 'note', 'Session names disappear when you forget this session. Saving a person requires their consent through enrollment.'));
}

function drawHistory(query = '') {
  const target = document.querySelector('#history-results');
  target.replaceChildren();
  const rows = state.history.filter(item => `${item.speaker} ${item.text}`.toLowerCase().includes(query.trim().toLowerCase()));
  if (!rows.length) {
    target.append(el('div', 'card empty', query ? 'No conversations match your search.' : 'No conversation in this session.'));
    return;
  }
  const timeline = el('div', 'card timeline');
  const header = el('div', 'timeline-head');
  header.append(el('span', '', 'Today'), el('small', '', '· Local preview'));
  timeline.append(header);
  rows.forEach((item, index) => {
    const row = el('div', 'timeline-row');
    const rail = el('div', 'timeline-rail');
    rail.append(el('span', `timeline-dot ${item.color}`));
    if (index < rows.length - 1) rail.append(el('span', 'timeline-stem'));
    const text = el('div');
    const name = el('span', `timeline-name ${item.color}`, item.speaker);
    text.append(name, el('span', 'timeline-time', item.time), el('p', 'timeline-text', item.text));
    row.append(rail, text);
    timeline.append(row);
  });
  target.append(timeline);
}

function renderHistory() {
  content.append(brand());
  heading('Conversations', 'Catch up on what you missed');
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
  drawHistory();
  const info = el('div', 'info-card');
  info.append(icon('lock'), el('span', '', 'Preview data stays in this tab and disappears when it closes. Engine history is specified to delete after 24 hours.'));
  content.append(info, button('Forget this session', 'outline full', 'forget'));
}

function renderSpeak() {
  content.append(brand());
  heading('Speak for me', 'Type a message and let Attune say it aloud.');
  const area = el('textarea', 'compose');
  area.id = 'speak-text';
  area.maxLength = 280;
  area.placeholder = 'Type what you want to say…';
  area.value = state.draft;
  area.setAttribute('aria-label', 'Message to speak');
  content.append(area);
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
  const speak = button('Speak aloud', 'primary full', 'speak-now');
  speak.prepend(icon('volume'));
  content.append(selects, speak);
  content.append(el('h2', 'section-title', 'Quick replies'));
  const replies = el('div', 'quick-list');
  for (const text of ['Can you repeat that?', 'One moment, please.', 'Thank you!']) {
    const reply = button(text, 'quick-reply', 'quick-reply');
    replies.append(reply);
  }
  content.append(replies, el('p', 'note', 'Spoken replies appear in this preview’s conversation history. They are never saved to disk.'));
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
  heading('Glasses', 'Your preview controls and privacy settings');
  content.append(pressCard({title: state.powered ? 'Simulator connected' : 'Simulator off', sub: state.powered ? (state.paused ? 'Recognition is paused' : 'Camera and captions preview ready') : 'Camera and captions preview stopped', action: 'toggle-power', dot: state.powered && !state.paused}));
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
  features.append(settingToggle('Captions', '', 'captions', 'wave'), settingToggle('Name labels', '', 'names', 'people'), settingToggle('Sound alerts', '', 'alerts', 'volume'));
  content.append(features, el('h2', 'setting-label', 'Privacy'));
  const privacy = el('div', 'card setting-group');
  const people = pressCard({title:'Known people', sub:`${state.people.length} saved with consent`, iconName:'people', action:'people'});
  const history = pressCard({title:'Conversation history', sub:'Preview memory only · engine limit 24 hours', iconName:'clock', action:'history'});
  people.className = 'setting-row'; history.className = 'setting-row';
  privacy.append(people, history);
  content.append(privacy);
  const actions = el('div', 'action-stack');
  const pause = button(state.paused ? 'Resume recognition' : 'Pause recognition', 'outline full', 'pause');
  pause.prepend(icon('pause'));
  const power = button(state.powered ? 'Turn off glasses preview' : 'Turn on glasses preview', state.powered ? 'danger full' : 'primary full', 'toggle-power');
  power.prepend(icon('power'));
  actions.append(pause, power);
  content.append(actions, el('p', 'subtle-center', 'This screen controls the simulator only. No device is connected.'));
}

function renderLive() {
  content.append(back(), brand());
  heading('Live conversation', 'A preview of captions from the glasses');
  content.append(pill());
  if (!state.powered || state.paused || !state.features.captions) {
    content.append(el('div', 'card empty', state.paused ? 'Recognition is paused. Resume it in Settings.' : 'Captions are off in this preview.'));
    return;
  }
  for (const [name, line] of [['Maya', 'That sounds like a great idea.'], ['Leo', 'I agree, let’s go with that.']]) {
    const card = el('div', 'card live-caption');
    card.append(el('strong', '', state.features.names ? name : 'Someone'), el('p', '', line));
    content.append(card);
  }
  content.append(el('p', 'note', 'Sample captions shown for layout testing. No microphone or camera is active.'));
}

function render() {
  content.replaceChildren();
  app.dataset.theme = state.theme;
  document.querySelector('meta[name="theme-color"]').content = state.theme === 'dark' ? '#081a21' : '#f8f6f2';
  document.querySelectorAll('.tab').forEach(tab => {
    const active = tab.dataset.nav === state.screen || (state.screen === 'people' || state.screen === 'live') && tab.dataset.nav === 'home';
    tab.classList.toggle('active', active);
    if (active) tab.setAttribute('aria-current', 'page'); else tab.removeAttribute('aria-current');
  });
  ({home:renderHome, people:renderPeople, history:renderHistory, speak:renderSpeak, settings:renderSettings, live:renderLive})[state.screen]();
  viewport.scrollTop = 0;
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

function speak() {
  const text = document.querySelector('#speak-text').value.trim();
  if (!text) return showToast('Type a message first.');
  if (text.length > 280) return showToast('Keep replies under 280 characters.');
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
    state.history.push({speaker:'You',text,time:new Intl.DateTimeFormat('en-US',{hour:'numeric',minute:'2-digit'}).format(new Date()),color:'blue'});
    if (state.screen === 'history') render();
  };
  utterance.onerror = () => showToast('Speech could not be played.');
  window.speechSynthesis.speak(utterance);
  state.draft = text;
}

function getLocalVoices() {
  if (!('speechSynthesis' in window)) return [];
  return window.speechSynthesis.getVoices().filter(voice => voice.localService && voice.lang.toLowerCase().startsWith('en'));
}

document.addEventListener('click', event => {
  const control = event.target.closest('[data-action], [data-nav]');
  if (!control) return;
  const action = control.dataset.action ?? control.dataset.nav;
  if (['home','people','history','speak','settings','live'].includes(action)) return navigate(action);
  if (action === 'theme') { state.theme = control.dataset.theme; render(); return; }
  if (action === 'toggle-feature') { const key = control.dataset.feature; state.features[key] = !state.features[key]; render(); return; }
  if (action === 'pause') { state.paused = !state.paused; render(); showToast(state.paused ? 'Recognition paused in preview.' : 'Recognition resumed in preview.'); return; }
  if (action === 'toggle-power') { state.powered = !state.powered; if (!state.powered) state.paused = false; render(); showToast(state.powered ? 'Glasses preview on.' : 'Glasses preview off.'); return; }
  if (action === 'confirm-proposal') { state.proposal = ''; render(); showToast('Name confirmed for this session only.'); return; }
  if (action === 'reject-proposal') { state.proposal = ''; render(); showToast('Name proposal dismissed.'); return; }
  if (action === 'rename-person') {
    const person = state.people.find(item => item.id === control.dataset.personId);
    if (!person) return;
    const name = window.prompt('Rename saved person', person.name)?.trim();
    if (name && name.length <= 60) { person.name = name; render(); }
    return;
  }
  if (action === 'remove-person') {
    const person = state.people.find(item => item.id === control.dataset.personId);
    if (person && window.confirm(`Remove ${person.name} from this preview?`)) {
      state.people = state.people.filter(item => item.id !== person.id); render(); showToast('Person removed from preview.');
    }
    return;
  }
  if (action === 'forget') {
    if (window.confirm('Forget this session? The preview conversation and session names will disappear.')) {
      state.history = []; state.proposal = ''; window.speechSynthesis?.cancel(); render(); showToast('Session forgotten.');
    }
    return;
  }
  if (action === 'quick-reply') { state.draft = control.textContent; document.querySelector('#speak-text').value = state.draft; document.querySelector('#speak-text').focus(); return; }
  if (action === 'speak-now') speak();
});

document.addEventListener('input', event => {
  if (event.target.id === 'history-search') drawHistory(event.target.value);
  if (event.target.id === 'speak-text') state.draft = event.target.value;
});
document.addEventListener('change', event => {
  if (event.target.id === 'voice-select') state.voice = event.target.value;
  if (event.target.id === 'tone-select') state.tone = event.target.value;
});
if ('speechSynthesis' in window) {
  window.speechSynthesis.addEventListener('voiceschanged', () => {
    if (state.screen === 'speak') render();
  });
}

render();
