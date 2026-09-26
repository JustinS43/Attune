/*
 * Film player: at most two <video> elements (the clip on screen and the next one, cued), painted
 * into a canvas, with a watchdog that reloads a clip whose picture stops or goes black.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-08.
 *
 * Why it's built this way:
 * - One element per clip (9 x 1080p, all preload=auto) kept 9 hardware decoders and a pile of
 *   range requests alive next to the engine's GPU work; after a while the footage could go black
 *   while the element still said it was playing. Two elements are enough for instant cuts: the
 *   next clip is cued in the spare one, and loading a clip into an element releases its old one.
 * - The picture on screen is our own canvas, painted from the video. So what the watchdog samples
 *   is exactly what the audience sees, a stalled clip freezes on its last good frame instead of
 *   turning black, and a scene change holds the old frame until the new clip has a picture.
 * - The watchdog reloads the on-screen clip at the same time when no new frame has arrived for
 *   1.5 s while playing, when the picture has been black for 3 s, when it has been buffering for
 *   4 s, or after a load error (retrying with back-off up to every 8 s; the engine that serves
 *   the footage may be restarting).
 */

const NO_FRAME_MS = 1500;
const BLACK_SAMPLES = 3; // one sample a second
const BLACK_LEVEL = 3; // mean of R, G, B out of 255
const BUFFERING_MS = 4000;
const MIN_RECOVER_GAP_MS = 2000;
const MAX_RETRY_MS = 8000;

