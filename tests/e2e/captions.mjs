/*
 * Captions reach the glasses and the phone: records what each page received and rendered while
 * the engine replays a WAV with known text. harness.py compares it with the script.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-37.
 *
 *   node captions.mjs '{"base": "...", "seconds": 45, "out": "..."}'
 *
 * Prints {ok, lens: {received, rendered, sr}, phone: {received, rendered}, checks}.
 */

import { ARGS, Report, launch, openPage, sleep, waitFor } from './lib.mjs';

const base = ARGS.base || 'http://127.0.0.1:8013';
const seconds = ARGS.seconds || 45;
const report = new Report(ARGS.out);
const browser = await launch();

const lens = await openPage(browser, { width: 1280, height: 720, name: 'lens' });
const phone = await openPage(browser, { width: 390, height: 844, name: 'phone' });
await lens.page.goto(`${base}/lens/?source=live&mode=color`, { waitUntil: 'load' });
await phone.page.goto(`${base}/phone/`, { waitUntil: 'load' });
report.add('captions: lens connected', !!(await waitFor(() => lens.page.evaluate(() => window.attuneLens?.store.state.connected), { timeout: 15000 })));
report.add('captions: phone connected', !!(await waitFor(() => phone.page.evaluate(() => document.querySelector('#app').dataset.link === 'live'), { timeout: 15000 })));
await phone.page.click('[data-action="live"]');
console.log('READY'); // harness: the pages listen now

// sample the lens view model (what the glasses draw) and the screen-reader line
await lens.page.evaluate(() => {
  const rec = (window.__rendered = { utts: {}, order: [], sr: [] });
  const sr = document.querySelector('#captions-live');
  new MutationObserver(() => {
    const t = sr.textContent.trim();
    if (t && rec.sr.at(-1) !== t) rec.sr.push(t);
  }).observe(sr, { childList: true, characterData: true, subtree: true });
  setInterval(() => {
    const v = window.attuneLens?.view;
    for (const u of v?.utterances || []) {
      const text = (u.tokens || []).map((t) => t.text).join(' ').trim();
      if (!text) continue;
      if (!(u.id in rec.utts)) rec.order.push(u.id);
      const prev = rec.utts[u.id];
      rec.utts[u.id] = { text: prev?.final && !u.final ? prev.text : text, final: !!u.final || !!prev?.final, maxAlpha: Math.max(prev?.maxAlpha || 0, u.alpha || 0) };
    }
  }, 150);
});
// the phone's live feed, sampled the same way (the last 12 captions are on screen)
await phone.page.evaluate(() => {
  const rec = (window.__rendered = { cards: [] });
  setInterval(() => {
    const cards = [...document.querySelectorAll('#live-feed .live-caption')].map((c) => ({
      who: c.querySelector('.live-name')?.textContent,
      text: c.querySelector('p')?.textContent,
      draft: c.classList.contains('draft'),
    }));
    rec.last = cards;
    for (const c of cards) if (!c.draft && !rec.cards.some((x) => x.text === c.text && x.who === c.who)) rec.cards.push(c);
  }, 200);
});

const end = Date.now() + seconds * 1000;
let shots = 0;
while (Date.now() < end) {
  await sleep(1000);
  if (shots < 3 && (await lens.page.evaluate(() => (window.attuneLens?.view?.utterances || []).length > 0))) {
    await report.shot(lens.page, `captions-lens-${shots}`);
    await report.shot(phone.page, `captions-phone-${shots}`);
    shots++;
    await sleep(4000);
  }
}

const finals = (msgs) => {
  const byId = new Map();
  for (const m of msgs) {
    if (m.type === 'caption') byId.set(m.utt_id, { ...(byId.get(m.utt_id) || {}), ...m, retracted: false });
    if (m.type === 'caption_retract' && byId.has(m.utt_id)) byId.get(m.utt_id).retracted = true;
  }
  return [...byId.values()].filter((c) => c.final && !c.retracted).map((c) => ({ utt_id: c.utt_id, text: c.text, speaker: c.speaker?.label, kind: c.speaker?.kind }));
};
const lensMsgs = await lens.page.evaluate(() => window.__e2e.msgs);
const phoneMsgs = await phone.page.evaluate(() => window.__e2e.msgs);
const lensRendered = await lens.page.evaluate(() => window.__rendered);
const phoneRendered = await phone.page.evaluate(() => window.__rendered);
report.clean(lens, 'captions lens');
report.clean(phone, 'captions phone');
await browser.close();
report.done({
  lens: {
    received: finals(lensMsgs),
    rendered: lensRendered.order.map((id) => ({ id, ...lensRendered.utts[id] })),
    sr: lensRendered.sr,
  },
  phone: { received: finals(phoneMsgs), rendered: phoneRendered.cards, onScreen: phoneRendered.last },
});
