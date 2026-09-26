/*
 * Shared helpers for the end-to-end browser scripts (headless Microsoft Edge via playwright-core).
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-37.
 *
 * Every script takes one JSON argument ({base, out, ...}) and prints one JSON line at the end:
 * {ok, checks: [{name, ok, detail, shot}]}. harness.py finds playwright-core and passes its
 * node_modules folder in NODE_PATH. The browser is muted: nothing a page does reaches the speakers.
 */

import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const require = createRequire(import.meta.url);

export function loadPlaywright() {
  const dirs = [process.env.ATTUNE_PLAYWRIGHT_DIR, ...(process.env.NODE_PATH || '').split(path.delimiter)].filter(Boolean);
  for (const d of dirs) {
    try {
      return require(path.join(d, 'playwright-core'));
    } catch {
      /* try the next one */
    }
  }
  return require('playwright-core');
}

export const ARGS = JSON.parse(process.argv[2] || '{}');

export async function launch() {
  const { chromium } = loadPlaywright();
  return chromium.launch({
    channel: 'msedge',
    headless: true,
    args: ['--mute-audio', '--autoplay-policy=no-user-gesture-required', '--disable-features=Translate'],
  });
}

/** Records every JSON message each page's WebSockets receive, and what the pages send. */
const WS_TAP = () => {
  const Native = window.WebSocket;
  const rec = (window.__e2e = { msgs: [], sent: [], counts: {}, opens: 0, closes: 0 });
  class Tapped extends Native {
    constructor(...a) {
      super(...a);
      rec.opens++;
      this.addEventListener('close', () => rec.closes++);
      this.addEventListener('message', (ev) => {
        if (typeof ev.data !== 'string') {
          rec.counts.frame = (rec.counts.frame || 0) + 1;
          return;
        }
        try {
          const m = JSON.parse(ev.data);
          rec.counts[m.type] = (rec.counts[m.type] || 0) + 1;
          if (m.type !== 'scene' && m.type !== 'status' && m.type !== 'thumbnails') {
            rec.msgs.push({ ...m, _t: performance.now() });
            if (rec.msgs.length > 4000) rec.msgs.splice(0, 1000);
          }
        } catch {
          /* not JSON */
        }
      });
    }
    send(data) {
      try {
        const m = JSON.parse(data);
        if (m.type === 'command') rec.sent.push({ name: m.name, args: m.args, _t: performance.now() });
      } catch {
        /* not JSON */
      }
      return super.send(data);
    }
  }
  window.WebSocket = Tapped;
};

/**
 * A new page that collects console errors, page errors, failed requests and HTTP errors.
 * `ignore` holds regexes for messages that are expected in a test (e.g. an engine restart).
 */
export async function openPage(browser, { width = 1280, height = 720, dark = false, name = 'page', ignore = [] } = {}) {
  const context = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 1,
    colorScheme: dark ? 'dark' : 'light',
    reducedMotion: 'no-preference',
  });
  await context.addInitScript(WS_TAP);
  const page = await context.newPage();
  const problems = [];
  const skip = (text) => ignore.some((rx) => rx.test(text));
  page.on('console', (msg) => {
    if (msg.type() === 'error' && !skip(msg.text())) problems.push({ kind: 'console', text: msg.text().slice(0, 300) });
  });
  page.on('pageerror', (err) => {
    if (!skip(String(err))) problems.push({ kind: 'pageerror', text: String(err).slice(0, 300) });
  });
  page.on('requestfailed', (req) => {
    const text = `${req.method()} ${req.url()} ${req.failure()?.errorText}`;
    // media range requests are cancelled by the browser when a video seeks: not a failure
    if (/ERR_ABORTED/.test(text) && /\.(mp4|webm)(\?|$)/.test(req.url())) return;
    if (!skip(text)) problems.push({ kind: 'requestfailed', text });
  });
  page.on('response', (res) => {
    if (res.status() >= 400 && !skip(`${res.status()} ${res.url()}`)) problems.push({ kind: 'http', text: `${res.status()} ${res.url()}` });
  });
  page.on('dialog', (d) => d.accept()); // confirm() in the phone app: the user said yes
  return { context, page, problems, name };
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function waitFor(fn, { timeout = 10000, every = 100 } = {}) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    try {
      last = await fn();
      if (last) return last;
    } catch {
      /* not yet */
    }
    await sleep(every);
  }
  return last;
}

