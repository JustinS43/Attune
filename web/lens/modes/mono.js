/*
 * Mode 2 - Mono green waveguide, Even Realities G1 class (binocular, 640x200 green display,
 * about 25 degrees diagonal, focused about 2 m away).
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-06, P-08.
 *
 * Physically honest (docs/glasses-realism.md): the display is a 640x200 band that covers about
 * 530x165 px of the 1920x1080 frame, centred horizontally and at or slightly above eye level
 * (display height levels 0-8, keys [ and ]; level 4 puts its centre about 5 degrees up). It is
 * head-locked, so there are no face-anchored bubbles: a speaker's name, 4 caption lines,
 * and < > chevrons at the band's edges toward the current speaker or a sound.
 * Captions are a running log with fixed line positions (modes/common.js): each speaker turn
 * starts on a new line, earlier turns dim, drafts are dimmer and firm up without reflowing,
 * and the text rolls up one line at a time. The header names the speaker of the line directly
 * under it, and a line whose speaker differs from the line before it starts with "NAME:", so
 * every line's speaker is clear. A proposal, "saved" line or a line waiting for its
 * translation (the original, small and dimmed, "TRANSLATING…") takes the fourth line and the
 * text rolls up to make room; untranslated words never join the caption lines. Pure green light only: no
 * fills, nothing dark (a waveguide cannot draw black), a 3-4 px glow, nothing outside the band.
 * Alerts become an icon and a word; name proposals read "SAM?  ✓ Y  ✕ N".
 */

import { W, H, FT, PX, font, clamp, lerp, hexA, rrect, icon, chevrons, degToPx, REDUCED_MOTION } from '../hud.js';
import { t3Flash } from '../alerts.js';
import { inView, createCaptionLog } from './common.js';

const G = '#28FF46'; // rgb(40,255,70)
const VW = 640; // the display's own pixels
const VH = 200;
const S = 530 / VW; // display px -> design px (the band is about 530x165 of the frame)
export const MONO_LEVELS = 9; // G1 display height levels 0-8
export const MONO_DEFAULT_LEVEL = 4;
/** Centre of the band above eye level, in degrees, for a display height level (1 degree a step). */
const levelDeg = (level) => 1 + clamp(level, 0, MONO_LEVELS - 1);
const F = {
  head: font(680, 22, FT),
  body: font(560, 24, FT),
  small: font(560, 16, FT),
  big: font(680, 34, FT),
  mid: font(520, 21, FT),
  orig: font(500, 20, FT), // a line waiting for its translation: smaller and dimmed
  // alerts must read over a bright scene: heavier and larger, drawn with an outline (strong())
  alertBig: font(800, 38, FT),
  alertMid: font(700, 24, FT),
  alertHead: font(800, 23, FT),
  alertLine: font(700, 30, FT),
  alertSmall: font(650, 16, FT),
};
const LINE_X = 52;
const LINE_W = VW - LINE_X * 2;
const HEAD_Y = 30;
const BODY_Y = 76;
const LH = 36; // header + 4 lines = 5 lines in 200 px, like Even's own caption app

/** Where the band sits in the 1920x1080 frame for a display height level. */
export function monoRect(level = MONO_DEFAULT_LEVEL) {
  const w = VW * S;
  const h = VH * S;
  const cy = H / 2 - degToPx(levelDeg(level));
  return { x: (W - w) / 2, y: cy - h / 2, w, h };
}

function glowOn(ctx, k = 1) {
  ctx.shadowColor = hexA(G, 0.9);
  ctx.shadowBlur = 3.5 * PX * k; // canvas px: the glow is 3-4 px on a 1080p frame
}

function text(ctx, str, x, y, f, a = 1, align = 'left', spacing = 0) {
  ctx.font = f;
  ctx.textAlign = align;
  ctx.letterSpacing = `${spacing}px`;
  ctx.globalAlpha = a;
  ctx.fillStyle = G;
  ctx.fillText(str, x, y);
  ctx.letterSpacing = '0px';
  ctx.textAlign = 'left';
}

/**
 * Alert text that holds up over a bright scene while staying pure green light: the glyphs are
 * thickened with a green outline under a wider glow (more emitted light, no dark fill).
 */
