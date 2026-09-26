/*
 * Demo page, "Glasses POV" view: what the wearer sees through the glasses, at about true size.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-20.
 *
 * The flat Glasses view squeezes the camera's whole ~75° frame onto a monitor that fills only
 * ~30° of your view, so every caption looks about 2.5x too small. This view runs its own lens
 * (?chrome=0), enlarges it so one degree on the glasses takes one degree of your view, crops it
 * around the device's display, and draws a soft, out-of-focus glasses frame in front.
 *
 * Scale ("True size"):
 *   The lens frame is 1920 px for 75° across, focal length FOCAL = 1251 px (web/lens/hud.js).
 *   The viewer's own focal length in CSS px is  Fv = screen.width * distance / screenWidthCm
 *   (default: a 16-inch 16:10 laptop, 34.5 cm wide, seen from 60 cm -> the screen spans 32°).
 *   True size = Fv / FOCAL CSS px per lens px (2.7x the flat view on a 1920-px-wide screen).
 *   ?pov-screen=<cm>&pov-distance=<cm> override the assumption.
 * "Whole view" shows the full camera frame (the flat view's framing) with the same frame on top.
 *
 * The frame is a stand-in, drawn closer in than real rims (those sit 40-60° out, beyond any
 * screen). Its openings adapt so the device's display always falls inside the right lens
 * (Corner) or across both (Colour, Mono).
 *
 * The POV lens loads when the view is first opened, follows the main lens's mode, source,
 * G1 height and monocular placement (both ways), and unloads 30 s after you leave the view.
 */

const FOCAL = 1251;
const FW = 1920;
const FH = 1080;
const UNLOAD_MS = 30_000;
const SYNC_MS = 300;
const ZOOM_STORE = 'attune.demo.pov.zoom';

const MODE_ALIASES = {
  color: 'color', colour: 'color', full: 'color', ar: 'color',
  mono: 'mono', green: 'mono', waveguide: 'mono',
  'mono-corner': 'corner', corner: 'corner', monocular: 'corner',
};
const MODE_URL = { color: 'color', mono: 'mono', corner: 'mono-corner' };

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

// Which glasses each look stands for, and how its frame is drawn.
const DEVICES = {
  color: {
    name: 'Meta Orion class',
    frame: { shape: 'rect', rim: 0.05, rimColor: '#15171b', rimAlpha: 1, hi: 0.07, outer: 0.93, nose: 0.1 },
  },
  mono: {
    name: 'Even Realities G1 class',
    frame: { shape: 'panto', rim: 0.012, rimColor: '#a7adb5', rimAlpha: 0.6, hi: 0.18, outer: 0.88, nose: 0.12 },
  },
  corner: {
    name: 'Meta Ray-Ban Display class',
    frame: { shape: 'wayfarer', rim: 0.062, rimColor: '#0b0c0e', rimAlpha: 1, hi: 0.05, outer: 0.95, nose: 0.1 },
  },
  glass: {
    name: 'Google Glass class',
    frame: { shape: 'panto', rim: 0.01, rimColor: '#b4bac2', rimAlpha: 0.55, hi: 0.18, outer: 0.86, nose: 0.12 },
  },
};

function readViewing(params) {
  const cm = (k, d, lo, hi) => {
    const v = Number.parseFloat(params.get(k));
    return Number.isFinite(v) && v >= lo && v <= hi ? v : d;
  };
  return { screenCm: cm('pov-screen', 34.5, 5, 2000), distanceCm: cm('pov-distance', 60, 10, 5000) };
}

const deg = (rad) => (rad * 180) / Math.PI;
const round = (x, n = 0) => Number(x.toFixed(n));

/**
 * Mount the POV view.
 *   root      the <section class="pov"> element (see index.html)
 *   mainLens  the main lens <iframe> (same origin)
 *   params    the demo page's URL params (engine, assets are passed on)
 *   onFrameLoad(frame)  called when the POV lens loads (the demo wires its keys there)
 */
