/*
 * Helpers shared by the compact glasses modes (mono waveguide and monocular corner HUD).
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-06, P-07.
 */

import { createLineWrap, scrollTo, clamp } from '../hud.js';

/**
 * Direction from the wearer to a caption's speaker or an alert, as an angle in radians
 * (0 = right, -PI/2 = up / ahead, PI = left, PI/2 = down / behind), or null when unknown.
 * Faces in view give ← ↖ ↑ ↗ → from where they sit in the frame; off-screen and alert sides
 * give ← → or ↓ (behind). The side and the "up" flag already carry hysteresis (store.js), so
 * the arrow does not flicker between two directions at a boundary.
 */
export function dirAngle(dir) {
  if (!dir) return null;
  if (dir.side === 'behind') return Math.PI / 2;
  if (dir.off) return dir.side === 'left' ? Math.PI : 0;
  if (dir.side === 'ahead') return -Math.PI / 2;
  if (dir.up) return dir.side === 'left' ? (-3 * Math.PI) / 4 : -Math.PI / 4;
  return dir.side === 'left' ? Math.PI : 0;
}

export function sideAngle(side) {
  if (side === 'left') return Math.PI;
  if (side === 'right') return 0;
  if (side === 'behind') return Math.PI / 2;
  return null;
}

/** Names of the people in view, for the idle line ("In view: Mark, Jess"). */
export function inView(view) {
  return [...new Set(view.faces.filter((f) => f.known).map((f) => f.label))].slice(0, 3);
}

/** The caption a one-line display should show: the most recently updated one. */
export function primaryCaption(view) {
  return view.feed.find((b) => b.alpha > 0.02) ?? null;
}

/** A line label's short name: "Mark" -> "MARK", a description "Person in red sweater" -> "RED SWEATER". */
export function shortName(name) {
  const n = String(name || 'Someone').trim();
  const short = n.replace(/^(person|someone) (in|with) /i, '');
  return (short || n).toUpperCase();
}

/**
 * A running caption log for a head-locked display with fixed line positions, like live
 * roll-up captions:
 * - every utterance (view.utterances: all of its ".n" segments merged in spoken order) starts
 *   on a new line, in the order they were spoken, so text never jumps above or below other
 *   text; if the engine re-attributes an utterance only its name and dimming change;
 * - lines never reflow once laid out (hud.js createLineWrap); the window rolls up one line at
 *   a time with a short ease;
 * - an utterance that has faded leaves the log from the top, with the same roll;
 * - the current speaker is the newest utterance's, so the name only changes when the speaker does;
 * - with `names`, an utterance whose speaker differs from the one before it in the log starts
 *   with a short "NAME:" label (and keeps it once it has one, so its line never reflows when the
 *   one before it leaves), so every line's speaker is clear under a one-name header;
 * - an utterance with no words yet (its line is still waiting for a translation) takes no line.
 */
export function createCaptionLog() {
  const wrap = createLineWrap();
  let entries = []; // { id, key, b, tokens, alpha, start, end, lineStart }
  let scroll = {};
  let width = 0;
  let fontStr = '';

  function reset() {
    entries = [];
    wrap.reset();
    scroll = {};
  }

  return {
    get wrap() {
      return wrap;
    },
    /**
     * Update from the view; returns { lines, top, current } or null when there is nothing to show.
     * lines[i] = { entry, index } for every laid-out line; top is the (fractional) first row.
     */
    update(ctx, view, w, f, rows, anim, dt, rollUp = false, names = false) {
      if (w !== width || f !== fontStr) {
        width = w;
        fontStr = f;
        reset();
      }
      const utts = view.utterances ?? [];
      const byId = new Map(utts.map((u) => [u.id, u]));
      for (const e of entries) {
        const u = byId.get(e.id);
        e.alpha = u ? u.alpha : 0;
        if (u) Object.assign(e, { b: u.b, key: u.key, tokens: u.tokens });
      }
      for (const u of utts) {
        if (u.alpha > 0 && !entries.some((e) => e.id === u.id)) entries.push({ id: u.id, b: u.b, key: u.key, tokens: u.tokens, alpha: u.alpha });
      }
      if (!entries.some((e) => e.alpha > 0)) {
        reset();
        return null;
      }
      // turns that have faded leave from the top (the rest rolls up to fill their lines)
      let dropped = false;
      while (entries.length > 1 && entries[0].alpha <= 0) {
        entries.shift();
        dropped = true;
      }
      // (the first entry that has a line: one still waiting for its translation has none)
      const k = dropped ? entries.find((e) => e.lineStart >= 0)?.lineStart ?? 0 : 0;
      if (k > 0) {
        wrap.dropLines(k);
        if (scroll.top != null) scroll.top -= k;
      }
      const tokens = [];
      let prev = null;
      for (const e of entries) {
        e.start = tokens.length;
        const has = e.tokens.length > 0;
        if (names && has) {
          e.named = e.named || (!!prev && prev.key !== e.key);
          if (e.named) tokens.push({ text: `${shortName(e.b?.name)}:`, final: true, br: true, label: true });
        }
        e.tokens.forEach((t, i) => tokens.push(i === 0 && !(names && e.named) ? { ...t, br: true } : t));
        e.end = tokens.length;
        if (has) prev = e;
      }
      const n = wrap.update(ctx, tokens, w, f, anim);
      const top = scrollTo(scroll, n, rows, dt, rollUp);
      const lines = wrap.lines.map((ln, index) => ({ index, entry: entries.find((e) => ln.start >= e.start && ln.start < e.end) ?? entries[entries.length - 1] }));
      for (const e of entries) e.lineStart = lines.findIndex((l) => l.entry === e);
      const current = [...entries].reverse().find((e) => e.alpha > 0) ?? entries[entries.length - 1];
      return { lines, top, current: current.b, currentKey: current.key, currentAlpha: current.alpha, entries };
    },
    /** The text of the rows in the window (for tests and measurements). */
    shown(state, rows) {
      if (!state) return [];
      const out = [];
      const top = Math.round(state.top);
      for (let i = Math.max(0, top); i < Math.min(state.lines.length, top + rows); i++) {
        const ln = wrap.lines[i];
        out.push(wrap.tokens.slice(ln.start, ln.end).map((t) => t.text).join(' '));
      }
      return out;
    },
    /** Calls fn(token, meta, x, row, lineAlpha, entry) for every word in (or entering) the window. */
    eachVisible(state, rows, fn) {
      if (!state) return;
      const { lines, top } = state;
      for (let i = Math.max(0, Math.floor(top)); i < lines.length; i++) {
        const row = i - top;
        if (row > rows) break;
        const lineA = row < 0 ? clamp(1 + row * 1.6) : 1;
        wrap.eachWord(i, (t, m, x) => fn(t, m, x, row, lineA, lines[i].entry));
      }
    },
  };
}