/** Collects checks and prints the result line. */
export class Report {
  constructor(out) {
    this.out = out;
    this.checks = [];
    if (out) fs.mkdirSync(out, { recursive: true });
  }
  add(name, ok, detail = '', extra = {}) {
    this.checks.push({ name, ok: !!ok, detail: typeof detail === 'string' ? detail : JSON.stringify(detail), ...extra });
    process.stderr.write(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` - ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}\n`);
    return ok;
  }
  async shot(page, name) {
    if (!this.out) return '';
    const file = path.join(this.out, `${name.replace(/[^a-z0-9._-]+/gi, '_')}.png`);
    try {
      await page.screenshot({ path: file });
    } catch {
      return '';
    }
    return file;
  }
  /** Adds a check that the page had no console errors or failed requests, then clears them. */
  clean(p, name) {
    const list = p.problems.splice(0);
    return this.add(`${name}: no console errors or failed requests`, list.length === 0, list.slice(0, 6));
  }
  async guard(name, fn) {
    try {
      await fn();
    } catch (err) {
      this.add(`${name}: ran without a script error`, false, String(err?.stack || err).slice(0, 500));
    }
  }
  done(extra = {}) {
    const ok = this.checks.every((c) => c.ok);
    console.log(JSON.stringify({ ok, checks: this.checks, ...extra }));
  }
}

// --------------------------------------------------------------------------- audits (run in the page)

/**
 * Layout audit of the document (or of a same-origin frame's document): horizontal overflow,
 * overlapping controls, text smaller than `minFont`, text cut off by its box.
 */
export async function layoutAudit(target, { minFont = 11, root = 'body' } = {}) {
  return target.evaluate(({ minFont, root }) => {
    const vw = document.documentElement.clientWidth;
    const out = { overflowX: false, offscreen: [], overlaps: [], tinyText: [], clipped: [] };
    const scroller = document.scrollingElement;
    out.overflowX = scroller.scrollWidth > vw + 1;
    const visible = (el) => {
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none') return false;
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return false;
      if (el.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
      for (let a = el; a; a = a.parentElement) if (Number(getComputedStyle(a).opacity) === 0) return false;
      return true;
    };
    // the part of an element its scrolling / clipping ancestors actually show
    const shown = (el) => {
      const r = el.getBoundingClientRect();
      const box = { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
      for (let a = el.parentElement; a && a !== document.documentElement; a = a.parentElement) {
        const cs = getComputedStyle(a);
        if (/auto|scroll|hidden|clip/.test(`${cs.overflowX} ${cs.overflowY}`)) {
          const ar = a.getBoundingClientRect();
          box.left = Math.max(box.left, ar.left);
          box.top = Math.max(box.top, ar.top);
          box.right = Math.min(box.right, ar.right);
          box.bottom = Math.min(box.bottom, ar.bottom);
        }
        if (cs.position === 'fixed') break;
      }
      return box;
    };
    const label = (el) => `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ''}${el.className && typeof el.className === 'string' ? `.${el.className.trim().split(/\s+/).slice(0, 2).join('.')}` : ''} "${(el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 30)}"`;
    const base = document.querySelector(root) || document.body;
    const all = [...base.querySelectorAll('*')].filter(visible);
    for (const el of all) {
      const r = el.getBoundingClientRect();
      const pos = getComputedStyle(el).position;
      // a face box that runs past the edge of a clipped camera picture is not off the page
      const s = shown(el);
      if (s.right - s.left < 1 || s.bottom - s.top < 1) continue;
      if (pos !== 'fixed' && (s.right > vw + 1 || s.left < -1) && !el.closest('.sr-only')) {
        // only report the outermost offender
        if (!out.offscreen.some((o) => o.el.contains(el))) out.offscreen.push({ el, r });
      }
    }
    out.offscreen = out.offscreen.slice(0, 8).map(({ el, r }) => `${label(el)} [${Math.round(r.left)}..${Math.round(r.right)}] vw ${vw}`);
    const controls = all.filter((el) => el.matches('button, a[href], input, select, textarea, [role="button"], [role="switch"], [role="tab"]'));
    for (let i = 0; i < controls.length; i++) {
      for (let j = i + 1; j < controls.length; j++) {
        const a = controls[i];
        const b = controls[j];
        if (a.contains(b) || b.contains(a)) continue;
        const ra = shown(a); // content scrolled under a tab bar is clipped, not overlapped
        const rb = shown(b);
        const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
        const h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
        if (w > 4 && h > 4) {
          // a control drawn over another: only a problem when both are hit-testable at that spot
          const x = Math.max(ra.left, rb.left) + w / 2;
          const y = Math.max(ra.top, rb.top) + h / 2;
          const hit = document.elementFromPoint(x, y);
          if (hit && (a.contains(hit) || b.contains(hit))) out.overlaps.push(`${label(a)} x ${label(b)}`);
        }
      }
    }
    out.overlaps = out.overlaps.slice(0, 8);
    const walker = document.createTreeWalker(base, NodeFilter.SHOW_TEXT);
    const seen = new Set();
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.textContent.trim()) continue;
      const el = node.parentElement;
      if (!el || seen.has(el) || !visible(el) || el.closest('.sr-only, svg, noscript, script, style')) continue;
      seen.add(el);
      const cs = getComputedStyle(el);
      const size = parseFloat(cs.fontSize);
      if (size < minFont) out.tinyText.push(`${label(el)} ${size}px`);
      if ((cs.overflow === 'hidden' || cs.overflowX === 'hidden') && cs.textOverflow !== 'ellipsis' && el.scrollWidth > el.clientWidth + 2 && cs.whiteSpace === 'nowrap') {
        out.clipped.push(`${label(el)} ${el.scrollWidth}>${el.clientWidth}`);
      }
    }
    out.tinyText = out.tinyText.slice(0, 10);
    out.clipped = out.clipped.slice(0, 10);
    return out;
  }, { minFont, root });
}

