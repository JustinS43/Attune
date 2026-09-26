/*
 * Speech bubbles and name tags for the full-colour AR mode.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-07 (and the face tags of P-06).
 *
 * Built to be read all day, so nothing moves unless it has to:
 * - One card per person. While they are quiet it is a small name tag above the head; when they
 *   talk it grows into a speech bubble whose header is that same name (no second pill), with a
 *   tail to the face: solid for the detected speaker, dashed for a probable one.
 * - One slot per bubble, chosen once: centred above the head, or beside the face when there is
 *   no room above. Slots never cover a face (anyone's, with a margin), the status pill or an
 *   alert. A slot that stops fitting is kept for SLOT_HOLD_S before the bubble glides (about
 *   0.25 s, critically damped, no bounce) to a new one; an alert that needs the space moves it
 *   at once.
 * - Faces are filtered in store.js; a dead zone here keeps small head movements from moving the
 *   bubble at all, and the tail stretches toward the face instead.
 * - A bubble's width is fixed when it appears (room for about bubble_chars characters) and its
 *   height is reserved for bubble_lines lines, so words fill in without reflowing earlier lines;
 *   a third line rolls the text up by one line. Drafts are dimmer and firm up in place.
 * - The current speaker is fully opaque, earlier speakers dim, and each bubble stays at least
 *   long enough to be read (store.js); at most three at a time.
 * - Off-screen speakers dock to their side's edge in the order they arrived, at a height chosen
 *   with the bubbles' rules (never over a face, a card, the pill or an alert; a place that stops
 *   fitting is held for SLOT_HOLD_S before the dock glides to the nearest free one); the
 *   wearer's own words sit in the "You" bar; translations show the original line plus a
 *   language tag, and a line still waiting for its translation shows only there, small and
 *   dimmed with "translating…", never mixed into the main text.
 * - Calm when many people talk: nothing blinks, pulses or loops. The speaking meter is a still
 *   glyph that only rises or settles (about 0.2 s) when a speaker starts or stops, held through
 *   short pauses; docked speakers get one static chevron toward their side and a steady edge light.
 */

