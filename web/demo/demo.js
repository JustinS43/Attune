/*
 * Demo page: the glasses view (left) and the phone app (right) running at the same time,
 * or either one full size.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-17, P-18 (glasses guide).
 *
 *   /demo/                       glasses + phone
 *   /demo/?view=lens|phone       start with one of them full size
 *   /demo/?view=guide            the glasses guide (the frames stay loaded, just hidden)
 *   /demo/?source=film&mode=mono passed on to the glasses view (source, mode, engine)
 *   /demo/?palette=apricot       passed on to the phone app
 *
 * Keys work wherever the focus is (the two pages are same-origin frames):
 *   `  next view      Alt+1 both · Alt+2 glasses · Alt+3 phone · Alt+4 glasses guide
 */

import { mountGuide } from './guide.js';

const VIEWS = ['split', 'lens', 'phone', 'guide'];
const STORE = 'attune.demo.view';
const params = new URLSearchParams(location.search);

const lens = document.querySelector('#lens');
const phone = document.querySelector('#phone');
const stage = document.querySelector('.stage');
const buttons = [...document.querySelectorAll('.views button')];
const guide = document.querySelector('#guide');

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

function setView(next) {
  if (!VIEWS.includes(next)) return;
  view = next;
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
  stage.inert = view === 'guide';
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
  if (e.altKey && !e.ctrlKey && !e.metaKey && /^Digit[1234]$/.test(e.code)) {
    e.preventDefault();
    e.stopImmediatePropagation();
    setView(VIEWS[Number(e.code.slice(-1)) - 1]);
    return;
  }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === '`') {
    e.preventDefault();
    e.stopImmediatePropagation();
    setView(VIEWS[(VIEWS.indexOf(view) + 1) % VIEWS.length]);
  }
}

window.addEventListener('keydown', onKeydown, true);
for (const frame of [lens, phone]) {
  frame.addEventListener('load', () => {
    try {
      frame.contentWindow.addEventListener('keydown', onKeydown, true);
    } catch {
      /* a frame from another origin (e.g. ?engine=) keeps its own keys */
    }
  });
}

// when the glasses view is full size, give it the keyboard so M, V, H... work straight away
function focusFor() {
  if (view === 'lens') lens.focus();
  else if (view === 'phone') phone.focus();
  else if (view === 'guide') guide.focus({ preventScroll: true });
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

setView(view);