export function createFilmPlayer({ layer, base }) {
  const canvas = document.createElement('canvas');
  canvas.className = 'film-frame';
  canvas.setAttribute('aria-hidden', 'true');
  layer.prepend(canvas);
  const ctx = canvas.getContext('2d', { alpha: false });
  // black-picture check: shrink on the GPU first, then read back only a 16x9 thumbnail
  const mid = document.createElement('canvas');
  mid.width = 64;
  mid.height = 36;
  const mctx = mid.getContext('2d', { alpha: false });
  const sampler = document.createElement('canvas');
  sampler.width = 16;
  sampler.height = 9;
  const sctx = sampler.getContext('2d', { alpha: false, willReadFrequently: true });

  let painted = false; // the canvas holds a film frame
  let error = null;
  let wantPlaying = false;
  let target = null; // element holding the clip that should be on screen
  let shown = null; // element currently painted to the canvas
  let nextCue = null; // { clip, t } to cue in the spare element once the target is on screen
  let lastSample = 0;
  const stats = { recoveries: 0, last: '' };

  const url = (clip) => `${base}${clip}.mp4`;
  const now = () => performance.now();

  function makeVideo() {
    const v = document.createElement('video');
    v.muted = true;
    v.playsInline = true;
    v.preload = 'auto';
    v.className = 'film-video';
    v.setAttribute('aria-hidden', 'true');
    v.clip = null;
    v.frames = 0;
    v.lastFrameAt = 0;
    v.armedAt = 0;
    v.dark = 0;
    v.retries = 0;
    v.loadedOnce = false;
    v.addEventListener('loadedmetadata', () => {
      // a seek requested before the metadata arrived: apply it now
      if (v.want != null && Math.abs(v.currentTime - v.want) > 0.1) v.currentTime = v.want;
    });
    v.addEventListener('loadeddata', () => {
      v.loadedOnce = true;
      v.dirty = true;
      if (error?.kind === 'footage') error = null;
    });
    v.addEventListener('seeked', () => {
      v.dirty = true;
      if (v.want != null && v.want > 0.5 && v.currentTime < 0.05 && (!v.seekable.length || v.seekable.end(0) === 0)) {
        error = { kind: 'range', text: 'This web server cannot seek video (no HTTP Range support). Use the engine, or a server with Range requests.' };
      }
    });
    v.addEventListener('playing', () => {
      v.armedAt = now();
    });
    v.addEventListener('error', () => onError(v));
    const onFrame = (_t, meta) => {
      v.presented = meta.mediaTime;
      v.frames++;
      v.lastFrameAt = now();
      v.rvfc = v.requestVideoFrameCallback(onFrame);
    };
    // (re)start the frame-callback chain; recover() calls it again in case the chain was lost
    v.watchFrames = () => {
      if (!('requestVideoFrameCallback' in v)) return;
      if (v.rvfc != null) v.cancelVideoFrameCallback(v.rvfc);
      v.rvfc = v.requestVideoFrameCallback(onFrame);
    };
    v.watchFrames();
    layer.appendChild(v);
    return v;
  }
  const pool = [makeVideo(), makeVideo()];

  function load(v, clip, t) {
    if (v.clip !== clip) {
      v.pause();
      v.clip = clip;
      v.frames = 0;
      v.dark = 0;
      v.retries = 0;
      v.loadedOnce = false;
      v.src = url(clip); // replacing the source releases the element's previous decoder
    }
    v.want = t;
    v.presented = t;
    if (Math.abs(v.currentTime - t) > 0.02 || v.readyState < 1) v.currentTime = t;
  }

  function unload(v) {
    v.pause();
    v.clip = null;
    v.removeAttribute('src');
    v.load();
  }

  function onError(v) {
    // ignore errors from an element we emptied or have since pointed at another clip
    if (!v.clip || !v.error || !v.src.endsWith(`${v.clip}.mp4`)) return;
    const code = v.error?.code;
    // say so on screen when the file looks missing, or when retrying keeps failing
    if (!v.loadedOnce && code === 4) error = { kind: 'footage', text: `Film footage not found at ${url(v.clip)}` };
    else if (v.retries >= 3) error = { kind: 'footage', text: `Film footage stopped loading (${url(v.clip)}): ${v.error?.message || `error ${code}`}` };
    // either way keep trying with back-off: the engine may just be restarting (loadeddata clears the error)
    const clip = v.clip;
    const delay = Math.min(MAX_RETRY_MS, 500 * 2 ** v.retries);
    setTimeout(() => {
      if (v.clip === clip) recover(v, `load error ${code ?? ''} ${v.error?.message ?? ''}`.trim(), true);
    }, delay);
  }

  /** Reload an element's clip at the time it was showing, keeping the last good frame on screen. */
  function recover(v, why, force = false) {
    if (!v.clip) return;
    const t0 = now();
    if (!force && t0 - (v.recoveredAt ?? 0) < MIN_RECOVER_GAP_MS) return;
    v.recoveredAt = t0;
    v.retries++;
    stats.recoveries++;
    stats.last = `${v.clip}: ${why}`;
    console.warn(`[lens] film clip ${v.clip} ${why}; reloading it`);
    const t = v.presented ?? v.currentTime ?? v.want ?? 0;
    const clip = v.clip;
    v.clip = null;
    v.removeAttribute('src');
    v.load();
    const retries = v.retries;
    load(v, clip, t);
    v.retries = retries;
    v.watchFrames();
    v.armedAt = now();
    if (v === target && wantPlaying) v.play().catch(() => {});
  }

  function spareOf(v) {
    return pool[0] === v ? pool[1] : pool[0];
  }

  function cueNext() {
    if (!nextCue || !target) return;
    const spare = spareOf(target);
    if (nextCue.clip !== target.clip) {
      load(spare, nextCue.clip, nextCue.t);
      spare.pause();
    }
    nextCue = null;
  }

  function paint(v) {
    const w = Math.max(1, Math.round(canvas.clientWidth * Math.min(window.devicePixelRatio || 1, 2)));
    const h = Math.max(1, Math.round(canvas.clientHeight * Math.min(window.devicePixelRatio || 1, 2)));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    const vw = v.videoWidth || 16;
    const vh = v.videoHeight || 9;
    const k = Math.max(w / vw, h / vh); // object-fit: cover
    const dw = vw * k;
    const dh = vh * k;
    ctx.drawImage(v, (w - dw) / 2, (h - dh) / 2, dw, dh);
    painted = true;
    v.painted = v.frames;
    v.dirty = false;
  }

  function sampleDark(v) {
    try {
      mctx.drawImage(canvas, 0, 0, mid.width, mid.height);
      sctx.drawImage(mid, 0, 0, sampler.width, sampler.height);
      const d = sctx.getImageData(0, 0, sampler.width, sampler.height).data;
      let s = 0;
      for (let i = 0; i < d.length; i += 4) s += d[i] + d[i + 1] + d[i + 2];
      return s / ((d.length / 4) * 3) < BLACK_LEVEL;
    } catch {
      return false;
    }
  }

  function watchdog(v, t) {
    if (!wantPlaying || document.visibilityState !== 'visible' || v.paused || v.seeking || !v.clip) return;
    if (v.readyState >= 3) {
      v.bufferingSince = 0;
      const since = Math.max(v.lastFrameAt, v.armedAt);
      if (since && t - since > NO_FRAME_MS && 'requestVideoFrameCallback' in v) {
        recover(v, `showed no new frame for ${Math.round(t - since)} ms`);
        return;
      }
    } else {
      v.bufferingSince ||= t;
      if (t - v.bufferingSince > BUFFERING_MS) {
        v.bufferingSince = 0;
        recover(v, `was buffering for ${BUFFERING_MS / 1000} s`);
        return;
      }
    }
    if (t - lastSample > 1000) {
      lastSample = t;
      v.dark = sampleDark(v) ? v.dark + 1 : 0;
      if (v.dark >= BLACK_SAMPLES) {
        v.dark = 0;
        recover(v, `picture was black for ${BLACK_SAMPLES} s`);
      }
    }
  }

  document.addEventListener('visibilitychange', () => {
    // frame callbacks stop while hidden: give every clip a fresh grace period when we come back
    if (document.visibilityState === 'visible') for (const v of pool) v.armedAt = now();
  });

  return {
    canvas,
    stats,
    get error() {
      return error?.text ?? null;
    },
    /** The element whose clip should be on screen (its time drives the script). */
    get video() {
      return target;
    },
    /** True once the target clip is the one painted on screen. */
    get ready() {
      return !!target && target === shown;
    },
    /** What the glass panels blur: the canvas, once it holds a frame. */
    get frameSource() {
      return painted ? canvas : null;
    },
    get elements() {
      return pool;
    },
    /** Make `clip` at `t` the clip on screen; `next` is cued in the spare element afterwards. */
    cue(clip, t, next) {
      let v = pool.find((e) => e.clip === clip);
      if (!v) v = shown && shown.clip !== clip ? spareOf(shown) : (shown ?? pool[0]);
      load(v, clip, t);
      for (const e of pool) if (e !== v) e.pause();
      target = v;
      nextCue = next ?? null;
      v.armedAt = now();
      if (wantPlaying) v.play().catch(() => {});
      if (v === shown) {
        v.dirty = true;
        cueNext();
      }
    },
    setPlaying(on) {
      wantPlaying = on;
      if (!target) return;
      if (on) {
        target.armedAt = now();
        target.play().catch(() => {});
      } else target.pause();
    },
    /** Once per animation frame: promote a ready clip, paint new frames, watch for failures. */
    update() {
      const t = now();
      const v = target;
      if (!v) return;
      if (v !== shown && v.readyState >= 2 && !v.seeking) {
        for (const e of pool) e.classList.toggle('active', e === v);
        shown = v;
        v.dirty = true;
        v.armedAt = t;
        cueNext();
      }
      if (v === shown && (v.dirty || v.frames !== v.painted) && v.readyState >= 2) paint(v);
      watchdog(v, t);
      // a clip that never gets to show a picture (stuck loading) is reloaded too
      if (v !== shown && v.clip && !v.error && wantPlaying && document.visibilityState === 'visible' && t - v.armedAt > BUFFERING_MS) {
        v.armedAt = t;
        recover(v, 'did not load within 4 s');
      }
    },
    /** Release both decoders (switching to Live). The canvas keeps its last frame, hidden. */
    release() {
      wantPlaying = false;
      for (const v of pool) unload(v);
      target = null;
      shown = null;
      nextCue = null;
      painted = false;
    },
    /** Test hook: pretend the on-screen clip failed, to exercise the recovery path. */
    recover(why = 'manual test') {
      if (target) recover(target, why, true);
    },
  };
}
