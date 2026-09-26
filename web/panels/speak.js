/*
 * Speak panel (S)
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-10. Contracts: docs/contracts.md (3, 4).
 *
 * A text box (Enter speaks), presets on keys 1-5 from welcome.config.presets, suggested replies
 * on keys 7-9 from `reply_suggestions`, a "speaking..." state and the `reply_spoken` confirmations.
 */

import { h, keycap, section, speakerLabel, speakerColor } from './ui.js';

export const DEFAULT_PRESETS = ['Nice to meet you', 'Can you repeat that?', 'One moment', 'Thank you', 'I read captions, go ahead'];
const MAX_CHARS = 280;
const CONFIRM_TIMEOUT_MS = 12000;
const VOICE_NAME = { elevenlabs: 'ElevenLabs', kokoro: 'Kokoro (offline)' };

export function createSpeak(ctx) {
  const { send, toast } = ctx;
  const st = { pending: null, spoken: [], captions: [] };

  const box = h('textarea', { class: 'atp-input atp-compose', rows: '3', maxlength: String(MAX_CHARS), placeholder: 'Type what you want to say. Enter speaks it.', 'aria-label': 'Message to speak' });
  const count = h('span', { class: 'atp-muted', text: `0 / ${MAX_CHARS}` });
  const speakBtn = h('button', { class: 'atp-btn atp-primary', type: 'button', onclick: () => sayTyped() }, 'Speak ', h('kbd', { class: 'atp-key atp-key-dark', text: '↵' }));
  box.addEventListener('input', () => { count.textContent = `${box.value.length} / ${MAX_CHARS}`; });
  box.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sayTyped();
    }
  });

  const state = h('div', { class: 'atp-speaking', hidden: true, 'aria-live': 'polite' });
  const presetList = h('div', { class: 'atp-replies' });
  const suggestList = h('div', { class: 'atp-replies' });
  const spokenList = h('ul', { class: 'atp-spoken' });
  const contextList = h('ul', { class: 'atp-context' });

  const el = h('div', { class: 'atp-view atp-speak' },
    section('Say it for me', { hint: 'Enter to speak · Shift+Enter new line' }, box, h('div', { class: 'atp-row atp-between' }, count, speakBtn), state),
    section('Suggested replies', { hint: '7 – 9' }, suggestList),
    section('Presets', { hint: '1 – 5' }, presetList),
    section('Conversation', { hint: 'last lines' }, contextList),
    section('Spoken', { hint: 'what the laptop said' }, spokenList),
  );

  function say(text, source) {
    text = String(text || '').trim();
    if (!text) return toast('Type something to say first', 'warn');
    if (text.length > MAX_CHARS) return toast(`Keep it under ${MAX_CHARS} characters`, 'warn');
    send('speak', { text, source });
    clearTimeout(st.pending?.timer);
    st.pending = { text, source, timer: setTimeout(() => { st.pending = null; renderState('timeout'); }, CONFIRM_TIMEOUT_MS) };
    renderState();
  }

  function sayTyped() {
    const text = box.value.trim();
    if (!text) return toast('Type something to say first', 'warn');
    say(text, 'typed');
    box.value = '';
    count.textContent = `0 / ${MAX_CHARS}`;
  }

  function renderState(kind) {
    if (st.pending) {
      state.hidden = false;
      state.className = 'atp-speaking active';
      state.replaceChildren(h('span', { class: 'atp-wave' }, h('i'), h('i'), h('i'), h('i')), h('div', { class: 'atp-grow' }, h('strong', { text: 'Speaking…' }), h('span', { class: 'atp-muted', text: `“${st.pending.text}”` })));
    } else if (kind === 'timeout') {
      state.hidden = false;
      state.className = 'atp-speaking warn';
      state.replaceChildren(h('i', { class: 'atp-dot' }), h('span', { text: 'No confirmation from the speaker yet. Check the engine status.' }));
      setTimeout(() => { if (!st.pending) state.hidden = true; }, 5000);
    } else state.hidden = true;
  }

  function replyButton(key, text, source, empty) {
    const onclick = () => {
      // a suggestion that changed under the pointer must not be spoken by accident
      if (source === 'suggestion' && performance.now() - suggestionsAt < 700) return toast('Suggestions just changed. Click again.', 'warn', 1800);
      say(text, source);
    };
    return h('button', { class: `atp-reply${text ? '' : ' empty'}`, type: 'button', disabled: !text, onclick }, keycap(key), h('span', { text: text || empty }));
  }

  function renderPresets() {
    const presets = ctx.state.presets?.length ? ctx.state.presets : DEFAULT_PRESETS;
    presetList.replaceChildren(...presets.slice(0, 5).map((p, i) => replyButton(String(i + 1), p, 'preset')));
  }

  let suggestionsAt = 0;
  let suggestionsKey = '';
  function renderSuggestions() {
    const opts = ctx.state.suggestions || [];
    const key = JSON.stringify(opts);
    if (key === suggestionsKey) return;
    suggestionsKey = key;
    suggestionsAt = performance.now();
    suggestList.replaceChildren(...[0, 1, 2].map((i) => replyButton(String(7 + i), opts[i], 'suggestion', i === 0 ? 'Suggestions appear after a few lines of talk' : '—')));
  }

  function renderSpoken() {
    spokenList.replaceChildren();
    if (!st.spoken.length) spokenList.append(h('li', { class: 'atp-empty', text: 'Nothing spoken yet this session.' }));
    for (const s of st.spoken) {
      spokenList.append(h('li', {}, h('span', { class: 'atp-grow', text: s.text }), h('span', { class: `atp-voice ${s.voice}`, text: VOICE_NAME[s.voice] || s.voice || 'voice' })));
    }
  }

  function renderContext() {
    contextList.replaceChildren();
    if (!st.captions.length) contextList.append(h('li', { class: 'atp-empty', text: 'Captions show here so you know what you are answering.' }));
    for (const c of st.captions) {
      const label = speakerLabel(c.speaker);
      contextList.append(h('li', { class: c.final === false ? 'draft' : '' },
        h('strong', { style: { color: speakerColor(label) }, text: label }),
        h('span', { text: c.translation || c.text }),
        c.translation && c.lang && c.lang !== 'en' ? h('em', { text: `${c.lang.toUpperCase()} · ${c.text}` }) : null));
    }
  }

  renderPresets();
  renderSuggestions();
  renderSpoken();
  renderContext();
  renderState();

  return {
    el,
    title: 'Speak',
    focus() { box.focus(); },
    preset(i) {
      const presets = ctx.state.presets?.length ? ctx.state.presets : DEFAULT_PRESETS;
      if (presets[i]) say(presets[i], 'preset');
    },
    suggestion(i) {
      const text = (ctx.state.suggestions || [])[i];
      if (text) say(text, 'suggestion');
      else toast('No suggestion on that key yet', 'warn', 1800);
    },
    onMessage(m) {
      switch (m.type) {
        case 'welcome': renderPresets(); break;
        case 'reply_suggestions': renderSuggestions(); break;
        case 'reply_spoken': {
          st.spoken.unshift({ text: m.text, voice: m.voice });
          st.spoken.length = Math.min(st.spoken.length, 8);
          if (st.pending && st.pending.text.trim() === String(m.text || '').trim()) {
            clearTimeout(st.pending.timer);
            st.pending = null;
            renderState();
            toast(`Spoken with ${VOICE_NAME[m.voice] || m.voice}`, m.voice === 'kokoro' ? 'warn' : 'ok', 2200);
          }
          renderSpoken();
          break;
        }
        case 'caption': {
          const i = st.captions.findIndex((c) => c.utt_id === m.utt_id);
          if (i >= 0) st.captions[i] = m;
          else st.captions.push(m);
          if (st.captions.length > 5) st.captions.splice(0, st.captions.length - 5);
          renderContext();
          break;
        }
        case 'forgotten': st.captions = []; renderContext(); break;
        default: break;
      }
    },
  };
}
