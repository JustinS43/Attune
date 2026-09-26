// Phone station screens end to end (P-35), against tests/pages_engine/station_e2e/fake_engine.py.
//
// Usage: node phone_station.mjs <base url, e.g. http://127.0.0.1:8011> [screenshot folder]
// Needs playwright-core (PLAYWRIGHT_CORE = its folder) and Microsoft Edge (channel "msedge").
// A 390x844 phone, apricot palette. Checks, keyboard only where a person would use it:
//  1. Enroll tab -> name, own consent -> "Look at the laptop camera" with the live preview, the
//     oval and hints -> "Read this sentence" with a level meter -> "Alex is saved".
//  2. Double tap on Ana (the laptop sees Sam) -> the save sheet's consent -> "That isn't the
//     person you were looking at" -> Try again -> the same again -> Save as someone new -> saved.
//  3. Double tap on Sam -> saved, linked to the glasses face.
//  4. The laptop camera doesn't start -> the fallback screen, then Escape cancels a save.
// Exit code 1 on the first failure.
//
// Load-tolerant (P-37): every wait is ATTUNE_E2E_SLOW times (default 3) what an idle laptop
// needs, a failure screen ends a wait at once with what it says, and short-lived states (the
// hints, the green face box, the level meter) are recorded as they happen instead of polled.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const base = (process.argv[2] || 'http://127.0.0.1:8011').replace(/\/$/, '');
const shots = process.argv[3] || '';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_CORE || 'playwright-core');
if (shots) fs.mkdirSync(shots, { recursive: true });

const SLOW = Math.max(1, Number(process.env.ATTUNE_E2E_SLOW) || 3);
const T = (ms) => Math.round(ms * SLOW);
// what the station says when a save can't go on; a wait for anything else stops on these
const FAIL_TITLES = [
  "We couldn't see your face clearly",
  "We didn't hear enough",
  "Can't use the laptop camera",
  'Nothing was saved',
  "The laptop didn't answer",
];

let step = 0;
const log = (...a) => console.log(`[${String(++step).padStart(2, '0')}]`, ...a);
function check(ok, what) {
  if (!ok) throw new Error(`FAILED: ${what}`);
  log('ok', what);
}

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true, reducedMotion: 'reduce' });
const page = await context.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
const shot = async (name) => { if (shots) await page.screenshot({ path: path.join(shots, `${name}.png`) }); };
const title = () => page.locator('#st-title').textContent();
async function waitTitle(text, timeout = 30000) {
  const handle = await page.waitForFunction(({ want, bad }) => {
    const t = document.querySelector('#st-title')?.textContent || '';
    if (t.includes(want)) return { ok: true, t };
    const stop = bad.find((b) => t.includes(b) && !b.includes(want) && !want.includes(b));
    return stop ? { ok: false, t } : false;
  }, { want: text, bad: FAIL_TITLES }, { timeout: T(timeout) });
  const got = await handle.jsonValue();
  if (!got.ok) throw new Error(`FAILED: waiting for "${text}", the screen says "${got.t}"`);
}
const dialogOpen = () => page.evaluate(() => { const d = document.querySelector('.st-overlay'); return !!d && !d.hidden; });

/** A console page's WebSocket, to press "save this person" like key D on the lens does. */
function saveStart(trackId) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`${base.replace(/^http/, 'ws')}/ws`);
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'hello', role: 'console', frames: false }));
      ws.send(JSON.stringify({ type: 'command', name: 'save.start', args: { track_id: trackId } }));
      setTimeout(() => { ws.close(); resolve(); }, 300);
    };
    ws.onerror = reject;
  });
}

async function keyboardConsentAndSave() {
  // the person ticks consent themselves; keyboard: focus the box, Space, then Save
  await page.waitForSelector('#save-consent', { state: 'visible', timeout: T(10000) });
  await page.focus('#save-consent');
  await page.keyboard.press('Space');
  await page.focus('#save-ok');
  await page.keyboard.press('Enter');
}

