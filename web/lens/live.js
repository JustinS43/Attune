/*
 * Live source: the Attune engine over the shared WebSocket client (role lens, with frames).
 * Binary JPEG frames are decoded off the main thread (createImageBitmap) and drawn to a
 * 1280x720 canvas; every JSON message goes straight into the lens store.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-06.
 */

import { connect, engineUrl } from '../shared/ws.js';

export function createLiveSource({ canvas, emit, setConnected, onFrameCount }) {
  const ctx = canvas.getContext('2d', { alpha: false });
  let link = null;
  let decoding = false;
  let pending = null;
  let frames = 0;
  let lastFrameAt = 0;

  async function decode(frame) {
    decoding = true;
    try {
      const bmp = await createImageBitmap(frame.blob);
      ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
      bmp.close();
      frames++;
      lastFrameAt = performance.now();
      if (frames === 1) canvas.classList.add('live-on');
      onFrameCount?.(frames);
    } catch {
      // a corrupt frame: skip it
    }
    decoding = false;
    if (pending) {
      const next = pending;
      pending = null;
      decode(next);
    }
  }

  return {
    kind: 'live',
    get url() {
      return engineUrl();
    },
    get connected() {
      return !!link?.connected;
    },
    get link() {
      return link;
    },
    /** ms since the last decoded frame (Infinity before the first). */
    get frameAge() {
      return frames ? performance.now() - lastFrameAt : Infinity;
    },
    get blurSource() {
      return frames ? canvas : null;
    },
    start() {
      if (link) return;
      if (frames) canvas.classList.add('live-on');
      link = connect({
        role: 'lens',
        frames: true,
        onMessage: emit,
        onFrame: (f) => {
          if (decoding) pending = f; // keep only the newest frame while one decodes
          else decode(f);
        },
        onState: setConnected,
      });
    },
    stop() {
      canvas.classList.remove('live-on');
      link?.close();
      link = null;
      setConnected(false);
    },
    send(name, args = {}) {
      link?.send(name, args);
    },
  };
}