import {
  FD, FT, MINT, ACCENT, font, clamp, lerp, easeOut, hexA, rrect, textW, glass, icon,
  eqBars, dot, keycap, chevrons, arrow, faceBrackets, edgeGlow, REGION, tailFill,
  springStep, createLineWrap, scrollTo, REDUCED_MOTION,
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
const PADB = 16;
const HEADH = 30;
const BODY_GAP = 8;
const ORIG_H = 30;
const RAD = 22;
const TAG_H = 50;
// Layout bounds follow the mode's display region (hud.js REGION): nothing is drawn outside it.
const MARGIN = 24; // from the display's edges
const TOP_GAP = 24;
const GAP = 26; // between a bubble above the head and the head
const SIDE_GAP = 30; // between a bubble beside the face and the face
const FACE_PAD = 14; // keep-out margin around every face
const SLOT_HOLD_S = 0.7; // an unfit slot is tolerated this long before the bubble moves
const MOVE_W = 16; // spring stiffness (rad/s): a move settles in about 0.25 s
const WIDTHS = [1, 0.82, 0.68]; // share of the full reserved width tried when space is tight
const SAMPLE = 'Did you hear the doorbell a minute ago? The meeting starts at three.';
const SPEAK_HOLD_S = 0.6; // a speaking glyph stays up through pauses this short
const STILL = 0.6; // fixed phase for eqBars: a still level glyph, never a moving meter
const R = () => REGION;
/** The status pill (alerts.js drawStatus) is always there: slots slide around it. */
const pillRect = () => ({ x: R().x + 14, y: R().y + 12, w: 440, h: 78 });
const TOLERANCE = 150; // px²: a rounded corner grazing a face or a panel is not an overlap
const minY = () => R().y + TOP_GAP;
const bottomY = () => R().y + R().h - 150; // keep clear of the You bar
const midX = () => R().x + R().w / 2;

const overlap = (a, b) => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
/** Where a face must not be covered: the face box (forehead to chin), with a margin. */
const keepOut = (f, pad = FACE_PAD) => ({ x: f.cx - f.w * 0.55 - pad, y: f.cy - f.h * 0.62 - pad, w: f.w * 1.1 + pad * 2, h: f.h * 1.22 + pad * 2 });
/** Rope-style dead zone: the anchor only moves once the face has moved more than dz. */
const dead = (d, dz) => (d > dz ? d - dz : d < -dz ? d + dz : 0);

export function createBubbleLayer() {
  const cards = new Map(); // face key -> card (tag or bubble)
  let ghosts = []; // what a card showed before it re-formed elsewhere, fading out in place
  const docks = new Map(); // off-screen key -> docked card
  const dockSlots = { left: [], right: [] }; // slot index -> key, in arrival order
  let lowerCard = null;
  const youCard = { a: 0, wrap: createLineWrap(), scroll: {}, b: null };
  let avgChar = 0;

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
        h.sub = face.relation || null;
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

  /** Full reserved body width: about bubble_chars characters of body text (24-42). */
  function reserveW(ctx, cfg) {
    if (!avgChar) avgChar = textW(ctx, SAMPLE, F.body) / SAMPLE.length;
    return Math.round(avgChar * clamp(cfg.bubble_chars, 24, 42));
  }
  const rowsOf = (cfg) => clamp(Math.round(cfg.bubble_lines), 1, 3);
  const bubbleH = (rows, orig) => PADT + HEADH + BODY_GAP + (orig ? ORIG_H : 0) + rows * LH + PADB;

  // ------------------------------------------------------------ slots
  /** The rect a card of size w x h takes in `slot` next to anchor A (a dead-zoned face). */
  function slotRect(slot, A, w, h) {
    const g = R();
    const eye = A.cy - A.h * 0.15;
    let x;
    let y;
    if (slot === 'above' || slot === 'below') {
      x = clamp(A.cx - w / 2, g.x + MARGIN, g.x + g.w - MARGIN - w);
      // above the hair when there is room, else as high as the display allows (clear of the face)
      y = slot === 'above' ? Math.max(minY(), A.top - GAP - h) : A.cy + A.h * 0.62 + GAP;
    } else {
      // beside the face at eye level, slid inside the display if needed (fit() rejects it if
      // that would cover the face)
      x = slot === 'right' ? A.cx + A.w * 0.55 + SIDE_GAP : A.cx - A.w * 0.55 - SIDE_GAP - w;
      x = clamp(x, g.x + MARGIN, Math.max(g.x + MARGIN, g.x + g.w - MARGIN - w));
      y = clamp(eye - h / 2, minY(), Math.max(minY(), bottomY() - h));
    }
    // slide past the status pill rather than give up the slot (beside: down; above/below: right)
    const P = pillRect();
    if (overlap({ x, y, w, h }, P) > TOLERANCE) {
      if (slot === 'left' || slot === 'right') y = Math.min(P.y + P.h + 10, Math.max(minY(), bottomY() - h));
      else x = Math.min(P.x + P.w + 10, g.x + g.w - MARGIN - w);
    }
    return { x, y, w, h, slot };
  }

  /**
   * How well a rect fits: out of the display, over a face, over a placed bubble, or over an
   * obstacle (status pill, alerts). Returns { ok, hard, cost }; `hard` means an obstacle wants
   * the space now.
   */
  function fit(r, selfKey, A, faces, placed, obstacles) {
    const g = R();
    let cost = 0;
    let hard = false;
    const out = Math.max(0, g.x + MARGIN - r.x) + Math.max(0, r.x + r.w - (g.x + g.w - MARGIN)) + Math.max(0, minY() - r.y) + Math.max(0, r.y + r.h - bottomY());
    cost += out * 400;
    const over = (a, b) => {
      const v = overlap(a, b);
      return v > TOLERANCE ? v : 0;
    };
    if (A) cost += over(r, keepOut(A)) * 2;
    for (const f of faces) {
      if (f.key === selfKey || f.tiny) continue;
      cost += over(r, keepOut(f)) * 2;
    }
    for (const p of placed) cost += over(r, p) * 0.5; // an older bubble can step back instead
    cost += over(r, pillRect()) * 3;
    for (const o of obstacles) {
      const v = over(r, o);
      if (v > 0) {
        cost += v * 3;
        hard = true; // an alert or a toast needs the space now
      }
    }
    return { ok: cost < 1, hard, cost };
  }

  function slotOrder(A, first) {
    const toward = A.cx > midX() ? 'left' : 'right';
    const away = toward === 'left' ? 'right' : 'left';
    const order = ['above', toward, away, 'below'];
    if (first) order.unshift(first);
    return [...new Set(order)];
  }

  /** Pick a slot (and, for a new bubble, a width): the first that fits, else the least bad. */
  function chooseSlot(card, A, faces, placed, obstacles, sizes, first) {
    let best = null;
    for (const size of sizes) {
      for (const slot of slotOrder(A, first)) {
        const r = slotRect(slot, A, size.w, size.h);
        const q = fit(r, card.key, A, faces, placed, obstacles);
        if (q.ok) return { slot, size, r };
        if (!best || q.cost < best.cost) best = { slot, size, r, cost: q.cost };
      }
    }
    return best;
  }

  // ------------------------------------------------------------ drawing helpers
  function drawTail(ctx, rect, slot, face, dashed, a) {
    let base;
    let tip;
    let nx;
    let ny;
    const r = rect;
    const eye = face.cy - face.h * 0.15;
    if (slot === 'above') {
      const bx = clamp(face.cx, r.x + RAD + 14, r.x + r.w - RAD - 14);
      base = [bx, r.y + r.h - 1];
      tip = [face.cx, Math.max(face.top - 4, r.y + r.h + 10)];
      nx = 1; ny = 0;
    } else if (slot === 'below') {
      const bx = clamp(face.cx, r.x + RAD + 14, r.x + r.w - RAD - 14);
      base = [bx, r.y + 1];
      tip = [face.cx, Math.min(face.cy + face.h * 0.62, r.y - 10)];
      nx = 1; ny = 0;
    } else if (slot === 'left') {
      const by = clamp(eye, r.y + RAD + 10, r.y + r.h - RAD - 10);
      base = [r.x + r.w - 1, by];
      tip = [Math.max(face.cx - face.w * 0.52, r.x + r.w + 10), eye];
      nx = 0; ny = 1;
    } else {
      const by = clamp(eye, r.y + RAD + 10, r.y + r.h - RAD - 10);
      base = [r.x + 1, by];
      tip = [Math.min(face.cx + face.w * 0.52, r.x - 10), eye];
      nx = 0; ny = 1;
    }
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
      ctx.fillStyle = tailFill();
      ctx.fill();
      ctx.strokeStyle = 'rgba(255,255,255,0.16)';
      ctx.lineWidth = 1.2;
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawHeader(ctx, hd, x, cy) {
    let hx = x;
    if (hd.lead === 'userplus') {
      icon(ctx, 'userplus', hx - 2, cy - 11, 22, 'rgba(255,255,255,0.85)', 2);
      hx += 30;
    } else if (hd.lead === 'ring') {
      ctx.save();
      ctx.strokeStyle = hd.color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(hx + 7, cy, 8, 0, Math.PI * 2);
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

  function drawLangTag(ctx, b, x, cy) {
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
      // translation pending: three still dots (no blinking)
      ctx.save();
      ctx.globalAlpha *= 0.7;
      for (let i = 0; i < 3; i++) {
        ctx.beginPath();
        ctx.arc(x + 62 + i * 8, cy + 1, 2.2, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }
    return w;
  }

  /** The tail of a one-line string that fits in maxW, with a leading ellipsis when cut. */
  function fitTail(ctx, text, maxW, f) {
    if (textW(ctx, text, f) <= maxW) return text;
    const words = text.split(/\s+/);
    while (words.length > 1 && textW(ctx, `… ${words.join(' ')}`, f) > maxW) words.shift();
    return `… ${words.join(' ')}`;
  }

  /**
   * A line still waiting for its translation: the original, small and dimmed, then a still
   * "translating…" hint. It never joins the main line, so languages are never mixed there.
   */
  function drawPending(ctx, orig, x, y, maxW) {
    const hint = 'translating…';
    const hw = textW(ctx, hint, F.orig);
    ctx.save();
    ctx.font = F.orig;
    ctx.textBaseline = 'alphabetic';
    const text = fitTail(ctx, orig, Math.max(40, maxW - hw - 18), F.orig);
    ctx.fillStyle = 'rgba(255,255,255,0.46)';
    ctx.fillText(text, x, y);
    ctx.fillStyle = hexA(ACCENT, 0.75);
    ctx.fillText(hint, x + textW(ctx, text, F.orig) + 14, y);
    ctx.restore();
  }

  /** In a dock or the lower caption (no original row): the waiting line under the text, if it fits. */
  function pendingBelow(ctx, b, wrap, rows, x, y, w) {
    if (!b.pending || !b.orig) return;
    const n = Math.min(wrap.lines.length, rows);
    if (n < rows) drawPending(ctx, b.orig, x, y + n * LH + 26, w);
  }

  /**
   * Caption text in a fixed window of `rows` lines: words fade in where they will stay (drafts
   * dimmer, firming up in place), and the window rolls up one line at a time.
   */
  function drawText(ctx, wrap, top, x, y, rows, anim, o = {}) {
    const lines = wrap.lines;
    if (!lines.length) return;
    const lh = o.lh ?? LH;
    const base = o.base ?? 31;
    ctx.save();
    ctx.beginPath();
    ctx.rect(x - 6, y - 2, o.w + 12, rows * lh + 6);
    ctx.clip();
    ctx.font = o.font ?? F.body;
    ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = o.color ?? '#FFFFFF';
    const alpha0 = ctx.globalAlpha;
    for (let i = Math.max(0, Math.floor(top)); i < lines.length; i++) {
      const row = i - top;
      if (row > rows) break;
      const lineA = row < 0 ? clamp(1 + row * 1.6) : 1; // a line leaving at the top fades as it goes
      const ly = y + base + row * lh;
      wrap.eachWord(i, (t, m, wx) => {
        const p = clamp((anim - m.born) / 0.2);
        if (p <= 0) return;
        const firm = t.final ? (m.firm == null ? 1 : clamp((anim - m.firm) / 0.25)) : 0;
        ctx.globalAlpha = alpha0 * lineA * p * lerp(o.draft ?? 0.64, 1, firm);
        const rise = REDUCED_MOTION ? 0 : (1 - easeOut(p)) * 4;
        ctx.fillText(t.text, x + wx, ly + rise);
      });
    }
    ctx.restore();
  }

  /**
   * The speaking glyph's height (0.18 quiet .. 1 speaking) for one card. It changes only when the
   * speaker starts or stops (held through pauses under SPEAK_HOLD_S), easing over about 0.2 s.
   */
  function speakLevel(o, on, anim) {
    if (on) o.spkAt = anim;
    const want = o.spkAt != null && anim - o.spkAt < SPEAK_HOLD_S ? 1 : 0;
    const dt = o.spkT == null ? 1 : clamp(anim - o.spkT, 0, 0.1);
    o.spkT = anim;
    o.spk = REDUCED_MOTION || o.spk == null ? want : o.spk + (want - o.spk) * (1 - Math.exp(-dt * 16));
    return lerp(0.18, 1, o.spk);
  }

  function drawCard(ctx, card, anim) {
    const { x, y, w, h } = card;
    const a = card.a;
    if (a <= 0.004) return;
    const item = card.item;
    const hd = item.hd;
    const s = REDUCED_MOTION ? 1 : lerp(0.96, 1, easeOut(clamp(card.appear)));
    const ox = x + w / 2;
    const oy = card.slot === 'above' ? y + h : y + h / 2;
    ctx.save();
    ctx.translate(ox, oy);
    ctx.scale(s, s);
    ctx.translate(-ox, -oy);
    if (card.tailA > 0.01 && card.face) drawTail(ctx, card, card.slot, card.face, item.b?.dashed, a * card.tailA);
    const r = Math.min(RAD, h / 2);
    glass(ctx, card.blur, x, y, w, h, r, {
      alpha: a,
      glow: hd.unknown ? null : hd.color,
      border: hd.unknown ? null : hd.lead === 'ring' ? hexA(ACCENT, 0.55) : 'rgba(255,255,255,0.20)',
    });
    ctx.save();
    ctx.globalAlpha *= a;
    if (hd.unknown) {
      rrect(ctx, x + 1, y + 1, w - 2, h - 2, r - 1);
      ctx.setLineDash([7, 6]);
      ctx.strokeStyle = 'rgba(255,255,255,0.6)';
      ctx.lineWidth = 1.6;
      ctx.stroke();
      ctx.setLineDash([]);
    }
    // the header row sits at the same place in a tag and in a bubble, so the name never jumps
    const cy = y + lerp(TAG_H / 2, PADT + HEADH / 2, clamp((h - TAG_H) / 40));
    ctx.save();
    rrect(ctx, x, y, w, h, r);
    ctx.clip();
    drawHeader(ctx, hd, x + PADX - (card.kind === 'tag' ? 2 : 0), cy);
    if (card.textA > 0.01 && (card.b || card.lastB)) {
      const b = card.b ?? card.lastB;
      ctx.save();
      ctx.globalAlpha *= card.textA;
      const rx = x + w - PADX - 26;
      eqBars(ctx, rx, cy, hd.unknown ? '#FFFFFF' : hd.color, STILL, speakLevel(card, b.speaking, anim), 18);
      if (b.lang && b.lang !== 'en') drawLangTag(ctx, b, rx - 104, cy);
      let by = y + PADT + HEADH + BODY_GAP;
      if (card.orig) {
        if (b.pending && b.orig) drawPending(ctx, b.orig, x + PADX, by + 21, card.bw - PADX * 2);
        else if (b.orig) {
          ctx.font = F.orig;
          ctx.textBaseline = 'alphabetic';
          ctx.fillStyle = 'rgba(255,255,255,0.6)';
          ctx.fillText(fitTail(ctx, b.orig, card.bw - PADX * 2, F.orig), x + PADX, by + 21);
        }
        by += ORIG_H;
      }
      drawText(ctx, card.wrap, card.scroll.top ?? 0, x + PADX, by, card.rows, anim, { w: card.bw - PADX * 2 });
      ctx.restore();
    }
    ctx.restore();
    ctx.restore();
    ctx.restore();
  }

  /**
   * A card that changes form (tag <-> bubble) somewhere else does not fly there: what it showed
   * fades out in place and the new form fades in at its new place.
   */
  function relocate(c, r, anim) {
    if (c.fresh || !c.w) return;
    const far = Math.hypot(r.x + r.w / 2 - (c.x + c.w / 2), r.y + r.h / 2 - (c.y + c.h / 2)) > Math.max(90, c.h);
    if (!far) return;
    if (c.a > 0.02) ghosts.push({ ...c, item: { ...c.item }, wrap: c.wrap, scroll: { ...c.scroll }, born: anim, a0: c.a });
    c.fresh = true;
    c.a = 0;
    c.appear = 0;
    c.tailA = 0;
    c.textA = 0;
  }

  function springCard(card, t, dt) {
    if (REDUCED_MOTION || card.fresh) {
      Object.assign(card, { x: t.x, y: t.y, w: t.w, h: t.h, vx: 0, vy: 0, vw: 0, vh: 0 });
      card.fresh = false;
      return;
    }
    [card.x, card.vx] = springStep(card.x, card.vx, t.x, dt, MOVE_W);
    [card.y, card.vy] = springStep(card.y, card.vy, t.y, dt, MOVE_W);
    [card.w, card.vw] = springStep(card.w, card.vw, t.w, dt, MOVE_W);
    [card.h, card.vh] = springStep(card.h, card.vh, t.h, dt, MOVE_W);
  }
  const ease = (cur, target, dt, rate) => cur + (target - cur) * (1 - Math.exp(-dt * rate));

  // ------------------------------------------------------------ face cards (tags and bubbles)
  function layoutFaces(ctx, view, env) {
    const { anim, dt } = env;
    const cfg = view.config;
    const dim = view.paused ? 0.3 : 1;
    const rows = rowsOf(cfg);
    const full = reserveW(ctx, cfg);
    const faces = view.faces;
    const faceByKey = new Map(faces.map((f) => [f.key, f]));
    const bubbleByKey = new Map(view.bubbles.map((b) => [b.face.key, b]));

    // every person in view (or held for a moment after the tracker lost them) has a card
    const live = new Set();
    for (const f of faces) if (!f.tiny || bubbleByKey.has(f.key)) live.add(f.key);
    for (const k of bubbleByKey.keys()) live.add(k);
    for (const key of live) {
      const b = bubbleByKey.get(key) ?? null;
      const f = b?.face ?? faceByKey.get(key);
      let card = cards.get(key);
      if (!card) {
        card = {
          key, kind: 'tag', slot: null, badSince: null, anchor: { cx: f.cx, cy: f.cy, w: f.w, h: f.h, top: f.top },
          x: 0, y: 0, w: 0, h: 0, vx: 0, vy: 0, vw: 0, vh: 0, fresh: true, a: 0, appear: 0, tailA: 0, textA: 0,
          threadId: null, bw: 0, rows, orig: false, wrap: createLineWrap(), scroll: {}, born: anim, since: anim,
        };
        cards.set(key, card);
      }
      card.seen = anim;
      card.face = f;
      card.b = b;
      if (b) card.lastB = b;
      // dead zone: small head movements leave the anchor (and the bubble) where it is
      if (!f.ghost) {
        const A = card.anchor;
        const dz = Math.max(16, A.w * 0.12);
        A.cx += dead(f.cx - A.cx, dz);
        A.cy += dead(f.cy - A.cy, dz * 0.8);
        A.w += dead(f.w - A.w, A.w * 0.12);
        A.h += dead(f.h - A.h, A.h * 0.12);
        A.top = A.cy - A.h * 0.72;
      }
      const hd = header(f, b);
      card.item = { hd, b };
      if (b) {
        if (card.threadId !== b.id) {
          // a new bubble: fresh text, width fixed now, slot chosen now (keeping the tag's if it fits)
          card.threadId = b.id;
          card.kind = 'bubble';
          card.wrap.reset();
          card.scroll = {};
          card.orig = !!b.lang && b.lang !== 'en';
          card.bw = 0;
          card.pendingSlot = true;
          card.since = anim;
        }
        if (b.lang && b.lang !== 'en') card.orig = true;
        card.rows = rows;
        card.headW = headerWidth(ctx, hd, true, !!b.lang && b.lang !== 'en') + PADX * 2;
      } else {
        if (card.kind === 'bubble') {
          card.kind = 'tag';
          card.threadId = null;
          card.pendingSlot = true;
        }
        card.headW = PADX * 2 - 4 + headerWidth(ctx, hd, false, false);
      }
    }

    // place bubbles oldest first (an established bubble never makes way for a newer one), then tags
    const obstacles = env.obstacles || [];
    const placed = [];
    const list = [...cards.values()].filter((c) => live.has(c.key));
    const bubbles = list.filter((c) => c.kind === 'bubble').sort((a, b) => a.since - b.since);
    const tags = list.filter((c) => c.kind === 'tag');
    const sizes = (c) => WIDTHS.map((k) => ({ w: Math.max(c.headW, Math.round(full * k) + PADX * 2), h: bubbleH(c.rows, c.orig) }));
    for (const c of bubbles) {
      const A = c.anchor;
      if (c.pendingSlot || !c.slot) {
        const pick = chooseSlot(c, A, faces, placed, obstacles, sizes(c), c.slot);
        relocate(c, pick.r, anim);
        c.slot = pick.slot;
        c.bw = pick.size.w;
        c.pendingSlot = false;
        c.badSince = null;
      }
      c.bw = Math.max(c.bw, c.headW); // a longer name may widen it, never the text
      const th = bubbleH(c.rows, c.orig);
      let r = slotRect(c.slot, A, c.bw, th);
      // once placed, a bubble only moves for faces, the display edge or an alert: never because
      // another bubble arrived (the older one steps back instead, below)
      const q = fit(r, c.key, A, faces, [], obstacles);
      if (q.ok) c.badSince = null;
      else {
        c.badSince ??= anim;
        if (q.hard || anim - c.badSince > SLOT_HOLD_S) {
          const pick = chooseSlot(c, A, faces, placed, obstacles, [{ w: c.bw, h: th }], null);
          if (pick && pick.slot !== c.slot && fit(pick.r, c.key, A, faces, [], obstacles).ok) {
            c.slot = pick.slot;
            r = pick.r;
            c.badSince = null;
          }
        }
      }
      c.target = r;
      placed.push(r);
    }
    for (const c of tags) {
      const A = c.anchor;
      const size = [{ w: c.headW, h: TAG_H }];
      if (c.pendingSlot || !c.slot) {
        const pick = chooseSlot(c, A, faces, [], obstacles, size, c.slot);
        relocate(c, pick.r, anim);
        c.slot = pick.slot;
        c.pendingSlot = false;
        c.badSince = null;
      }
      let r = slotRect(c.slot, A, c.headW, TAG_H);
      const q = fit(r, c.key, A, faces, [], obstacles);
      if (q.ok) c.badSince = null;
      else {
        c.badSince ??= anim;
        if (q.hard || anim - c.badSince > SLOT_HOLD_S) {
          const pick = chooseSlot(c, A, faces, [], obstacles, size, null);
          if (pick.slot !== c.slot && fit(pick.r, c.key, A, faces, [], obstacles).ok) {
            c.slot = pick.slot;
            r = pick.r;
            c.badSince = null;
          }
        }
      }
      c.target = r;
    }

    // where two bubbles overlap, the one updated less recently steps back (never interleaved text)
    for (const c of bubbles) c.yielded = false;
    for (let i = 0; i < bubbles.length; i++) {
      for (let j = i + 1; j < bubbles.length; j++) {
        const p = bubbles[i];
        const q = bubbles[j];
        if (!p.b || !q.b) continue;
        const v = overlap(p.target, q.target);
        if (v <= 0.12 * Math.min(p.target.w * p.target.h, q.target.w * q.target.h)) continue;
        (p.b.tUpdate < q.b.tUpdate ? p : q).yielded = true;
      }
    }
    // motion and fades
    for (const c of list) {
      springCard(c, c.target, dt);
      const b = c.b;
      const ghost = !!c.face?.ghost;
      let aT;
      if (c.kind === 'bubble' && b) {
        // the panel keeps its name while the words fade (b.alpha), then shrinks back to a tag
        aT = (b.current ? 1 : 0.62) * (c.yielded ? 0.18 : 1);
      } else {
        aT = ghost ? 0.6 : 1;
        if (placed.some((p) => overlap(p, c.target) > 0)) aT = 0; // a tag never sits under a bubble
      }
      c.a = REDUCED_MOTION ? aT * dim : ease(c.a, aT * dim, dt, 10);
      c.appear = ease(c.appear, 1, dt, 9);
      c.tailA = ease(c.tailA, c.kind === 'bubble' ? (ghost ? 0.4 : 1) : 0, dt, 10);
      c.textA = REDUCED_MOTION && c.kind !== 'bubble' ? 0 : ease(c.textA, c.kind === 'bubble' && b ? b.alpha : 0, dt, 14);
      if (c.kind === 'bubble' && b) {
        c.wrap.update(ctx, b.tokens, c.bw - PADX * 2, F.body, anim);
        scrollTo(c.scroll, c.wrap.lines.length, c.rows, dt);
      }
    }
    // cards whose person left fade out where they are
    for (const [key, c] of cards) {
      if (live.has(key)) continue;
      c.a = ease(c.a, 0, dt, 10);
      c.tailA = ease(c.tailA, 0, dt, 10);
      if (c.a < 0.01 || anim - c.seen > 3) cards.delete(key);
    }
    return placed;
  }

  // ------------------------------------------------------------ off-screen docking
  /**
   * How well a dock at rect r fits (the same rules as a bubble's slot, fit()): inside the
   * display, off every face, the status pill and alerts, clear of on-screen cards and the
   * other docks. Returns { cost, hard }; cost < 1 fits.
   */
  function dockFit(r, faces, cardsRects, obstacles, docked) {
    const over = (a, b) => {
      const v = overlap(a, b);
      return v > TOLERANCE ? v : 0;
    };
    let cost = (Math.max(0, minY() - r.y) + Math.max(0, r.y + r.h - bottomY())) * 400;
    let hard = false;
    for (const f of faces) if (!f.tiny) cost += over(r, keepOut(f)) * 2;
    for (const c of cardsRects) cost += over(r, c) * 0.5;
    for (const o of docked) cost += over(r, o) * 3;
    cost += over(r, pillRect()) * 3;
    for (const o of obstacles) {
      const v = over(r, o);
      if (v > 0) {
        cost += v * 3;
        hard = true;
      }
    }
    return { cost, hard };
  }

  /** The best free height along the dock's edge: fits first, then nearest to `pref`. */
  function dockSpot(x, w, h, pref, faces, cardsRects, obstacles, docked) {
    let best = null;
    const lo = minY();
    const hi = Math.max(lo, bottomY() - h);
    const ys = [pref];
    for (let y = lo; y <= hi + 0.5; y += 8) ys.push(y);
    ys.push(hi);
    for (const y0 of ys) {
      const y = clamp(y0, lo, hi);
      const q = dockFit({ x, y, w, h }, faces, cardsRects, obstacles, docked);
      const ok = q.cost < 1;
      const score = (ok ? 0 : 1e9 + q.cost) + Math.abs(y - pref);
      if (!best || score < best.score) best = { y, score, ok };
    }
    return best;
  }

  function drawOffscreen(ctx, blur, view, env, dim, placed = []) {
    const { anim, dt } = env;
    const cfg = view.config;
    const rows = rowsOf(cfg);
    const DW = Math.round(reserveW(ctx, cfg) * 0.8) + PADX * 2;
    const DH = bubbleH(rows, false);
    // what a dock must not cover: faces, on-screen bubbles and tags, the pill, alerts
    const obstacles = env.obstacles || [];
    const cardsRects = [...placed];
    for (const c of cards.values()) if (c.kind === 'tag' && c.target && c.a > 0.05) cardsRects.push(c.target);
    const docked = [];
    const present = new Set();
    for (const o of view.offscreen) {
      if (o.side !== 'left' && o.side !== 'right') continue;
      present.add(o.key);
      let d = docks.get(o.key);
      if (!d) {
        const slots = dockSlots[o.side];
        let idx = slots.indexOf(undefined);
        if (idx < 0) idx = slots.length;
        slots[idx] = o.key;
        d = { key: o.key, side: o.side, idx, a: 0, h: TAG_H, vh: 0, y: null, ty: 0, vy: 0, badSince: null, wrap: createLineWrap(), scroll: {}, threadId: null, cand: null, candSince: 0 };
        docks.set(o.key, d);
      }
      // a reported side must hold for a moment before the dock changes edge
      if (o.side !== d.side) {
        if (d.cand !== o.side) {
          d.cand = o.side;
          d.candSince = anim;
        } else if (anim - d.candSince > SLOT_HOLD_S) {
          dockSlots[d.side][d.idx] = undefined;
          const slots = dockSlots[o.side];
          let idx = slots.indexOf(undefined);
          if (idx < 0) idx = slots.length;
          slots[idx] = o.key;
          d.side = o.side;
          d.idx = idx;
          d.a = 0;
          d.cand = null;
          d.y = null; // a new edge: choose a new place along it
        }
      } else d.cand = null;
      d.o = o;
      d.seen = anim;
    }
    // docks in slot order along each edge, so the earlier arrival keeps its place
    const order = [...docks.values()].sort((p, q) => (p.side === q.side ? p.idx - q.idx : p.side < q.side ? -1 : 1));
    for (const d of order) {
      const key = d.key;
      const on = present.has(key);
      const b = on ? d.o.bubble : null;
      const aT = on ? (b ? b.alpha * (b.current ? 1 : 0.7) : 0.85) : 0;
      d.a = REDUCED_MOTION ? aT : ease(d.a, aT, dt, 9);
      if (!on && d.a < 0.01) {
        dockSlots[d.side][d.idx] = undefined;
        docks.delete(key);
        continue;
      }
      if (b && d.threadId !== b.id) {
        d.threadId = b.id;
        d.wrap.reset();
        d.scroll = {};
      }
      if (on) d.b = b;
      const th = d.b && on ? DH : TAG_H;
      if (REDUCED_MOTION) d.h = th;
      else [d.h, d.vh] = springStep(d.h, d.vh, th, dt, MOVE_W);
      const o = d.o;
      const bb = d.b;
      const side = d.side;
      // its place along the edge: chosen like a bubble's slot (never over a face, the pill, an
      // alert or a card), with room for the full bubble so a tag never has to move to grow. A
      // place that stops fitting is kept for SLOT_HOLD_S (an alert moves it at once); then the
      // dock glides to the nearest free height.
      const home = R().y + 190 + d.idx * (DH + 16);
      const bx = side === 'left' ? R().x + 64 : R().x + R().w - 64 - DW;
      if (d.y == null) {
        d.ty = dockSpot(bx, DW, DH, home, view.faces, cardsRects, obstacles, docked).y;
        d.y = d.ty;
        d.vy = 0;
        d.badSince = null;
      } else {
        const q = dockFit({ x: bx, y: d.ty, w: DW, h: DH }, view.faces, cardsRects, obstacles, docked);
        if (q.cost < 1) d.badSince = null;
        else {
          d.badSince ??= anim;
          if (q.hard || anim - d.badSince > SLOT_HOLD_S) {
            const spot = dockSpot(bx, DW, DH, d.ty, view.faces, cardsRects, obstacles, docked);
            if (spot.ok) {
              d.ty = spot.y;
              d.badSince = null;
            }
          }
        }
        if (REDUCED_MOTION) d.y = d.ty;
        else [d.y, d.vy] = springStep(d.y, d.vy, d.ty, dt, MOVE_W);
      }
      docked.push({ x: bx, y: d.ty, w: DW, h: DH });
      d.rect = { x: bx, y: d.y, w: DW, h: d.h };
      const a = d.a * dim;
      if (a < 0.01) continue;
      const y = d.y;
      const slide = REDUCED_MOTION ? 0 : (1 - easeOut(clamp(d.a / Math.max(0.01, aT || 1)))) * 40;
      const x = side === 'left' ? R().x + 64 - slide : R().x + R().w - 64 - DW + slide;
      // a steady, faint edge light in the speaker's colour
      ctx.save();
      const ex = side === 'left' ? R().x : R().x + R().w;
      const g = ctx.createRadialGradient(ex, y + d.h / 2, 0, ex, y + d.h / 2, 420);
      g.addColorStop(0, hexA(o.color, (bb ? 0.26 : 0.14) * a));
      g.addColorStop(1, hexA(o.color, 0));
      ctx.fillStyle = g;
      ctx.fillRect(side === 'left' ? ex : ex - 420, y + d.h / 2 - 420, 420, 840);
      ctx.restore();
      glass(ctx, blur, x, y, DW, d.h, Math.min(RAD, d.h / 2), { alpha: a, glow: o.color });
      ctx.save();
      ctx.globalAlpha *= a;
      const dir = side === 'left' ? -1 : 1;
      // one static chevron toward the speaker; its side only changes after the SLOT_HOLD_S hold above
      chevrons(ctx, (side === 'left' ? x - 24 : x + DW + 24), y + Math.min(d.h, TAG_H) / 2, dir, o.color, 15, 4, 1, a);
      const hy = y + (d.h > TAG_H + 4 ? PADT + HEADH / 2 : d.h / 2);
      ctx.save();
      rrect(ctx, x, y, DW, d.h, RAD);
      ctx.clip();
      dot(ctx, x + PADX + 7, hy, 7, o.color);
      ctx.font = F.name;
      ctx.textBaseline = 'middle';
      ctx.fillStyle = '#FFFFFF';
      ctx.fillText(o.name, x + PADX + 26, hy);
      const nameW = textW(ctx, o.name, F.name);
      ctx.font = F.sub;
      ctx.fillStyle = 'rgba(255,255,255,0.62)';
      ctx.fillText(`·  ${side === 'left' ? 'on your left' : 'on your right'}`, x + PADX + 26 + nameW + 12, hy + 1);
      if (bb) {
        eqBars(ctx, x + DW - PADX - 26, hy, o.color, STILL, speakLevel(d, bb.speaking && on, anim), 18);
        d.wrap.update(ctx, bb.tokens, DW - PADX * 2, F.body, anim);
        scrollTo(d.scroll, d.wrap.lines.length, rows, dt);
        drawText(ctx, d.wrap, d.scroll.top ?? 0, x + PADX, y + PADT + HEADH + BODY_GAP, rows, anim, { w: DW - PADX * 2 });
        pendingBelow(ctx, bb, d.wrap, rows, x + PADX, y + PADT + HEADH + BODY_GAP, DW - PADX * 2);
      }
      ctx.restore();
      ctx.restore();
    }
    // someone behind you: glow along the bottom edge, one line docked above the You bar
    let by = bottomY() + 10;
    for (const o of view.offscreen) {
      if (o.side !== 'behind') continue;
      const b = o.bubble;
      const a = (b ? b.alpha : 1) * dim;
      edgeGlow(ctx, 'behind', o.color, a * 0.5, 480);
      const w = Math.min(R().w - 200, 900);
      const head = `${o.name}  ·  behind you`;
      const headW = textW(ctx, head, F.sub);
      const text = b ? fitTail(ctx, b.text, w - headW - 136, F.sub) : '';
      glass(ctx, blur, midX() - w / 2, by - 50, w, 44, 22, { alpha: a, glow: o.color });
      ctx.save();
      ctx.globalAlpha *= a;
      // a static arrow pointing down: behind you
      arrow(ctx, midX() - w / 2 + 28, by - 28, 20, Math.PI / 2, o.color, 3);
      dot(ctx, midX() - w / 2 + 52, by - 28, 6, o.color);
      ctx.font = F.sub;
      ctx.textBaseline = 'middle';
      ctx.fillStyle = '#FFFFFF';
      ctx.fillText(head, midX() - w / 2 + 70, by - 27);
      if (text) {
        ctx.fillStyle = 'rgba(255,255,255,0.85)';
        ctx.fillText(`—  ${text}`, midX() - w / 2 + 82 + headW, by - 27);
      }
      ctx.restore();
      by -= 54;
    }
  }

  // ------------------------------------------------------------ lower caption + You bar
  /** A voice the engine could not place: a fixed-size caption at the bottom centre. */
  function drawLower(ctx, blur, view, env, dim) {
    const { anim, dt } = env;
    const b = view.lower;
    const cfg = view.config;
    const rows = rowsOf(cfg);
    if (b && (!lowerCard || lowerCard.threadId !== b.id)) {
      lowerCard = { threadId: b.id, a: lowerCard?.a ?? 0, wrap: createLineWrap(), scroll: {}, b };
    }
    if (!lowerCard) return null;
    const c = lowerCard;
    if (b) c.b = b;
    const aT = b ? b.alpha * (b.current ? 1 : 0.7) : 0;
    c.a = REDUCED_MOTION ? aT : ease(c.a, aT, dt, 9);
    if (!b && c.a < 0.01) {
      lowerCard = null;
      return null;
    }
    const bb = c.b;
    const w = Math.round(reserveW(ctx, cfg)) + PADX * 2;
    const h = bubbleH(rows, false);
    const x = midX() - w / 2;
    const y = bottomY() - h - 6;
    const a = c.a * dim;
    glass(ctx, blur, x, y, w, h, RAD, { alpha: a, glow: bb.color });
    ctx.save();
    ctx.globalAlpha *= a;
    const hy = y + PADT + HEADH / 2;
    dot(ctx, x + PADX + 7, hy, 7, bb.color);
    ctx.font = F.name;
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#FFFFFF';
    ctx.fillText(bb.name, x + PADX + 26, hy);
    eqBars(ctx, x + w - PADX - 26, hy, bb.color, STILL, speakLevel(c, !!b && bb.speaking, anim), 18);
    c.wrap.update(ctx, bb.tokens, w - PADX * 2, F.body, anim);
    scrollTo(c.scroll, c.wrap.lines.length, rows, dt);
    drawText(ctx, c.wrap, c.scroll.top ?? 0, x + PADX, y + PADT + HEADH + BODY_GAP, rows, anim, { w: w - PADX * 2 });
    pendingBelow(ctx, bb, c.wrap, rows, x + PADX, y + PADT + HEADH + BODY_GAP, w - PADX * 2);
    ctx.restore();
    return { x: x - 10, y: y - 10, w: w + 20, h: h + 20 };
  }

  /** The wearer's own words: one line of fixed width that rolls up as it fills. */
  function drawYou(ctx, blur, view, env, dim) {
    const { anim, dt } = env;
    const b = view.you;
    const c = youCard;
    if (b && c.threadId !== b.id) {
      c.threadId = b.id;
      c.wrap.reset();
      c.scroll = {};
    }
    if (b) c.b = b;
    c.a = REDUCED_MOTION ? (b ? 1 : 0) : ease(c.a, b ? 1 : 0, dt, 8);
    const bb = c.b;
    if (!bb || c.a < 0.01) return;
    const label = bb.typed ? 'You (typed)' : 'You';
    const lw = textW(ctx, 'You (typed)', F.youLabel);
    const w = Math.min(R().w - 240, 1000);
    const h = 60;
    const x = midX() - w / 2;
    const y = R().y + R().h - 118 + (REDUCED_MOTION ? 0 : (1 - easeOut(c.a)) * 12);
    const a = c.a * (b ? b.alpha : 1) * dim;
    glass(ctx, blur, x, y, w, h, h / 2, { alpha: a, tint: 'rgba(14,16,22,0.46)' });
    ctx.save();
    ctx.globalAlpha *= a;
    ctx.font = F.youLabel;
    ctx.textBaseline = 'middle';
    ctx.fillStyle = bb.typed ? ACCENT : 'rgba(255,255,255,0.58)';
    ctx.fillText(label, x + 24, y + h / 2 + 1);
    const tx = x + 24 + lw + 16;
    const tw = w - (tx - x) - 28;
    c.wrap.update(ctx, bb.tokens, tw, F.you, anim);
    scrollTo(c.scroll, c.wrap.lines.length, 1, dt);
    drawText(ctx, c.wrap, c.scroll.top ?? 0, tx, y + 12, 1, anim, { w: tw, font: F.you, lh: 36, base: 28, draft: 0.72 });
    ctx.restore();
  }

  // ------------------------------------------------------------ main entry
  function render(ctx, view, env) {
    const { blur, anim } = env;
    const dim = view.paused ? 0.3 : 1;

    // face brackets: strangers when they first appear, proposals while they wait for an answer.
    // The dashes stand still (t 0); brackets only fade in and out.
    for (const f of view.faces) {
      if (f.tiny || f.ghost) continue;
      const prop = f.proposal;
      const t = 0;
      if (prop && prop.state === 'proposed') {
        faceBrackets(ctx, f, { a: 0.9 * dim, color: ACCENT, dashed: true, t, scale: 1.3 });
      } else if (prop && prop.state === 'confirmed' && prop.age < 1.2) {
        faceBrackets(ctx, f, { a: (1 - prop.age / 1.2) * dim, color: MINT, t, scale: 1.3 });
      } else if (!f.known && anim - f.born < 3.5) {
        const a = clamp((anim - f.born) / 0.35) * clamp((3.5 - (anim - f.born)) / 0.5);
        faceBrackets(ctx, f, { a: a * 0.85 * dim, color: '#FFFFFF', dashed: true, t, scale: 1.3 });
      }
    }

    const lowerRect = lowerCard ? lastLower : null;
    const placed = layoutFaces(ctx, view, { ...env, obstacles: [...(env.obstacles || []), ...(lowerRect ? [lowerRect] : [])] });

    ghosts = ghosts.filter((g) => anim - g.born < 0.25);
    for (const g of ghosts) {
      g.a = g.a0 * (REDUCED_MOTION ? 0 : 1 - (anim - g.born) / 0.25);
      g.blur = blur;
      drawCard(ctx, g, anim);
    }
    // tags first, then earlier bubbles, the current speaker's on top
    const order = [...cards.values()].sort((a, b) => rank(a) - rank(b));
    for (const c of order) {
      c.blur = blur;
      drawCard(ctx, c, anim);
    }
    for (const f of view.faces) {
      if (!f.tiny || cards.has(f.key)) continue;
      ctx.save();
      ctx.globalAlpha = dim * (f.ghost ? 0.5 : 1);
      dot(ctx, f.cx, f.top - 8, 7, f.color, 14);
      ctx.strokeStyle = 'rgba(255,255,255,0.7)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(f.cx, f.top - 8, 11, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }

    drawOffscreen(ctx, blur, view, { ...env, obstacles: [...(env.obstacles || []), ...(lowerRect ? [lowerRect] : [])] }, dim, placed);
    lastLower = drawLower(ctx, blur, view, env, dim);
    drawYou(ctx, blur, view, env, dim);
    return placed;
  }
  let lastLower = null;

  /** Draw order: tags, then bubbles from the least to the most recently updated. */
  function rank(c) {
    if (c.kind !== 'bubble' || !c.b) return -1e9;
    return c.b.current ? 1e9 : c.b.tUpdate;
  }

  /** Card states for tests and measurements: where each card is and what it shows. */
  function debug() {
    const out = [];
    for (const c of cards.values()) {
      const top = Math.round(c.scroll.top ?? 0);
      const shown = [];
      for (let i = top; i < Math.min(c.wrap.lines.length, top + c.rows); i++) {
        const ln = c.wrap.lines[i];
        shown.push(c.wrap.tokens.slice(ln.start, ln.end).map((t) => t.text).join(' '));
      }
      out.push({ key: c.key, kind: c.kind, slot: c.slot, x: c.x, y: c.y, w: c.w, h: c.h, a: c.a, thread: c.threadId, lines: c.kind === 'bubble' ? shown : [], utt: c.b?.utt_id ?? null, face: c.face ? { cx: c.face.cx, cy: c.face.cy, w: c.face.w, h: c.face.h, track: c.face.track_id } : null });
    }
    return out;
  }

  /** Docked off-screen speakers for tests and measurements: side, place and size. */
  function debugDocks() {
    return [...docks.values()].map((d) => ({ key: d.key, side: d.side, idx: d.idx, ty: d.ty, a: d.a, ...d.rect }));
  }

  return { render, debug, debugDocks, cards };
}
