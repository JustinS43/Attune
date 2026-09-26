/*
 * Mode 2 - Mono green waveguide, Even Realities G1 class (binocular, 640x200 green display,
 * about 25 degrees diagonal, focused about 2 m away).
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-06, P-08.
 *
 * Physically honest (docs/glasses-realism.md): the display is a 640x200 band that covers about
 * 530x165 px of the 1920x1080 frame, centred horizontally and at or slightly above eye level
 * (display height levels 0-8, keys [ and ]; level 4 puts its centre about 5 degrees up). It is
 * head-locked, so there are no face-anchored bubbles: the current speaker's name, 4 caption lines,
 * and < > chevrons at the band's edges toward the speaker or a sound. Pure green light only: no
 * fills, nothing dark (a waveguide cannot draw black), a 3-4 px glow, nothing outside the band.
 * Alerts become an icon and a word; name proposals read "SAM?  ✓ Y  ✕ N".
 */

import { W, H, FT, PX, font, clamp, hexA, rrect, icon, chevrons, wrapPx, degToPx } from '../hud.js';
import { t3Flash } from '../alerts.js';
import { inView, primaryCaption } from './common.js';

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

function edgeChevrons(ctx, side, anim, strong) {
  const a = strong ? 0.55 + 0.45 * Math.sin(anim * 7) : 0.85;
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

function monoIcon(ctx, name, x, y, size, lw = 2.2, a = 1) {
  ctx.globalAlpha = a;
  icon(ctx, name, x, y, size, G, lw);
}

export function createMonoMode() {
  return {
    id: 'mono',
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
        // an urgent alarm takes the whole band
        const flash = urgent.acked ? 1 : 0.35 + 0.65 * t3Flash(urgent.age);
        monoIcon(ctx, urgent.acked ? 'check' : 'warn', VW / 2 - 190, 44, 42, 2.6, flash);
        text(ctx, urgent.label.toUpperCase(), VW / 2 - 136, 80, F.big, flash, 'left', 3);
        text(ctx, urgent.acked ? 'ACKNOWLEDGED' : urgent.detail.toUpperCase(), VW / 2, 124, F.mid, 0.9, 'center', 2);
        if (!urgent.acked) {
          text(ctx, 'A  ACKNOWLEDGE', VW / 2, 176, F.small, 0.7, 'center', 2.5);
          edgeChevrons(ctx, urgent.side, anim, true);
        }
      } else {
        const cap = primaryCaption(view);
        // header: a sound, else the speaker, else who is in view
        if (chip) {
          const w = `${chip.label.toUpperCase()}${chip.count > 1 ? ` ×${chip.count}` : ''}`;
          monoIcon(ctx, chip.acked ? 'check' : chip.icon, LINE_X - 2, 11, 24, 2.3);
          text(ctx, w, LINE_X + 30, HEAD_Y, F.head, 1, 'left', 2);
          if (cap) text(ctx, chip.acked ? 'OK' : chip.detail.toUpperCase(), VW - LINE_X, HEAD_Y, F.small, 0.75, 'right', 2);
          edgeChevrons(ctx, chip.side, anim, !chip.acked);
        } else if (cap) {
          const name = cap.name.toUpperCase();
          text(ctx, name, LINE_X, HEAD_Y, F.head, cap.alpha, 'left', 2);
          ctx.font = F.head;
          ctx.letterSpacing = '2px';
          const nw = ctx.measureText(name).width;
          ctx.letterSpacing = '0px';
          if (cap.speaking) {
            ctx.globalAlpha = cap.alpha * 0.9;
            ctx.fillStyle = G;
            for (let i = 0; i < 3; i++) {
              const v = 0.35 + 0.65 * Math.abs(Math.sin(anim * (7 + i * 2.3) + i));
              ctx.fillRect(LINE_X + nw + 12 + i * 6, HEAD_Y - 6 - 7 * v, 3, 14 * v);
            }
          }
          const right = cap.translated ? `${cap.lang.toUpperCase()} → EN` : cap.pending ? `${cap.lang.toUpperCase()} …` : cap.dir?.off ? cap.dir.side.toUpperCase() : '';
          if (right) text(ctx, right, VW - LINE_X, HEAD_Y, F.small, 0.75 * cap.alpha, 'right', 2);
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

        const prop = view.pendingProposal;
        const toast = view.toasts.find((t) => t.kind === 'learned' && t.age < 3);
        const footer = !!prop || !!toast;
        const footY = BODY_Y + 3 * LH;
        if (cap) {
          const lines = wrapPx(ctx, cap.text, LINE_W, F.body);
          const maxLines = footer ? 3 : 4;
          const shown = lines.slice(-maxLines);
          const a = cap.alpha * (cap.final ? 1 : 0.66);
          shown.forEach((ln, i) => text(ctx, (i === 0 && lines.length > maxLines ? '… ' : '') + ln, LINE_X, BODY_Y + i * LH, F.body, a));
          if (!chip && cap.dir && cap.dir.side !== 'ahead') edgeChevrons(ctx, cap.dir.side, anim, !!cap.dir.off);
        } else if (chip) {
          // nobody talking: the band has room to spell the sound out
          const more = view.alerts.filter((x) => x !== chip && !x.watch && x.level !== 'urgent');
          text(ctx, chip.acked ? 'Acknowledged' : chip.detail, LINE_X, BODY_Y, F.body, 0.95);
          if (more[0]) text(ctx, `+ ${more[0].label} · ${more[0].detail}`, LINE_X, BODY_Y + LH, F.body, 0.75);
          if (!chip.acked && !footer) text(ctx, 'A  ACKNOWLEDGE', VW - LINE_X, footY, F.small, 0.65, 'right', 2.5);
        }
        if (prop) {
          const label = `${prop.name.toUpperCase()}?`;
          text(ctx, label, VW / 2 - 70, footY, F.head, 1, 'right', 2);
          monoIcon(ctx, 'check', VW / 2 - 50, footY - 19, 20, 2.4);
          text(ctx, 'Y', VW / 2 - 22, footY, F.head, 0.85, 'left', 1);
          monoIcon(ctx, 'cross', VW / 2 + 18, footY - 19, 20, 2.4);
          text(ctx, 'N', VW / 2 + 46, footY, F.head, 0.85, 'left', 1);
        } else if (toast) {
          monoIcon(ctx, 'check', VW / 2 - 134, footY - 18, 18, 2.4, 0.9);
          text(ctx, `${toast.text.toUpperCase()} SAVED`, VW / 2 - 108, footY, F.head, 0.9 * clamp((3 - toast.age) / 0.4), 'left', 2);
        }
      }
      ctx.restore();
      ctx.restore();
    },
  };
}
