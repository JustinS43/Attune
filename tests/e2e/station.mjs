/*
 * The phone's laptop-station screens (P-35) the way the other pages are checked: layout at both
 * phone sizes, both palettes and the dark theme, WCAG contrast, screen-reader names, visible
 * focus, and no console errors (the mismatch and fallback screens at 390x844). The flow itself
 * (hints, mismatch, fallback, Escape) is checked by
 * tests/pages_engine/station_e2e/phone_station.mjs, which run_e2e.py also runs.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-37.
 *
 *   node station.mjs '{"base": "http://127.0.0.1:8013", "out": "<folder for screenshots>"}'
 *
 * Runs against tests/pages_engine/station_e2e/fake_engine.py: the real hub, save flow and
 * station with fake camera, mic and models. No camera or mic is opened and the "people" are
 * drawn shapes; the fake engine keeps its prints in a temporary folder.
 */

import { ARGS, Report, launch, openPage, sleep, waitFor, layoutAudit, contrastAudit, a11yAudit, focusAudit } from './lib.mjs';

const base = ARGS.base || 'http://127.0.0.1:8013';
const report = new Report(ARGS.out);
const browser = await launch();
const fmt = (a) => (Array.isArray(a) ? a.join(' | ') : a);

const title = (page) => page.evaluate(() => document.querySelector('#st-title')?.textContent || '');
const waitTitle = (page, text, timeout = 30000) => waitFor(async () => (await title(page)).includes(text), { timeout });

/** Press "save this person" for a glasses track from a console link (like key D on the lens). */
async function saveStart(page, trackId) {
  await page.evaluate(({ url, trackId }) => new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'hello', role: 'console', frames: false }));
      ws.send(JSON.stringify({ type: 'command', name: 'save.start', args: { track_id: trackId } }));
      setTimeout(() => { ws.close(); resolve(); }, 300);
    };
    ws.onerror = reject;
  }), { url: `${base.replace(/^http/, 'ws')}/ws`, trackId });
}

async function audit(page, tag, shotName) {
  const shot = await report.shot(page, shotName);
  const lay = await layoutAudit(page, { root: '.st-overlay' });
  report.add(`${tag}: no horizontal scroll`, !lay.overflowX);
  report.add(`${tag}: nothing off the side`, lay.offscreen.length === 0, fmt(lay.offscreen));
  report.add(`${tag}: no overlapping controls`, lay.overlaps.length === 0, fmt(lay.overlaps));
  report.add(`${tag}: text at least 11 px`, lay.tinyText.length === 0, fmt(lay.tinyText));
  report.add(`${tag}: no cut-off text`, lay.clipped.length === 0, fmt(lay.clipped));
  report.checks.at(-1).shot = shot;
  const c = await contrastAudit(page, { root: '.st-overlay' });
  report.add(`${tag}: text contrast meets WCAG AA (${c.checked} checked)`, c.fails.length === 0, fmt(c.fails));
  const a = await a11yAudit(page, { root: '.st-overlay' });
  report.add(`${tag}: every control has a screen-reader name`, a.unnamed.length === 0, fmt(a.unnamed));
  // the dialog's buttons must be reachable without scrolling the page sideways or off the bottom
  const fits = await page.evaluate(() => {
    const vh = window.innerHeight;
    return [...document.querySelectorAll('.st-overlay button')].filter((b) => b.offsetParent).map((b) => {
      const r = b.getBoundingClientRect();
      const scroller = b.closest('.st-overlay');
      return { name: b.textContent.trim(), below: r.bottom > vh + 1, scrollable: scroller.scrollHeight > scroller.clientHeight };
    }).filter((b) => b.below && !b.scrollable).map((b) => b.name);
  });
  report.add(`${tag}: every button is on screen or can be scrolled to`, fits.length === 0, fits);
}

async function startAtLaptop(page, name) {
  await page.click('.tab[data-nav="enroll"]');
  await page.waitForSelector('#station-start', { timeout: 10000 });
  await page.fill('#station-name', name);
  await page.check('#station-consent');
  await page.click('#station-start');
}

