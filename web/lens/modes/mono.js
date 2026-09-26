/*
 * Mode 2 - Mono green waveguide (Even Realities G1 / Vuzix Z100 class).
 *
 * These glasses have one small monochrome display band and no face tracking, so everything is
 * redrawn into a 640x200 virtual band (scaled up, centred just below the eye line): green
 * phosphor text only, no fills, no images, no colour. The current speaker's name and 2-3 caption
 * lines; chevrons at the band edges point toward the speaker or a sound; alerts become an icon
 * and a word; name proposals read "SAM?  ✓ Y  ✕ N".
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-06.
 */

import { W, H, FT, PHOSPHOR, PX, font, clamp, hexA, rrect, icon, chevrons, wrapPx } from '../hud.js';
import { t3Flash } from '../alerts.js';
import { inView, primaryCaption } from './common.js';

const G = PHOSPHOR;
// a darker intensity of the same green, drawn under every glyph so text holds over bright scenes
const KEYLINE = 'rgba(3, 26, 10, 0.72)';
const VW = 640;
const VH = 200;
const S = 1.42; // virtual px -> design px
const F = {
  head: font(600, 19, FT),
  body: font(450, 27, FT),
  small: font(500, 14, FT),
  big: font(650, 34, FT),
  mid: font(500, 18, FT),
};
const LINE_X = 46;
const LINE_W = VW - LINE_X * 2;
const LH = 36;

function glowOn(ctx, k = 1) {
  ctx.shadowColor = hexA(G, 0.85);
  ctx.shadowBlur = 7 * S * PX * k;
}

function text(ctx, str, x, y, f, a = 1, align = 'left', spacing = 0) {
  ctx.font = f;
  ctx.textAlign = align;
  ctx.letterSpacing = `${spacing}px`;
  ctx.globalAlpha = a;
  ctx.save();
  ctx.shadowBlur = 0;
  ctx.strokeStyle = KEYLINE;
  ctx.lineWidth = 4;
  ctx.lineJoin = 'round';
  ctx.strokeText(str, x, y);
  ctx.restore();
  ctx.fillStyle = G;
  ctx.fillText(str, x, y);
  ctx.letterSpacing = '0px';
  ctx.textAlign = 'left';
}

function edgeChevrons(ctx, side, anim, strong) {
  const a = strong ? 0.55 + 0.45 * Math.sin(anim * 7) : 0.85;
  ctx.globalAlpha = 1;
  const n = strong ? 2 : 1;
  for (const [col, lw] of [[KEYLINE, 6.5], [G, 3]]) {
    ctx.save();
    if (col === KEYLINE) ctx.shadowBlur = 0;
    if (side === 'left') chevrons(ctx, 22, VH / 2 + 8, -1, col, 13, lw, n, a);
    else if (side === 'right') chevrons(ctx, VW - 22, VH / 2 + 8, 1, col, 13, lw, n, a);
    else if (side === 'behind') {
      ctx.translate(VW / 2, VH - 8);
      ctx.rotate(Math.PI / 2);
      chevrons(ctx, 0, 0, 1, col, 10, lw - 0.4, n, a);
    }
    ctx.restore();
  }
}

/** A stroked icon with the same dark keyline under it. */
function monoIcon(ctx, name, x, y, size, lw = 2.2) {
  ctx.save();
  ctx.shadowBlur = 0;
  icon(ctx, name, x, y, size, KEYLINE, lw + 2.6);
  ctx.restore();
  icon(ctx, name, x, y, size, G, lw);
}

function checkMark(ctx, x, y, size, a) {
  ctx.globalAlpha = a;
  monoIcon(ctx, 'check', x, y - size / 2, size, 2.4);
}
function crossMark(ctx, x, y, size, a) {
  ctx.globalAlpha = a;
  monoIcon(ctx, 'cross', x, y - size / 2, size, 2.4);
}