/**
 * Controls and frames the keyboard can still reach inside something faded out (opacity 0) or
 * moved off the page: a hidden pane must be `inert`, or Tab walks into an invisible app.
 */
export async function hiddenFocusAudit(target, { root = 'body' } = {}) {
  return target.evaluate(async ({ root }) => {
    const base = document.querySelector(root) || document.body;
    const vw = document.documentElement.clientWidth;
    const sel = 'a[href], button, input, select, textarea, iframe, [tabindex]:not([tabindex="-1"])';
    const out = [];
    for (const el of base.querySelectorAll(sel)) {
      if (el.disabled || el.closest('[inert], [hidden]')) continue;
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      let faded = null;
      for (let a = el; a; a = a.parentElement) {
        if (Number(getComputedStyle(a).opacity) === 0) {
          faded = a;
          break;
        }
      }
      const r = el.getBoundingClientRect();
      const off = r.width > 0 && (r.left >= vw || r.right <= 0);
      if (faded && !off) {
        // a bar that fades out but comes back when a control in it gets the focus is fine
        const before = document.activeElement;
        el.focus({ preventScroll: true });
        await new Promise((ok) => setTimeout(ok, 450));
        const back = Number(getComputedStyle(faded).opacity) > 0.5;
        el.blur();
        before?.focus?.({ preventScroll: true });
        if (back) continue;
      }
      if (faded || off) {
        const who = `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ''} "${(el.innerText || el.title || el.getAttribute('aria-label') || '').trim().slice(0, 24)}"`;
        out.push(`${who} ${faded ? `in faded .${String(faded.className).split(' ')[0]}` : 'off the page'}`);
      }
    }
    return out.slice(0, 10);
  }, { root });
}

/**
 * WCAG 2.1 contrast of visible text against the nearest solid background behind it.
 * Text on images, video, canvas or gradients is reported as "unknown", not as a failure.
 */