function strong(ctx, str, x, y, f, a = 1, align = 'left', spacing = 0, lw = 1.6) {
  ctx.save();
  ctx.font = f;
  ctx.textAlign = align;
  ctx.letterSpacing = `${spacing}px`;
  ctx.globalAlpha = a;
  ctx.shadowColor = hexA(G, 1);
  ctx.shadowBlur = 7 * PX;
  ctx.strokeStyle = G;
  ctx.lineWidth = lw;
  ctx.lineJoin = 'round';
  ctx.strokeText(str, x, y);
  ctx.shadowBlur = 3.5 * PX;
  ctx.fillStyle = G;
  ctx.fillText(str, x, y);
  ctx.restore();
}

function strongIcon(ctx, name, x, y, size, a = 1) {
  ctx.save();
  ctx.globalAlpha = a;
  ctx.shadowColor = hexA(G, 1);
  ctx.shadowBlur = 7 * PX;
  icon(ctx, name, x, y, size, G, 3.2);
  ctx.restore();
}

function edgeChevrons(ctx, side, anim, strong) {
  const a = strong && !REDUCED_MOTION ? 0.55 + 0.45 * Math.sin(anim * 7) : 0.85;
  const n = strong ? 2 : 1;
  ctx.save();
  if (side === 'left') chevrons(ctx, 22, VH / 2 + 6, -1, G, 12, 3, n, a);
  else if (side === 'right') chevrons(ctx, VW - 22, VH / 2 + 6, 1, G, 12, 3, n, a);
  else if (side === 'behind') {
    ctx.translate(VW / 2, VH - 9);
    ctx.rotate(Math.PI / 2);
    chevrons(ctx, 0, 0, 1, G, 9, 2.6, n, a);
  }
  ctx.restore();
}

/** `str` cut with an ellipsis to fit maxW in font f. */
function fitEnd(ctx, str, f, maxW) {
  ctx.font = f;
  if (ctx.measureText(str).width <= maxW) return str;
  let out = str;
  while (out.length > 1 && ctx.measureText(`${out}…`).width > maxW) out = out.slice(0, -1);
  return `${out.trimEnd()}…`;
}

function monoIcon(ctx, name, x, y, size, lw = 2.2, a = 1) {
  ctx.globalAlpha = a;
  icon(ctx, name, x, y, size, G, lw);
}

