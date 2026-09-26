/*
 * Lens view controller: the stage, the two sources, the three glasses modes, keys and chrome.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-06 (with P-07 bubbles.js, P-08 alerts.js, P-12 keys).
 *
 * Sources (V, or ?source=live|film):
 *   Live - the engine over web/shared/ws.js (role lens, with frames).
 *   Film - the launch film's footage + script, replayed as the same contract messages.
 * Glasses modes (M, or ?mode=color|mono|mono-corner): each redraws the same view model within
 * that display's limits. Both sources feed one store, so every mode renders both the same way.
 */

import { onKey, listKeys } from '../shared/keys.js';
import { createStore, createViewBuilder } from './store.js';
import { W, setPixelScale, nowS, addSkew, setManualClock } from './hud.js';
import { createColorMode } from './modes/color.js';
import { createMonoMode, MONO_LEVELS, MONO_DEFAULT_LEVEL } from './modes/mono.js';
import { createCornerMode } from './modes/corner.js';
import { createLiveSource } from './live.js';
import { createFilmSource } from './film/film.js';

const params = new URLSearchParams(location.search);
const MODE_ALIASES = {
  color: 'color', colour: 'color', full: 'color', ar: 'color',
  mono: 'mono', green: 'mono', waveguide: 'mono',
  'mono-corner': 'corner', corner: 'corner', monocular: 'corner',
};
const MODE_ORDER = ['color', 'mono', 'corner'];
const MODE_URL = { color: 'color', mono: 'mono', corner: 'mono-corner' };
const ASSETS = params.get('assets') || '/data/reels/film/';

// ---------------------------------------------------------------- model
const store = createStore();
let build = createViewBuilder(store);
const listeners = new Set();
const srCaptions = document.getElementById('captions-live');
let lastSr = '';

function emit(msg) {
  if (sourceKind === 'live') store.state.clock = nowS();
  store.apply(msg);
  if (msg?.type === 'caption' && msg.final) {
    const text = `${msg.speaker?.label ?? 'Someone'}: ${msg.translation || msg.text}`;
    if (text !== lastSr) srCaptions.textContent = lastSr = text;
  }
  for (const fn of listeners) {
    try {
      fn(msg);
    } catch (err) {
      console.warn('[lens] listener failed', err);
    }
  }
}

// ---------------------------------------------------------------- DOM
const $ = (id) => document.getElementById(id);
const stage = $('stage');
const media = $('media');
const liveCanvas = $('live-frame');
const notice = { el: $('notice'), title: $('notice-title'), detail: $('notice-detail'), hint: $('notice-hint'), key: '' };
const chrome = {
  name: $('mode-name'), device: $('mode-device'), swatch: $('mode-swatch'),
  transport: $('transport'), play: $('play'), scene: $('scene-name'), progress: $('scene-progress'), count: $('scene-count'),
};
const help = $('help');

const modes = { color: createColorMode(), mono: createMonoMode(), corner: createCornerMode() };
const layers = [...document.querySelectorAll('canvas.hud')].map((canvas) => ({
  id: canvas.dataset.mode,
  canvas,
  ctx: canvas.getContext('2d'),
  until: 0,
  dirty: false,
}));

let modeId = MODE_ALIASES[params.get('mode')] ?? 'color';
let sourceKind = params.get('source') === 'live' || params.get('source') === 'film'
  ? params.get('source')
  : location.pathname.includes('/web/lens') ? 'film' : 'live';
let chromeHidden = params.get('chrome') === '0';
let film = null;
let filmState = 'idle'; // idle | loading | ready | error
let filmError = '';
let live = null;
let source = null;
let startT = Number.parseFloat(params.get('t'));
// G1 display height level (0-8, keys [ and ]) and the monocular placement (G: Ray-Ban Display / Glass)
const heightParam = Number.parseInt(params.get('height'), 10);
let monoLevel = Number.isFinite(heightParam) ? Math.max(0, Math.min(MONO_LEVELS - 1, heightParam)) : MONO_DEFAULT_LEVEL;
modes.corner.variant = params.get('variant') === 'glass' ? 'glass' : 'rayban';