export async function contrastAudit(target, { root = 'body', limit = 25 } = {}) {
  return target.evaluate(({ root, limit }) => {
    const parse = (c) => {
      const m = c.match(/rgba?\(([^)]+)\)/);
      if (!m) return null;
      const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
      return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
    };
    const lum = ({ r, g, b }) => {
      const f = (v) => {
        v /= 255;
        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    const blend = (top, bottom) => ({
      r: top.r * top.a + bottom.r * (1 - top.a),
      g: top.g * top.a + bottom.g * (1 - top.a),
      b: top.b * top.a + bottom.b * (1 - top.a),
      a: 1,
    });
    let owner = null; // the element whose solid background the text sits on
    const background = (el) => {
      owner = null;
      const layers = [];
      for (let n = el; n; n = n.parentElement) {
        const cs = getComputedStyle(n);
        if (cs.backgroundImage && cs.backgroundImage !== 'none') return { unknown: `gradient/image on ${n.tagName.toLowerCase()}.${n.className}` };
        if (['IMG', 'VIDEO', 'CANVAS', 'IFRAME'].includes(n.tagName)) return { unknown: n.tagName };
        const bg = parse(cs.backgroundColor);
        if (bg && bg.a > 0) {
          layers.push(bg);
          if (bg.a >= 0.999) {
            owner = n;
            break;
          }
        }
        if (cs.backdropFilter && cs.backdropFilter !== 'none' && bg && bg.a < 0.9) return { unknown: 'glass (backdrop-filter)' };
      }
      let color = { r: 255, g: 255, b: 255, a: 1 };
      const pageBg = parse(getComputedStyle(document.documentElement).backgroundColor);
      if (pageBg && pageBg.a > 0) color = pageBg;
      for (const layer of layers.reverse()) color = blend(layer, color);
      return { color };
    };
    const base = document.querySelector(root) || document.body;
    const walker = document.createTreeWalker(base, NodeFilter.SHOW_TEXT);
    const seen = new Set();
    const fails = [];
    const unknown = [];
    let checked = 0;
    while (walker.nextNode()) {
      const el = walker.currentNode.parentElement;
      if (!walker.currentNode.textContent.trim() || !el || seen.has(el)) continue;
      seen.add(el);
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      if (cs.visibility === 'hidden' || cs.display === 'none' || r.width < 1 || r.height < 1) continue;
      if (el.closest('[hidden], [inert], .sr-only, svg, noscript, script, style')) continue;
      if (el.closest('button:disabled, input:disabled, select:disabled, [aria-disabled="true"]')) continue; // WCAG 1.4.3 exempts inactive controls
      let total = 1;
      for (let n = el; n; n = n.parentElement) total *= Number(getComputedStyle(n).opacity);
      if (total < 0.05) continue;
      const fg = parse(cs.color);
      if (!fg) continue;
      const bg = background(el);
      // only opacity between the text and its own background dims the text against it
      // (an ancestor above that fades both together)
      let opacity = 1;
      for (let n = el; n && n !== owner; n = n.parentElement) opacity *= Number(getComputedStyle(n).opacity);
      const text = el.innerText.trim().slice(0, 40);
      if (bg.unknown) {
        unknown.push(`"${text}" (${bg.unknown})`);
        continue;
      }
      checked++;
      const fgc = blend({ ...fg, a: fg.a * opacity }, bg.color);
      const l1 = lum(fgc);
      const l2 = lum(bg.color);
      const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
      const size = parseFloat(cs.fontSize);
      const bold = Number(cs.fontWeight) >= 700;
      const large = size >= 24 || (bold && size >= 18.66);
      const need = large ? 3 : 4.5;
      if (ratio < need) {
        const rgb = (c) => `rgb(${Math.round(c.r)},${Math.round(c.g)},${Math.round(c.b)})`;
        const on = owner ? `${owner.tagName.toLowerCase()}.${String(owner.className).split(' ')[0]}` : 'page';
        fails.push(`"${text}" ${ratio.toFixed(2)}:1 (needs ${need}, ${size}px${bold ? ' bold' : ''}; ${rgb(fgc)} on ${rgb(bg.color)} of ${on}, opacity ${opacity.toFixed(2)})`);
      }
    }
    return { checked, fails: fails.slice(0, limit), unknown: unknown.slice(0, 8), unknownCount: unknown.length };
  }, { root, limit });
}

/** Controls without an accessible name, images without alt, frames without a title. */
export async function a11yAudit(target, { root = 'body' } = {}) {
  return target.evaluate(({ root }) => {
    const base = document.querySelector(root) || document.body;
    const name = (el) => {
      if (el.getAttribute('aria-label')?.trim()) return true;
      const by = el.getAttribute('aria-labelledby');
      if (by && by.split(/\s+/).some((id) => document.getElementById(id)?.textContent.trim())) return true;
      if (el.getAttribute('title')?.trim()) return true;
      if (el.labels && [...el.labels].some((l) => l.textContent.trim())) return true;
      if (el.closest('label')?.textContent.trim()) return true;
      if (['BUTTON', 'A', 'SUMMARY'].includes(el.tagName) || el.getAttribute('role')) {
        if (el.textContent.trim()) return true;
        if ([...el.querySelectorAll('img[alt]')].some((i) => i.alt.trim())) return true;
      }
      if (el.placeholder?.trim()) return true; // weak, but a name
      return false;
    };
    const hidden = (el) => el.closest('[hidden], [inert], [aria-hidden="true"]') || getComputedStyle(el).display === 'none';
    const out = { unnamed: [], imgNoAlt: [], frameNoTitle: [], lang: document.documentElement.lang || '' };
    for (const el of base.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, [role=button], [role=switch], [role=tab], [role=checkbox]')) {
      if (hidden(el)) continue;
      if (!name(el)) out.unnamed.push(el.outerHTML.slice(0, 120));
    }
    for (const img of base.querySelectorAll('img')) if (!hidden(img) && !img.hasAttribute('alt')) out.imgNoAlt.push(img.outerHTML.slice(0, 100));
    for (const f of base.querySelectorAll('iframe')) if (!f.title?.trim()) out.frameNoTitle.push(f.outerHTML.slice(0, 100));
    return out;
  }, { root });
}

/**
 * Tabs through the page and reports focused controls that look the same focused and unfocused
 * (no outline, ring, border or background change).
 */
export async function focusAudit(page, frame = null, { steps = 25 } = {}) {
  const target = frame || page;
  await target.evaluate(() => document.activeElement?.blur());
  const invisible = [];
  const seen = [];
  for (let i = 0; i < steps; i++) {
    await page.keyboard.press('Tab');
    const res = await target.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body || el.tagName === 'IFRAME') return null;
      const style = (e) => {
        const cs = getComputedStyle(e);
        return [cs.outlineStyle, cs.outlineWidth, cs.outlineColor, cs.boxShadow, cs.borderColor, cs.backgroundColor, cs.color, cs.textDecorationLine].join('|');
      };
      const focused = style(el);
      const cs = getComputedStyle(el);
      const ring = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
      // compare with the same element unfocused
      const clone = el.cloneNode(true);
      clone.removeAttribute('id');
      clone.style.position = 'absolute';
      clone.style.left = '-9999px';
      el.parentElement?.append(clone);
      const plain = style(clone);
      clone.remove();
      const label = `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ''} "${(el.innerText || el.getAttribute('aria-label') || el.title || '').trim().slice(0, 30)}"`;
      return { label, visible: ring || focused !== plain };
    });
    if (!res) continue;
    if (seen.includes(res.label)) break;
    seen.push(res.label);
    if (!res.visible) invisible.push(res.label);
  }
  return { tabbed: seen.length, invisible };
}

/** True when a canvas has painted pixels (not all transparent or one flat colour). */
export async function canvasPainted(target, selector) {
  return target.evaluate((sel) => {
    const c = document.querySelector(sel);
    if (!c || !c.width || !c.height) return false;
    const probe = document.createElement('canvas');
    probe.width = 64;
    probe.height = 36;
    const ctx = probe.getContext('2d');
    ctx.drawImage(c, 0, 0, 64, 36);
    const d = ctx.getImageData(0, 0, 64, 36).data;
    let first = null;
    for (let i = 0; i < d.length; i += 4) {
      const px = `${d[i]},${d[i + 1]},${d[i + 2]},${d[i + 3]}`;
      if (first === null) first = px;
      else if (px !== first) return true;
    }
    return false;
  }, selector);
}

/** The WebSocket messages a page (or frame) has received, filtered by type. */
export async function wsMessages(target, type) {
  return target.evaluate((type) => (window.__e2e?.msgs || []).filter((m) => !type || m.type === type), type);
}

export async function wsSent(target, name) {
  return target.evaluate((name) => (window.__e2e?.sent || []).filter((m) => !name || m.name === name), name);
}
