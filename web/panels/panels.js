/*
 * Slide-over panels: console (C), speak (S), history (Y).
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-09 to P-11. Contracts: docs/contracts.md (3, 4, 5).
 *
 *   const { mountPanels } = await import('../panels/panels.js');
 *   const panels = mountPanels();          // slide-over on the lens view
 *   mountPanels({ docked: true });         // full-page console for a second screen (panels/index.html)
 *
 * The panels open their own WebSocket link with role 'console' (web/shared/ws.js), so they only
 * depend on the contracts, not on the lens internals. A `link` passed in is ignored on purpose.
 *
 * Keys: C console, S speak, Y history, E enroll, Esc close, 1-5 presets, 7-9 suggestions.
 * They go through onKey() from web/shared/keys.js when it exists; otherwise a local listener
 * with the same rule (never while typing in an input).
 */

import { connect } from '../shared/ws.js';
import { h, keycap, createFeedback } from './ui.js';
import { createConsole } from './console.js';
import { createSpeak } from './speak.js';
import { createHistory } from './history.js';

const VIEWS = [
  ['console', 'Console', 'C'],
  ['speak', 'Speak', 'S'],
  ['history', 'History', 'Y'],
];

function ensureStyles() {
  const href = new URL('./panels.css', import.meta.url).href;
  if ([...document.querySelectorAll('link[rel="stylesheet"]')].some((l) => l.href === href)) return;
  document.head.append(h('link', { rel: 'stylesheet', href }));
}

function isTyping(target) {
  const t = target instanceof Element ? target : null;
  return !!t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
}

