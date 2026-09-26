/*
 * Demo page: the glasses view (left) and the phone app (right) running at the same time,
 * or either one full size.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-17, P-18 (glasses guide), P-20 (glasses POV).
 *
 *   /demo/                       glasses + phone
 *   /demo/?view=lens|phone       start with one of them full size
 *   /demo/?view=guide            the glasses guide (the frames stay loaded, just hidden)
 *   /demo/?view=pov              Glasses POV: as if you wore the glasses, full window (pov.js)
 *   /demo/?source=film&mode=mono passed on to the glasses view (source, mode, engine)
 *   /demo/?palette=apricot       passed on to the phone app
 *
 * Keys work wherever the focus is (the two pages are same-origin frames):
 *   `  next view      Alt+1 both · Alt+2 glasses · Alt+3 phone · Alt+4 glasses guide · Alt+5 POV
 *   Z  (Glasses POV) closer / everything     Esc (Glasses POV) back to the previous view
 */

import { mountGuide } from './guide.js';
import { mountPov } from './pov.js';
import { connect } from '../shared/ws.js';

const VIEWS = ['split', 'lens', 'phone', 'guide', 'pov'];
const STORE = 'attune.demo.view';
const params = new URLSearchParams(location.search);

const lens = document.querySelector('#lens');
const phone = document.querySelector('#phone');
const stage = document.querySelector('.stage');
const buttons = [...document.querySelectorAll('.views button')];
const guide = document.querySelector('#guide');
const povRoot = document.querySelector('#pov');
const lensPane = document.querySelector('.lens-pane');
const phonePane = document.querySelector('.phone-pane');

// ------------------------------------------------------------------ frames
function pass(keys) {
  const out = new URLSearchParams();
  for (const k of keys) if (params.has(k)) out.set(k, params.get(k));
  const s = out.toString();
  return s ? `?${s}` : '';
}
lens.src = `../lens/${pass(['source', 'mode', 'engine', 'assets'])}`;
phone.src = `../phone/${pass(['engine', 'palette', 'demo'])}`;

// ------------------------------------------------------------------ views
function readStored() {
  try {
    return localStorage.getItem(STORE);
  } catch {
    return null;
  }
}

let view = VIEWS.includes(params.get('view')) ? params.get('view') : readStored();
if (!VIEWS.includes(view)) view = 'split';
let beforePov = 'lens'; // where Esc goes from the Glasses POV

function setView(next) {
  if (!VIEWS.includes(next)) return;
  if (next === 'pov' && view !== 'pov') beforePov = view;
  view = next;
  document.body.classList.remove('bar-peek');
  document.body.dataset.view = view;
  for (const b of buttons) b.setAttribute('aria-checked', String(b.dataset.view === view));
  try {
    localStorage.setItem(STORE, view);
  } catch {
    /* private window: the view just isn't remembered */
  }
  const p = new URLSearchParams(location.search);
  p.set('view', view);
  history.replaceState(null, '', `${location.pathname}?${p}`);
  // hidden views leave the tab order and the accessibility tree (the frames stay loaded)
  guide.inert = view !== 'guide';
  povRoot.inert = view !== 'pov';
  stage.inert = view === 'guide' || view === 'pov';
  // a pane faded out by the view (opacity 0) must not keep the keyboard focus either
  lensPane.inert = view === 'phone';
  phonePane.inert = view === 'lens';
  pov.setVisible(view === 'pov');
  fit();
}

for (const b of buttons) b.addEventListener('click', () => setView(b.dataset.view));

// ------------------------------------------------------------------ phone size
// The phone page is laid out at a real phone's CSS size and scaled as a whole, so it looks and
// behaves exactly like it does on a phone.
const PHONE_W = 390 + 22;
const PHONE_H = 844 + 22;
const CAPTION_H = 34;

function fit() {
  const stageBox = stage.getBoundingClientRect();
  const pad = 36; // stage padding, top + bottom
  const availH = stageBox.height - pad - CAPTION_H;
  let scale;
  if (view === 'phone') {
    scale = Math.min(availH / PHONE_H, (stageBox.width - pad) / PHONE_W, 1.2);
  } else {
    // side by side: the phone takes at most ~30% of the width, the glasses get the rest
    scale = Math.min(availH / PHONE_H, (stageBox.width * 0.3) / PHONE_W, 1);
  }
  scale = Math.max(scale, 0.35);
  document.documentElement.style.setProperty('--phone-scale', scale.toFixed(4));
  document.documentElement.style.setProperty('--phone-pane-w', `${Math.ceil(PHONE_W * scale + 24)}px`);
}

new ResizeObserver(fit).observe(stage);

// ------------------------------------------------------------------ keys
function onKeydown(e) {
  const t = e.target;
  const typing = t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
  if (e.altKey && !e.ctrlKey && !e.metaKey && /^Digit[1-5]$/.test(e.code)) {
    e.preventDefault();
    e.stopImmediatePropagation();
    const next = VIEWS[Number(e.code.slice(-1)) - 1];
    if (next === view && view === 'pov') peekBar(); // already there: show the bar for a moment
    setView(next);
    return;
  }
  if (e.altKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyC') {
    e.preventDefault();
    e.stopImmediatePropagation();
    if (!camBtn.disabled) setCamera(!cameraOn);
    return;
  }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (view === 'pov' && e.key === 'Escape') {
    e.preventDefault();
    e.stopImmediatePropagation();
    setView(beforePov === 'pov' ? 'lens' : beforePov);
    setTimeout(focusFor, 50);
    return;
  }
  if (view === 'pov' && e.code === 'KeyZ') {
    e.preventDefault();
    e.stopImmediatePropagation();
    pov.toggleZoom();
    return;
  }
  if (e.key === '`') {
    e.preventDefault();
    e.stopImmediatePropagation();
    setView(VIEWS[(VIEWS.indexOf(view) + 1) % VIEWS.length]);
  }
}

