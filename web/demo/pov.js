/*
 * Demo page, "Glasses POV" view: as if you were wearing the glasses.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-20.
 *
 * The view runs its own lens (?chrome=0) and fills the whole window with it, edge to edge: no
 * frame, no borders, no letterboxing. The 16:9 lens is cover-fitted to the window and then
 * brought closer, the scene and the HUD scaled together so the proportions stay those of the
 * device. The demo's top bar hides while this view is open (demo.js).
 *
 * Scale:
 *   "Closer" (default)  as close as the device's display allows: its region (ORION_REGION in
 *                       lens/modes/color.js, monoRect in mono.js, MONOCULAR in corner.js; see
 *                       docs/glasses-realism.md) stays fully on screen with a comfortable margin,
 *                       and the zoom is capped at CAP x cover so the people in the scene stay in
 *                       view. The picture is centred on straight ahead and shifted only as far as
 *                       it takes to keep the display clear of the edges.
 *   "Everything"        the whole camera frame, cover-fitted (full-bleed).
 *   Z toggles them.
 *
 * The POV lens loads when the view is first opened, follows the main lens's mode, source,
 * G1 height and monocular placement (both ways), and unloads 30 s after you leave the view.
 */

const FOCAL = 1251;
const FW = 1920;
const FH = 1080;
const CAP = 1.25; // the most "Closer" enlarges over cover
const MARGIN = 0.045; // air kept around the display, as a share of the window's smaller side
const UNLOAD_MS = 30_000;
const SYNC_MS = 300;
const IDLE_MS = 2600; // the pill fades after this long without the mouse moving
const ZOOM_STORE = 'attune.demo.pov.zoom';

const MODE_ALIASES = {
  color: 'color', colour: 'color', full: 'color', ar: 'color',
  focused: 'focused', focus: 'focused',
  mono: 'mono', green: 'mono', waveguide: 'mono',
  'mono-corner': 'corner', corner: 'corner', monocular: 'corner',
};
const MODE_URL = { color: 'color', focused: 'focused', mono: 'mono', corner: 'mono-corner' };

// Display geometry in lens pixels. Read from the lens's own modes when they load, so a change
// there follows automatically; these copies are only the fallback.
const geo = {
  orion: { x: 295, y: 60, w: 1330, h: 960 },
  monocular: { rayban: { x: 1028, y: 452, w: 307, h: 307 }, glass: { x: 1000, y: 204, w: 285, h: 160 } },
  monoDefault: 4,
  monoRect: (level) => {
    const cy = FH / 2 - FOCAL * Math.tan(((level + 1) * Math.PI) / 180);
    return { x: 695, y: cy - 82.5, w: 530, h: 165 };
  },
};
const geoReady = Promise.all([
  import('../lens/modes/color.js').then((m) => { if (m.ORION_REGION) geo.orion = m.ORION_REGION; }),
  import('../lens/modes/corner.js').then((m) => {
    if (m.MONOCULAR?.rayban?.rect) geo.monocular = { rayban: m.MONOCULAR.rayban.rect, glass: m.MONOCULAR.glass?.rect ?? geo.monocular.glass };
  }),
  import('../lens/modes/mono.js').then((m) => {
    if (typeof m.monoRect === 'function') geo.monoRect = m.monoRect;
    if (Number.isFinite(m.MONO_DEFAULT_LEVEL)) geo.monoDefault = m.MONO_DEFAULT_LEVEL;
  }),
].map((p) => p.catch(() => {})));

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/**
 * Mount the POV view.
 *   root      the <section class="pov"> element (see index.html)
 *   mainLens  the main lens <iframe> (same origin)
 *   params    the demo page's URL params (engine, assets are passed on)
 *   onFrameLoad(frame)  called when the POV lens loads (the demo wires its keys there)
 *   onPointer(y)        the mouse moved over the view, y in window px (the demo's top bar)
 */
