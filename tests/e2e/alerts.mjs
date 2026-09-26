/*
 * Sound alerts on the glasses and the phone, from a replayed alarm recording (never played aloud:
 * the engine reads the WAV, and this browser is muted).
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-37.
 *
 *   node alerts.mjs '{"base": "...", "kind": "smoke", "side": "left", "timeout": 45}'
 */

import { ARGS, Report, launch, openPage, sleep, waitFor, wsMessages, wsSent } from './lib.mjs';

const base = ARGS.base || 'http://127.0.0.1:8013';
const kind = ARGS.kind || 'smoke';
const side = ARGS.side || 'left';
const report = new Report(ARGS.out);
const browser = await launch();
const TEXT = { smoke: 'Smoke alarm', co: 'Carbon monoxide alarm', doorbell: 'Doorbell' };

const lens = await openPage(browser, { width: 1280, height: 720, name: 'lens' });
const phone = await openPage(browser, { width: 390, height: 844, name: 'phone' });
await lens.page.goto(`${base}/lens/?source=live&mode=color`, { waitUntil: 'load' });
await phone.page.goto(`${base}/phone/`, { waitUntil: 'load' });
await waitFor(() => phone.page.evaluate(() => document.querySelector('#app').dataset.link === 'live'), { timeout: 15000 });

const start = await waitFor(async () => (await wsMessages(phone.page, 'alert')).find((m) => m.state === 'start'), { timeout: (ARGS.timeout || 45) * 1000 });
report.add(`alert ${kind}: engine raises an alert from the recording`, !!start, start || 'no alert');
if (start) {
  report.add(`alert ${kind}: right kind`, start.kind === kind, start.kind);
  report.add(`alert ${kind}: right side (${side})`, start.side === side, start.side);
  await sleep(700);
  const lv = await lens.page.evaluate(() => {
    const a = window.attuneLens?.view?.activeAlert;
    return a && { kind: a.kind, side: a.side, label: a.label };
  });
  report.add(`alert ${kind}: shown on the glasses`, lv?.kind === kind, lv);
  await report.shot(lens.page, `alert-${kind}-lens`);
  const banner = await phone.page.evaluate(() => {
    const b = document.querySelector('#alert-banner');
    return { show: b.classList.contains('show'), text: b.innerText };
  });
  report.add(`alert ${kind}: phone banner says "${TEXT[kind]}"`, banner.show && banner.text.includes(TEXT[kind]), banner);
  report.add(`alert ${kind}: phone banner gives the side`, banner.text.includes(side === 'left' ? 'on your left' : side === 'right' ? 'on your right' : 'nearby'), banner.text);
  await report.shot(phone.page, `alert-${kind}-phone`);
  // "Got it" on the phone acknowledges it everywhere
  await phone.page.click('#alert-banner .alert-ack');
  const acked = await waitFor(async () => (await wsMessages(lens.page, 'alert')).find((m) => m.alert_id === start.alert_id && m.state === 'acknowledged'), { timeout: 5000 });
  report.add(`alert ${kind}: "Got it" on the phone sends alert.ack`, (await wsSent(phone.page, 'alert.ack')).some((s) => s.args.alert_id === start.alert_id));
  report.add(`alert ${kind}: the engine confirms (acknowledged reaches the glasses)`, !!acked);
  await sleep(1500);
  const after = await lens.page.evaluate(() => window.attuneLens?.view?.activeAlert?.kind ?? null);
  report.add(`alert ${kind}: glasses stop showing it as active`, after === null, after);
}
report.clean(lens, `alert ${kind} lens`);
report.clean(phone, `alert ${kind} phone`);
await browser.close();
report.done();