// ---------------------------------------------------------------- stage sizing (16:9 letterbox, DPR aware)
let scale = 1;
function layout() {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const sw = Math.min(vw, (vh * 16) / 9);
  const sh = (sw * 9) / 16;
  Object.assign(stage.style, { left: `${(vw - sw) / 2}px`, top: `${(vh - sh) / 2}px`, width: `${sw}px`, height: `${sh}px` });
  // the demo chrome scales with the window (1.3 at 1920x1080, down to 0.55 in a small window or
  // the side-by-side demo's iframe); lens.css applies it with zoom, so fixed px scale too
  const ui = Math.min(1.3, Math.max(0.55, 1.3 * Math.min(vw / 1920, vh / 1080)));
  $('app').style.setProperty('--ui', ui.toFixed(3));
  const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
  const cw = Math.round(sw * dpr);
  const ch = Math.round(sh * dpr);
  for (const l of layers) {
    if (l.canvas.width !== cw || l.canvas.height !== ch) {
      l.canvas.width = cw;
      l.canvas.height = ch;
    }
  }
  scale = cw / W;
  setPixelScale(scale);
}
window.addEventListener('resize', layout);
let dprQuery = null;
function watchDpr() {
  dprQuery?.removeEventListener('change', onDpr);
  dprQuery = matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
  dprQuery.addEventListener('change', onDpr);
}
function onDpr() {
  layout();
  watchDpr();
}
layout();
watchDpr();

// ---------------------------------------------------------------- blurred copy of the world for the glass panels
const blurCanvas = document.createElement('canvas');
blurCanvas.width = 384;
blurCanvas.height = 216;
const bctx = blurCanvas.getContext('2d', { alpha: false });
let blurReady = false;
function updateBlur(src) {
  if (!src) {
    blurReady = false;
    return;
  }
  bctx.filter = 'blur(6px) saturate(1.2)';
  bctx.drawImage(src, -10, -10, blurCanvas.width + 20, blurCanvas.height + 20);
  bctx.filter = 'none';
  blurReady = true;
}

// ---------------------------------------------------------------- sources
function setConnected(up) {
  store.state.connected = up;
  if (!up) store.reset();
}

async function loadFilm() {
  if (film || filmState === 'loading') return film;
  filmState = 'loading';
  try {
    film = await createFilmSource({
      assets: ASSETS,
      layer: media,
      emit,
      reset: () => store.reset(),
      setClock: (t) => {
        store.state.clock = t;
      },
    });
    filmState = 'ready';
  } catch (err) {
    filmState = 'error';
    filmError = String(err?.message ?? err);
  }
  return film;
}

async function setSource(kind) {
  sourceKind = kind;
  source?.stop();
  source = null;
  store.reset({ keepPeople: false });
  store.state.paused = false;
  store.state.connected = false;
  if (kind === 'live') {
    loadPanels();
    live ??= createLiveSource({ canvas: liveCanvas, emit, setConnected });
    source = live;
    live.start();
  } else {
    await loadFilm();
    if (sourceKind !== 'film' || !film) return;
    source = film;
    film.start(Number.isFinite(startT) ? startT : undefined);
    startT = NaN;
  }
  syncUrl();
  syncChrome();
}

// ---------------------------------------------------------------- modes and chrome
function setMode(id) {
  if (!modes[id] || id === modeId) return;
  const prev = layers.find((l) => l.id === modeId);
  if (prev) prev.until = nowS() * 1000 + 450;
  modeId = id;
  syncUrl();
  syncChrome();
}

function syncUrl() {
  const p = new URLSearchParams(location.search);
  p.set('source', sourceKind);
  p.set('mode', MODE_URL[modeId]);
  if (monoLevel !== MONO_DEFAULT_LEVEL) p.set('height', String(monoLevel));
  else p.delete('height');
  if (modes.corner.variant === 'glass') p.set('variant', 'glass');
  else p.delete('variant');
  p.delete('t');
  history.replaceState(null, '', `${location.pathname}?${p.toString()}`);
}

function syncChrome() {
  const m = modes[modeId];
  chrome.name.textContent = m.name;
  chrome.device.textContent = modeId === 'mono' ? `${m.device} · height ${monoLevel}/${MONO_LEVELS - 1}` : m.device;
  chrome.swatch.dataset.mode = modeId;
  for (const l of layers) l.canvas.classList.toggle('on', l.id === modeId);
  for (const b of document.querySelectorAll('[data-source]')) b.setAttribute('aria-pressed', String(b.dataset.source === sourceKind));
  for (const b of document.querySelectorAll('button[data-mode]')) b.setAttribute('aria-pressed', String(b.dataset.mode === modeId));
  chrome.transport.hidden = sourceKind !== 'film';
  document.body.classList.toggle('chrome-hidden', chromeHidden);
}