export function mountPov(root, { mainLens, params, onFrameLoad, onPointer }) {
  const $ = (s) => root.querySelector(s);
  const world = $('.pov-world');
  const frameEl = $('.pov-lens');
  const tools = $('.pov-tools');
  const zoomBtn = $('.pov-zoom');
  const infoBtn = $('.pov-info-btn');
  const info = $('.pov-info');
  const modeBtns = [...root.querySelectorAll('[data-pov-mode]')];

  let visible = false;
  let loaded = false;
  let unloadTimer = 0;
  let syncTimer = 0;
  let idleTimer = 0;
  let zoom = readZoom();
  let last = null; // the settings both lenses last agreed on
  let sourceHoldUntil = 0; // a source switch takes a moment to show in the URL
  let current = { mode: 'color', variant: 'rayban', height: geo.monoDefault, source: 'live' };
  let view = null;

  function readZoom() {
    try {
      return localStorage.getItem(ZOOM_STORE) === 'everything' ? 'everything' : 'closer';
    } catch {
      return 'closer';
    }
  }

  // ------------------------------------------------------------ lens state (both frames)
  function api(frame) {
    try {
      return frame.contentWindow?.attuneLens ?? null;
    } catch {
      return null;
    }
  }

  function readState(frame) {
    const a = api(frame);
    if (!a) return null;
    try {
      const q = new URLSearchParams(frame.contentWindow.location.search);
      const h = Number.parseInt(q.get('height'), 10);
      return {
        mode: MODE_ALIASES[q.get('mode')] ?? 'color',
        labels: q.get('labels') !== '0',
        source: q.get('source') === 'film' ? 'film' : 'live',
        height: Number.isFinite(h) ? h : geo.monoDefault,
        variant: a.modes?.corner?.variant === 'glass' ? 'glass' : 'rayban',
      };
    } catch {
      return null;
    }
  }

  function apply(frame, key, value) {
    const a = api(frame);
    if (!a) return false;
    if (key === 'mode') a.setMode(value);
    else if (key === 'labels') a.setLabels(value);
    else if (key === 'source') {
      a.setSource(value);
      sourceHoldUntil = performance.now() + 6000;
    } else if (key === 'height') {
      if (readState(frame)?.mode !== 'mono') return false; // setMonoLevel would switch to Mono
      a.setMonoLevel(value);
    } else if (key === 'variant') {
      const corner = a.modes?.corner;
      if (!corner) return false;
      corner.variant = value;
      // step out and back so the lens's label and URL catch up (the same trick as the guide)
      if (readState(frame)?.mode === 'corner') {
        a.setMode('color');
        a.setMode('corner');
      }
    }
    return true;
  }

  /** Keep the two lenses on the same settings; whichever changed since the last tick wins. */
  function sync() {
    const m = readState(mainLens);
    const p = loaded ? readState(frameEl) : null;
    if (m && p) {
      // the POV lens starts on the main lens's settings: until one of them changes, main leads
      last ??= { ...p };
      for (const key of ['source', 'mode', 'labels', 'variant', 'height']) {
        if (key === 'source' && performance.now() < sourceHoldUntil) continue;
        if (m[key] === p[key]) {
          last[key] = m[key];
          continue;
        }
        const povWins = p[key] !== last[key] && m[key] === last[key];
        const ok = povWins ? apply(mainLens, key, p[key]) : apply(frameEl, key, m[key]);
        if (ok) last[key] = povWins ? p[key] : m[key];
      }
    }
    const s = (loaded && readState(frameEl)) || m;
    if (s && (s.mode !== current.mode || s.variant !== current.variant || s.height !== current.height || s.source !== current.source)) {
      current = s;
      layout();
    }
  }

  /** Set the look on both lenses (the POV view's own Colour / Mono / Corner switch). */
  function setMode(mode) {
    apply(mainLens, 'mode', mode);
    if (loaded) apply(frameEl, 'mode', mode);
    if (last) last.mode = mode;
    sync();
  }

  // ------------------------------------------------------------ load / unload
  function lensSrc() {
    const m = readState(mainLens);
    const q = new URLSearchParams();
    q.set('chrome', '0');
    if (m) {
      q.set('source', m.source);
      q.set('mode', MODE_URL[m.mode]);
      if (!m.labels) q.set('labels', '0');
      if (m.height !== geo.monoDefault) q.set('height', String(m.height));
      if (m.variant === 'glass') q.set('variant', 'glass');
    } else {
      // the main lens hasn't started yet: give the POV lens the same URL settings it got
      for (const k of ['source', 'mode', 'labels', 'height', 'variant']) if (params.has(k)) q.set(k, params.get(k));
    }
    for (const k of ['engine', 'assets']) if (params.has(k)) q.set(k, params.get(k));
    const t = api(mainLens)?.film?.info?.t;
    if (m?.source === 'film' && Number.isFinite(t)) q.set('t', t.toFixed(2));
    return `../lens/?${q}`;
  }

  function load() {
    loaded = true;
    last = null;
    frameEl.src = lensSrc();
  }

  function unload() {
    loaded = false;
    last = null;
    frameEl.src = 'about:blank'; // closes its engine connection and frees the video decoders
  }

  frameEl.addEventListener('load', () => {
    onFrameLoad?.(frameEl);
    // mouse moves over the lens happen in its own document: wake the pill from there too
    try {
      frameEl.contentWindow.addEventListener('mousemove', (e) => {
        wake();
        const r = frameEl.getBoundingClientRect();
        onPointer?.(r.top + e.clientY * (r.height / (frameEl.offsetHeight || 1)));
      }, { passive: true });
      frameEl.contentWindow.addEventListener('click', () => toggleInfo(false));
    } catch {
      /* another origin */
    }
  });

  /** Film: carry the play position across when switching between the POV and the other views. */
  function carryFilmTime(from, to) {
    try {
      const a = api(from);
      const b = api(to);
      if (!a?.film || !b?.film || readState(from)?.source !== 'film') return;
      const t = a.film.info.t;
      if (Number.isFinite(t) && Math.abs(t - b.film.info.t) > 0.5) b.seek(t);
    } catch {
      /* the other lens isn't ready: it keeps its own time */
    }
  }

  function setVisible(on) {
    if (on === visible) return;
    visible = on;
    clearTimeout(unloadTimer);
    if (on) {
      if (!loaded) load();
      else carryFilmTime(mainLens, frameEl);
      clearInterval(syncTimer);
      syncTimer = setInterval(sync, SYNC_MS);
      sync();
      layout();
      wake();
    } else if (loaded) {
      toggleInfo(false);
      carryFilmTime(frameEl, mainLens);
      unloadTimer = setTimeout(() => {
        clearInterval(syncTimer);
        unload();
      }, UNLOAD_MS);
    }
  }

  // ------------------------------------------------------------ scale and placement
  function displayRect() {
    if (current.mode === 'mono') return geo.monoRect(current.height);
    if (current.mode === 'corner') return geo.monocular[current.variant] ?? geo.monocular.rayban;
    return geo.orion;
  }

  /**
   * Scale z (window px per lens px) and offset (tx, ty) of the lens for a w x h window.
   * Never below cover (no letterboxing). "Closer" grows until the display region plus a margin
   * just fits, capped at CAP x cover. Then: centred on straight ahead, nudged only as far as it
   * takes to keep the display inside the margin, and never so far that an edge shows.
   */
  function computeView(w, h) {
    const cover = Math.max(w / FW, h / FH);
    const d = displayRect();
    const m = MARGIN * Math.min(w, h);
    const fit = Math.min((w - 2 * m) / d.w, (h - 2 * m) / d.h);
    const z = zoom === 'closer' ? clamp(fit, cover, CAP * cover) : cover;
    const place = (size, lensSize, d0, dSize) => {
      let t = (size - lensSize * z) / 2; // straight ahead
      const lo = m - d0 * z; // the display's near edge at the margin
      const hi = size - m - (d0 + dSize) * z; // its far edge at the margin
      if (lo <= hi) t = clamp(t, lo, hi);
      else t = (lo + hi) / 2; // too big for the margin: centre it
      return clamp(t, size - lensSize * z, 0);
    };
    const tx = place(w, FW, d.x, d.w);
    const ty = place(h, FH, d.y, d.h);
    const display = { x: tx + d.x * z, y: ty + d.y * z, w: d.w * z, h: d.h * z };
    return {
      w, h, z, cover, tx, ty, display, margin: m,
      enlarge: z / cover,
      // everything of the display on screen (a pixel of slack for rounding)
      displayVisible: display.x >= -1 && display.y >= -1 && display.x + display.w <= w + 1 && display.y + display.h <= h + 1,
      // the lens covers the whole window (no black edges)
      fullBleed: tx <= 0.5 && ty <= 0.5 && tx + FW * z >= w - 0.5 && ty + FH * z >= h - 0.5,
    };
  }

  // The lens renders at CAP x cover (the largest it is ever shown) and is scaled down from there,
  // so switching zoom is a smooth transform and the enlarged picture stays sharp.
  let renderW = 0;
  function layout() {
    const w = root.clientWidth;
    const h = root.clientHeight;
    if (!w || !h) return;
    view = computeView(w, h);
    const rw = Math.round(FW * CAP * view.cover);
    if (Math.abs(rw - renderW) > 2) {
      renderW = rw;
      frameEl.style.width = `${rw}px`;
      frameEl.style.height = `${Math.round((rw * FH) / FW)}px`;
    }
    const k = (view.z * FW) / renderW;
    world.style.transform = `translate(${view.tx.toFixed(1)}px, ${view.ty.toFixed(1)}px) scale(${k.toFixed(4)})`;
    syncControls();
  }

  // ------------------------------------------------------------ the pill
  function syncControls() {
    for (const b of modeBtns) b.setAttribute('aria-pressed', String(b.dataset.povMode === current.mode));
    zoomBtn.dataset.zoom = zoom;
    zoomBtn.setAttribute('aria-label', `Scale: ${zoom === 'closer' ? 'Closer' : 'Everything'} (Z to switch)`);
    root.dataset.mode = current.mode;
  }

  for (const b of modeBtns) b.addEventListener('click', () => setMode(b.dataset.povMode));
  zoomBtn.addEventListener('click', () => setZoom(zoom === 'closer' ? 'everything' : 'closer'));

  function setZoom(z) {
    zoom = z === 'everything' ? 'everything' : 'closer';
    try {
      localStorage.setItem(ZOOM_STORE, zoom);
    } catch {
      /* private window */
    }
    layout();
    wake();
  }

  info.innerHTML = '<p>This is the wearer\'s view filling your screen, with the captions and the scene scaled together as they are on the device.</p>'
    + '<p><b>Closer</b> brings it in as far as the display allows; <b>Everything</b> shows the whole camera frame.</p>';

  function toggleInfo(force) {
    const open = force ?? info.hidden;
    info.hidden = !open;
    infoBtn.setAttribute('aria-expanded', String(open));
    if (open) wake();
  }
  infoBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleInfo();
  });
  document.addEventListener('click', (e) => {
    if (!info.hidden && !info.contains(e.target)) toggleInfo(false);
  });

  /** Show the pill for a moment; it fades again unless hovered, focused or its note is open. */
  function wake() {
    if (!visible) return;
    tools.classList.remove('idle');
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      if (tools.matches(':hover, :focus-within') || !info.hidden) return wake();
      tools.classList.add('idle');
    }, IDLE_MS);
  }
  root.addEventListener('mousemove', (e) => {
    wake();
    onPointer?.(e.clientY);
  }, { passive: true });

  new ResizeObserver(() => layout()).observe(root);
  geoReady.then(() => {
    current.height = readState(mainLens)?.height ?? geo.monoDefault;
    layout();
  });
  // follow the main lens even before the POV lens exists, so the look is right on first open
  mainLens.addEventListener('load', () => sync());

  const handle = {
    setVisible,
    setMode,
    toggleZoom: () => setZoom(zoom === 'closer' ? 'everything' : 'closer'),
    wake,
    focus: () => frameEl.focus(),
    get frame() {
      return frameEl;
    },
    /** For tests: the scale and placement, and whether the display is fully on screen. */
    check() {
      return { zoom, mode: current.mode, variant: current.variant, height: current.height, view, loaded };
    },
  };
  return handle;
}
