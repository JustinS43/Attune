/*
 * Soak: the demo page (glasses + phone) and a lens stay open for N minutes against an engine that
 * loops speech; samples page memory (performance.memory in Edge) and message rates.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-37.
 *
 *   node soak.mjs '{"base": "...", "minutes": 15, "every": 30}'
 */

import { ARGS, Report, launch, openPage, sleep } from './lib.mjs';

const base = ARGS.base || 'http://127.0.0.1:8013';
const minutes = ARGS.minutes || 15;
const every = (ARGS.every || 30) * 1000;
const report = new Report(ARGS.out);
const browser = await launch();
const demo = await openPage(browser, { width: 1366, height: 768, name: 'demo' });
const lens = await openPage(browser, { width: 1280, height: 720, name: 'lens' });
await demo.page.goto(`${base}/demo/?view=split`, { waitUntil: 'load' });
await lens.page.goto(`${base}/lens/?source=live&mode=mono`, { waitUntil: 'load' });
await sleep(3000);
const phoneFrame = demo.page.frame({ url: /\/phone\// });
if (phoneFrame) await phoneFrame.click('[data-action="live"]').catch(() => {});

const mem = (target) => target.evaluate(() => ({
  heap: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576 * 10) / 10 : null,
  counts: { ...(window.__e2e?.counts || {}) },
  nodes: document.getElementsByTagName('*').length,
}));
const samples = [];
const t0 = Date.now();
while (Date.now() - t0 < minutes * 60000) {
  await sleep(every);
  const s = { t_min: Math.round((Date.now() - t0) / 600) / 100, demo: await mem(demo.page), lens: await mem(lens.page) };
  if (phoneFrame) s.phone = await mem(phoneFrame);
  samples.push(s);
  process.stderr.write(`soak page ${s.t_min} min: demo ${s.demo.heap} MB (${s.demo.nodes} nodes), lens ${s.lens.heap} MB, phone ${s.phone?.heap} MB (${s.phone?.nodes} nodes)\n`);
}
if (samples.length >= 3) {
  for (const key of ['demo', 'lens', 'phone']) {
    const a = samples[1][key]?.heap;
    const b = samples.at(-1)[key]?.heap;
    if (a == null || b == null) continue;
    report.add(`soak: ${key} page JS heap growth under 50 MB`, b - a < 50, `${a} -> ${b} MB`);
    const n1 = samples[1][key].nodes;
    const n2 = samples.at(-1)[key].nodes;
    // a page that keeps every log line grows without end on a long wear; allow ~10 nodes a minute
    const mins = Math.max(1, samples.at(-1).t_min - samples[1].t_min);
    const perMin = (n2 - n1) / mins;
    report.add(`soak: ${key} page DOM stays bounded (under 10 new nodes a minute)`, perMin < 10, `${n1} -> ${n2} nodes, ${perMin.toFixed(1)}/min`);
  }
}
report.clean(demo, 'soak demo');
report.clean(lens, 'soak lens');
await browser.close();
report.done({ samples });