function setNotice(key, title, detail = '', hint = '') {
  if (notice.key === key) return;
  notice.key = key;
  notice.el.hidden = !key;
  if (!key) return;
  notice.title.textContent = title;
  notice.detail.innerHTML = detail;
  notice.hint.innerHTML = hint;
}

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

let filmSeen = false; // the first film frame has been on screen (seeks never bring the notice back)
function syncNotice() {
  if (!filmSeen && film?.blurSource) filmSeen = true;
  if (sourceKind === 'live') {
    if (!live?.connected) {
      setNotice('live-down', 'Connecting to Attune engine…',
        `Looking for the engine at <code>${esc(live?.url ?? '')}</code>`,
        'Start it with <code>uv run --project engine python -m attune</code> &nbsp;·&nbsp; press <kbd class="attune-kbd">V</kbd> for the film');
    } else if (live.frameAge > 2500) {
      setNotice('live-noframes', 'Connected · waiting for video', 'The engine is up but hasn\'t sent camera frames yet.', '');
    } else setNotice('');
  } else if (filmState === 'loading' || (filmState === 'ready' && !filmSeen && !film?.info.error)) {
    setNotice('film-loading', 'Loading the film…', 'Footage, face tracks and script', '');
  } else if (filmState === 'error' || film?.info.error) {
    setNotice('film-error', 'Film footage not found', esc(film?.info.error || filmError),
      'Serve the repo root and link <code>data/reels</code>, or pass <code>?assets=</code>');
  } else setNotice('');
}

let chromeTick = 0;
function syncTransport(now) {
  if (sourceKind !== 'film' || !film || now - chromeTick < 90) return;
  chromeTick = now;
  const info = film.info;
  if (chrome.scene.textContent !== info.label) chrome.scene.textContent = info.label;
  const c = `${info.index + 1} / ${info.count}`;
  if (chrome.count.textContent !== c) chrome.count.textContent = c;
  chrome.progress.style.width = `${Math.max(0, Math.min(1, info.progress)) * 100}%`;
  chrome.play.classList.toggle('paused', !info.playing);
}

// ---------------------------------------------------------------- frame loop
let last = nowS() * 1000;
let renderError = null;
let offlineMode = false; // attuneLens.renderAt() drives every frame; the live loop stands aside
function frame() {
  if (!offlineMode) step(nowS() * 1000);
  requestAnimationFrame(frame);
}
/** One frame. `at` (offline rendering) is the exact film time; `dtOverride` the step in seconds. */
function step(now, at, dtOverride) {
  const anim = now / 1000;
  const dt = dtOverride ?? Math.min(0.1, Math.max(0, (now - last) / 1000));
  last = now;
  try {
    if (source?.kind === 'film') source.tick(at);
    else store.state.clock = anim;
    store.prune();
    const view = build(dt, anim, sourceKind);
    const rendering = layers.filter((l) => l.id === modeId || now < l.until);
    if (rendering.some((l) => modes[l.id].blur)) updateBlur(source?.blurSource ?? null);
    const env = { anim, dt, blur: blurReady ? blurCanvas : null, chrome: !chromeHidden, px: scale, monoLevel };
    for (const l of layers) {
      const on = rendering.includes(l);
      if (!on && !l.dirty) continue;
      const { ctx, canvas } = l;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      l.dirty = on;
      if (!on) continue;
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      modes[l.id].render(ctx, view, env);
    }
    stage.classList.toggle('paused', view.paused);
    syncNotice();
    syncTransport(now);
    lastView = view;
  } catch (err) {
    if (!renderError) {
      renderError = err;
      console.error('[lens] render failed', err);
    }
  }
}
let lastView = null;

// ---------------------------------------------------------------- actions
function send(name, args) {
  source?.send(name, args);
}

function answer(accept) {
  const p = lastView?.pendingProposal;
  if (!p) return false;
  send('name.answer', { proposal_id: p.proposal_id, accept });
  return true;
}

function acknowledge() {
  const al = lastView?.alerts.find((a) => !a.acked && a.state !== 'clear' && !a.watch);
  if (al) send('alert.ack', { alert_id: al.id });
}

function forget() {
  send('session.forget');
  store.reset();
  store.toast('info', 'Session forgotten · strangers and captions cleared', { icon: 'trash', color: '#8FF3E0' });
}

