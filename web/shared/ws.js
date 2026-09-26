/*
 * Attune WebSocket client shared by the lens, the panels and the phone app.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-03. Contracts: docs/contracts.md (3 and 4).
 *
 *   import { connect } from '/shared/ws.js';
 *   const link = connect({
 *     role: 'lens',                 // 'lens' | 'console' | 'phone'
 *     frames: true,                 // ask for binary video frames (lens only)
 *     onMessage: (msg) => {},       // every JSON message, e.g. {type: 'caption', seq, ...}
 *     onFrame: (frame) => {},       // {frameNo, t, blob} (image/jpeg)
 *     onState: (connected) => {},   // link up / down
 *   });
 *   link.send('pause.toggle');      // a command, see contracts section 4
 *   link.send('speak', {text: 'Hi', source: 'typed'});
 *
 * The engine address defaults to the page's own host. Add `?engine=host:port` to the
 * page URL to reach an engine elsewhere (e.g. the phone app served from another port).
 * The link reconnects by itself (0.5 s, doubling to 5 s) and re-sends its hello.
 */

export function engineUrl() {
  const params = new URLSearchParams(location.search);
  const host = params.get('engine') || location.host || 'localhost:8000';
  const secure = location.protocol === 'https:';
  return `${secure ? 'wss' : 'ws'}://${host}/ws`;
}

/** Parse a binary frame: little-endian uint64 frame_no, float64 capture time, then JPEG bytes. */
export function parseFrame(buffer) {
  const view = new DataView(buffer);
  return {
    frameNo: Number(view.getBigUint64(0, true)),
    t: view.getFloat64(8, true),
    blob: new Blob([new Uint8Array(buffer, 16)], { type: 'image/jpeg' }),
  };
}

export function connect({ role, frames = false, onMessage, onFrame, onState, url } = {}) {
  let ws = null;
  let closed = false;
  let delay = 500;
  let timer = null;
  let connected = false;
  const pending = [];

  const setState = (up) => {
    if (up !== connected) {
      connected = up;
      onState?.(up);
    }
  };

  function open() {
    if (closed) return;
    ws = new WebSocket(url || engineUrl());
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => {
      delay = 500;
      ws.send(JSON.stringify({ type: 'hello', role, frames }));
      while (pending.length) ws.send(pending.shift());
      setState(true);
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        onMessage?.(msg);
      } else if (onFrame) {
        if (ev.data instanceof ArrayBuffer && ev.data.byteLength >= 16) {
          onFrame(parseFrame(ev.data));
        }
      }
    };
    ws.onclose = () => {
      setState(false);
      if (closed) return;
      timer = setTimeout(open, delay);
      delay = Math.min(delay * 2, 5000);
    };
    ws.onerror = () => ws.close();
  }

  open();
  return {
    get connected() {
      return connected;
    },
    /** Send a command; queued (up to 20) while the link is down. */
    send(name, args = {}) {
      const text = JSON.stringify({ type: 'command', name, args });
      if (connected && ws.readyState === WebSocket.OPEN) ws.send(text);
      else if (pending.length < 20) pending.push(text);
    },
    close() {
      closed = true;
      clearTimeout(timer);
      ws?.close();
    },
  };
}
