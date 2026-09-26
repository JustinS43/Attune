/*
 * Every page, every view, every key: loads cleanly, reacts, lays out at the target sizes, reads
 * well (contrast), shows keyboard focus and names its controls for screen readers.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-37.
 *
 *   node pages.mjs '{"base": "http://127.0.0.1:8013", "out": "<folder for screenshots>", "only": ["demo", "lens"]}'
 *
 * Runs against a live engine (harness.py starts one in replay mode).
 */

import {
  ARGS, Report, launch, openPage, sleep, waitFor, layoutAudit, contrastAudit, a11yAudit, focusAudit,
  canvasPainted, wsMessages, wsSent, hiddenFocusAudit,
} from './lib.mjs';

const base = ARGS.base || 'http://127.0.0.1:8013';
const only = new Set(ARGS.only || ['demo', 'lens', 'pov', 'guide', 'phone', 'panels']);
const report = new Report(ARGS.out);
const browser = await launch();

const fmt = (a) => (Array.isArray(a) ? a.join(' | ') : a);
function layoutChecks(prefix, audit, { tiny = true } = {}) {
  report.add(`${prefix}: no horizontal scroll`, !audit.overflowX);
  report.add(`${prefix}: nothing off the side`, audit.offscreen.length === 0, fmt(audit.offscreen));
  report.add(`${prefix}: no overlapping controls`, audit.overlaps.length === 0, fmt(audit.overlaps));
  if (tiny) report.add(`${prefix}: text at least 11 px`, audit.tinyText.length === 0, fmt(audit.tinyText));
  report.add(`${prefix}: no cut-off text`, audit.clipped.length === 0, fmt(audit.clipped));
}
function a11yChecks(prefix, a) {
  report.add(`${prefix}: every control has a screen-reader name`, a.unnamed.length === 0, fmt(a.unnamed));
  report.add(`${prefix}: images have alt text`, a.imgNoAlt.length === 0, fmt(a.imgNoAlt));
  report.add(`${prefix}: frames have titles`, a.frameNoTitle.length === 0, fmt(a.frameNoTitle));
}
function contrastCheck(prefix, c) {
  report.add(`${prefix}: text contrast meets WCAG AA (${c.checked} checked)`, c.fails.length === 0, fmt(c.fails));
}