export function createMonoMode() {
  return {
    id: 'mono',
    name: 'Mono waveguide',
    device: 'Green monochrome display band · Even Realities G1 / Vuzix Z100 class',
    blur: false,
    render(ctx, view, env) {
      const { anim } = env;
      const x0 = (W - VW * S) / 2;
      const y0 = H * 0.6 - (VH * S) / 2;
      ctx.save();
      ctx.translate(x0, y0);
      ctx.scale(S, S);
      ctx.textBaseline = 'alphabetic';

      // the display band itself (a faint outline so viewers see how small it is)
      ctx.save();
      const oa = env.chrome ? 1 : 0.55;
      rrect(ctx, 0, 0, VW, VH, 14);
      ctx.strokeStyle = hexA(G, 0.13 * oa);
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.strokeStyle = hexA(G, 0.42 * oa);
      ctx.lineWidth = 1.6;
      for (const [cx, cy, dx, dy] of [[0, 0, 1, 1], [VW, 0, -1, 1], [0, VH, 1, -1], [VW, VH, -1, -1]]) {
        ctx.beginPath();
        ctx.moveTo(cx, cy + dy * 18);
        ctx.lineTo(cx, cy + dy * 6);
        ctx.quadraticCurveTo(cx, cy, cx + dx * 6, cy);
        ctx.lineTo(cx + dx * 18, cy);
        ctx.stroke();
      }
      ctx.restore();

      ctx.save();
      glowOn(ctx);
      const urgent = view.alerts.find((a) => a.level === 'urgent' && !a.watch);
      const chip = view.alerts.find((a) => a !== urgent && !a.watch);

      if (view.status === 'connecting') {
        text(ctx, 'CONNECTING…', VW / 2, VH / 2 + 4, F.big, 0.55 + 0.35 * Math.sin(anim * 3), 'center', 3);
        text(ctx, 'ATTUNE ENGINE OFFLINE', VW / 2, VH / 2 + 40, F.small, 0.55, 'center', 2.5);
      } else if (view.paused) {
        ctx.globalAlpha = 0.9;
        ctx.fillStyle = G;
        ctx.fillRect(VW / 2 - 92, VH / 2 - 22, 6, 26);
        ctx.fillRect(VW / 2 - 80, VH / 2 - 22, 6, 26);
        text(ctx, 'PAUSED', VW / 2 - 62, VH / 2 + 3, F.big, 0.95, 'left', 4);
        text(ctx, 'P TO RESUME', VW / 2, VH / 2 + 42, F.small, 0.55, 'center', 2.5);
      } else if (urgent) {
        // an urgent alarm takes the whole band
        const flash = urgent.acked ? 1 : 0.35 + 0.65 * t3Flash(urgent.age);
        ctx.globalAlpha = flash;
        monoIcon(ctx, urgent.acked ? 'check' : 'warn', VW / 2 - 186, 50, 40, 2.4);
        text(ctx, urgent.label.toUpperCase(), VW / 2 - 132, 84, F.big, flash, 'left', 3);
        text(ctx, urgent.acked ? 'ACKNOWLEDGED' : urgent.detail.toUpperCase(), VW / 2, 124, F.mid, 0.85, 'center', 2);
        if (!urgent.acked) {
          text(ctx, 'A  ACKNOWLEDGE', VW / 2, 170, F.small, 0.6, 'center', 2.5);
          edgeChevrons(ctx, urgent.side, anim, true);
        }
      } else {
        const cap = primaryCaption(view);
        // header: sound chip, else the speaker, else who is in view
        if (chip) {
          const w = `${chip.label.toUpperCase()}${chip.count > 1 ? ` ×${chip.count}` : ''}`;
          ctx.globalAlpha = 1;
          monoIcon(ctx, chip.acked ? 'check' : chip.icon, LINE_X - 2, 13, 22, 2.2);
          text(ctx, w, LINE_X + 28, 31, F.head, 1, 'left', 2);
          if (cap) text(ctx, chip.acked ? 'OK' : chip.detail.toUpperCase(), VW - LINE_X, 31, F.small, 0.7, 'right', 2);
          edgeChevrons(ctx, chip.side, anim, !chip.acked);
        } else if (cap) {
          const name = cap.name.toUpperCase();
          text(ctx, name, LINE_X, 31, F.head, cap.alpha, 'left', 2);
          ctx.font = F.head;
          ctx.letterSpacing = '2px';
          const nw = ctx.measureText(name).width;
          ctx.letterSpacing = '0px';
          if (cap.speaking) {
            ctx.globalAlpha = cap.alpha * 0.9;
            ctx.fillStyle = G;
            for (let i = 0; i < 3; i++) {
              const v = 0.35 + 0.65 * Math.abs(Math.sin(anim * (7 + i * 2.3) + i));
              ctx.fillRect(LINE_X + nw + 12 + i * 6, 25 - 7 * v, 3, 14 * v);
            }
          }
          const right = cap.translated ? `${cap.lang.toUpperCase()} → EN` : cap.pending ? `${cap.lang.toUpperCase()} …` : cap.dir?.off ? cap.dir.side.toUpperCase() : '';
          if (right) text(ctx, right, VW - LINE_X, 31, F.small, 0.7 * cap.alpha, 'right', 2);
        } else {
          const names = inView(view);
          text(ctx, names.length ? `IN VIEW · ${names.join(' · ').toUpperCase()}` : 'LISTENING', LINE_X, 31, F.small, 0.5, 'left', 2.5);
        }
        ctx.globalAlpha = 0.22;
        ctx.fillStyle = G;
        ctx.shadowBlur = 0;
        ctx.fillRect(LINE_X, 44, LINE_W, 1);
        glowOn(ctx);

        const prop = view.pendingProposal;
        const toast = view.toasts.find((t) => t.kind === 'learned' && t.age < 3);
        const footer = !!prop || !!toast;
        if (cap) {
          const lines = wrapPx(ctx, cap.text, LINE_W, F.body);
          const maxLines = footer ? 2 : 3;
          const shown = lines.slice(-maxLines);
          const a = cap.alpha * (cap.final ? 1 : 0.62);
          shown.forEach((ln, i) => text(ctx, (i === 0 && lines.length > maxLines ? '… ' : '') + ln, LINE_X, 82 + i * LH, F.body, a));
          if (!chip && cap.dir && cap.dir.side !== 'ahead') edgeChevrons(ctx, cap.dir.side, anim, !!cap.dir.off);
        } else if (chip) {
          // nobody talking: the band has room to spell the sound out
          const more = view.alerts.filter((x) => x !== chip && !x.watch && x.level !== 'urgent');
          text(ctx, chip.acked ? 'ACKNOWLEDGED' : chip.detail, LINE_X, 82, F.body, 0.95);
          if (more[0]) text(ctx, `+ ${more[0].label} · ${more[0].detail}`, LINE_X, 82 + LH, F.body, 0.7);
          if (!chip.acked && !footer) text(ctx, 'A  ACKNOWLEDGE', VW - LINE_X, 182, F.small, 0.55, 'right', 2.5);
        }
        if (prop) {
          const label = `${prop.name.toUpperCase()}?`;
          text(ctx, label, VW / 2 - 70, 180, F.head, 1, 'right', 2);
          checkMark(ctx, VW / 2 - 44, 174, 20, 1);
          text(ctx, 'Y', VW / 2 - 18, 180, F.head, 0.8, 'left', 1);
          crossMark(ctx, VW / 2 + 22, 174, 20, 1);
          text(ctx, 'N', VW / 2 + 48, 180, F.head, 0.8, 'left', 1);
        } else if (toast) {
          checkMark(ctx, VW / 2 - 130, 174, 18, 0.9);
          text(ctx, `${toast.text.toUpperCase()} SAVED`, VW / 2 - 104, 180, F.head, 0.9 * clamp((3 - toast.age) / 0.4), 'left', 2);
        }
      }
      ctx.restore();
      ctx.restore();
    },
  };
}