export function mountPanels({ docked = false, root = document.body, open: initial } = {}) {
  ensureStyles();
  const state = { connected: false, paused: false, presets: [], suggestions: [], people: [], hwLink: null, sessionId: null };
  let active = null;

  const rootEl = h('div', { class: `atp-root${docked ? ' atp-docked' : ''}` });
  const { toast, confirm } = createFeedback(rootEl);
  let link = null;
  const views = {};

  const ctx = {
    state,
    toast,
    confirm,
    isOpen: (name) => docked || active === name,
    send(name, args = {}) {
      if (!state.connected) toast('Engine offline: sent when it reconnects', 'warn', 2400);
      link.send(name, args);
      if (name === 'session.forget') dispatch({ type: 'forgotten' });
    },
  };

  views.console = createConsole(ctx);
  views.speak = createSpeak(ctx);
  views.history = createHistory(ctx);

  // ---------------------------------------------------------------- chrome
  const connPill = h('span', { class: 'atp-conn', role: 'status' });
  const pausedPill = h('span', { class: 'atp-paused-pill', hidden: true, text: 'Paused' });
  const tabs = VIEWS.map(([name, label, key]) => h('button', { class: 'atp-tab', type: 'button', role: 'tab', 'aria-selected': 'false', onclick: () => openView(name) }, label, keycap(key)));
  const closeBtn = h('button', { class: 'atp-close', type: 'button', 'aria-label': 'Close panel (Esc)', title: 'Close (Esc)', text: '✕', onclick: () => closeView() });
  const brand = h('div', { class: 'atp-brand' }, h('img', {
    class: 'atp-wordmark',
    src: '../shared/brand/attune-wordmark-dark.svg',
    alt: 'Attune',
    width: '112',
    height: '30',
  }));

  if (docked) {
    const header = h('header', { class: 'atp-topbar' }, brand, connPill, pausedPill,
      h('div', { class: 'atp-legend' }, 'Keys ', keycap('1'), '–', keycap('5'), ' presets · ', keycap('7'), '–', keycap('9'), ' suggestions · ', keycap('E'), ' enroll'));
    const cols = VIEWS.map(([name, label, key]) => h('section', { class: 'atp-column', id: `atp-col-${name}`, 'aria-label': label },
      h('header', { class: 'atp-col-head' }, h('h2', { text: label }), keycap(key)), h('div', { class: 'atp-body' }, views[name].el)));
    rootEl.append(header, h('main', { class: 'atp-columns' }, cols));
  } else {
    const panel = h('aside', { class: 'atp-panel', 'aria-label': 'Attune panels', 'aria-hidden': 'true', inert: true },
      h('header', { class: 'atp-head' }, h('div', { class: 'atp-head-row' }, brand, connPill, pausedPill, closeBtn), h('nav', { class: 'atp-tabs', role: 'tablist' }, tabs)),
      h('div', { class: 'atp-body' }, VIEWS.map(([name]) => h('div', { class: 'atp-slot', dataset: { view: name }, hidden: true }, views[name].el))));
    panel.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !e.defaultPrevented) {
        e.preventDefault();
        once('esc', () => closeView());
      }
    });
    rootEl.append(panel);
  }
  root.append(rootEl);

  function renderChrome() {
    connPill.className = `atp-conn ${state.connected ? 'ok' : 'bad'}`;
    connPill.replaceChildren(h('i'), state.connected ? 'Live' : 'Offline, reconnecting');
    connPill.title = state.connected ? `Connected to the engine${state.sessionId ? ` · session ${state.sessionId}` : ''}` : 'No link to the engine; commands are queued';
    pausedPill.hidden = !state.paused;
    VIEWS.forEach(([name], i) => {
      tabs[i].classList.toggle('on', active === name);
      tabs[i].setAttribute('aria-selected', String(active === name));
    });
  }

  // ---------------------------------------------------------------- open / close
  function openView(name, { focus = true } = {}) {
    if (docked) {
      document.getElementById(`atp-col-${name}`)?.scrollIntoView({ behavior: 'smooth', inline: 'start' });
      if (focus) (name === 'console' ? null : views[name].focus?.());
      return;
    }
    const panel = rootEl.querySelector('.atp-panel');
    const was = active;
    active = name;
    for (const slot of rootEl.querySelectorAll('.atp-slot')) slot.hidden = slot.dataset.view !== name;
    panel.classList.add('open');
    panel.removeAttribute('aria-hidden');
    panel.inert = false;
    if (was !== name) {
      rootEl.querySelector('.atp-body').scrollTop = 0;
      views[name].onOpen?.();
    }
    renderChrome();
    if (focus) {
      if (views[name].focus) setTimeout(() => views[name].focus(), 60);
      else setTimeout(() => tabs[VIEWS.findIndex(([n]) => n === name)].focus(), 60);
    }
  }

  function closeView() {
    if (docked || !active) return;
    const panel = rootEl.querySelector('.atp-panel');
    panel.classList.remove('open');
    panel.setAttribute('aria-hidden', 'true');
    if (panel.contains(document.activeElement)) document.activeElement.blur();
    panel.inert = true;
    active = null;
    renderChrome();
  }

  function toggleView(name) {
    if (!docked && active === name) closeView();
    else openView(name);
  }

  // ---------------------------------------------------------------- engine link
  function dispatch(msg) {
    for (const v of Object.values(views)) {
      try {
        v.onMessage?.(msg);
      } catch (err) {
        console.warn('[panels] view failed on', msg.type, err);
      }
    }
  }

  function onMessage(msg) {
    switch (msg.type) {
      case 'welcome':
        state.sessionId = msg.session_id ?? state.sessionId;
        state.paused = !!msg.paused;
        if (Array.isArray(msg.config?.presets)) state.presets = msg.config.presets;
        renderChrome();
        break;
      case 'paused':
        state.paused = !!msg.paused;
        renderChrome();
        break;
      case 'reply_suggestions': state.suggestions = Array.isArray(msg.options) ? msg.options : []; break;
      case 'people': state.people = msg.people || msg.list || msg.items || []; break;
      case 'hw_link': state.hwLink = { connected: !!msg.connected, firmware: msg.firmware, driver: msg.driver }; break;
      default: break;
    }
    dispatch(msg);
  }

  link = connect({
    role: 'console',
    frames: false,
    onMessage,
    onState(up) {
      state.connected = up;
      renderChrome();
    },
  });

  // ---------------------------------------------------------------- keys
  const last = {};
  function once(id, fn) {
    // keys.js and the local fallback may both see one press; act on it once
    const now = performance.now();
    if (last[id] && now - last[id] < 40) return;
    last[id] = now;
    fn();
  }
  const bindings = [
    ['c', 'Console panel', () => toggleView('console')],
    ['s', 'Speak panel', () => toggleView('speak')],
    ['y', 'History panel', () => toggleView('history')],
    ['e', 'Enroll a person', () => { openView('console', { focus: false }); setTimeout(() => views.console.focusEnroll(), 80); }],
    ['Escape', 'Close panel', () => closeView()],
    ...[1, 2, 3, 4, 5].map((n) => [String(n), `Speak preset ${n}`, () => views.speak.preset(n - 1)]),
    ...[7, 8, 9].map((n) => [String(n), `Speak suggestion ${n - 6}`, () => views.speak.suggestion(n - 7)]),
  ];

  function localKeys() {
    const map = new Map(bindings.map(([k, , fn]) => [k, fn]));
    const onKeydown = (e) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.repeat || isTyping(e.target)) return;
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      const fn = map.get(key);
      if (!fn) return;
      e.preventDefault();
      once(key, fn);
    };
    document.addEventListener('keydown', onKeydown);
    return () => document.removeEventListener('keydown', onKeydown);
  }

  let unbindKeys = () => {};
  import('../shared/keys.js')
    .then((mod) => {
      if (typeof mod.onKey !== 'function') throw new Error('no onKey yet');
      const offs = bindings.map(([k, desc, fn]) => mod.onKey(k.length === 1 ? k.toUpperCase() : k, () => once(k, fn), desc));
      unbindKeys = () => offs.forEach((off) => typeof off === 'function' && off());
    })
    .catch(() => {
      unbindKeys = localKeys();
    });

  renderChrome();
  if (docked) {
    views.history.onOpen?.();
  } else if (initial) openView(initial, { focus: false });

  return {
    open: openView,
    close: closeView,
    toggle: toggleView,
    get active() {
      return active;
    },
    get link() {
      return link;
    },
    destroy() {
      unbindKeys();
      link.close();
      rootEl.remove();
    },
  };
}
