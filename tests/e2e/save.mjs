/*
 * "Save this person" through the glasses (P-29): D on the lens is the double tap; the phone shows
 * the consent sheet (or tells the wearer why nobody can be saved). Nothing is enrolled without the
 * person's own tick: this test never ticks it, it cancels.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-37. (The laptop-station enrollment is stream ENROLL's.)
 */

import { ARGS, Report, launch, openPage, sleep, waitFor, wsMessages, wsSent } from './lib.mjs';

const base = ARGS.base || 'http://127.0.0.1:8013';
const report = new Report(ARGS.out);
const browser = await launch();
const lens = await openPage(browser, { width: 1280, height: 720, name: 'lens' });
const phone = await openPage(browser, { width: 390, height: 844, name: 'phone' });
await lens.page.goto(`${base}/lens/?source=live&mode=color`, { waitUntil: 'load' });
await phone.page.goto(`${base}/phone/`, { waitUntil: 'load' });
await waitFor(() => phone.page.evaluate(() => document.querySelector('#app').dataset.link === 'live'), { timeout: 15000 });
await waitFor(() => lens.page.evaluate(() => window.attuneLens?.store.state.connected), { timeout: 15000 });
await sleep(3000);

for (const mode of ['color', 'mono', 'mono-corner']) {
  await lens.page.evaluate((m) => window.attuneLens.setMode(m === 'mono-corner' ? 'corner' : m), mode);
  await sleep(300);
  await lens.page.evaluate(() => document.activeElement?.blur());
  const n0 = (await wsMessages(phone.page, 'save_cancel')).length + (await wsMessages(phone.page, 'save_request')).length;
  await lens.page.keyboard.press('d');
  report.add(`save ${mode}: D sends save.start`, !!(await waitFor(async () => (await wsSent(lens.page, 'save.start')).length > 0, { timeout: 2000 })));
  const answer = await waitFor(async () => {
    const all = [...(await wsMessages(phone.page, 'save_cancel')), ...(await wsMessages(phone.page, 'save_request'))];
    return all.length > n0 && all.sort((a, b) => a._t - b._t).at(-1);
  }, { timeout: 6000 });
  report.add(`save ${mode}: the engine answers the phone (request or reason)`, !!answer, answer && { type: answer.type, reason: answer.reason, name: answer.name });
  await sleep(600);
  const sheet = await phone.page.evaluate(() => {
    const s = document.querySelector('.save-sheet');
    return s && { open: !s.hidden, phase: s.dataset.phase, title: s.querySelector('.save-title')?.textContent, text: s.innerText.slice(0, 200) };
  });
  report.add(`save ${mode}: the phone sheet opens and says what happens`, !!sheet?.open && !!sheet.title, sheet);
  await report.shot(phone.page, `save-${mode}-phone`);
  await report.shot(lens.page, `save-${mode}-lens`);
  // the glasses show their own card: the consent wait, or the same hint as the phone
  const lensSays = await lens.page.evaluate(() => window.attuneLens?.saveFlow?.debug?.() ?? null);
  const agrees = !!lensSays && (answer?.type === 'save_request' ? ['waiting', 'face', 'voice'].includes(lensSays.phase) : lensSays.phase === 'hint');
  report.add(`save ${mode}: the glasses say it too`, agrees, lensSays);
  if (answer?.type === 'save_request') {
    const consent = await phone.page.evaluate(() => ({ box: !!document.querySelector('#save-consent'), okDisabled: document.querySelector('#save-ok')?.disabled }));
    report.add(`save ${mode}: Save stays disabled until the person ticks consent`, consent.box && consent.okDisabled === true, consent);
    await phone.page.click('#save-cancel');
    const cancel = await waitFor(async () => (await wsSent(phone.page, 'save.cancel')).length > 0, { timeout: 3000 });
    report.add(`save ${mode}: Cancel sends save.cancel and nothing is enrolled`, !!cancel && (await wsSent(phone.page, 'enroll.start')).length === 0);
  }
  // let the sheet close before the next try
  await waitFor(() => phone.page.evaluate(() => document.querySelector('.save-sheet')?.hidden !== false), { timeout: 8000 });
  await sleep(500);
}
report.clean(lens, 'save lens');
report.clean(phone, 'save phone');
await browser.close();
report.done();
