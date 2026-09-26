/*
 * Keyboard shortcuts shared by every page (one registry, one listener).
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-12.
 *
 *   import { onKey, listKeys } from '../shared/keys.js';
 *   const off = onKey('M', () => cycleMode(), 'Cycle glasses mode');
 *   onKey('Y', () => answer(true), 'Accept name', { priority: 10 });   // runs before priority 0
 *   off();                                                              // unregister
 *
 * - Keys are single characters (case-insensitive: 'm' and 'M' are the same key) or KeyboardEvent.key
 *   names: 'ArrowLeft', 'ArrowRight', 'Escape', 'Enter', 'Space' (the space bar), '?'.
 * - Keystrokes are ignored while the user types in an input, textarea, select or contenteditable
 *   element, and when Ctrl, Alt or Meta is held (so browser shortcuts keep working).
 * - Several handlers may share a key. They run from the highest priority down (then in the order
 *   they were registered). A handler returns `true` to consume the key, which stops the handlers
 *   below it; anything else lets the next one run too.
 * - listKeys() returns [{ key, label, description }] for a help overlay.
 *
 * Key map: the lens uses M V H Y N A P F Space ← → ?; the panels use C S E and 1-9.
 */

const registry = new Map(); // normalized key -> [{ handler, description, priority, order, repeat }]
let order = 0;
let installed = false;

/** Normalize a key name or a KeyboardEvent.key value. */
export function normKey(key) {
  if (key === ' ' || key === 'Spacebar' || key === 'space') return 'Space';
  if (typeof key === 'string' && key.length === 1) return key.toUpperCase();
  return key;
}

const LABELS = { ArrowLeft: '←', ArrowRight: '→', ArrowUp: '↑', ArrowDown: '↓', Space: 'Space', Escape: 'Esc' };

/** Human label for a key, e.g. 'ArrowLeft' -> '←'. */
export function keyLabel(key) {
  const k = normKey(key);
  return LABELS[k] ?? k;
}

/** True when a keystroke belongs to a text field rather than to the shortcuts. */
export function isTypingTarget(el) {
  if (!el || el === document.body) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (tag === 'INPUT') {
    const type = (el.type || 'text').toLowerCase();
    return !['button', 'checkbox', 'radio', 'range', 'submit', 'reset', 'color', 'file', 'image'].includes(type);
  }
  return false;
}

function onKeyDown(e) {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const path = typeof e.composedPath === 'function' ? e.composedPath() : [e.target];
  if (path.some((n) => n instanceof Element && isTypingTarget(n))) return;
  if (isTypingTarget(document.activeElement)) return;
  const list = registry.get(normKey(e.key));
  if (!list || !list.length) return;
  let ran = false;
  for (const entry of [...list]) {
    if (e.repeat && !entry.repeat) continue;
    ran = true;
    let consumed = false;
    try {
      consumed = entry.handler(e) === true;
    } catch (err) {
      console.warn(`[keys] handler for ${e.key} failed`, err);
    }
    if (consumed) break;
  }
  if (ran) e.preventDefault();
}

function install() {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  window.addEventListener('keydown', onKeyDown);
}

/**
 * Register a shortcut. Returns a function that removes it.
 * @param {string} key          e.g. 'M', 'ArrowLeft', 'Space', '?'
 * @param {(e: KeyboardEvent) => (boolean|void)} handler  return true to consume the key
 * @param {string} [description] shown by listKeys() / the help overlay; omit to hide it
 * @param {{priority?: number, repeat?: boolean}} [opts] repeat: also fire on auto-repeat
 */
export function onKey(key, handler, description = '', opts = {}) {
  install();
  const k = normKey(key);
  const entry = { handler, description, priority: opts.priority ?? 0, order: order++, repeat: !!opts.repeat };
  const list = registry.get(k) ?? [];
  list.push(entry);
  list.sort((a, b) => b.priority - a.priority || a.order - b.order);
  registry.set(k, list);
  return () => {
    const cur = registry.get(k);
    if (!cur) return;
    const i = cur.indexOf(entry);
    if (i >= 0) cur.splice(i, 1);
  };
}

/** Every described shortcut, in registration order: [{ key, label, description }]. */
export function listKeys() {
  const out = [];
  for (const [key, list] of registry) {
    for (const e of [...list].sort((a, b) => a.order - b.order)) {
      if (e.description) out.push({ key, label: keyLabel(key), description: e.description, order: e.order });
    }
  }
  return out.sort((a, b) => a.order - b.order).map(({ key, label, description }) => ({ key, label, description }));
}
