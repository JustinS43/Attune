/*
 * Speech bubbles and name tags for the full-colour AR mode.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-07 (and the face tags of P-06).
 *
 * - Every visible face gets a name tag above the head (a dot for faces under 40 px). When the
 *   person talks, the tag grows into a speech bubble with a tail to the face: solid for the
 *   detected speaker, dashed for a probable one.
 * - Drafts are drawn softer and firm up when final; each bubble shows the latest
 *   bubble_chars x bubble_lines of text and fades bubble_fade_s after its last word.
 * - Bubbles are pushed apart when they overlap (and away from the status pill and alerts) and
 *   glide to their new place; faces and bubbles are smoothed, so nothing jitters.
 * - Off-screen speakers dock to the screen edge with a glowing chevron; the wearer's own words
 *   and typed replies sit in the "You" bar at the bottom; translations show the original line
 *   plus a language tag.
 */

import {
  W, H, FD, FT, MINT, ACCENT, font, clamp, lerp, easeOut, easeBack, follow, hexA, rrect, textW, glass, icon,
  eqBars, dot, keycap, chevrons, faceBrackets, edgeGlow,
} from './hud.js';

const F = {
  name: font(620, 25, FD),
  nameIt: font('italic 560', 25, FD),
  sub: font(450, 20, FT),
  body: font(430, 31, FT),
  orig: font('italic 400', 22, FT),
  lang: font(620, 16, FT),
  you: font(430, 28, FT),
  youLabel: font(620, 20, FT),
};
const LH = 41;
const PADX = 22;
const PADT = 15;
const PADB = 18;
const HEADH = 30;
const RAD = 22;
const TAG_H = 50;
const MARGIN = 28;
const MIN_Y = 30;
const BOTTOM = H - 150; // keep clear of the You bar
const GLASS_TAIL = 'rgba(30,33,41,0.80)';

