/*
 * The phone's ElevenLabs settings form (P-41, Settings → Speak for me) against a real engine:
 * a saved key is never shown back (page, inputs, storage, URL, WebSocket messages, API
 * answers), a page on another origin can't read or change it, the form works inside the demo
 * page's phone frame, and the ?demo phone never calls the settings API.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-37.
 *
 *   node settings.mjs '{"base": "http://127.0.0.1:8013", "other": "http://localhost:8013",
 *                       "key": "<dummy>", "old": "<key already in .env>", "voice": "...",
 *                       "frameVoice": "...", "out": "<folder for screenshots>"}'
 *
 * Only ever run against a test engine whose working folder is a temporary one: the form writes
 * that folder's .env. The keys are dummies; ElevenLabs is off in the test engine's config.
 */

import { ARGS, Report, launch, openPage, sleep, waitFor, layoutAudit, contrastAudit, a11yAudit } from './lib.mjs';

const { base, other, key, old, voice, frameVoice } = ARGS;
const report = new Report(ARGS.out);
const browser = await launch();
const fmt = (a) => (Array.isArray(a) ? a.join(' | ') : a);
const API = '/api/settings/elevenlabs';

const FORM = '.speech-settings-form';
const status = (t) => t.evaluate((f) => document.querySelector(`${f} [data-status]`)?.textContent || '', FORM);

async function openSettings(t) {
  await t.click('.tab[data-nav="settings"]');
  await t.waitForSelector(FORM, { timeout: 10000 });
  return waitFor(async () => !/Loading/.test(await status(t)), { timeout: 10000 });
}

/** Everywhere a page could let a secret slip: markup, input values, storage, URL, WebSocket traffic. */
function leaks(t, secrets) {
  return t.evaluate((secrets) => {
    const where = [];
    for (const s of secrets) {
      if (document.documentElement.outerHTML.includes(s)) where.push(`${s.slice(0, 6)}… in the page`);
      for (const i of document.querySelectorAll('input, textarea')) if ((i.value || '').includes(s)) where.push(`${s.slice(0, 6)}… in #${i.id || i.name}`);
      try {
        for (const st of [localStorage, sessionStorage]) for (let k = 0; k < st.length; k++) if ((st.getItem(st.key(k)) || '').includes(s)) where.push(`${s.slice(0, 6)}… in web storage`);
      } catch {
        /* storage off */
      }
      if (location.href.includes(s)) where.push(`${s.slice(0, 6)}… in the URL`);
      if (JSON.stringify(window.__e2e || {}).includes(s)) where.push(`${s.slice(0, 6)}… in WebSocket traffic`);
    }
    return where;
  }, secrets);
}

/** The settings API's answers as the page saw them. */
function tapApi(page) {
  const seen = [];
  page.on('response', async (res) => {
    if (!res.url().includes(API)) return;
    let body = '';
    try {
      body = await res.text();
    } catch {
      /* no body */
    }
    seen.push({ method: res.request().method(), status: res.status(), body });
  });
  return seen;
}

async function auditForm(t, page, tag) {
  const shot = await report.shot(page, tag.replace(/\s+/g, '-'));
  const lay = await layoutAudit(t, { root: '.speech-settings' });
  report.add(`${tag}: form fits (no overflow, overlaps, tiny or cut-off text)`, !lay.overflowX && lay.offscreen.length + lay.overlaps.length + lay.tinyText.length + lay.clipped.length === 0, fmt([...lay.offscreen, ...lay.overlaps, ...lay.tinyText, ...lay.clipped]), { shot });
  const c = await contrastAudit(t, { root: '.speech-settings' });
  report.add(`${tag}: form text contrast meets WCAG AA (${c.checked} checked)`, c.fails.length === 0, fmt(c.fails));
  const a = await a11yAudit(t, { root: '.speech-settings' });
  report.add(`${tag}: every field and button has a screen-reader name`, a.unnamed.length === 0, fmt(a.unnamed));
}

// ------------------------------------------------------------------ 1. the laptop's own phone page
await report.guard('settings phone', async () => {
  const p = await openPage(browser, { width: 390, height: 844, name: 'settings' });
  const { page } = p;
  const api = tapApi(page);
  await page.goto(`${base}/phone/`, { waitUntil: 'load' });
  await waitFor(() => page.evaluate(() => document.querySelector('#app').dataset.link === 'live'), { timeout: 15000 });
  await openSettings(page);
  const before = await status(page);
  report.add('settings: a key already in .env shows as configured, never as text', /API key configured/.test(before), before);
  report.add('settings: the key field starts empty', (await page.inputValue('#elevenlabs-key')) === '');
  report.add('settings: the key field is a password field (masked while typing)', (await page.getAttribute('#elevenlabs-key', 'type')) === 'password');
  await auditForm(page, page, 'settings 390x844');
  // save a (dummy) key and a voice id
  await page.fill('#elevenlabs-key', key);
  await page.fill('#elevenlabs-voice', voice);
  await page.click(`${FORM} button[type="submit"]`);
  const saved = await waitFor(async () => /Saved on this laptop/.test(await status(page)), { timeout: 10000 });
  report.add('settings: Save stores the details and says to restart', !!saved, await status(page));
  report.add('settings: the key field is cleared after saving', (await page.inputValue('#elevenlabs-key')) === '');
  report.add('settings: the field then says a key is configured', /Key configured/.test((await page.getAttribute('#elevenlabs-key', 'placeholder')) || ''));
  let where = await leaks(page, [key, old]);
  report.add('settings: the saved key appears nowhere on the page (markup, fields, storage, URL, WebSocket)', where.length === 0, where);
  // come back later: still configured, still never shown
  await page.reload({ waitUntil: 'load' });
  await waitFor(() => page.evaluate(() => document.querySelector('#app').dataset.link === 'live'), { timeout: 15000 });
  await openSettings(page);
  const again = await status(page);
  report.add('settings: after a reload it says "configured … never shown here"', /never shown here/.test(again), again);
  report.add('settings: after a reload the key field is empty and the voice id is back', (await page.inputValue('#elevenlabs-key')) === '' && (await page.inputValue('#elevenlabs-voice')) === voice, await page.inputValue('#elevenlabs-voice'));
  where = await leaks(page, [key, old]);
  report.add('settings: after a reload the key still appears nowhere', where.length === 0, where);
  await sleep(300);
  const bodies = api.filter((r) => r.body.includes(key) || r.body.includes(old));
  report.add(`settings: no API answer contains a key (${api.length} answers)`, api.length >= 2 && bodies.length === 0, api.map((r) => `${r.method} ${r.status}`));
  report.clean(p, 'settings phone');
  await p.context.close();
});