function toggleHelp(force) {
  const open = force ?? help.hidden;
  if (open) {
    const list = $('help-list');
    list.replaceChildren();
    for (const k of listKeys()) {
      const dt = document.createElement('dt');
      const kbd = document.createElement('kbd');
      kbd.className = 'attune-kbd';
      kbd.textContent = k.label;
      dt.append(kbd);
      const dd = document.createElement('dd');
      dd.textContent = k.description;
      list.append(dt, dd);
    }
  }
  help.hidden = !open;
}

function setMonoLevel(level) {
  const next = Math.max(0, Math.min(MONO_LEVELS - 1, level));
  if (modeId !== 'mono') setMode('mono');
  if (next === monoLevel) return;
  monoLevel = next;
  syncUrl();
  syncChrome();
}

// ---------------------------------------------------------------- keys (web/shared/keys.js)
onKey('M', () => setMode(MODE_ORDER[(MODE_ORDER.indexOf(modeId) + 1) % MODE_ORDER.length]), 'Next glasses mode');
onKey('V', () => setSource(sourceKind === 'live' ? 'film' : 'live'), 'Switch source: Live / Film');
onKey('H', () => {
  chromeHidden = !chromeHidden;
  syncChrome();
}, 'Hide or show the demo chrome');
onKey('Y', () => answer(true), 'Yes, that\'s their name', { priority: 10 });
onKey('N', () => answer(false), 'No, wrong name', { priority: 10 });
onKey('A', acknowledge, 'Acknowledge the sound alert');
onKey('P', () => send('pause.toggle'), 'Pause or resume recognition');
onKey('F', forget, 'Forget this session');
onKey('Space', () => {
  if (sourceKind !== 'film' || !film) return false;
  if (offlineMode) {
    exitOffline();
    return true;
  }
  film.togglePlay();
  return true;
}, 'Play or pause the film');
onKey('ArrowLeft', () => sourceKind === 'film' && film?.prev(), 'Previous film scene');
onKey('ArrowRight', () => sourceKind === 'film' && film?.next(), 'Next film scene');
onKey(']', () => setMonoLevel(monoLevel + 1), 'Mono display higher (G1 height level)');
onKey('[', () => setMonoLevel(monoLevel - 1), 'Mono display lower');
onKey('G', () => {
  modes.corner.variant = modes.corner.variant === 'glass' ? 'rayban' : 'glass';
  if (modeId !== 'corner') setMode('corner');
  syncUrl();
  syncChrome();
}, 'Monocular: Ray-Ban Display / Google Glass placement');
onKey('?', () => toggleHelp(), 'Show these shortcuts');
onKey('Escape', () => toggleHelp(false));

// ---------------------------------------------------------------- chrome buttons
document.addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.dataset.source) setSource(b.dataset.source);
  else if (b.dataset.mode) setMode(b.dataset.mode);
  else if (b.id === 'help-btn') toggleHelp();
  else if (b.id === 'play') film?.togglePlay();
  else if (b.id === 'prev') film?.prev();
  else if (b.id === 'next') film?.next();
  b.blur(); // so Space never re-triggers a focused button
});

