// Headless page recorder for scripts/bench_captions.py (TODO A-22).
//
//   node bench_pages.mjs <port> <out.jsonl> <folder holding node_modules/playwright-core>
//
// Opens the lens (Colour mode) and the phone's live screen in headless Edge and appends one
// JSON line whenever the caption text a page shows changes: {page, t (epoch s), key, text}.
// The lens is read from window.attuneLens.view (the view model every glasses mode draws from):
// each caption card's visible lines, plus the dimmed original of a line still waiting for its
// translation. Lens rows with `move` record an utterance changing bubbles. The phone is read
// from its #live-feed cards. Stops when stdin gets a line (or closes).
import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import readline from 'node:readline';

const [port, outPath, pwDir] = process.argv.slice(2);
const { chromium } = createRequire(path.join(pwDir, 'package.json'))('playwright-core');
const out = fs.createWriteStream(outPath, { flags: 'a' });
const log = (row) => out.write(`${JSON.stringify(row)}\n`);

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
await ctx.exposeFunction('__benchLog', log);

const lens = await ctx.newPage();
await lens.goto(`http://127.0.0.1:${port}/lens/?mode=color`);
await lens.evaluate(() => {
  const last = new Map();
  const owner = new Map();
  const now = () => (performance.timeOrigin + performance.now()) / 1000;
  const tick = () => {
    const v = window.attuneLens?.view;
    if (v) {
      const t = now();
      for (const b of v.feed || []) {
        const text = [...(b.lines || []), b.pending ? b.orig || '' : ''].join(' ').trim();
        if (last.get(b.key) !== text) {
          last.set(b.key, text);
          window.__benchLog({ page: 'lens', t, key: b.key, text });
        }
      }
      for (const u of v.utterances || []) {
        if (owner.has(u.id) && owner.get(u.id) !== u.key) {
          window.__benchLog({ page: 'lens', t, move: u.id, from: owner.get(u.id), to: u.key });
        }
        owner.set(u.id, u.key);
      }
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});

const phone = await ctx.newPage();
await phone.goto(`http://127.0.0.1:${port}/phone/`);
await phone.waitForTimeout(1500);
await phone.evaluate(() => document.querySelector('[data-action="live"]')?.click());
await phone.evaluate(() => {
  let last = '';
  setInterval(() => {
    const feed = document.querySelector('#live-feed');
    if (!feed) return;
    const text = [...feed.querySelectorAll('.live-caption p:not(.orig)')].map((p) => p.innerText).join(' | ');
    if (text !== last) {
      last = text;
      window.__benchLog({ page: 'phone', t: (performance.timeOrigin + performance.now()) / 1000, key: 'feed', text });
    }
  }, 16);
});
log({ page: 'ready', t: Date.now() / 1000 });

const rl = readline.createInterface({ input: process.stdin });
await new Promise((resolve) => {
  rl.once('line', resolve);
  rl.once('close', resolve);
});
await browser.close();
out.end();