// ------------------------------------------------------------------ 2. a page on another origin
await report.guard('settings other origin', async () => {
  // http://localhost:8013 is a different origin from http://127.0.0.1:8013 (as a rebinding or
  // another local web page would be); it must not read or change the key
  const p = await openPage(browser, { width: 390, height: 844, name: 'settings-other', ignore: [/elevenlabs|CORS|Failed to fetch|net::ERR_FAILED|403/i] });
  const { page } = p;
  await page.goto(`${other}/phone/?demo`, { waitUntil: 'load' });
  const tries = await page.evaluate(async ({ base, API }) => {
    const out = {};
    const attempt = async (name, init) => {
      try {
        const r = await fetch(base + API, init);
        out[name] = r.type === 'opaque' ? 'sent (opaque, unreadable)' : `read ${r.status}`;
      } catch (err) {
        out[name] = `blocked (${err.name})`;
      }
    };
    await attempt('read', {});
    await attempt('save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: 'e2e_attacker_key' }) });
    await attempt('save_no_cors', { method: 'POST', mode: 'no-cors', body: JSON.stringify({ api_key: 'e2e_attacker_key' }) });
    return out;
  }, { base, API });
  report.add('settings: a page on another origin can\'t read the settings', /blocked/.test(tries.read), tries);
  report.add('settings: a page on another origin can\'t save a key (the engine refuses it; .env is checked after)', /blocked|opaque/.test(tries.save) && /blocked|opaque/.test(tries.save_no_cors), tries);
  report.clean(p, 'settings other origin');
  await p.context.close();
});

// ------------------------------------------------------------------ 3. inside the demo page's phone frame
await report.guard('settings demo frame', async () => {
  const p = await openPage(browser, { width: 1366, height: 820, name: 'settings-demo' });
  const { page } = p;
  const api = tapApi(page);
  await page.goto(`${base}/demo/`, { waitUntil: 'load' });
  await page.evaluate(() => window.attuneDemo.setView('phone'));
  const frame = await waitFor(() => page.frame({ url: /\/phone\// }), { timeout: 10000 });
  await waitFor(() => frame.evaluate(() => document.querySelector('#app')?.dataset.link === 'live'), { timeout: 15000 });
  await openSettings(frame);
  const st = await status(frame);
  report.add('settings demo frame: the form loads inside the demo page', /never shown here/.test(st), st);
  await auditForm(frame, page, 'settings demo frame');
  // a new voice id only: an empty key field keeps the saved key
  await frame.fill('#elevenlabs-voice', frameVoice);
  await frame.click(`${FORM} button[type="submit"]`);
  const saved = await waitFor(async () => /Saved on this laptop/.test(await status(frame)), { timeout: 10000 });
  report.add('settings demo frame: saving works inside the frame (voice only; the key is kept)', !!saved, await status(frame));
  const where = await leaks(frame, [key, old]);
  report.add('settings demo frame: the key appears nowhere in the frame', where.length === 0, where);
  await sleep(300);
  report.add('settings demo frame: no API answer contains a key', api.length >= 2 && !api.some((r) => r.body.includes(key) || r.body.includes(old)), api.map((r) => `${r.method} ${r.status}`));
  report.clean(p, 'settings demo frame');
  await p.context.close();
});

// ------------------------------------------------------------------ 4. the ?demo phone (no engine) never calls it
await report.guard('settings ?demo', async () => {
  const p = await openPage(browser, { width: 390, height: 844, name: 'settings-demo-mode' });
  const { page } = p;
  const calls = [];
  page.on('request', (r) => { if (r.url().includes(API)) calls.push(r.method()); });
  await page.goto(`${base}/phone/?demo`, { waitUntil: 'load' });
  await sleep(600);
  await page.click('.tab[data-nav="settings"]');
  await page.waitForSelector(FORM, { timeout: 10000 });
  await sleep(500);
  const st = await status(page);
  report.add('settings ?demo: says it is a demo and never calls the settings API', /Demo only/.test(st) && calls.length === 0, { status: st, calls });
  report.clean(p, 'settings ?demo');
  await p.context.close();
});

await browser.close();
report.done();
