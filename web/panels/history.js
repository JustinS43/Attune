/*
 * History panel (Y)
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-11. Contracts: docs/contracts.md (5, History API).
 *
 * Sessions, a timeline coloured per speaker, full-text search with a person filter, the sounds
 * you missed, and a talk-time chart (plain CSS bars). Every call goes to the engine's
 * /api/history routes; a missing or empty history shows a friendly empty state.
 */

import { h, section, clockText, speakerColor, engineHttpBase } from './ui.js';

const KIND_BADGE = { reply: 'Reply', alert: 'Alert', name_confirmed: 'Name', translation: 'Translation' };
const ALERT_ICON = {
  smoke: '🔥', co: '☁', doorbell: '🔔', knock: '🚪', siren: '🚨', horn: '🚗', scream: '😱', glass: '💥',
  baby: '👶', dog: '🐕', phone: '📱', timer: '⏲️', water: '🚰',
};

/** GET /api/history/<path>; resolves null on 404 / network errors so callers show the empty state. */
export async function historyGet(path) {
  try {
    const res = await fetch(`${engineHttpBase()}/api/history/${path}`, { headers: { Accept: 'application/json' } });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/** Row times: wall-clock epoch seconds show as a time of day; engine-clock seconds as m:ss. */
export function rowTime(t) {
  if (typeof t === 'string') {
    const d = new Date(t);
    return Number.isNaN(d.getTime()) ? t : d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  }
  if (typeof t === 'number' && t > 1e9) return new Date(t * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  return clockText(t);
}

function sessionLabel(s) {
  const t = s.started_t;
  if ((typeof t === 'number' && t > 1e9) || typeof t === 'string') {
    const d = new Date(typeof t === 'number' ? t * 1000 : t);
    if (!Number.isNaN(d.getTime())) return d.toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' });
  }
  return `Session ${String(s.session_id).slice(0, 8)}`;
}

export function createHistory(ctx) {
  const st = { sessions: [], current: null, rows: [], query: '', person: '', loading: false, offline: false };

  const search = h('input', { class: 'atp-input', type: 'search', placeholder: 'Search what was said…', 'aria-label': 'Search history' });
  const person = h('select', { class: 'atp-input atp-select', 'aria-label': 'Filter by person' });
  const refresh = h('button', { class: 'atp-icon-btn', type: 'button', title: 'Refresh', 'aria-label': 'Refresh history', text: '↻', onclick: () => load() });
  const sessionsRow = h('div', { class: 'atp-sessions', role: 'tablist', 'aria-label': 'Sessions' });
  const timeline = h('ol', { class: 'atp-timeline' });
  const timelineTitle = h('span', { class: 'atp-card-hint' });
  const missedList = h('ul', { class: 'atp-missed' });
  const talkChart = h('div', { class: 'atp-talk' });
  const body = h('div', { class: 'atp-history-body' });

  const el = h('div', { class: 'atp-view atp-history' },
    h('div', { class: 'atp-search' }, h('span', { class: 'atp-search-icon', 'aria-hidden': 'true', text: '⌕' }), search, person, refresh),
    sessionsRow, body);

  const timelineCard = section('Timeline', {}, timeline);
  timelineCard.querySelector('.atp-card-head').append(timelineTitle);
  const timelineHeading = timelineCard.querySelector('h3');
  const missedCard = section('Sounds you missed', {}, missedList);
  const talkCard = section('Talk time', { hint: 'per speaker' }, talkChart);

  let debounce;
  search.addEventListener('input', () => {
    clearTimeout(debounce);
    debounce = setTimeout(runSearch, 300);
  });
  search.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { clearTimeout(debounce); runSearch(); }
  });
  person.addEventListener('change', () => runSearch());

  function renderPersonOptions() {
    const names = new Set();
    for (const p of ctx.state.people || []) if (p.name) names.add(p.name);
    for (const r of st.rows) if (r.speaker_label && !/^you/i.test(r.speaker_label)) names.add(r.speaker_label);
    const current = person.value;
    person.replaceChildren(h('option', { value: '', text: 'Everyone' }), ...[...names].sort().map((n) => h('option', { value: n, text: n })));
    person.value = [...names].includes(current) ? current : '';
  }

  function emptyState(title, sub) {
    return h('div', { class: 'atp-empty-state' }, h('div', { class: 'atp-empty-art', 'aria-hidden': 'true' }, h('i'), h('i')), h('strong', { text: title }), h('span', { text: sub }));
  }

  function renderSessions() {
    sessionsRow.replaceChildren(...st.sessions.map((s) => h('button', {
      type: 'button', role: 'tab', class: `atp-session${st.current === s.session_id ? ' on' : ''}`, 'aria-selected': String(st.current === s.session_id),
      onclick: () => openSession(s.session_id),
    }, h('strong', { text: sessionLabel(s) }), h('span', { text: `${s.lines ?? 0} lines${s.ended_t ? '' : ' · live'}` }))));
  }

  function renderRows(rows, highlight) {
    timeline.replaceChildren();
    if (!rows.length) {
      timeline.append(h('li', { class: 'atp-empty', text: highlight ? 'Nothing matches that search.' : 'No lines in this session yet.' }));
      return;
    }
    for (const r of rows) {
      const label = r.speaker_label || (r.kind === 'alert' ? 'Sound' : 'Someone');
      const color = r.kind === 'alert' ? '#FF4D4F' : speakerColor(label);
      const text = h('p', {});
      appendHighlighted(text, r.text || '', highlight);
      timeline.append(h('li', { class: `atp-tl atp-tl-${r.kind || 'caption'}`, style: { '--spk': color } },
        h('span', { class: 'atp-tl-dot' }),
        h('div', { class: 'atp-grow' },
          h('div', { class: 'atp-tl-head' }, h('strong', { text: label }), KIND_BADGE[r.kind] ? h('span', { class: 'atp-badge on', text: KIND_BADGE[r.kind] }) : null,
            r.lang && r.lang !== 'en' ? h('span', { class: 'atp-badge', text: r.lang.toUpperCase() }) : null, h('time', { text: rowTime(r.t) })),
          text,
          r.translation ? h('p', { class: 'atp-tl-trans', text: r.translation }) : null)));
    }
  }

  function appendHighlighted(node, text, q) {
    if (!q) return node.append(text);
    const lower = text.toLowerCase();
    const needle = q.toLowerCase();
    let i = 0;
    for (;;) {
      const j = lower.indexOf(needle, i);
      if (j < 0) break;
      node.append(text.slice(i, j), h('mark', { text: text.slice(j, j + needle.length) }));
      i = j + needle.length;
    }
    node.append(text.slice(i));
  }

  function renderMissed(rows) {
    missedList.replaceChildren();
    if (!rows?.length) {
      missedList.append(h('li', { class: 'atp-empty', text: 'No sound alerts in this session.' }));
      return;
    }
    for (const r of rows) {
      const kind = r.kind === 'alert' ? String(r.text || '').split(/\s/)[0].toLowerCase() : r.kind;
      missedList.append(h('li', {}, h('span', { class: 'atp-missed-icon', text: ALERT_ICON[kind] || ALERT_ICON[r.alert_kind] || '◉' }),
        h('div', { class: 'atp-grow' }, h('strong', { text: r.text || r.kind || 'Sound' }), r.speaker_label ? h('span', { class: 'atp-muted', text: r.speaker_label }) : null),
        h('time', { text: rowTime(r.t) })));
    }
  }

  function renderTalk(data) {
    talkChart.replaceChildren();
    const entries = Object.entries(data || {}).map(([label, mins]) => ({ label, mins: Array.isArray(mins) ? mins : [], total: (Array.isArray(mins) ? mins : []).reduce((a, m) => a + (Number(m.seconds) || 0), 0) }))
      .filter((e) => e.total > 0).sort((a, b) => b.total - a.total);
    if (!entries.length) {
      talkChart.append(h('div', { class: 'atp-empty', text: 'Talk time shows once people have spoken.' }));
      return;
    }
    const max = entries[0].total;
    const lastMinute = Math.max(...entries.flatMap((e) => e.mins.map((m) => Number(m.minute) || 0)), 0);
    const peak = Math.max(...entries.flatMap((e) => e.mins.map((m) => Number(m.seconds) || 0)), 1);
    for (const e of entries) {
      const color = speakerColor(e.label);
      const perMinute = new Array(lastMinute + 1).fill(0);
      for (const m of e.mins) perMinute[Number(m.minute) || 0] += Number(m.seconds) || 0;
      talkChart.append(h('div', { class: 'atp-talk-row', style: { '--spk': color } },
        h('div', { class: 'atp-talk-head' }, h('strong', { text: e.label }), h('span', { text: `${Math.floor(e.total / 60)}m ${Math.round(e.total % 60)}s` })),
        h('div', { class: 'atp-talk-bar', role: 'img', 'aria-label': `${e.label}: ${Math.round(e.total)} seconds` }, h('i', { style: { width: `${Math.max(3, (e.total / max) * 100)}%` } })),
        h('div', { class: 'atp-talk-minutes', 'aria-hidden': 'true' }, perMinute.map((s) => h('i', { style: { height: `${Math.max(8, (s / peak) * 100)}%`, opacity: s ? 1 : 0.18 }, title: `${Math.round(s)} s` })))));
    }
  }

  function showBody(...cards) {
    body.replaceChildren(...cards);
  }

  async function openSession(id) {
    st.current = id;
    renderSessions();
    timelineHeading.textContent = 'Timeline';
    timelineTitle.textContent = 'Loading…';
    showBody(talkCard, missedCard, timelineCard);
    const enc = encodeURIComponent(id);
    const [rows, missed, talk] = await Promise.all([historyGet(`sessions/${enc}`), historyGet(`missed?session=${enc}`), historyGet(`talktime?session=${enc}`)]);
    if (st.current !== id) return;
    st.rows = Array.isArray(rows) ? rows : rows?.rows || [];
    timelineTitle.textContent = `${st.rows.length} lines`;
    renderRows(st.rows);
    renderMissed(Array.isArray(missed) ? missed : missed?.rows);
    renderTalk(talk);
    renderPersonOptions();
  }

  async function runSearch() {
    const q = search.value.trim();
    const who = person.value;
    if (!q && !who) {
      if (st.current) openSession(st.current);
      return;
    }
    const params = new URLSearchParams();
    params.set('q', q);
    if (who) params.set('person', who);
    timelineHeading.textContent = 'Search results';
    timelineTitle.textContent = 'Searching…';
    showBody(timelineCard);
    const rows = await historyGet(`search?${params}`);
    const list = Array.isArray(rows) ? rows : rows?.rows || [];
    timelineTitle.textContent = `${list.length} match${list.length === 1 ? '' : 'es'}${who ? ` · ${who}` : ''}`;
    renderRows(list, q);
  }

  async function load() {
    if (st.loading) return;
    st.loading = true;
    refresh.classList.add('spin');
    const sessions = await historyGet('sessions');
    st.loading = false;
    refresh.classList.remove('spin');
    renderPersonOptions();
    if (!Array.isArray(sessions)) {
      st.sessions = [];
      renderSessions();
      showBody(emptyState('History starts when the engine runs', 'Conversations are saved on this laptop for 24 hours. Nothing is saved yet, or the engine is not reachable.'));
      return;
    }
    st.sessions = sessions.slice().sort((a, b) => (Number(b.started_t) || 0) - (Number(a.started_t) || 0));
    renderSessions();
    if (!st.sessions.length) {
      showBody(emptyState('No conversations yet', 'Once people talk, their captions, your replies and any alerts appear here for 24 hours.'));
      return;
    }
    const keep = st.sessions.some((s) => s.session_id === st.current) ? st.current : st.sessions[0].session_id;
    if (search.value.trim() || person.value) {
      st.current = keep;
      renderSessions();
      runSearch();
    } else openSession(keep);
  }

  showBody(emptyState('History', 'Open this panel to load the saved conversations.'));
  renderPersonOptions();

  return {
    el,
    title: 'History',
    onOpen() { load(); },
    focus() { search.focus(); },
    onMessage(m) {
      if (m.type === 'people') renderPersonOptions();
      if (m.type === 'forgotten' && ctx.isOpen('history')) setTimeout(load, 600);
    },
  };
}