export function createMonoMode() {
  const log = createCaptionLog();
  let last = null;
  return {
    id: 'mono',
    /** What the band shows (for tests and measurements). */
    debug: () => last,
    name: 'Mono green waveguide',
    device: 'Binocular · Even Realities G1 class',
    blur: false,
    render(ctx, view, env) {
      const { anim } = env;
      const r = monoRect(env.monoLevel ?? MONO_DEFAULT_LEVEL);
      ctx.save();
      ctx.translate(r.x, r.y);
      ctx.scale(S, S);
      ctx.textBaseline = 'alphabetic';

      // the display area itself, as a faint outline only while the demo chrome is visible
      if (env.chrome) {
        ctx.save();
        rrect(ctx, 0, 0, VW, VH, 12);
        ctx.strokeStyle = hexA(G, 0.12);
        ctx.lineWidth = 1.2;
        ctx.setLineDash([3, 7]);
        ctx.stroke();
        ctx.restore();
      }
      // nothing is drawn outside the band
      ctx.beginPath();
      ctx.rect(0, 0, VW, VH);
      ctx.clip();

      ctx.save();
      glowOn(ctx);
      const urgent = view.alerts.find((a) => a.level === 'urgent' && !a.watch);
      const chip = view.alerts.find((a) => a !== urgent && !a.watch);

      if (view.status === 'connecting') {
        text(ctx, 'CONNECTING…', VW / 2, VH / 2 + 2, F.big, 0.55 + 0.35 * Math.sin(anim * 3), 'center', 3);
        text(ctx, 'ATTUNE ENGINE OFFLINE', VW / 2, VH / 2 + 40, F.small, 0.6, 'center', 2.5);
      } else if (view.paused) {
        ctx.globalAlpha = 0.95;
        ctx.fillStyle = G;
        ctx.fillRect(VW / 2 - 94, VH / 2 - 24, 7, 28);
        ctx.fillRect(VW / 2 - 81, VH / 2 - 24, 7, 28);
        text(ctx, 'PAUSED', VW / 2 - 62, VH / 2 + 3, F.big, 0.95, 'left', 4);
        text(ctx, 'P TO RESUME', VW / 2, VH / 2 + 44, F.small, 0.65, 'center', 2.5);
      } else if (urgent) {
        // an urgent alarm takes the whole band: the word flashes in the T3 rhythm but never
        // drops below half brightness, so it stays readable over a bright scene
        const flash = urgent.acked || REDUCED_MOTION ? 1 : 0.55 + 0.45 * t3Flash(urgent.age);
        const label = urgent.label.toUpperCase();
        ctx.font = F.alertBig;
        ctx.letterSpacing = '3px';
        const lw = ctx.measureText(label).width;
        ctx.letterSpacing = '0px';
        const x0 = VW / 2 - (lw + 58) / 2;
        strongIcon(ctx, urgent.acked ? 'check' : 'warn', x0, 38, 46, flash);
        strong(ctx, label, x0 + 58, 80, F.alertBig, flash, 'left', 3, 2);
        strong(ctx, urgent.acked ? 'ACKNOWLEDGED' : urgent.detail.toUpperCase(), VW / 2, 128, F.alertMid, 1, 'center', 2, 1.2);
        if (!urgent.acked) {
          strong(ctx, 'A  ACKNOWLEDGE', VW / 2, 176, F.alertSmall, 0.85, 'center', 2.5, 0.8);
          edgeChevrons(ctx, urgent.side, anim, true);
        }
      } else {
        const prop = view.pendingProposal;
        const toast = view.toasts.find((t) => t.kind === 'learned' && t.age < 3);
        const saving = !!view.save; // P-29: the save line (save.js) takes the fourth line
        const waiting = !prop && !toast && !saving ? view.translating : null; // a line awaiting its translation
        const footer = !!prop || !!toast || saving || !!waiting;
        const footY = BODY_Y + 3 * LH;
        const rows = footer ? 3 : 4;
        const st = log.update(ctx, view, LINE_W, F.body, rows, anim, view.dt, false, true);
        const cur = st?.current ?? null; // the newest speaker: the chevrons point to them
        // the header names the speaker of the line directly under it (lines from someone else
        // start with their own "NAME:"), so a name never sits above another person's words
        const topLine = st?.lines.length ? st.lines[clamp(Math.round(st.top), 0, st.lines.length - 1)] : null;
        const cap = topLine?.entry.b ?? cur;
        const capA = topLine ? topLine.entry.alpha : st?.currentAlpha ?? 0;
        last = { key: chip ? `alert:${chip.id}` : st?.currentKey ?? null, name: chip ? chip.label : cap?.name ?? null, lines: log.shown(st, rows), side: !chip && cur?.dir && cur.dir.side !== 'ahead' ? cur.dir.side : null, translating: waiting?.text ?? null };
        // header: a sound, else the speaker of the top line, else who is in view
        if (chip) {
          const w = `${chip.label.toUpperCase()}${chip.count > 1 ? ` ×${chip.count}` : ''}`;
          strongIcon(ctx, chip.acked ? 'check' : chip.icon, LINE_X - 4, 8, 28);
          strong(ctx, w, LINE_X + 32, HEAD_Y + 1, F.alertHead, 1, 'left', 2, 1.2);
          if (cap) strong(ctx, chip.acked ? 'OK' : chip.detail.toUpperCase(), VW - LINE_X, HEAD_Y, F.alertSmall, 0.9, 'right', 2, 0.8);
          edgeChevrons(ctx, chip.side, anim, !chip.acked);
        } else if (cap) {
          const name = cap.name.toUpperCase();
          text(ctx, name, LINE_X, HEAD_Y, F.head, capA, 'left', 2);
          ctx.font = F.head;
          ctx.letterSpacing = '2px';
          const nw = ctx.measureText(name).width;
          ctx.letterSpacing = '0px';
          ctx.globalAlpha = capA * 0.9;
          ctx.fillStyle = G;
          for (let i = 0; i < 3; i++) {
            const v = cap.speaking && !REDUCED_MOTION ? 0.35 + 0.65 * Math.abs(Math.sin(anim * (7 + i * 2.3) + i)) : 0.3;
            ctx.fillRect(LINE_X + nw + 12 + i * 6, HEAD_Y - 6 - 7 * v, 3, 14 * v);
          }
          const right = cap.translated ? `${cap.lang.toUpperCase()} → EN` : cap.pending ? `${cap.lang.toUpperCase()} …` : cap.dir?.off ? cap.dir.side.toUpperCase() : '';
          if (right) text(ctx, right, VW - LINE_X, HEAD_Y, F.small, 0.75 * capA, 'right', 2);
        } else {
          const names = inView(view);
          text(ctx, names.length ? `IN VIEW · ${names.join(' · ').toUpperCase()}` : 'LISTENING', LINE_X, HEAD_Y, F.small, 0.55, 'left', 2.5);
        }
        ctx.save();
        ctx.shadowBlur = 0;
        ctx.globalAlpha = 0.28;
        ctx.fillStyle = G;
        ctx.fillRect(LINE_X, 42, LINE_W, 1.2);
        ctx.restore();

        if (st) {
          // the caption lines: fixed rows under the rule; lines leaving at the top fade out
          ctx.save();
          ctx.beginPath();
          ctx.rect(0, 46, VW, BODY_Y + (rows - 1) * LH + 10 - 46);
          ctx.clip();
          log.eachVisible(st, rows, (t, m, x, row, lineA, e) => {
            const p = clamp((anim - m.born) / 0.2);
            if (p <= 0) return;
            const firm = t.final ? (m.firm == null ? 1 : clamp((anim - m.firm) / 0.25)) : 0;
            const earlier = e.key === st.currentKey ? 1 : 0.5; // an earlier speaker's lines step back
            text(ctx, t.text, LINE_X + x, BODY_Y + row * LH, F.body, e.alpha * lineA * p * lerp(0.62, 1, firm) * earlier);
          });
          ctx.restore();
          if (!chip && cur?.dir && cur.dir.side !== 'ahead') edgeChevrons(ctx, cur.dir.side, anim, !!cur.dir.off);
        } else if (chip) {
          // nobody talking: the band has room to spell the sound out, large and heavy
          const more = view.alerts.filter((x) => x !== chip && !x.watch && x.level !== 'urgent');
          strong(ctx, chip.acked ? 'Acknowledged' : chip.detail, LINE_X, BODY_Y + 6, F.alertLine, 1, 'left', 0.5, 1.4);
          if (more[0]) strong(ctx, `+ ${more[0].label} · ${more[0].detail}`, LINE_X, BODY_Y + LH + 14, F.alertMid, 0.9, 'left', 0, 1);
          if (!chip.acked && !footer) strong(ctx, 'A  ACKNOWLEDGE', VW - LINE_X, footY, F.alertSmall, 0.85, 'right', 2.5, 0.8);
        }
        if (saving) view.save.drawMono(ctx, { x: LINE_X, y: footY, w: LINE_W, vw: VW });
        else if (prop) {
          const label = `${prop.name.toUpperCase()}?`;
          text(ctx, label, VW / 2 - 70, footY, F.head, 1, 'right', 2);
          monoIcon(ctx, 'check', VW / 2 - 50, footY - 19, 20, 2.4);
          text(ctx, 'Y', VW / 2 - 22, footY, F.head, 0.85, 'left', 1);
          monoIcon(ctx, 'cross', VW / 2 + 18, footY - 19, 20, 2.4);
          text(ctx, 'N', VW / 2 + 46, footY, F.head, 0.85, 'left', 1);
        } else if (toast) {
          monoIcon(ctx, 'check', VW / 2 - 134, footY - 18, 18, 2.4, 0.9);
          text(ctx, `${toast.text.toUpperCase()} SAVED`, VW / 2 - 108, footY, F.head, 0.9 * clamp((3 - toast.age) / 0.4), 'left', 2);
        } else if (waiting) {
          // the original, small and dimmed, until its translation takes a caption line
          const a = clamp(waiting.alpha);
          const hint = 'TRANSLATING…';
          ctx.font = F.small;
          ctx.letterSpacing = '2px';
          const hw = ctx.measureText(hint).width;
          ctx.letterSpacing = '0px';
          const lang = `${String(waiting.lang || '').toUpperCase().slice(0, 2)}`;
          const lw = lang ? ctx.measureText(lang).width + 12 : 0;
          if (lang) text(ctx, lang, LINE_X, footY, F.small, 0.7 * a, 'left', 1);
          text(ctx, fitEnd(ctx, waiting.text, F.orig, LINE_W - hw - lw - 18), LINE_X + lw, footY, F.orig, 0.5 * a);
          text(ctx, hint, VW - LINE_X, footY, F.small, 0.75 * a, 'right', 2);
        }
      }
      ctx.restore();
      ctx.restore();
    },
  };
}