window.addEventListener('keydown', onKeydown, true);
function wireKeys(frame) {
  try {
    frame.contentWindow.addEventListener('keydown', onKeydown, true);
  } catch {
    /* a frame from another origin (e.g. ?engine=) keeps its own keys */
  }
}
for (const frame of [lens, phone]) frame.addEventListener('load', () => wireKeys(frame));

// when the glasses view is full size, give it the keyboard so M, V, H... work straight away
function focusFor() {
  if (view === 'lens') lens.focus();
  else if (view === 'phone') phone.focus();
  else if (view === 'guide') guide.focus({ preventScroll: true });
  else if (view === 'pov') pov.focus();
}
for (const b of buttons) b.addEventListener('click', () => setTimeout(focusFor, 50));

// ------------------------------------------------------------------ glasses guide
const MODE_URL = { color: 'color', mono: 'mono', corner: 'mono-corner' };

// Put the glasses view into a look: through attuneLens when the frame's script is reachable,
// otherwise by reloading the frame with ?mode= (and ?variant= for the Google Glass placement).
function tryLook(mode, variant = 'rayban') {
  let done = false;
  try {
    const api = lens.contentWindow?.attuneLens;
    if (api?.setMode) {
      const corner = api.modes?.corner;
      if (mode === 'corner' && corner && corner.variant !== variant) {
        corner.variant = variant;
        // setMode ignores the current mode; step out and back so the label and URL catch up
        if (lens.contentWindow.location.search.includes('mode=mono-corner')) api.setMode('color');
      }
      api.setMode(mode);
      done = true;
    }
  } catch {
    /* another origin: fall back to reloading the frame */
  }
  if (!done) {
    let q;
    try {
      q = new URLSearchParams(lens.contentWindow.location.search);
    } catch {
      q = new URLSearchParams(pass(['source', 'engine', 'assets']));
    }
    q.set('mode', MODE_URL[mode]);
    if (mode === 'corner' && variant === 'glass') q.set('variant', 'glass');
    else q.delete('variant');
    lens.src = `../lens/?${q}`;
  }
  setView('lens');
  setTimeout(focusFor, 50);
}

mountGuide(guide, { onTry: tryLook });

// ------------------------------------------------------------------ glasses POV
// Its own lens (?chrome=0), loaded on first open, kept in step with the main one, unloaded 30 s
// after leaving the view.
const pov = mountPov(povRoot, {
  mainLens: lens,
  params,
  onFrameLoad: wireKeys,
  // the mouse has gone well below the bar: let it slide away again
  onPointer: (y) => {
    if (document.body.classList.contains('bar-peek') && y > bar.offsetHeight + 24) hideBarSoon();
  },
});

// The bar is hidden in the POV (pov.css). It comes back when the mouse reaches the top edge and
// goes again once the mouse has left it for a moment.
const bar = document.querySelector('.bar');
let peekTimer = 0;
function peekBar() {
  clearTimeout(peekTimer);
  document.body.classList.add('bar-peek');
  peekTimer = setTimeout(hideBarSoon, 2500);
}
function hideBarSoon() {
  if (bar.matches(':hover')) return; // mouseleave hides it later
  clearTimeout(peekTimer);
  peekTimer = setTimeout(() => document.body.classList.remove('bar-peek'), 500);
}
povRoot.querySelector('.pov-peek').addEventListener('mouseenter', peekBar);
bar.addEventListener('mouseenter', () => clearTimeout(peekTimer));
bar.addEventListener('mouseleave', () => {
  if (view === 'pov') hideBarSoon();
});

// scripted demos and tests: attuneDemo.setView('pov'), attuneDemo.pov.check()
window.attuneDemo = { setView: (v) => setView(v), pov };

setView(view);

// ------------------------------------------------------------------ camera on / off
// The engine really stops using the webcam (command camera.set); captions and sound alerts keep
// running from the microphone. Every page hears the new state (message `camera`).
const camBtn = document.querySelector('#cam-toggle');
const camLabel = camBtn.querySelector('.cam-label');
const camCards = [...document.querySelectorAll('.cam-off-card')]; // the Glasses view's and the POV's
let cameraOn = true;

function showCamera(on) {
  cameraOn = on;
  camBtn.setAttribute('aria-pressed', String(on));
  camLabel.textContent = on ? 'Camera on' : 'Camera off';
  camBtn.title = `${on ? 'Turn the camera off' : 'Turn the camera on'} (Alt+C)`;
  for (const c of camCards) c.hidden = on;
}

const link = connect({
  role: 'console',
  onState: (up) => {
    camBtn.disabled = !up;
  },
  onMessage: (msg) => {
    if (msg.type === 'welcome' && typeof msg.camera_on === 'boolean') showCamera(msg.camera_on);
    else if (msg.type === 'camera') showCamera(Boolean(msg.on));
  },
});

function setCamera(on) {
  showCamera(on); // shown at once; the engine confirms with a `camera` message
  link.send('camera.set', { on });
}

camBtn.addEventListener('click', () => setCamera(!cameraOn));
for (const b of document.querySelectorAll('.cam-on-btn')) b.addEventListener('click', () => setCamera(true));
