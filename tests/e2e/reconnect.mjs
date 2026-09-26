/*
 * The engine restarts; every page must notice and recover on its own.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-37.
 *
 * Talks to harness.py over stdin/stdout: prints READY once the pages are live, DOWN once they
 * noticed the engine went away, waits for "UP" on stdin, then checks recovery and prints the result.
 */

import readline from 'node:readline';
import { ARGS, Report, launch, openPage, sleep, waitFor, wsMessages } from './lib.mjs';

const base = ARGS.base || 'http://127.0.0.1:8013';
const report = new Report(ARGS.out);
const browser = await launch();
// the restart itself makes the browser log refused connections: expected here
const ignore = [/WebSocket/i, /ERR_CONNECTION_REFUSED/, /net::ERR_/, /Failed to load resource/];
const lens = await openPage(browser, { width: 1280, height: 720, name: 'lens', ignore });
const phone = await openPage(browser, { width: 390, height: 844, name: 'phone', ignore });
const demo = await openPage(browser, { width: 1366, height: 768, name: 'demo', ignore });
await lens.page.goto(`${base}/lens/?source=live&mode=color`, { waitUntil: 'load' });
await phone.page.goto(`${base}/phone/`, { waitUntil: 'load' });
await demo.page.goto(`${base}/demo/?view=split`, { waitUntil: 'load' });

const state = async () => ({
  lens: await lens.page.evaluate(() => ({ connected: !!window.attuneLens?.store.state.connected, notice: document.querySelector('#notice').hidden ? '' : document.querySelector('#notice-title').textContent })),
  phone: await phone.page.evaluate(() => ({ link: document.querySelector('#app').dataset.link, hint: document.querySelector('.desktop-hint').innerText })),
  demo: await demo.page.evaluate(() => ({ camEnabled: !document.querySelector('#cam-toggle').disabled })),
});

const up = await waitFor(async () => {
  const s = await state();
  return s.lens.connected && s.phone.link === 'live' && s.demo.camEnabled && s;
}, { timeout: 20000 });
if (!up) {
  report.add('reconnect: pages connected before the restart', false, await state());
  report.done();
  process.exit(0);
}
console.log('READY');

const down = await waitFor(async () => {
  const s = await state();
  return !s.lens.connected && s.phone.link === 'offline' && !s.demo.camEnabled && s;
}, { timeout: 25000 });
report.add('reconnect: every page shows it lost the engine', !!down, down || (await state()));
if (down) {
  await report.shot(lens.page, 'reconnect-lens-down');
  await report.shot(phone.page, 'reconnect-phone-down');
}
// a reply typed while offline is queued and spoken once the link is back
await phone.page.click('.tab[data-nav="speak"]');
await sleep(300);
await phone.page.fill('#speak-text', 'Sorry, one moment please.');
await phone.page.click('#speak-button');
const toast = await phone.page.evaluate(() => document.querySelector('#toast').textContent);
report.add('reconnect: offline phone says replies wait for the link', /Offline/i.test(toast), toast);
console.log('DOWN');

const rl = readline.createInterface({ input: process.stdin });
await new Promise((resolve) => rl.on('line', (l) => l.trim() === 'UP' && resolve()));
const t0 = Date.now();
const back = await waitFor(async () => {
  const s = await state();
  return s.lens.connected && !s.lens.notice && s.phone.link === 'live' && s.demo.camEnabled && s;
}, { timeout: 30000, every: 250 });
report.add('reconnect: every page recovers by itself', !!back, back || (await state()));
if (back) report.add('reconnect: recovered within 10 s of the engine being up', Date.now() - t0 < 10000, `${((Date.now() - t0) / 1000).toFixed(1)} s`);
const spoken = await waitFor(async () => (await wsMessages(phone.page, 'reply_spoken')).find((m) => /one moment please/i.test(m.text)), { timeout: 20000 });
report.add('reconnect: the reply queued while offline is spoken after reconnecting', !!spoken);
const framesBack = await waitFor(() => lens.page.evaluate(() => window.__e2e.counts.frame > 0 && document.querySelector('#notice').hidden), { timeout: 10000 });
report.add('reconnect: the glasses get video again', !!framesBack);
// welcome after reconnect carries the new session
const welcomes = (await wsMessages(phone.page, 'welcome')).map((m) => m.session_id);
report.add('reconnect: the phone got a new session welcome', welcomes.length >= 2 && welcomes.at(-1) !== welcomes[0], welcomes);
await report.shot(phone.page, 'reconnect-phone-back');
report.clean(lens, 'reconnect lens');
report.clean(phone, 'reconnect phone');
report.clean(demo, 'reconnect demo');
rl.close();
await browser.close();
report.done();