export function mountPov(root, { mainLens, params, onFrameLoad }) {
  const $ = (s) => root.querySelector(s);
  const world = $('.pov-world');
  const frameEl = $('.pov-lens');
  const svg = $('.pov-frame');
  const tag = $('.pov-tag');
  const infoBtn = $('.pov-info-btn');
  const info = $('.pov-info');
  const modeBtns = [...root.querySelectorAll('[data-pov-mode]')];
  const zoomBtns = [...root.querySelectorAll('[data-pov-zoom]')];
  const viewing = readViewing(params);

  let visible = false;
  let loaded = false;
  let unloadTimer = 0;
  let syncTimer = 0;
  let zoom = readZoom();
  let last = null; // the settings both lenses last agreed on
  let sourceHoldUntil = 0; // a source switch takes a moment to show in the URL
  let current = { mode: 'color', variant: 'rayban', height: geo.monoDefault, source: 'live' };
  let crop = null;
  let frameKey = '';

  function readZoom() {
    try {
      return localStorage.getItem(ZOOM_STORE) === 'whole' ? 'whole' : 'true';
    } catch {
      return 'true';
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
      for (const key of ['source', 'mode', 'variant', 'height']) {
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
      if (m.height !== geo.monoDefault) q.set('height', String(m.height));
      if (m.variant === 'glass') q.set('variant', 'glass');
    } else {
      // the main lens hasn't started yet: give the POV lens the same URL settings it got
      for (const k of ['source', 'mode', 'height', 'variant']) if (params.has(k)) q.set(k, params.get(k));
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

  frameEl.addEventListener('load', () => onFrameLoad?.(frameEl));

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
    } else if (loaded) {
      carryFilmTime(frameEl, mainLens);
      unloadTimer = setTimeout(() => {
        clearInterval(syncTimer);
        unload();
      }, UNLOAD_MS);
    }
  }

  // ------------------------------------------------------------ crop and scale
  function deviceKey() {
    return current.mode === 'corner' && current.variant === 'glass' ? 'glass' : current.mode;
  }

  function displayRect() {
    if (current.mode === 'mono') return geo.monoRect(current.height);
    if (current.mode === 'corner') return geo.monocular[current.variant] ?? geo.monocular.rayban;
    return geo.orion;
  }

  function computeCrop(w, h) {
    const cover = Math.max(w / FW, h / FH);
    const fv = (screen.width || w) * (viewing.distanceCm / viewing.screenCm); // viewer focal, CSS px
    const trueZ = fv / FOCAL;
    const z = zoom === 'true' ? Math.max(cover, trueZ) : cover;
    const d = displayRect();
    // where the display's centre goes on the stage
    let focus;
    let target;
    if (current.mode === 'color') {
      focus = { x: FW / 2, y: FH / 2 };
      target = { x: w / 2, y: h / 2 };
    } else if (current.mode === 'mono') {
      focus = { x: d.x + d.w / 2, y: d.y + d.h / 2 };
      target = { x: w / 2, y: h * 0.42 };
    } else {
      focus = { x: d.x + d.w / 2, y: d.y + d.h / 2 };
      target = { x: w * 0.715, y: h * (current.variant === 'glass' ? 0.33 : 0.5) };
    }
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
    const tx = clamp(target.x - focus.x * z, w - FW * z, 0);
    const ty = clamp(target.y - focus.y * z, h - FH * z, 0);
    return {
      z, cover, trueZ, tx, ty, w, h,
      display: { x: tx + d.x * z, y: ty + d.y * z, w: d.w * z, h: d.h * z },
      fieldH: 2 * deg(Math.atan(w / 2 / (z * FOCAL))),
      screenDeg: 2 * deg(Math.atan(viewing.screenCm / 2 / viewing.distanceCm)),
    };
  }

  // The POV lens renders at up to 1.6x the stage's cover size and is scaled up the rest of the
  // way, so its canvases stay a sane size (a true-size lens would be ~5000 px wide).
  let renderW = 0;
  function layout() {
    const w = root.clientWidth;
    const h = root.clientHeight;
    if (!w || !h) return;
    crop = computeCrop(w, h);
    const want = Math.round(FW * Math.min(crop.trueZ, 1.6 * crop.cover, 2.2 * crop.cover));
    const rw = Math.max(Math.round(FW * crop.cover), want);
    if (Math.abs(rw - renderW) > 2) {
      renderW = rw;
      frameEl.style.width = `${rw}px`;
      frameEl.style.height = `${Math.round((rw * FH) / FW)}px`;
    }
    const k = (crop.z * FW) / renderW;
    world.style.transform = `translate(${crop.tx.toFixed(1)}px, ${crop.ty.toFixed(1)}px) scale(${k.toFixed(4)})`;
    drawFrame();
    syncControls();
  }

  // ------------------------------------------------------------ the glasses frame
  // One lens outline as a polygon: a superellipse in the box (x0,y0)-(x1,y1), with the inner
  // lower corner pulled away from the nose. side +1 = the wearer's right lens (right of view).
  function lensPoints(shape, box, side, nose) {
    const cx = (box.x0 + box.x1) / 2;
    const cy = (box.y0 + box.y1) / 2;
    const a = (box.x1 - box.x0) / 2;
    const b = (box.y1 - box.y0) / 2;
    const pts = [];
    const n = 96;
    for (let i = 0; i < n; i++) {
      const t = (i / n) * Math.PI * 2;
      const c = Math.cos(t);
      const s = Math.sin(t); // + is down
      let ex;
      let ey;
      if (shape === 'panto') {
        ex = s < 0 ? 3.4 : 2.2; // flatter top, round bottom
        ey = ex;
      } else if (shape === 'wayfarer') {
        ex = 4.2;
        ey = 4.2;
      } else {
        ex = 5;
        ey = 5;
      }
      let u = Math.sign(c) * Math.abs(c) ** (2 / ex);
      const v = Math.sign(s) * Math.abs(s) ** (2 / ey);
      if (shape === 'wayfarer') u *= 1 - 0.1 * ((v + 1) / 2); // wider at the top
      let x = cx + a * u;
      const y = cy + b * v;
      const inward = -u * side; // > 0 on the nose side
      if (inward > 0 && v > 0) x += side * nose * a * 2 * v ** 2.4 * inward;
      pts.push([x, y]);
    }
    return pts;
  }

  const pathOf = (pts) => `M${pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join('L')}Z`;

  function inside(pts, x, y) {
    let hit = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const [xi, yi] = pts[i];
      const [xj, yj] = pts[j];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
    }
    return hit;
  }

  /**
   * The lens outlines: nominal, then grown (and the nose cut eased) until the display is clear of
   * the frame. The frame may overlap the display only in a thin band along the stage's edges, so
   * captions are never behind it. Colour and Mono show one fused picture across both eyes, so
   * their openings merge in the middle (no bar across the text) and only the nose shows, at the
   * bottom. Corner has its display in the right eye only: a real bridge separates the lenses and
   * the square has to fit inside the right one.
   */
  function frameGeometry(w, h, f) {
    const rim = f.rim * h;
    const mono = current.mode === 'corner';
    const gap = mono ? 0.03 * w : -0.07 * w; // < 0: the openings overlap
    let bx = w / 2; // the bridge
    let right = { x0: bx + gap / 2, x1: w - 0.035 * w - rim, y0: 0.05 * h + rim * 0.6, y1: 0.95 * h - rim * 0.6 };
    let nose = f.nose;
    const d = crop.display;
    // the rim lies outside the outline; keep clear of its blur and leave a little air
    const m = 0.012 * h + 2 * Math.max(2, 0.006 * h);
    // Orion's display is nearly as big as the view, so its frame keeps a wider edge band and
    // may cover the display's far corners (where nothing but the status pill ever sits)
    const wide = current.mode === 'color';
    const band = wide
      ? { x0: 0.06 * w, x1: w - 0.06 * w, y0: 0.1 * h, y1: h - 0.1 * h }
      : { x0: 0.035 * w, x1: w - 0.035 * w, y0: 0.06 * h, y1: h - 0.08 * h };
    const r = {
      x0: Math.max(band.x0, d.x - m), x1: Math.min(band.x1, d.x + d.w + m),
      y0: Math.max(band.y0, d.y - m), y1: Math.min(band.y1, d.y + d.h + m),
    };
    const need = [];
    if (r.x1 > r.x0 && r.y1 > r.y0) {
      for (const fx of [0, 0.25, 0.5, 0.75, 1]) {
        for (const fy of [0, 0.5, 1]) {
          if (fx % 1 && fy === 0.5) continue; // the outline, not the middle
          if (wide && !(fx % 1) && fy !== 0.5) continue; // no far corners
          need.push([r.x0 + (r.x1 - r.x0) * fx, r.y0 + (r.y1 - r.y0) * fy]);
        }
      }
    }
    let pts = null;
    const mirrorX = (x) => 2 * bx - x;
    const ok = ([x, y]) => (mono ? inside(pts.r, x, y) : inside(pts.r, x, y) || inside(pts.l, x, y));
    for (let i = 0; i < 60; i++) {
      pts = {
        r: lensPoints(f.shape, right, 1, nose),
        l: lensPoints(f.shape, { x0: mirrorX(right.x1), x1: mirrorX(right.x0), y0: right.y0, y1: right.y1 }, -1, nose),
      };
      const miss = need.filter((p) => !ok(p));
      if (!miss.length) break;
      // grow toward the missing points, and ease the nose cut
      nose *= 0.9;
      const g = 0.006 * Math.max(w, h); // small steps: grow no further than needed
      for (const [px, y] of miss) {
        const x = mono || px >= bx ? px : mirrorX(px);
        // where the point sits in the box, -1..1 each way: push out the side(s) it is near
        const u = (2 * (x - right.x0)) / (right.x1 - right.x0) - 1;
        const v = (2 * (y - right.y0)) / (right.y1 - right.y0) - 1;
        if (u > 0.5) right = { ...right, x1: right.x1 + g };
        if (u < -0.5 && mono) {
          right = { ...right, x0: right.x0 - g };
          bx = right.x0 - gap / 2; // the bridge moves with the lens
        }
        if (v < -0.5) right = { ...right, y0: right.y0 - g };
        if (v > 0.5) right = { ...right, y1: right.y1 + g };
      }
    }
    return { pts, rim, fits: need.every(ok), need: need.length, bridge: bx };
  }

  let lastFrame = null;
  function drawFrame() {
    const { w, h } = crop;
    const key = deviceKey();
    const f = DEVICES[key].frame;
    const { pts, rim, fits, need, bridge } = frameGeometry(w, h, f);
    lastFrame = { rim, fits, need, bridge, device: key };
    const sig = `${w}x${h}|${key}|${pathOf(pts.r)}|${pathOf(pts.l)}`;
    if (sig === frameKey) return;
    frameKey = sig;
    const pr = pathOf(pts.r);
    const pl = pathOf(pts.l);
    const blurFar = (0.014 * h).toFixed(1); // near-eye defocus
    const blurRim = (Math.max(2, 0.006 * h)).toFixed(1);
    // temples: the frame's hinge and arm running back at each side, just outside the rims
    const ty = 0.26 * h;
    const temple = (s) => {
      const xEdge = s > 0 ? w : 0;
      const xIn = s > 0 ? Math.max(...pts.r.map((p) => p[0])) : Math.min(...pts.l.map((p) => p[0]));
      const th = Math.max(6, rim * 1.2 + 0.01 * h);
      return `M${xIn},${ty - th * 0.5} L${xEdge + s * 40},${ty - th * 1.2} L${xEdge + s * 40},${ty + th * 1.3} L${xIn},${ty + th * 0.6}Z`;
    };
    // nose pads: small soft ovals low on the inside of each lens
    const pad = (s) => {
      const lens = s > 0 ? pts.r : pts.l;
      const inner = lens.filter((p) => p[1] > h * 0.6 && p[1] < h * 0.8);
      const x = inner.length ? (s > 0 ? Math.min(...inner.map((p) => p[0])) : Math.max(...inner.map((p) => p[0]))) : w / 2;
      return `<ellipse cx="${(x + s * 0.004 * w).toFixed(1)}" cy="${(0.72 * h).toFixed(1)}" rx="${(0.012 * w).toFixed(1)}" ry="${(0.05 * h).toFixed(1)}" fill="#c9d2da" fill-opacity="0.10"/>`;
    };
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    svg.innerHTML = `
      <defs>
        <filter id="pov-far" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="${blurFar}"/></filter>
        <filter id="pov-rim" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="${blurRim}"/></filter>
        <mask id="pov-outside" maskUnits="userSpaceOnUse" x="0" y="0" width="${w}" height="${h}">
          <rect width="${w}" height="${h}" fill="#fff"/>
          <g filter="url(#pov-far)"><path d="${pr}" fill="#000"/><path d="${pl}" fill="#000"/></g>
        </mask>
        <mask id="pov-ring" maskUnits="userSpaceOnUse" x="0" y="0" width="${w}" height="${h}">
          <g filter="url(#pov-rim)">
            <path d="${pr}" fill="#fff" stroke="#fff" stroke-width="${(rim * 2).toFixed(1)}" stroke-linejoin="round"/>
            <path d="${pl}" fill="#fff" stroke="#fff" stroke-width="${(rim * 2).toFixed(1)}" stroke-linejoin="round"/>
            <path d="${pr}" fill="#000"/><path d="${pl}" fill="#000"/>
          </g>
        </mask>
        <linearGradient id="pov-hi" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#fff" stop-opacity="${f.hi}"/>
          <stop offset="0.35" stop-color="#fff" stop-opacity="0"/>
          <stop offset="0.8" stop-color="#fff" stop-opacity="0"/>
          <stop offset="1" stop-color="#fff" stop-opacity="${(f.hi * 0.6).toFixed(3)}"/>
        </linearGradient>
        <radialGradient id="pov-vig" cx="50%" cy="46%" r="72%">
          <stop offset="0.55" stop-color="#000" stop-opacity="0"/>
          <stop offset="1" stop-color="#000" stop-opacity="0.38"/>
        </radialGradient>
        <radialGradient id="pov-nose" cx="50%" cy="100%" r="50%">
          <stop offset="0" stop-color="#1a1513" stop-opacity="0.85"/>
          <stop offset="1" stop-color="#1a1513" stop-opacity="0"/>
        </radialGradient>
      </defs>
      <rect width="${w}" height="${h}" fill="url(#pov-vig)"/>
      <rect width="${w}" height="${h}" fill="#040507" fill-opacity="${f.outer}" mask="url(#pov-outside)"/>
      <g filter="url(#pov-far)" opacity="0.9">
        <path d="${temple(1)}" fill="${f.rimColor}" fill-opacity="${f.rimAlpha}"/>
        <path d="${temple(-1)}" fill="${f.rimColor}" fill-opacity="${f.rimAlpha}"/>
      </g>
      <g mask="url(#pov-ring)">
        <rect width="${w}" height="${h}" fill="${f.rimColor}" fill-opacity="${f.rimAlpha}"/>
        <rect width="${w}" height="${h}" fill="url(#pov-hi)"/>
      </g>
      <g filter="url(#pov-rim)">${pad(1)}${pad(-1)}</g>
      <ellipse cx="${bridge.toFixed(1)}" cy="${h * 1.02}" rx="${(0.11 * w).toFixed(1)}" ry="${(0.2 * h).toFixed(1)}" fill="url(#pov-nose)" filter="url(#pov-far)"/>`;
  }

  // ------------------------------------------------------------ controls, label, info
  function syncControls() {
    const key = deviceKey();
    for (const b of modeBtns) b.setAttribute('aria-pressed', String(b.dataset.povMode === current.mode));
    for (const b of zoomBtns) b.setAttribute('aria-pressed', String(b.dataset.povZoom === zoom));
    const atTrue = zoom === 'true' && crop.trueZ > crop.cover * 1.02;
    const scaleWord = atTrue ? 'about true size' : 'whole view';
    tag.textContent = `Glasses POV · ${DEVICES[key].name} · ${scaleWord}`;
    root.dataset.device = key;

    const enlarge = crop.z / crop.cover;
    const sd = round(crop.screenDeg);
    const lines = [];
    if (zoom === 'true') {
      lines.push(`<b>About true size.</b> This assumes a ${viewing.screenCm === 34.5 ? '16-inch laptop screen (34.5 cm wide)' : `${viewing.screenCm} cm wide screen`} seen from ${viewing.distanceCm} cm: the screen then fills ${sd}° of your view.`);
      if (atTrue) {
        lines.push(`The picture is enlarged ${enlarge.toFixed(1)}× from the full-width Glasses view, so one degree on the glasses takes one degree of your view. You see the middle ${round(crop.fieldH)}° of the camera's 75°.`);
      } else {
        lines.push('At this distance the whole camera frame already fits at true size or larger, so it is shown whole.');
      }
    } else {
      lines.push(`<b>Whole view.</b> The camera's full 75° frame, squeezed onto a screen that fills about ${sd}° of your view, so the display and its text look ${(crop.trueZ / crop.cover).toFixed(1)}× smaller than on the glasses.`);
    }
    if (current.mode === 'color' && zoom === 'true') lines.push('Orion\'s display (about 70° diagonal) is wider than this screen at true size: you see its middle part.');
    lines.push('The frame is drawn closer in than real rims, which sit 40–60° out, beyond any screen. Sit so your screen fills about 32° of your view, or add <code>?pov-screen=</code>cm<code>&amp;pov-distance=</code>cm.');
    info.innerHTML = lines.map((l) => `<p>${l}</p>`).join('');
  }

  for (const b of modeBtns) b.addEventListener('click', () => setMode(b.dataset.povMode));
  for (const b of zoomBtns) b.addEventListener('click', () => setZoom(b.dataset.povZoom));

  function setZoom(z) {
    zoom = z === 'whole' ? 'whole' : 'true';
    try {
      localStorage.setItem(ZOOM_STORE, zoom);
    } catch {
      /* private window */
    }
    layout();
  }

  function toggleInfo(force) {
    const open = force ?? info.hidden;
    info.hidden = !open;
    infoBtn.setAttribute('aria-expanded', String(open));
  }
  infoBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleInfo();
  });
  document.addEventListener('click', (e) => {
    if (!info.hidden && !info.contains(e.target)) toggleInfo(false);
  });

  new ResizeObserver(() => layout()).observe(root);
  geoReady.then(() => {
    current.height = readState(mainLens)?.height ?? geo.monoDefault;
    layout();
  });
  // follow the main lens even before the POV lens exists, so the frame is right on first open
  mainLens.addEventListener('load', () => sync());

  const handle = {
    setVisible,
    setMode,
    toggleZoom: () => setZoom(zoom === 'true' ? 'whole' : 'true'),
    focus: () => frameEl.focus(),
    get frame() {
      return frameEl;
    },
    /** For tests: the crop, and whether the display falls inside the frame's opening(s). */
    check() {
      return { zoom, mode: current.mode, variant: current.variant, crop, frame: lastFrame, loaded };
    },
  };
  return handle;
}
