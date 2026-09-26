/*
 * Small DOM helpers shared by the console, speak and history panels.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-09 to P-11.
 * Every piece of engine text is inserted with textContent, never as HTML.
 */

/**
 * Create an element. `attrs` keys: class, text, on<Event> handlers, dataset (object),
 * style (object), and anything else as an attribute (false/null skips it).
 */
export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === false || value === null || value === undefined) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key === 'style') for (const [prop, v] of Object.entries(value)) node.style.setProperty(prop.startsWith('--') ? prop : prop.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`), String(v));
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (key in node && typeof value !== 'string') node[key] = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** A small keycap label, e.g. keycap('S'). */
export const keycap = (key) => h('kbd', { class: 'atp-key', text: key });

/** A titled card section inside a panel. */
export function section(title, { hint, actions, id } = {}, ...body) {
  const head = h('header', { class: 'atp-card-head' }, h('h3', { text: title }), hint ? h('span', { class: 'atp-card-hint', text: hint }) : null, actions ? h('div', { class: 'atp-card-actions' }, actions) : null);
  return h('section', { class: 'atp-card', id }, head, ...body);
}

/** Engine-clock seconds to m:ss (engine times only compare with each other). */
export function clockText(t) {
  if (typeof t !== 'number' || !Number.isFinite(t)) return '';
  const s = Math.max(0, Math.floor(t));
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = String(s % 60).padStart(2, '0');
  return hrs ? `${hrs}:${String(mins).padStart(2, '0')}:${secs}` : `${mins}:${secs}`;
}

/** Wall-clock time or date text for ISO strings / epoch seconds. */
export function dateText(value, withTime = false) {
  if (value === null || value === undefined || value === '') return '';
  const numeric = typeof value === 'number' || (typeof value === 'string' && /^\d+(?:\.\d+)?$/.test(value));
  const date = numeric ? new Date(Number(value) > 1e11 ? Number(value) : Number(value) * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const opts = withTime ? { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' } : { month: 'short', day: 'numeric', year: 'numeric' };
  return new Intl.DateTimeFormat(undefined, opts).format(date);
}

/** Stable colour for a speaker label, from the HUD palette. */
const SPEAKER_COLORS = ['#7CF5D6', '#6AB8FF', '#FFC857', '#C9A7FF', '#FF9F43', '#8FF3E0', '#FF8FB1'];
export function speakerColor(label = '') {
  const key = String(label).toLowerCase();
  if (key.startsWith('you')) return '#FFFFFF';
  let hash = 0;
  for (const ch of key) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return SPEAKER_COLORS[hash % SPEAKER_COLORS.length];
}

/** Human label for a caption's speaker object (contracts section 2, Speaker). */
export function speakerLabel(speaker) {
  if (!speaker) return 'Someone';
  if (speaker.kind === 'you') return 'You';
  if (speaker.kind === 'you_typed') return 'You (typed)';
  return speaker.label || (speaker.kind === 'offscreen' ? 'Off screen' : 'Someone');
}

/** Toasts and confirm dialogs live inside the panel root so they share its look. */
export function createFeedback(root) {
  const stack = h('div', { class: 'atp-toasts', role: 'status', 'aria-live': 'polite' });
  root.append(stack);

  function toast(text, tone = 'info', ms = 3200) {
    const node = h('div', { class: `atp-toast atp-${tone}`, text });
    stack.append(node);
    requestAnimationFrame(() => node.classList.add('show'));
    setTimeout(() => {
      node.classList.remove('show');
      setTimeout(() => node.remove(), 300);
    }, ms);
  }

  /** Promise<boolean>. Esc or Cancel resolves false. */
  function confirmDialog({ title, body, confirm = 'Confirm', danger = false }) {
    return new Promise((resolve) => {
      const previous = document.activeElement;
      const done = (answer) => {
        overlay.classList.remove('show');
        setTimeout(() => overlay.remove(), 200);
        previous?.focus?.();
        resolve(answer);
      };
      const ok = h('button', { class: danger ? 'atp-btn atp-danger' : 'atp-btn atp-primary', type: 'button', text: confirm, onclick: () => done(true) });
      const cancel = h('button', { class: 'atp-btn atp-ghost', type: 'button', text: 'Cancel', onclick: () => done(false) });
      const dialog = h('div', { class: 'atp-dialog', role: 'alertdialog', 'aria-modal': 'true', 'aria-label': title },
        h('h4', { text: title }), h('p', { text: body }), h('div', { class: 'atp-dialog-actions' }, cancel, ok));
      const overlay = h('div', { class: 'atp-overlay', onkeydown: (e) => { if (e.key === 'Escape') { e.stopPropagation(); done(false); } }, onclick: (e) => { if (e.target === overlay) done(false); } }, dialog);
      root.append(overlay);
      requestAnimationFrame(() => overlay.classList.add('show'));
      cancel.focus();
    });
  }

  return { toast, confirm: confirmDialog };
}

/** Base URL for HTTP calls to the engine (same host as the WebSocket, see ws.js). */
export function engineHttpBase() {
  const params = new URLSearchParams(location.search);
  const host = params.get('engine');
  if (!host) return '';
  return `${location.protocol === 'https:' ? 'https' : 'http'}://${host}`;
}