try {
  await page.goto(`${base}/phone/?palette=apricot`);
  await page.waitForFunction(() => document.querySelector('#app')?.dataset.link === 'live', null, { timeout: T(15000) });
  check(true, 'phone connected to the fake engine');
  // every hint, the green face box and the level meter, however briefly they show (the fake
  // camera's face moves into place in ~2 s; on a busy laptop a state can pass between polls)
  await page.evaluate(() => {
    const seen = (window.__seen = { hints: [], ok: false, box: false, mirrored: '', meterMax: 0 });
    new MutationObserver(() => {
      const t = document.querySelector('.st-hint')?.textContent;
      if (t && seen.hints[seen.hints.length - 1] !== t) seen.hints.push(t);
      if (document.querySelector('.st-cam.ok')) {
        seen.ok = true;
        const b = document.querySelector('.st-box');
        if (b && !b.hidden && parseFloat(b.style.width) > 20) seen.box = true;
        const m = document.querySelector('.st-mirror');
        if (m && !seen.mirrored) seen.mirrored = getComputedStyle(m).transform;
      }
      const level = Number(document.querySelector('.st-meter')?.getAttribute('aria-valuenow'));
      if (level > seen.meterMax) seen.meterMax = level;
    }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['class', 'style', 'hidden', 'aria-valuenow'] });
  });
  const sawHint = (re, timeout = 15000) => page.waitForFunction((src) => window.__seen.hints.some((h) => new RegExp(src, 'i').test(h)), re, { timeout: T(timeout) });

  // ---------------------------------------------------------------- 1. Enroll tab
  await page.click('[data-nav="enroll"]');
  await page.waitForSelector('#station-start');
  check(await page.isDisabled('#station-start'), 'Start is disabled before a name and consent');
  check(!(await page.$('#enroll-preview')), 'no glasses-camera photo step in station mode');
  await shot('01-enroll-tab');
  await page.focus('#station-name');
  await page.keyboard.type('Alex');
  await page.keyboard.press('Tab');
  check(await page.evaluate(() => document.activeElement?.id === 'station-consent'), 'Tab moves from the name to the consent box');
  await page.keyboard.press('Space');
  await page.keyboard.press('Tab');
  check(await page.evaluate(() => document.activeElement?.id === 'station-start'), 'Tab reaches Start at the laptop');
  await page.keyboard.press('Enter');
  check(await dialogOpen(), 'the station screen opens');
  const a11y = await page.evaluate(() => {
    const d = document.querySelector('.st-overlay');
    return { role: d.getAttribute('role'), modal: d.getAttribute('aria-modal'), label: d.getAttribute('aria-labelledby'), live: !!document.querySelector('.st-sr[aria-live="polite"]') };
  });
  check(a11y.role === 'dialog' && a11y.modal === 'true' && a11y.label === 'st-title' && a11y.live, 'dialog semantics and a polite live region');
  await waitTitle('Look at the laptop camera');
  await page.waitForFunction(() => document.activeElement?.id === 'st-title', null, { timeout: T(3000) });
  check(true, 'focus moves to the new heading');
  await page.waitForSelector('.st-cam.has-image', { timeout: T(10000) });
  check(true, 'the live preview shows a picture');
  await sawHint('middle');
  check(true, 'hint: move to the middle');
  await shot('02-face');
  await sawHint('closer');
  check(true, 'hint: come closer');
  await page.waitForFunction(() => window.__seen.ok && window.__seen.box, null, { timeout: T(10000) });
  check(true, 'the face box is drawn on the preview');
  const mirrored = await page.evaluate(() => window.__seen.mirrored);
  check(mirrored.startsWith('matrix(-1'), 'the preview is mirrored like a selfie camera');
  await shot('03-face-good');
  // Tab stays inside the dialog
  for (let i = 0; i < 6; i++) await page.keyboard.press('Tab');
  check(await page.evaluate(() => document.querySelector('.st-overlay').contains(document.activeElement)), 'Tab stays inside the dialog');
  await waitTitle('Read this sentence');
  const sentence = await page.locator('.st-sentence').textContent();
  check(sentence.split(' ').length >= 12, `the sentence is shown (${sentence.slice(0, 40)}...)`);
  await page.waitForFunction(() => window.__seen.meterMax > 30, null, { timeout: T(10000) });
  check(true, 'the level meter moves');
  await shot('04-voice');
  await waitTitle('Alex is saved');
  const summary = await page.locator('.st-summary').textContent();
  check(summary.includes('Face saved') && summary.includes('Voice saved'), 'saved: face and voice');
  await shot('05-saved');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter'); // Done
  await page.waitForFunction(() => document.querySelector('.st-overlay').hidden, null, { timeout: T(3000) });
  check(true, 'Done closes the screen');

  // ---------------------------------------------------------------- 2. mismatch
  await saveStart(8); // Ana on the glasses; the laptop camera shows Sam
  await keyboardConsentAndSave();
  await waitTitle('Look at the laptop camera', 10000);
  check(await page.evaluate(() => document.querySelector('.save-sheet').hidden), 'the save sheet hands over to the station screen');
  await waitTitle("That isn't the person you were looking at", 30000);
  await shot('06-mismatch');
  await page.getByRole('button', { name: 'Try again' }).click();
  await waitTitle('Look at the laptop camera', 10000);
  check(true, 'Try again goes back to the camera');
  await waitTitle("That isn't the person you were looking at", 30000);
  await page.getByRole('button', { name: 'Save as someone new' }).click();
  await waitTitle('Read this sentence', 20000);
  await waitTitle('Ana is saved', 30000);
  check(true, 'saved as someone new after the mismatch');
  await page.getByRole('button', { name: 'Done' }).click();

  // ---------------------------------------------------------------- 3. linked save
  await saveStart(7);
  await keyboardConsentAndSave();
  await waitTitle('Read this sentence', 30000);
  check(true, 'no mismatch when the laptop sees the same person');
  await waitTitle('Sam is saved', 30000);
  await page.getByRole('button', { name: 'Done' }).click();

  // ---------------------------------------------------------------- 4. camera missing, Escape
  await page.fill('#station-name', 'Nocam Test');
  await page.check('#station-consent');
  await page.click('#station-start');
  await waitTitle("Can't use the laptop camera", 15000);
  const msg = await page.locator('.st-lead').first().textContent();
  check(/laptop camera/i.test(msg), `fallback message: ${msg}`);
  await shot('07-fallback');
  await page.getByRole('button', { name: 'Close' }).click();
  await page.fill('#station-name', 'Esc Test');
  await page.check('#station-consent');
  await page.click('#station-start');
  await waitTitle('Look at the laptop camera', 10000);
  await page.keyboard.press('Escape');
  await waitTitle('Nothing was saved', 10000);
  check(true, 'Escape cancels, nothing saved');
  await page.getByRole('button', { name: 'Close' }).click();

  const width = await page.evaluate(() => [document.documentElement.scrollWidth, document.querySelector('.st-overlay').scrollWidth]);
  check(width[0] <= 390, `no sideways scrolling (${width[0]} px)`);
  check(errors.length === 0, `no page errors${errors.length ? `: ${errors.join(' | ')}` : ''}`);
  console.log('PASS phone station screens');
} catch (err) {
  console.error(String(err?.stack || err));
  try { console.error('title now:', await title()); } catch {}
  await shot('zz-failure').catch(() => {});
  if (errors.length) console.error('page errors:', errors.join(' | '));
  process.exitCode = 1;
} finally {
  await browser.close();
}