async function run(width, height, palette, dark) {
  const tag = `station ${width}x${height}${palette ? ` ${palette}` : ''}${dark ? ' dark' : ''}`;
  const p = await openPage(browser, { width, height, name: 'station' });
  const { page } = p;
  await page.goto(`${base}/phone/${palette ? `?palette=${palette}` : ''}`, { waitUntil: 'load' });
  const live = await waitFor(() => page.evaluate(() => document.querySelector('#app').dataset.link === 'live'), { timeout: 15000 });
  report.add(`${tag}: phone connects to the station engine`, !!live);
  if (dark) {
    await page.click('.tab[data-nav="settings"]');
    await sleep(250);
    await page.click('[data-action="theme"][data-theme="dark"]');
    await sleep(250);
  }
  // the Enroll tab in station mode
  await page.click('.tab[data-nav="enroll"]');
  await page.waitForSelector('#station-start', { timeout: 10000 });
  const tabShot = await report.shot(page, `${tag.replace(/\s+/g, '-')}-enroll-tab`);
  const lay = await layoutAudit(page, { root: '#app' });
  report.add(`${tag} enroll tab: no overlapping controls, nothing tiny or cut off`, !lay.overflowX && lay.overlaps.length + lay.tinyText.length + lay.clipped.length + lay.offscreen.length === 0, fmt([...lay.overlaps, ...lay.tinyText, ...lay.clipped, ...lay.offscreen]));
  report.checks.at(-1).shot = tabShot;
  const tc = await contrastAudit(page, { root: '#app' });
  report.add(`${tag} enroll tab: text contrast meets WCAG AA (${tc.checked} checked)`, tc.fails.length === 0, fmt(tc.fails));
  // a whole save at the laptop: camera, sentence, saved
  await startAtLaptop(page, 'Alex');
  const cam = await waitTitle(page, 'Look at the laptop camera', 15000);
  report.add(`${tag}: Start opens "Look at the laptop camera"`, !!cam, await title(page));
  await page.waitForSelector('.st-cam.has-image', { timeout: 10000 }).catch(() => {});
  await audit(page, `${tag} camera step`, `${tag.replace(/\s+/g, '-')}-camera`);
  const voice = await waitTitle(page, 'Read this sentence', 30000);
  report.add(`${tag}: the sentence step follows`, !!voice, await title(page));
  await sleep(600);
  await audit(page, `${tag} sentence step`, `${tag.replace(/\s+/g, '-')}-sentence`);
  const saved = await waitTitle(page, 'is saved', 30000);
  report.add(`${tag}: the person is saved`, !!saved, await title(page));
  await audit(page, `${tag} saved`, `${tag.replace(/\s+/g, '-')}-saved`);
  const focus = await focusAudit(page, null, { steps: 6 });
  report.add(`${tag} saved: keyboard focus is visible (${focus.tabbed} controls)`, focus.invisible.length === 0, fmt(focus.invisible));
  await page.getByRole('button', { name: 'Done' }).click();
  await waitFor(() => page.evaluate(() => document.querySelector('.st-overlay')?.hidden !== false), { timeout: 5000 });
  if (width === 390 && !palette && !dark) {
    // the identity check fails (the laptop sees someone else): the mismatch screen
    await saveStart(page, 8);
    await page.waitForSelector('#save-consent', { state: 'visible', timeout: 10000 });
    await page.check('#save-consent');
    await page.click('#save-ok');
    const mismatch = await waitTitle(page, "That isn't the person", 40000);
    report.add(`${tag}: a different face at the laptop gets the mismatch screen`, !!mismatch, await title(page));
    if (mismatch) await audit(page, `${tag} mismatch`, `${tag.replace(/\s+/g, '-')}-mismatch`);
    await page.keyboard.press('Escape');
    const cancelled = await waitTitle(page, 'Nothing was saved', 10000);
    report.add(`${tag}: Escape cancels and says nothing was saved`, !!cancelled, await title(page));
    if (cancelled) await page.getByRole('button', { name: 'Close' }).click();
    // after a cancelled save the same person can be asked again at once (the right person may
    // simply not have been at the laptop yet)
    await sleep(500);
    await saveStart(page, 8);
    const again = await page.waitForSelector('#save-consent', { state: 'visible', timeout: 6000 }).then(() => true, () => false);
    report.add(`${tag}: after Escape the same person can be saved again right away`, again, again ? 'the consent sheet came back' : 'nothing on the phone (the engine log says "Save: Ana is already being saved" for 90 s)');
    if (again) {
      await page.click('#save-cancel');
      await waitFor(() => page.evaluate(() => document.querySelector('.save-sheet')?.hidden !== false), { timeout: 5000 });
    }
    // the laptop camera gives nothing: the fallback screen
    await startAtLaptop(page, 'Nocam Test');
    const fallback = await waitTitle(page, "Can't use the laptop camera", 20000);
    report.add(`${tag}: no laptop camera gets a clear fallback screen`, !!fallback, await title(page));
    if (fallback) {
      await audit(page, `${tag} fallback`, `${tag.replace(/\s+/g, '-')}-fallback`);
      await page.getByRole('button', { name: 'Close' }).click();
    }
  }
  report.clean(p, tag);
  await p.context.close();
}

for (const [w, h, palette, dark] of [[390, 844, '', false], [360, 740, '', false], [390, 844, 'apricot', false], [390, 844, '', true]]) {
  await report.guard(`station ${w}x${h}${palette ? ` ${palette}` : ''}${dark ? ' dark' : ''}`, () => run(w, h, palette, dark));
}
await browser.close();
report.done();