export function createBubbleLayer() {
  const cards = new Map(); // key -> render state

  // ------------------------------------------------------------ content -> header description
  function header(face, b) {
    const proposal = face?.proposal;
    const proposing = proposal?.state === 'proposed';
    const confirmedAt = proposal?.state === 'confirmed' ? proposal : null;
    const known = face ? face.known : b?.color !== '#E6EAF0';
    const h = {
      name: b?.name ?? face.label,
      italic: false,
      lead: 'dot',
      color: face?.color ?? b?.color ?? '#FFFFFF',
      sub: null,
      subColor: 'rgba(255,255,255,0.62)',
      keys: false,
      unknown: false,
    };
    if (face) {
      if (proposing) {
        h.name = `${proposal.name}?`;
        h.lead = 'ring';
        h.keys = true;
        h.color = ACCENT;
      } else if (!known) {
        h.name = face.label;
        h.italic = true;
        h.lead = 'userplus';
        h.unknown = true;
        h.sub = 'New';
      } else {
        h.name = face.label;
        h.sub = face.relation || (face.status === 'enrolled' ? null : null);
        if (confirmedAt && confirmedAt.age < 2.2) {
          h.sub = 'Added';
          h.subColor = MINT;
        }
      }
    }
    return h;
  }

  function headerWidth(ctx, h, withEq, withLang) {
    let w = h.lead === 'userplus' ? 30 : 26;
    w += textW(ctx, h.name, h.italic ? F.nameIt : F.name);
    if (h.sub) w += 12 + textW(ctx, h.sub, F.sub);
    if (h.keys) w += 14 + 76;
    if (withLang) w += 104;
    if (withEq) w += 42;
    return w;
  }

  // ------------------------------------------------------------ measure one card
  function measure(ctx, item, cfg) {
    const { face, b } = item;
    const hd = header(face, b);
    item.hd = hd;
    if (item.type === 'tag') {
      const w = PADX * 2 - 4 + headerWidth(ctx, hd, false, false);
      return { w, h: TAG_H };
    }
    const withLang = b.lang && b.lang !== 'en';
    let bodyW = 0;
    const words = [];
    const lines = b.pending ? b.lines : b.lines;
    lines.forEach((ln, i) => {
      const txt = i === 0 && b.cut ? `… ${ln}` : ln;
      bodyW = Math.max(bodyW, textW(ctx, txt, F.body));
    });
    for (let i = 0; i < lines.length; i++) {
      const parts = lines[i].split(' ');
      if (i === 0 && b.cut) parts.unshift('…');
      for (const p of parts) words.push({ text: p, line: i });
    }
    let orig = null;
    if (b.orig) {
      const max = cfg.bubble_chars + 6;
      orig = b.orig.length > max ? `…${b.orig.slice(-max)}` : b.orig;
      bodyW = Math.max(bodyW, textW(ctx, orig, F.orig));
    }
    item.words = words;
    item.orig = orig;
    const headW = headerWidth(ctx, hd, true, withLang);
    const w = Math.max(220, PADX * 2 + Math.max(headW, bodyW));
    const h = PADT + HEADH + 8 + lines.length * LH + (orig ? 30 : 0) + PADB - 4;
    return { w, h };
  }

  // ------------------------------------------------------------ targets + push apart
  function target(item, m) {
    const f = item.face;
    const gap = item.type === 'bubble' ? 30 : 12;
    let x = f.cx - m.w / 2;
    let y = f.top - gap - m.h;
    let place = 'above';
    if (y < MIN_Y) y = MIN_Y;
    // beside the face only when a bubble squeezed against the top would cover the eyes
    if (item.type === 'bubble' && y + m.h > f.top + f.h * 0.22) {
      place = f.cx > W / 2 ? 'left' : 'right';
      y = f.cy - f.h * 0.25 - m.h / 2;
      x = place === 'left' ? f.cx - f.w * 0.7 - 34 - m.w : f.cx + f.w * 0.7 + 34;
    }
    return { x, y, w: m.w, h: m.h, place, mass: item.type === 'bubble' ? (item.b.speaking ? 4 : 2.5) : 1, ax: f.cx };
  }

  function relax(rects, obstacles) {
    const m = 14;
    for (let it = 0; it < 14; it++) {
      for (let i = 0; i < rects.length; i++) {
        for (let j = i + 1; j < rects.length; j++) push(rects[i], rects[j], m);
        for (const o of obstacles) if (o.owner !== rects[i].key) push(rects[i], o, m, true);
      }
      for (const r of rects) clampRect(r);
    }
  }
  function push(a, b, m, fixed = false) {
    const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x) + m;
    const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y) + m;
    if (ox <= m * 0.5 || oy <= m * 0.5) return;
    const sa = fixed ? 1 : b.mass / (a.mass + b.mass);
    const sb = fixed ? 0 : 1 - sa;
    if (oy < ox) {
      const acy = a.y + a.h / 2;
      const bcy = b.y + b.h / 2;
      const d = acy === bcy ? (a.ax <= (b.ax ?? 0) ? -1 : 1) : acy < bcy ? -1 : 1;
      a.y += d * oy * sa;
      b.y -= d * oy * sb;
    } else {
      const acx = a.x + a.w / 2;
      const bcx = b.x + b.w / 2;
      const d = acx === bcx ? ((a.ax ?? 0) <= (b.ax ?? 0) ? -1 : 1) : acx < bcx ? -1 : 1;
      a.x += d * ox * sa;
      b.x -= d * ox * sb;
    }
  }
  function clampRect(r) {
    r.x = clamp(r.x, MARGIN, W - MARGIN - r.w);
    r.y = clamp(r.y, MIN_Y, Math.max(MIN_Y, BOTTOM - r.h));
  }

  // ------------------------------------------------------------ drawing helpers
  function drawTail(ctx, rect, face, dashed, a) {
    let base;
    let tip;
    let nx;
    let ny;
    const r = rect;
    if (r.y + r.h <= face.cy - face.h * 0.2) {
      const bx = clamp(face.cx, r.x + RAD + 14, r.x + r.w - RAD - 14);
      base = [bx, r.y + r.h - 1];
      tip = [face.cx, Math.max(face.top - 6, r.y + r.h + 16)];
      nx = 1; ny = 0;
    } else if (r.x + r.w <= face.cx - face.w * 0.25) {
      const by = clamp(face.cy - face.h * 0.2, r.y + RAD + 10, r.y + r.h - RAD - 10);
      base = [r.x + r.w - 1, by];
      tip = [face.cx - face.w * 0.62, face.cy - face.h * 0.2];
      nx = 0; ny = 1;
    } else if (r.x >= face.cx + face.w * 0.25) {
      const by = clamp(face.cy - face.h * 0.2, r.y + RAD + 10, r.y + r.h - RAD - 10);
      base = [r.x + 1, by];
      tip = [face.cx + face.w * 0.62, face.cy - face.h * 0.2];
      nx = 0; ny = 1;
    } else if (r.y >= face.cy) {
      const bx = clamp(face.cx, r.x + RAD + 14, r.x + r.w - RAD - 14);
      base = [bx, r.y + 1];
      tip = [face.cx, face.cy + face.h * 0.72];
      nx = 1; ny = 0;
    } else return;
    const dx = tip[0] - base[0];
    const dy = tip[1] - base[1];
    const len = Math.hypot(dx, dy);
    if (len < 6) return;
    // control point: leave the bubble perpendicular to its edge, then bend toward the face
    const cx = base[0] + (ny ? dx * 0.55 : dx * 0.1);
    const cy = base[1] + (ny ? dy * 0.1 : dy * 0.55);
    ctx.save();
    ctx.globalAlpha *= a;
    if (dashed) {
      ctx.setLineDash([7, 6]);
      ctx.strokeStyle = 'rgba(255,255,255,0.82)';
      ctx.lineWidth = 2.4;
      ctx.lineCap = 'round';
      ctx.beginPath();
      ctx.moveTo(base[0], base[1]);
      ctx.quadraticCurveTo(cx, cy, tip[0], tip[1]);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(tip[0], tip[1], 4.5, 0, Math.PI * 2);
      ctx.stroke();
    } else {
      const hw = Math.min(14, 6 + len * 0.12);
      ctx.beginPath();
      ctx.moveTo(base[0] - nx * hw, base[1] - ny * hw);
      ctx.quadraticCurveTo(cx - nx * hw * 0.35, cy - ny * hw * 0.35, tip[0], tip[1]);
      ctx.quadraticCurveTo(cx + nx * hw * 0.35, cy + ny * hw * 0.35, base[0] + nx * hw, base[1] + ny * hw);
      ctx.closePath();
      ctx.fillStyle = GLASS_TAIL;
      ctx.fill();
      ctx.strokeStyle = 'rgba(255,255,255,0.16)';
      ctx.lineWidth = 1.2;
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawHeader(ctx, hd, x, cy, anim, o = {}) {
    let hx = x;
    if (hd.lead === 'userplus') {
      icon(ctx, 'userplus', hx - 2, cy - 11, 22, 'rgba(255,255,255,0.85)', 2);
      hx += 30;
    } else if (hd.lead === 'ring') {
      const p = 0.5 + 0.5 * Math.sin(anim * 4);
      ctx.save();
      ctx.strokeStyle = hd.color;
      ctx.lineWidth = 2;
      ctx.globalAlpha *= 0.5 + 0.5 * p;
      ctx.beginPath();
      ctx.arc(hx + 7, cy, 7 + p * 1.5, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
      hx += 26;
    } else {
      dot(ctx, hx + 7, cy, 7, hd.color);
      hx += 26;
    }
    const nf = hd.italic ? F.nameIt : F.name;
    ctx.font = nf;
    ctx.textBaseline = 'middle';
    ctx.fillStyle = hd.unknown ? 'rgba(255,255,255,0.92)' : '#FFFFFF';
    ctx.fillText(hd.name, hx, cy);
    hx += textW(ctx, hd.name, nf) + 12;
    if (hd.sub) {
      ctx.font = F.sub;
      ctx.fillStyle = hd.subColor;
      ctx.fillText(hd.sub, hx, cy + 1);
      hx += textW(ctx, hd.sub, F.sub) + 12;
    }
    if (hd.keys) {
      hx += 2;
      hx += keycap(ctx, hx, cy, 'Y', { size: 16, color: MINT, stroke: hexA(MINT, 0.6), fill: hexA(MINT, 0.14) }) + 8;
      keycap(ctx, hx, cy, 'N', { size: 16 });
    }
    return hx;
  }

  function drawLangTag(ctx, b, x, cy, anim) {
    const w = 94;
    rrect(ctx, x, cy - 14, w, 28, 14);
    ctx.fillStyle = 'rgba(255,255,255,0.14)';
    ctx.fill();
    icon(ctx, 'globe', x + 8, cy - 9, 18, ACCENT, 2);
    ctx.font = F.lang;
    ctx.textBaseline = 'middle';
    ctx.fillStyle = ACCENT;
    const code = (b.lang || '').toUpperCase().slice(0, 2);
    if (b.translated) ctx.fillText(`${code} → EN`, x + 31, cy + 1);
    else {
      ctx.fillText(code, x + 31, cy + 1);
      for (let i = 0; i < 3; i++) {
        ctx.globalAlpha = 0.35 + 0.65 * Math.max(0, Math.sin(anim * 6 - i * 0.8));
        ctx.beginPath();
        ctx.arc(x + 62 + i * 8, cy + 1, 2.2, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }
    return w;
  }

  /** Update the per-word birth times so new words ease in like the film. */
  function syncWords(cs, item, anim) {
    const b = item.b;
    const full = b.text.split(/\s+/).filter(Boolean);
    if (cs.utt !== b.utt_id) {
      cs.births = [];
      cs.full = [];
    }
    let fresh = 0;
    const firstShow = cs.births.length === 0;
    for (let i = 0; i < full.length; i++) {
      if (cs.births[i] === undefined || cs.full[i] !== full[i]) cs.births[i] = anim + (firstShow ? fresh++ * 0.035 : 0);
    }
    cs.full = full;
    // displayed words are the tail of the utterance: map each to its index in the full text
    const shown = item.words.filter((w) => w.text !== '…').length;
    let k = full.length - shown;
    cs.words = item.words.map((w) => (w.text === '…' ? { ...w, born: -1e9 } : { ...w, born: cs.births[k++] ?? anim }));
    cs.utt = b.utt_id;
  }

  function drawBody(ctx, cs, item, x, y, anim) {
    const b = item.b;
    let by = y + PADT + HEADH + 8;
    if (item.orig) {
      ctx.font = F.orig;
      ctx.textBaseline = 'alphabetic';
      ctx.fillStyle = 'rgba(255,255,255,0.6)';
      ctx.fillText(item.orig, x + PADX, by + 21);
      by += 30;
    }
    ctx.font = F.body;
    ctx.textBaseline = 'alphabetic';
    const space = textW(ctx, ' ', F.body);
    const firm = cs.firm;
    let line = -1;
    let wx = 0;
    for (const w of cs.words) {
      if (w.line !== line) {
        line = w.line;
        wx = x + PADX;
      }
      const p = clamp((anim - w.born) / 0.22);
      if (p > 0) {
        const ly = by + 31 + line * LH + (1 - easeOut(p)) * 7;
        const dim = w.text === '…' ? 0.5 : lerp(b.pending ? 0.66 : 0.7, 1, firm);
        ctx.globalAlpha = p * dim;
        ctx.fillStyle = '#FFFFFF';
        ctx.fillText(w.text, wx, ly);
      }
      wx += textW(ctx, w.text, F.body) + space;
    }
    ctx.globalAlpha = 1;
  }

  function drawCard(ctx, blur, cs, item, anim) {
    const { x, y, w, h } = cs;
    const a = cs.a * (item.dim ?? 1);
    if (a <= 0.004) return;
    const hd = item.hd;
    const pop = lerp(0.9, 1, easeBack(clamp(cs.a), 1.3));
    const ox = x + w / 2;
    const oy = y + h;
    ctx.save();
    ctx.translate(ox, oy);
    ctx.scale(pop, pop);
    ctx.translate(-ox, -oy);
    if (item.type === 'bubble' && item.face && cs.tailA > 0.01) drawTail(ctx, cs, item.face, item.b.dashed, a * cs.tailA);
    const r = Math.min(RAD, h / 2);
    glass(ctx, item.blur, x, y, w, h, r, {
      alpha: a,
      glow: hd.unknown ? null : hd.color,
      border: hd.unknown ? null : hd.lead === 'ring' ? hexA(ACCENT, 0.55) : 'rgba(255,255,255,0.20)',
    });
    ctx.save();
    ctx.globalAlpha *= a;
    if (hd.unknown) {
      rrect(ctx, x + 1, y + 1, w - 2, h - 2, r - 1);
      ctx.setLineDash([7, 6]);
      ctx.lineDashOffset = -anim * 18;
      ctx.strokeStyle = 'rgba(255,255,255,0.6)';
      ctx.lineWidth = 1.6;
      ctx.stroke();
      ctx.setLineDash([]);
    }
    const cy = item.type === 'tag' ? y + h / 2 : y + PADT + HEADH / 2;
    drawHeader(ctx, hd, x + PADX - (item.type === 'tag' ? 2 : 0), cy, anim);
    if (item.type === 'bubble') {
      const b = item.b;
      let rx = x + w - PADX;
      rx -= 26;
      eqBars(ctx, rx, cy, hd.unknown ? '#FFFFFF' : hd.color, anim, b.speaking ? Math.max(0.55, item.face?.lip ?? 1) : 0.18, 18);
      if (b.lang && b.lang !== 'en') drawLangTag(ctx, b, rx - 104, cy, anim);
      ctx.save();
      rrect(ctx, x, y, w, h, r);
      ctx.clip();
      drawBody(ctx, cs, item, x, y, anim);
      ctx.restore();
    }
    ctx.restore();
    ctx.restore();
  }

  // ------------------------------------------------------------ off-screen docking
  function drawOffscreen(ctx, blur, list, view, anim, dim) {
    const bySide = { left: [], right: [], behind: [] };
    for (const o of list) (bySide[o.side === 'right' ? 'right' : o.side === 'left' ? 'left' : 'behind']).push(o);
    for (const side of ['left', 'right']) {
      let y = 300;
      for (const o of bySide[side]) {
        const b = o.bubble;
        const a = (b ? b.alpha : 1) * dim;
        const nameW = textW(ctx, o.name, F.name);
        const where = `·  ${side}`;
        const whereW = textW(ctx, where, F.sub);
        let w;
        let h;
        let lines = [];
        if (b) {
          lines = b.lines.map((l, i) => (i === 0 && b.cut ? `… ${l}` : l));
          const bodyW = Math.max(...lines.map((l) => textW(ctx, l, F.body)));
          w = Math.max(bodyW, 26 + nameW + 12 + whereW + 60) + PADX * 2;
          h = PADT + HEADH + 8 + lines.length * LH + PADB - 4;
        } else {
          w = PADX * 2 + 26 + nameW + 12 + whereW;
          h = TAG_H;
        }
        const key = `off:${o.key}`;
        let cs = cards.get(key);
        if (!cs) {
          cs = { a: 0, slide: 0, y, h, seen: anim };
          cards.set(key, cs);
        }
        cs.seen = anim;
        cs.present = true;
        cs.a += (1 - cs.a) * follow(view.dt, 9);
        cs.y += (y - cs.y) * follow(view.dt, 9);
        cs.h += (h - cs.h) * follow(view.dt, 14);
        const ea = easeOut(cs.a);
        const slide = (1 - ea) * 60;
        const x = side === 'left' ? 76 - slide : W - 76 - w + slide;
        const pulse = 0.75 + 0.25 * Math.sin(anim * 5);
        // edge light in the speaker's colour
        ctx.save();
        const ex = side === 'left' ? 0 : W;
        const g = ctx.createRadialGradient(ex, cs.y + cs.h / 2, 0, ex, cs.y + cs.h / 2, 420);
        g.addColorStop(0, hexA(o.color, (b ? 0.42 : 0.22) * a * ea * pulse));
        g.addColorStop(1, hexA(o.color, 0));
        ctx.fillStyle = g;
        ctx.fillRect(side === 'left' ? 0 : W - 420, cs.y + cs.h / 2 - 420, 420, 840);
        ctx.restore();
        glass(ctx, blur, x, cs.y, w, cs.h, Math.min(RAD, cs.h / 2), { alpha: a * ea, glow: o.color });
        ctx.save();
        ctx.globalAlpha *= a * ea;
        const d = side === 'left' ? -1 : 1;
        chevrons(ctx, (side === 'left' ? x - 26 : x + w + 26) + Math.sin(anim * 6) * 4 * d, cs.y + cs.h / 2, d, o.color, 15, 4);
        const hy = b ? cs.y + PADT + HEADH / 2 : cs.y + cs.h / 2;
        dot(ctx, x + PADX + 7, hy, 7, o.color);
        ctx.font = F.name;
        ctx.textBaseline = 'middle';
        ctx.fillStyle = '#FFFFFF';
        ctx.fillText(o.name, x + PADX + 26, hy);
        ctx.font = F.sub;
        ctx.fillStyle = 'rgba(255,255,255,0.62)';
        ctx.fillText(where, x + PADX + 26 + nameW + 12, hy + 1);
        if (b) {
          eqBars(ctx, x + w - PADX - 26, hy, o.color, anim, b.speaking ? 1 : 0.18, 18);
          ctx.save();
          rrect(ctx, x, cs.y, w, cs.h, RAD);
          ctx.clip();
          ctx.font = F.body;
          ctx.textBaseline = 'alphabetic';
          ctx.fillStyle = b.final ? '#FFFFFF' : 'rgba(255,255,255,0.74)';
          lines.forEach((l, i) => ctx.fillText(l, x + PADX, cs.y + PADT + HEADH + 8 + 31 + i * LH));
          ctx.restore();
        }
        ctx.restore();
        y += h + 18;
      }
    }
    // someone behind you: glow along the bottom edge, docked above the You bar
    let by = BOTTOM + 10;
    for (const o of bySide.behind) {
      const b = o.bubble;
      const a = (b ? b.alpha : 1) * dim;
      edgeGlow(ctx, 'behind', o.color, a * 0.8, 480);
      const text = b ? b.lines.join(' ') : '';
      const parts = `${o.name}  ·  behind you${text ? '  —  ' + text : ''}`;
      const w = textW(ctx, parts, F.sub) + 90;
      glass(ctx, blur, W / 2 - w / 2, by - 50, w, 44, 22, { alpha: a, glow: o.color });
      ctx.save();
      ctx.globalAlpha *= a;
      dot(ctx, W / 2 - w / 2 + 26, by - 28, 6, o.color);
      ctx.font = F.sub;
      ctx.textBaseline = 'middle';
      ctx.fillStyle = '#FFFFFF';
      ctx.fillText(parts, W / 2 - w / 2 + 44, by - 27);
      ctx.restore();
      by -= 54;
    }
  }

  // ------------------------------------------------------------ lower caption + You bar
  function drawLower(ctx, blur, b, anim, dim) {
    if (!b) return;
    const lines = b.lines.map((l, i) => (i === 0 && b.cut ? `… ${l}` : l));
    const nameW = textW(ctx, b.name, F.name);
    const bodyW = Math.max(...lines.map((l) => textW(ctx, l, F.body)));
    const w = Math.max(bodyW, nameW + 80) + PADX * 2;
    const h = PADT + HEADH + 8 + lines.length * LH + PADB - 4;
    const x = W / 2 - w / 2;
    const y = BOTTOM - h - 6;
    const a = b.alpha * dim;
    glass(ctx, blur, x, y, w, h, RAD, { alpha: a, glow: b.color });
    ctx.save();
    ctx.globalAlpha *= a;
    const hy = y + PADT + HEADH / 2;
    dot(ctx, x + PADX + 7, hy, 7, b.color);
    ctx.font = F.name;
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#FFFFFF';
    ctx.fillText(b.name, x + PADX + 26, hy);
    eqBars(ctx, x + w - PADX - 26, hy, b.color, anim, b.speaking ? 1 : 0.18, 18);
    ctx.font = F.body;
    ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = b.final ? '#FFFFFF' : 'rgba(255,255,255,0.72)';
    lines.forEach((l, i) => ctx.fillText(l, x + PADX, y + PADT + HEADH + 8 + 31 + i * LH));
    ctx.restore();
  }

  function drawYou(ctx, blur, b, view, anim, dim) {
    const key = 'you-bar';
    let cs = cards.get(key);
    if (!cs) {
      cs = { a: 0, w: 300, seen: anim };
      cards.set(key, cs);
    }
    const present = !!b;
    cs.a += ((present ? 1 : 0) - cs.a) * follow(view.dt, 8);
    if (b) cs.b = b;
    const bb = cs.b;
    if (!bb || cs.a < 0.01) return;
    const label = bb.typed ? 'You (typed)' : 'You';
    const text = bb.lines.map((l, i) => (i === 0 && bb.cut ? `… ${l}` : l)).join(' ');
    const lw = textW(ctx, label, F.youLabel);
    const tw = textW(ctx, text, F.you);
    const w = Math.min(W - 200, tw + lw + 22 * 2 + 40 + (view.speaking && !bb.final ? 40 : 0));
    cs.w += (w - cs.w) * follow(view.dt, 14);
    const h = 60;
    const x = W / 2 - cs.w / 2;
    const y = H - 118 + (1 - easeOut(cs.a)) * 16;
    const a = cs.a * (present ? b.alpha : 1) * dim;
    glass(ctx, blur, x, y, cs.w, h, h / 2, { alpha: a, tint: 'rgba(14,16,22,0.46)' });
    ctx.save();
    ctx.globalAlpha *= a;
    rrect(ctx, x, y, cs.w, h, h / 2);
    ctx.clip();
    ctx.font = F.youLabel;
    ctx.textBaseline = 'middle';
    ctx.fillStyle = bb.typed ? ACCENT : 'rgba(255,255,255,0.58)';
    ctx.fillText(label, x + 24, y + h / 2 + 1);
    ctx.font = F.you;
    ctx.fillStyle = bb.final ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.72)';
    ctx.fillText(text, x + 24 + lw + 16, y + h / 2 + 1);
    ctx.restore();
  }

  // ------------------------------------------------------------ main entry
  function render(ctx, view, env) {
    const { blur, anim } = env;
    const dim = view.paused ? 0.3 : 1;
    const cfg = view.config;
    const dt = view.dt;

    // face brackets: strangers when they first appear, proposals while they wait for an answer
    for (const f of view.faces) {
      if (f.tiny) continue;
      const prop = f.proposal;
      if (prop && prop.state === 'proposed') {
        faceBrackets(ctx, f, { a: 0.9 * dim, color: ACCENT, dashed: true, t: anim, scale: 1.3 });
      } else if (prop && prop.state === 'confirmed' && prop.age < 1.2) {
        faceBrackets(ctx, f, { a: (1 - prop.age / 1.2) * dim, color: MINT, t: anim, scale: 1.3 });
      } else if (!f.known && anim - f.born < 3.5) {
        const a = clamp((anim - f.born) / 0.35) * clamp((3.5 - (anim - f.born)) / 0.5);
        faceBrackets(ctx, f, { a: a * 0.85 * dim, color: '#FFFFFF', dashed: true, t: anim, scale: 1.3 });
      }
    }

    // items: a bubble or a tag per face, dots for tiny faces
    const items = [];
    const bubbleByFace = new Map(view.bubbles.map((b) => [b.face.key, b]));
    for (const f of view.faces) {
      const b = bubbleByFace.get(f.key);
      if (f.tiny && !b) {
        items.push({ key: f.key, type: 'dot', face: f });
        continue;
      }
      items.push({ key: f.key, type: b ? 'bubble' : 'tag', face: f, b, blur, dim });
    }

    const live = items.filter((i) => i.type !== 'dot');
    const rects = [];
    for (const it of live) {
      const m = measure(ctx, it, cfg);
      const t = target(it, m);
      t.key = it.key;
      it.target = t;
      rects.push(t);
    }
    // keep clear of the status pill, the alerts, and everyone else's face (eyes to chin)
    const obstacles = [{ x: 30, y: 26, w: 470, h: 78 }, ...(env.obstacles || [])];
    for (const f of view.faces) {
      if (f.tiny) continue;
      obstacles.push({ x: f.cx - f.w * 0.3, y: f.cy - f.h * 0.28, w: f.w * 0.6, h: f.h * 0.62, owner: f.key });
    }
    relax(rects, obstacles);

    // smooth toward targets
    const present = new Set();
    for (const it of live) {
      present.add(it.key);
      let cs = cards.get(it.key);
      const t = it.target;
      if (!cs) {
        cs = { x: t.x, y: t.y, w: t.w, h: t.h, a: 0, tailA: 0, firm: 0, words: [], utt: null };
        cards.set(it.key, cs);
      }
      const kp = follow(dt, 9);
      const ks = follow(dt, 14);
      cs.x += (t.x - cs.x) * kp;
      cs.y += (t.y - cs.y) * kp;
      cs.w += (t.w - cs.w) * ks;
      cs.h += (t.h - cs.h) * ks;
      const aT = it.type === 'bubble' ? it.b.alpha : 1;
      cs.a += (aT - cs.a) * follow(dt, 10);
      cs.tailA += ((it.type === 'bubble' ? 1 : 0) - cs.tailA) * follow(dt, 10);
      if (it.type === 'bubble') {
        if (cs.utt !== it.b.utt_id) cs.firm = 0;
        cs.firm += ((it.b.final ? 1 : 0) - cs.firm) * follow(dt, 8);
        syncWords(cs, it, anim);
      }
      cs.item = it;
      cs.seen = anim;
    }
    // cards whose face disappeared fade out in place
    for (const [key, cs] of cards) {
      if (key.startsWith('off:') || key === 'you-bar') continue;
      if (!present.has(key)) {
        cs.a += (0 - cs.a) * follow(dt, 10);
        if (cs.a < 0.01 || anim - cs.seen > 3) cards.delete(key);
      }
    }
    for (const [key, cs] of cards) {
      if (key.startsWith('off:') && anim - cs.seen > 0.05) {
        cs.a += (0 - cs.a) * follow(dt, 10);
        if (cs.a < 0.01) cards.delete(key);
      }
    }

    // draw tags first, then bubbles (speakers on top)
    const order = [...cards.entries()]
      .filter(([k]) => !k.startsWith('off:') && k !== 'you-bar')
      .sort((a, b) => rank(a[1]) - rank(b[1]));
    for (const [, cs] of order) {
      const it = cs.item;
      it.dim = dim;
      it.blur = blur;
      drawCard(ctx, blur, cs, it, anim);
    }
    for (const it of items) {
      if (it.type !== 'dot') continue;
      const f = it.face;
      ctx.save();
      ctx.globalAlpha = dim;
      dot(ctx, f.cx, f.top - 8, 7, f.color, 14);
      ctx.strokeStyle = 'rgba(255,255,255,0.7)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(f.cx, f.top - 8, 11, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }

    drawOffscreen(ctx, blur, view.offscreen, view, anim, dim);
    drawLower(ctx, blur, view.lower, anim, dim);
    drawYou(ctx, blur, view.you, view, anim, dim);
    return rects;
  }

  function rank(cs) {
    const it = cs.item;
    if (!it) return 0;
    if (it.type === 'bubble') return it.b.speaking ? 3 : 2;
    return 1;
  }

  return { render };
}