// ---------------------------------------------------------------- panels (optional, built in parallel)
// A link that follows the active source, so the panels' commands reach the engine or the film.
const panelLink = {
  send,
  get connected() {
    return sourceKind === 'film' ? !!film : !!live?.connected;
  },
  get source() {
    return sourceKind;
  },
  /** Subscribe to every message the lens receives; returns an unsubscribe function. */
  onMessage(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
};

// ---------------------------------------------------------------- deterministic offline rendering
// attuneLens.renderAt(t, mode) shows film time t exactly: the video frame for t, and a HUD whose
// state and animations are derived from film time alone (the animation clock is pinned to it),
// so the same t always renders the same picture. Consecutive calls (t rising by up to 0.5 s in
// one scene) continue the simulation like playback; any other call re-runs it from a few seconds
// before t, so a single frame is also exact. See docs/glasses-realism.md for driving it.
const ANIM0 = 10000; // animation clock = ANIM0 + film time while rendering offline
const OFFLINE_FPS = 60;
const WARMUP_S = 7; // longer than any caption fade (4 s + 3 s grace), toast (3.4 s) or alert tail
let offlineAt = null;

function enterOffline() {
  if (offlineMode) return;
  offlineMode = true;
  offlineAt = null;
  document.body.classList.add('offline');
}

function exitOffline() {
  if (!offlineMode) return;
  offlineMode = false;
  offlineAt = null;
  setManualClock(null);
  last = nowS() * 1000;
  document.body.classList.remove('offline');
  if (film && !film.playing) film.togglePlay();
}

function offlineStep(t, dt) {
  setManualClock(ANIM0 + t);
  step(nowS() * 1000, t, dt);
}

async function renderAt(t, mode) {
  for (let i = 0; i < 100 && !film; i++) await new Promise((r) => setTimeout(r, 100));
  if (!film) throw new Error('film not loaded');
  if (sourceKind !== 'film') await setSource('film');
  enterOffline();
  if (mode) {
    const id = MODE_ALIASES[mode] ?? mode;
    if (id !== modeId) setMode(id);
  }
  for (const l of layers) l.until = 0; // no crossfade offline
  const k = film.segmentAt(t);
  const continuing = offlineAt != null && t >= offlineAt && t - offlineAt <= 0.5 && k === film.segment && film.segmentAt(offlineAt) === k;
  let tt;
  if (continuing) tt = offlineAt;
  else {
    // restart the script a few seconds before t (or at the scene start) with fresh HUD state
    const from = Math.min(t, Math.max(film.segmentRange(k).t0, t - WARMUP_S));
    film.offlineStart(from);
    tt = from;
    build = createViewBuilder(store);
    const variant = modes.corner.variant;
    modes.color = createColorMode();
    modes.mono = createMonoMode();
    modes.corner = createCornerMode();
    modes.corner.variant = variant;
    offlineStep(tt, 0);
  }
  const dt = 1 / OFFLINE_FPS;
  while (tt < t - 1e-6) {
    const next = Math.min(t, tt + dt);
    offlineStep(next, next - tt);
    tt = next;
  }
  const painted = await film.frameAt(t);
  offlineStep(t, 0); // the final picture with its exact frame (dt 0: nothing moves twice)
  offlineAt = t;
  if (!painted) console.warn(`[lens] renderAt(${t}): the video frame did not arrive in time`);
  return { t: lastView?.clock, painted, mode: modeId };
}

// debugging and scripted demos: attuneLens.seek(33), attuneLens.setMode('mono')
window.attuneLens = {
  store, setMode, setSource, setMonoLevel, modes,
  seek: (t) => film?.seek(t),
  get film() {
    return film;
  },
  /**
   * Show film time t exactly (optionally in a mode): the video frame for t and the HUD as it is at
   * t, with animations derived from film time. Call it frame by frame for offline rendering;
   * attuneLens.release() (or Space) returns to normal playback.
   */
  renderAt,
  async hold(t, mode) {
    const r = await renderAt(t, mode);
    return r.t;
  },
  release: exitOffline,
  /** Step the renderer n frames at 60 fps right now (for screenshots of a hidden tab). */
  settle(n = 60) {
    for (let i = 0; i < n; i++) {
      addSkew(1000 / 60);
      step(nowS() * 1000);
    }
    return lastView?.clock;
  },
  get view() {
    return lastView;
  },
};

// The URL's ?mode= always wins on load. No mode is remembered anywhere else (no storage); after
// load only M, the mode buttons or attuneLens.setMode change it, and each writes it back to the
// URL. A page restored from the back/forward cache re-reads its URL to stay in step.
window.addEventListener('pageshow', (e) => {
  if (!e.persisted) return;
  const m = MODE_ALIASES[new URLSearchParams(location.search).get('mode')];
  if (m) setMode(m);
});

// The panels open their own engine link (role console). Mount them when the Live source is
// used; in Film they load on first use (C, S or Y), so film playback from a plain static server
// makes no WebSocket requests at all.
let panels = null;
let panelsLoading = null;
const lazyPanelKeys = [];
function loadPanels() {
  panelsLoading ??= import('../panels/panels.js')
    .then((m) => {
      panels = m.mountPanels?.({ link: panelLink }) ?? null;
      for (const off of lazyPanelKeys.splice(0)) off();
      return panels;
    })
    .catch(() => null);
  return panelsLoading;
}
for (const [key, view, label] of [['C', 'console', 'Console panel'], ['S', 'speak', 'Speak panel'], ['Y', 'history', 'History panel']]) {
  lazyPanelKeys.push(onKey(key, () => {
    if (panels || panelsLoading) return false;
    loadPanels().then((p) => p?.toggle?.(view));
    return true;
  }, label));
}

syncChrome();
setSource(sourceKind);
requestAnimationFrame(frame);