// ============================================================================ demo
async function demo() {
  const p = await openPage(browser, { width: 1366, height: 768, name: 'demo' });
  const { page } = p;
  await page.goto(`${base}/demo/?view=split`, { waitUntil: 'load' });
  await waitFor(() => page.evaluate(() => !document.querySelector('#cam-toggle').disabled), { timeout: 15000 });
  await sleep(2500);
  report.add('demo: camera switch enabled once connected', await page.evaluate(() => !document.querySelector('#cam-toggle').disabled));
  const lensFrame = page.frame({ url: /\/lens\// });
  const phoneFrame = page.frame({ url: /\/phone\// });
  report.add('demo: glasses and phone frames loaded', !!lensFrame && !!phoneFrame);
  const views = ['split', 'lens', 'phone', 'guide', 'pov'];
  // buttons
  for (const v of views) {
    await page.click(`.views button[data-view="${v}"]`);
    await sleep(v === 'pov' ? 1500 : 500);
    const got = await page.evaluate(() => ({ view: document.body.dataset.view, checked: document.querySelector('.views button[aria-checked="true"]')?.dataset.view }));
    report.add(`demo: button shows view ${v}`, got.view === v && (v === 'pov' || got.checked === v), got);
    if (v !== 'pov') {
      const shot = await report.shot(page, `demo-1366-${v}`);
      layoutChecks(`demo 1366x768 ${v}`, await layoutAudit(page, { root: v === 'guide' ? '#guide' : 'body' }), { tiny: v !== 'guide' });
      report.checks.at(-1).shot = shot;
    }
    const hidden = await hiddenFocusAudit(page);
    report.add(`demo ${v}: hidden panes are out of the keyboard order`, hidden.length === 0, fmt(hidden));
  }
  // Esc leaves the POV
  await page.keyboard.press('Escape');
  await sleep(400);
  report.add('demo: Esc leaves the Glasses POV', await page.evaluate(() => document.body.dataset.view !== 'pov'));
  // the ` key cycles
  await page.click('.views button[data-view="split"]');
  await page.evaluate(() => document.activeElement?.blur());
  const seq = [];
  for (let i = 0; i < 5; i++) {
    await page.keyboard.press('Backquote');
    await sleep(350);
    seq.push(await page.evaluate(() => document.body.dataset.view));
  }
  report.add('demo: ` cycles lens, phone, guide, pov, split', fmt(seq) === 'lens | phone | guide | pov | split', seq);
  // Alt+1..5, also with the focus inside the glasses frame
  for (let i = 1; i <= 5; i++) {
    await page.keyboard.press(`Alt+Digit${i}`);
    await sleep(350);
    const v = await page.evaluate(() => document.body.dataset.view);
    report.add(`demo: Alt+${i} shows ${views[i - 1]}`, v === views[i - 1], v);
  }
  await page.keyboard.press('Alt+Digit2');
  await sleep(300);
  if (lensFrame) {
    await lensFrame.evaluate(() => document.body.focus());
    await page.locator('#lens').focus();
    await page.keyboard.press('Alt+Digit3');
    await sleep(300);
    report.add('demo: Alt+3 works with the focus in the glasses frame', (await page.evaluate(() => document.body.dataset.view)) === 'phone');
  }
  // Alt+C camera toggle: the engine confirms with a camera message, and every frame hears it
  await page.keyboard.press('Alt+Digit1');
  await sleep(300);
  const before = (await wsMessages(page, 'camera')).length;
  await page.keyboard.press('Alt+KeyC');
  const off = await waitFor(async () => (await wsMessages(page, 'camera')).slice(before).find((m) => m.on === false), { timeout: 5000 });
  const offState = await page.evaluate(() => ({ pressed: document.querySelector('#cam-toggle').getAttribute('aria-pressed'), card: !document.querySelector('#cam-off-card').hidden, label: document.querySelector('.cam-label').textContent }));
  report.add('demo: Alt+C turns the camera off (engine confirms)', !!off && offState.pressed === 'false' && offState.card, offState);
  const phoneHeard = phoneFrame ? await waitFor(async () => (await wsMessages(phoneFrame, 'camera')).some((m) => m.on === false), { timeout: 4000 }) : false;
  const lensHeard = lensFrame ? await waitFor(async () => (await wsMessages(lensFrame, 'camera')).some((m) => m.on === false), { timeout: 4000 }) : false;
  report.add('demo: the glasses and phone frames hear camera off', !!phoneHeard && !!lensHeard, { phoneHeard: !!phoneHeard, lensHeard: !!lensHeard });
  report.checks.at(-1).shot = await report.shot(page, 'demo-camera-off');
  await page.keyboard.press('Alt+KeyC');
  const on = await waitFor(async () => (await wsMessages(page, 'camera')).slice(before).find((m) => m.on === true), { timeout: 5000 });
  report.add('demo: Alt+C turns the camera back on', !!on && (await page.evaluate(() => document.querySelector('#cam-off-card').hidden)));
  // accessibility of the bar
  a11yChecks('demo', await a11yAudit(page));
  contrastCheck('demo bar', await contrastAudit(page, { root: '.bar' }));
  const focus = await focusAudit(page, null, { steps: 10 });
  report.add(`demo: keyboard focus is visible (${focus.tabbed} controls)`, focus.invisible.length === 0, fmt(focus.invisible));
  report.clean(p, 'demo');
  await p.context.close();
}

// ============================================================================ lens
async function lens() {
  for (const [w, h] of [[1280, 720], [1920, 1080]]) {
    for (const mode of ['color', 'mono', 'mono-corner', 'mono-corner&variant=glass']) {
      const p = await openPage(browser, { width: w, height: h, name: 'lens' });
      const { page } = p;
      await page.goto(`${base}/lens/?source=live&mode=${mode}`, { waitUntil: 'load' });
      const connected = await waitFor(() => page.evaluate(() => document.querySelector('#notice').hidden), { timeout: 15000 });
      await sleep(1500);
      const tag = `lens ${w}x${h} ${mode.replace('&variant=', '/')}`;
      report.add(`${tag}: connects and shows video (no notice)`, !!connected);
      const state = await page.evaluate(() => ({
        name: document.querySelector('#mode-name').textContent,
        url: location.search,
        pressed: document.querySelector('button[data-mode][aria-pressed="true"]')?.dataset.mode,
      }));
      const want = mode.startsWith('mono-corner') ? 'corner' : mode;
      report.add(`${tag}: the ${want} look is active`, state.pressed === want && state.url.includes(`mode=${mode.split('&')[0]}`), state);
      if (mode.includes('glass')) report.add(`${tag}: Google Glass placement kept in the URL`, state.url.includes('variant=glass'), state.url);
      report.add(`${tag}: video frames drawn`, await canvasPainted(page, '#live-frame'));
      report.add(`${tag}: HUD drawn`, await canvasPainted(page, 'canvas.hud.on'));
      const shot = await report.shot(page, `lens-${w}-${mode.replace(/[&=]/g, '-')}`);
      layoutChecks(tag, await layoutAudit(page, { root: '#chrome' }), { tiny: true });
      report.checks.at(-1).shot = shot;
      if (w === 1280 && mode === 'color') await lensKeys(page, tag);
      report.clean(p, tag);
      await p.context.close();
    }
  }
}

async function lensKeys(page, tag) {
  const q = () => page.evaluate(() => Object.fromEntries(new URLSearchParams(location.search)));
  await page.evaluate(() => document.activeElement?.blur());
  const modes = [];
  for (let i = 0; i < 3; i++) {
    await page.keyboard.press('m');
    await sleep(250);
    modes.push((await q()).mode);
  }
  report.add(`${tag}: M cycles mono, corner, colour`, fmt(modes) === 'mono | mono-corner | color', modes);
  await page.keyboard.press(']');
  await sleep(200);
  const hi = await q();
  await page.keyboard.press('[');
  await page.keyboard.press('[');
  await sleep(200);
  const lo = await q();
  const level = (s) => Number(s.height ?? 4); // no height in the URL = the default level 4
  report.add(`${tag}: ] and [ move the Mono display (height in the URL)`, hi.mode === 'mono' && level(hi) - level(lo) === 2, { hi, lo });
  await page.keyboard.press('g');
  await sleep(200);
  const g1 = await q();
  await page.keyboard.press('g');
  await sleep(200);
  const g2 = await q();
  report.add(`${tag}: G switches the monocular placement`, g1.mode === 'mono-corner' && g1.variant === 'glass' && !g2.variant, { g1, g2 });
  await page.keyboard.press('h');
  await sleep(200);
  const hidden = await page.evaluate(() => document.body.classList.contains('chrome-hidden'));
  await page.keyboard.press('h');
  await sleep(200);
  report.add(`${tag}: H hides and shows the chrome`, hidden && !(await page.evaluate(() => document.body.classList.contains('chrome-hidden'))));
  await page.keyboard.press('Shift+Slash');
  await sleep(200);
  const help = await page.evaluate(() => ({ open: !document.querySelector('#help').hidden, rows: document.querySelectorAll('#help-list dt').length }));
  await page.keyboard.press('Escape');
  await sleep(200);
  report.add(`${tag}: ? opens the shortcut list, Esc closes it`, help.open && help.rows >= 10 && (await page.evaluate(() => document.querySelector('#help').hidden)), help);
  // pause and resume: the engine answers with `paused`
  const n0 = (await wsMessages(page, 'paused')).length;
  await page.keyboard.press('p');
  const paused = await waitFor(async () => (await wsMessages(page, 'paused')).slice(n0).find((m) => m.paused === true), { timeout: 5000 });
  await sleep(300);
  const stagePaused = await page.evaluate(() => document.querySelector('#stage').classList.contains('paused'));
  await report.shot(page, 'lens-paused');
  await page.keyboard.press('p');
  const resumed = await waitFor(async () => (await wsMessages(page, 'paused')).slice(n0).find((m) => m.paused === false), { timeout: 5000 });
  report.add(`${tag}: P pauses and resumes recognition`, !!paused && stagePaused && !!resumed, { paused: !!paused, stagePaused, resumed: !!resumed });
  // panels (C S Y) load over the live source
  await page.keyboard.press('c');
  const panel = await waitFor(() => page.evaluate(() => !!document.querySelector('.atp-panel, [class*="atp"]')), { timeout: 5000 });
  report.add(`${tag}: C opens the console panel`, !!panel);
  await report.shot(page, 'lens-console-panel');
  await page.keyboard.press('Escape');
  // V: film and back
  await page.keyboard.press('v');
  await sleep(2500);
  const film = await page.evaluate(() => ({ url: location.search, notice: document.querySelector('#notice').hidden ? '' : document.querySelector('#notice-title').textContent }));
  report.add(`${tag}: V switches to the film`, film.url.includes('source=film') && !/not found/i.test(film.notice), film);
  await page.keyboard.press('v');
  await sleep(2500);
  report.add(`${tag}: V switches back to live and reconnects`, await waitFor(() => page.evaluate(() => location.search.includes('source=live') && document.querySelector('#notice').hidden), { timeout: 10000 }));
  // F forgets the session (command reaches the engine)
  await page.keyboard.press('f');
  await sleep(300);
  report.add(`${tag}: F sends session.forget`, (await wsSent(page, 'session.forget')).length > 0);
  const a = await a11yAudit(page);
  a11yChecks(tag, a);
  contrastCheck(`${tag} chrome`, await contrastAudit(page, { root: '#chrome' }));
  const focus = await focusAudit(page, null, { steps: 12 });
  report.add(`${tag}: keyboard focus is visible (${focus.tabbed} controls)`, focus.invisible.length === 0, fmt(focus.invisible));
}

// ============================================================================ POV
async function pov() {
  const p = await openPage(browser, { width: 1366, height: 768, name: 'pov' });
  const { page } = p;
  await page.goto(`${base}/demo/?view=lens`, { waitUntil: 'load' });
  await sleep(1500);
  await page.keyboard.press('Alt+Digit5');
  const loaded = await waitFor(() => page.evaluate(() => window.attuneDemo?.pov.check().loaded), { timeout: 15000 });
  await sleep(2000);
  const c1 = await page.evaluate(() => window.attuneDemo.pov.check());
  report.add('pov: opens full window with its own lens', !!loaded && (await page.evaluate(() => document.body.dataset.view)) === 'pov', c1);
  report.add('pov: starts "Closer"', c1.zoom === 'closer', c1);
  const shot1 = await report.shot(page, 'pov-closer');
  const povFrame = page.frames().find((f) => /chrome=0/.test(f.url()));
  if (povFrame) report.add('pov: lens painted', await waitFor(() => canvasPainted(povFrame, 'canvas.hud.on'), { timeout: 5000 }), '', { shot: shot1 });
  await page.keyboard.press('z');
  await sleep(500);
  const c2 = await page.evaluate(() => window.attuneDemo.pov.check());
  report.add('pov: Z shows everything', c2.zoom === 'everything', c2);
  await report.shot(page, 'pov-everything');
  await page.keyboard.press('z');
  await sleep(300);
  for (const mode of ['mono', 'corner', 'color']) {
    await page.evaluate((m) => document.querySelector(`[data-pov-mode="${m}"]`).click(), mode);
    await sleep(900);
    const c = await page.evaluate(() => window.attuneDemo.pov.check());
    const main = await page.evaluate(() => new URLSearchParams(document.querySelector('#lens').contentWindow.location.search).get('mode'));
    report.add(`pov: ${mode} look (main lens follows)`, c.mode === mode && main === (mode === 'corner' ? 'mono-corner' : mode), { pov: c.mode, main });
    await report.shot(page, `pov-${mode}`);
  }
  await page.keyboard.press('Escape');
  await sleep(400);
  report.add('pov: Esc returns to the view before', (await page.evaluate(() => document.body.dataset.view)) === 'lens');
  a11yChecks('pov', await a11yAudit(page, { root: '#pov' }));
  report.clean(p, 'pov');
  await p.context.close();
}

// ============================================================================ glasses guide
async function guide() {
  const p = await openPage(browser, { width: 1366, height: 768, name: 'guide' });
  const { page } = p;
  await page.goto(`${base}/demo/?view=guide`, { waitUntil: 'load' });
  await sleep(1500);
  const info = await page.evaluate(() => ({ text: document.querySelector('#guide').innerText.length, tries: document.querySelectorAll('#guide button[data-try]').length }));
  report.add('guide: content and "Try this look" buttons', info.text > 400 && info.tries >= 3, info);
  const shot = await report.shot(page, 'guide');
  layoutChecks('guide 1366x768', await layoutAudit(page, { root: '#guide' }), { tiny: true });
  report.checks.at(-1).shot = shot;
  contrastCheck('guide', await contrastAudit(page, { root: '#guide' }));
  a11yChecks('guide', await a11yAudit(page, { root: '#guide' }));
  const tries = await page.evaluate(() => [...document.querySelectorAll('#guide button[data-try]')].map((b) => `${b.dataset.try}/${b.dataset.variant || 'rayban'}`));
  for (const t of [...new Set(tries)]) {
    const [mode, variant] = t.split('/');
    await page.goto(`${base}/demo/?view=guide`, { waitUntil: 'load' });
    await sleep(1200);
    await page.evaluate(({ mode, variant }) => {
      const b = [...document.querySelectorAll('#guide button[data-try]')].find((x) => x.dataset.try === mode && (x.dataset.variant || 'rayban') === variant);
      b.click();
    }, { mode, variant });
    await sleep(1200);
    const res = await page.evaluate(() => ({ view: document.body.dataset.view, q: new URLSearchParams(document.querySelector('#lens').contentWindow.location.search) + '' }));
    const want = mode === 'corner' ? 'mono-corner' : mode;
    report.add(`guide: "Try" ${t} opens the glasses in that look`, res.view === 'lens' && res.q.includes(`mode=${want}`) && (variant !== 'glass' || res.q.includes('variant=glass')), res);
  }
  await page.goto(`${base}/demo/?view=guide`, { waitUntil: 'load' });
  await sleep(800);
  await page.evaluate(() => document.querySelector('#guide').focus());
  const focus = await focusAudit(page, null, { steps: 20 });
  report.add(`guide: keyboard focus is visible (${focus.tabbed} controls)`, focus.invisible.length === 0, fmt(focus.invisible));
  report.clean(p, 'guide');
  await p.context.close();
}

// ============================================================================ phone
async function phoneScreens(page, tag) {
  const out = {};
  const nav = async (name) => {
    await page.click(`.tab[data-nav="${name}"]`);
    await sleep(350);
  };
  for (const screen of ['home', 'history', 'speak', 'enroll', 'settings']) {
    await nav(screen);
    out[screen] = await page.evaluate(() => document.querySelector('#screen-content h1')?.textContent || '');
    if (screen === 'home' || screen === 'settings') {
      // the active tab's bar must not run through its label (it once struck out "Home")
      const bar = await page.evaluate(() => {
        const tab = document.querySelector('.tab.active');
        const label = tab?.querySelector('span')?.getBoundingClientRect();
        const after = tab && getComputedStyle(tab, '::after');
        if (!label || !after || after.content === 'none') return { none: true };
        if (getComputedStyle(tab).position === 'static') return { placedAgainst: 'the screen, not the tab' };
        const t = tab.getBoundingClientRect();
        const top = after.top !== 'auto' ? t.top + parseFloat(after.top) : t.bottom - parseFloat(after.bottom) - parseFloat(after.height);
        const bottom = top + parseFloat(after.height);
        return { top: Math.round(top), bottom: Math.round(bottom), label: [Math.round(label.top), Math.round(label.bottom)], crosses: top < label.bottom && bottom > label.top };
      });
      report.add(`${tag} ${screen}: the active tab's bar doesn't cross its label`, !bar.crosses && !bar.placedAgainst, bar);
    }
    const shot = await report.shot(page, `${tag.replace(/\s+/g, '-')}-${screen}`);
    layoutChecks(`${tag} ${screen}`, await layoutAudit(page, { root: '#app' }));
    report.checks.at(-1).shot = shot;
  }
  await nav('home');
  await page.click('[data-action="live"]');
  await sleep(300);
  out.live = await page.evaluate(() => document.querySelector('#screen-content h1')?.textContent || '');
  const tags = await page.evaluate(() => [...document.querySelectorAll('.lang-tag')].map((t) => t.textContent));
  report.add(`${tag} live view: no language tag for an unknown language ("UND")`, !tags.some((t) => /^(UND|UNK)$/i.test(t)), tags);
  layoutChecks(`${tag} live view`, await layoutAudit(page, { root: '#app' }));
  report.checks.at(-1).shot = await report.shot(page, `${tag.replace(/\s+/g, '-')}-live`);
  await nav('home');
  await page.click('.shortcut[data-action="people"]');
  await sleep(300);
  out.people = await page.evaluate(() => document.querySelector('#screen-content h1')?.textContent || '');
  layoutChecks(`${tag} people`, await layoutAudit(page, { root: '#app' }));
  report.checks.at(-1).shot = await report.shot(page, `${tag.replace(/\s+/g, '-')}-people`);
  return out;
}

async function phone() {
  for (const [w, h] of [[390, 844], [360, 740]]) {
    for (const palette of ['', 'apricot']) {
      const p = await openPage(browser, { width: w, height: h, name: 'phone' });
      const { page } = p;
      await page.goto(`${base}/phone/${palette ? `?palette=${palette}` : ''}`, { waitUntil: 'load' });
      const tag = `phone ${w}x${h}${palette ? ` ${palette}` : ''}`;
      const live = await waitFor(() => page.evaluate(() => document.querySelector('#app').dataset.link === 'live'), { timeout: 10000 });
      report.add(`${tag}: connects live`, !!live);
      if (palette) report.add(`${tag}: Apricot palette applied`, await page.evaluate(() => document.querySelector('#app').classList.contains('palette-apricot')));
      const titles = await phoneScreens(page, tag);
      report.add(`${tag}: every screen renders`, Object.values(titles).every(Boolean), titles);
      if (w === 390 && !palette) await phoneFlows(page, tag);
      // dark theme
      await page.click('.tab[data-nav="settings"]');
      await sleep(250);
      await page.click('[data-action="theme"][data-theme="dark"]');
      await sleep(300);
      contrastCheck(`${tag} dark settings`, await contrastAudit(page, { root: '#app' }));
      await page.click('.tab[data-nav="home"]');
      await sleep(300);
      contrastCheck(`${tag} dark home`, await contrastAudit(page, { root: '#app' }));
      report.checks.at(-1).shot = await report.shot(page, `${tag.replace(/\s+/g, '-')}-dark-home`);
      await page.click('.tab[data-nav="speak"]');
      await sleep(300);
      contrastCheck(`${tag} dark speak`, await contrastAudit(page, { root: '#app' }));
      await page.click('.tab[data-nav="settings"]');
      await sleep(250);
      await page.click('[data-action="theme"][data-theme="light"]');
      await sleep(250);
      await page.click('.tab[data-nav="home"]');
      await sleep(250);
      contrastCheck(`${tag} light home`, await contrastAudit(page, { root: '#app' }));
      a11yChecks(tag, await a11yAudit(page, { root: '#app' }));
      if (!palette) {
        const focus = await focusAudit(page, null, { steps: 18 });
        report.add(`${tag}: keyboard focus is visible (${focus.tabbed} controls)`, focus.invisible.length === 0, fmt(focus.invisible));
      }
      report.clean(p, tag);
      await p.context.close();
    }
  }
  // demo mode (no engine)
  const p = await openPage(browser, { width: 390, height: 844, name: 'phone-demo' });
  await p.page.goto(`${base}/phone/?demo`, { waitUntil: 'load' });
  await sleep(800);
  const demo = await p.page.evaluate(() => ({ link: document.querySelector('#app').dataset.link, pill: document.querySelector('.demo-pill')?.textContent }));
  report.add('phone ?demo: stays in demo mode with sample data', demo.link === 'demo', demo);
  report.clean(p, 'phone ?demo');
  await p.context.close();
}

async function phoneFlows(page, tag) {
  // history + search
  await page.click('.tab[data-nav="history"]');
  const hist = await waitFor(() => page.evaluate(() => !/Loading history/.test(document.querySelector('#history-results')?.innerText || '')), { timeout: 8000 });
  report.add(`${tag}: history loads from the laptop`, !!hist, await page.evaluate(() => document.querySelector('#history-results')?.innerText.slice(0, 120)));
  await page.fill('#history-search', 'train');
  await sleep(1200);
  const found = await page.evaluate(() => document.querySelector('#history-results')?.innerText || '');
  report.add(`${tag}: history search runs`, /Search results|No conversations match/.test(found), found.slice(0, 160));
  // speak for me: typed and preset; the engine says it (silently in tests) and confirms
  await page.click('.tab[data-nav="speak"]');
  await sleep(300);
  await page.fill('#speak-text', 'Nice to meet you, see you soon.');
  const n0 = (await wsMessages(page, 'reply_spoken')).length;
  await page.click('#speak-button');
  const spoken = await waitFor(async () => (await wsMessages(page, 'reply_spoken')).slice(n0).find((m) => /Nice to meet you, see you soon/.test(m.text)), { timeout: 20000 });
  report.add(`${tag}: speak-for-me sends and the engine confirms it was spoken`, !!spoken, spoken || 'no reply_spoken in 20 s');
  if (spoken) report.add(`${tag}: speak used the offline voice (never ElevenLabs in tests)`, spoken.voice === 'kokoro', spoken.voice);
  const preset = await page.evaluate(() => document.querySelector('.quick-reply[data-source="preset"]')?.textContent);
  await sleep(700);
  const n1 = (await wsMessages(page, 'reply_spoken')).length;
  await page.click('.quick-reply[data-source="preset"]');
  const spoken2 = await waitFor(async () => (await wsMessages(page, 'reply_spoken')).slice(n1).length > 0, { timeout: 20000 });
  report.add(`${tag}: a preset reply is spoken`, !!spoken2 && (await wsSent(page, 'speak')).some((s) => s.args.source === 'preset' && s.args.text === preset), preset);
  // settings: switches reach the engine; pause and resume come back as `paused`
  await page.click('.tab[data-nav="settings"]');
  await sleep(300);
  await page.click('[data-action="toggle-feature"][data-feature="translation"]');
  await page.click('[data-action="toggle-feature"][data-feature="translation"]');
  await sleep(300);
  const sw = (await wsSent(page, 'switch.set')).filter((s) => s.args.key === 'translation').map((s) => s.args.value);
  report.add(`${tag}: translation switch sends switch.set off then on`, fmt(sw) === 'false | true', sw);
  const p0 = (await wsMessages(page, 'paused')).length;
  await page.click('button[data-action="pause"].outline');
  const pz = await waitFor(async () => (await wsMessages(page, 'paused')).slice(p0).find((m) => m.paused), { timeout: 5000 });
  await sleep(300);
  const pausedText = await page.evaluate(() => document.querySelector('#screen-content').innerText);
  await page.click('button[data-action="pause"].outline');
  const rz = await waitFor(async () => (await wsMessages(page, 'paused')).slice(p0).find((m) => m.paused === false), { timeout: 5000 });
  report.add(`${tag}: pause and resume from the phone`, !!pz && !!rz && /Resume recognition/.test(pausedText), { paused: !!pz, resumed: !!rz });
  // the camera alone, from the phone: off and back on, and the phone says so
  const c0 = (await wsMessages(page, 'camera')).length;
  await page.click('button[data-action="camera"]');
  const camOff = await waitFor(async () => (await wsMessages(page, 'camera')).slice(c0).find((m) => m.on === false), { timeout: 5000 });
  await sleep(300);
  const offText = await page.evaluate(() => ({ status: document.querySelector('#screen-content .press-card')?.innerText.replace(/\s+/g, ' '), button: document.querySelector('button[data-action="camera"]')?.innerText }));
  const camShot = await report.shot(page, `${tag.replace(/\s+/g, '-')}-camera-off`);
  await page.click('button[data-action="camera"]');
  const camOn = await waitFor(async () => (await wsMessages(page, 'camera')).slice(c0).find((m) => m.on === true), { timeout: 5000 });
  const sent = (await wsSent(page, 'camera.set')).map((s) => s.args.on);
  report.add(`${tag}: camera off and on from Settings (camera.set, the engine confirms, the phone says so)`,
    !!camOff && !!camOn && fmt(sent) === 'false | true' && /camera off/i.test(offText.status || '') && /Turn camera on/.test(offText.button || ''),
    { sent, offText });
  report.checks.at(-1).shot = camShot;
  // forget session (confirm is accepted by the test)
  await page.click('button[data-action="forget"]');
  await sleep(500);
  report.add(`${tag}: forget session sends session.forget`, (await wsSent(page, 'session.forget')).length > 0);
  // contacts: the People screen from Settings
  await page.click('.setting-row[data-action="people"]');
  await sleep(300);
  const people = await page.evaluate(() => ({ h1: document.querySelector('h1')?.textContent, cards: document.querySelectorAll('.person-card').length }));
  report.add(`${tag}: People opens from Settings`, people.h1 === 'People', people);
}

// ============================================================================ panels
async function panels() {
  const p = await openPage(browser, { width: 1366, height: 768, name: 'panels' });
  const { page } = p;
  await page.goto(`${base}/panels/`, { waitUntil: 'load' });
  const up = await waitFor(() => page.evaluate(() => /connected|live/i.test(document.querySelector('.atp-conn')?.textContent || '')), { timeout: 10000 });
  await sleep(2500);
  report.add('panels: console connects', !!up, await page.evaluate(() => document.querySelector('.atp-conn')?.textContent));
  const shot = await report.shot(page, 'panels');
  layoutChecks('panels 1366x768', await layoutAudit(page));
  report.checks.at(-1).shot = shot;
  contrastCheck('panels', await contrastAudit(page));
  a11yChecks('panels', await a11yAudit(page));
  const status = (await wsMessages(page, 'event_log')).length;
  report.add('panels: event log arrives', status > 0, `${status} entries`);
  report.clean(p, 'panels');
  await p.context.close();
}

const sections = { demo, lens, pov, guide, phone, panels };
for (const [name, fn] of Object.entries(sections)) {
  if (only.has(name)) await report.guard(name, fn);
}
await browser.close();
report.done();
