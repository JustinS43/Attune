/*
 * Mode 3 - Monocular display, Meta Ray-Ban Display class (right eye only, 600x600 full colour,
 * about 20 degrees square), with a Google Glass placement variant (key G).
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-06, P-08.
 *
 * Physically honest (docs/glasses-realism.md): the display is a small square slightly below and
 * to the right of centre (about 307x307 px of the 1920x1080 frame, x 1028-1335, y 452-759), seen by
 * the right eye only, so nothing is drawn anywhere else and there is no second eye to fake. It is
 * a translucent, additive image (about 80% opacity, no dark panels). Captions sit at the bottom of
 * the square: a small "Live captions" label, the speaker's name and a direction arrow, then two
 * lines of white text. Alerts take over the square. The Google Glass variant is a 285x160 display
 * above the right eye's line of sight (x 1000-1285, y 204-364).
 */

import { FD, FT, MINT, ACCENT, PX, font, clamp, hexA, rrect, icon, eqBars, dot, keycap, arrow, logoMark, wrapPx, textW, follow } from '../hud.js';
import { t3Flash } from '../alerts.js';
import { dirAngle, sideAngle, inView, primaryCaption } from './common.js';

export const MONOCULAR = {
  rayban: {
    rect: { x: 1028, y: 452, w: 307, h: 307 }, radius: 18, k: 1,
    name: 'Monocular display', device: 'Right eye only · Meta Ray-Ban Display class',
  },
  glass: {
    rect: { x: 1000, y: 204, w: 285, h: 160 }, radius: 10, k: 0.86,
    name: 'Monocular display', device: 'Right eye, above the line of sight · Google Glass class',
  },
};
const OPACITY = 0.8; // the image is translucent: the world shows through it
const WHITE = '#FFFFFF';

function fonts(k) {
  return {
    label: font(620, 12.5 * k, FT),
    status: font(560, 12.5 * k, FT),
    name: font(640, 18 * k, FD),
    sub: font(560, 13 * k, FT),
    body: font(560, 22 * k, FT),
    big: font(720, 27 * k, FD),
    mid: font(520, 15.5 * k, FT),
    small: font(560, 13.5 * k, FT),
  };
}

/** White text with a faint glow of its own light (a display can only add light, never shade). */
function lit(ctx, str, x, y, f, color = WHITE, a = 1, align = 'left') {
  ctx.save();
  ctx.globalAlpha *= a;
  ctx.font = f;
  ctx.textAlign = align;
  ctx.fillStyle = color;
  ctx.shadowColor = hexA(color === WHITE ? '#DDEBFF' : color, 0.55);
  ctx.shadowBlur = 5 * PX;
  ctx.fillText(str, x, y);
  ctx.restore();
}

/**
 * Brightness headroom. The real display is far brighter than a video can be (thousands of nits
 * against a monitor's white), so white text would wash out over a white page in this simulation
 * where it doesn't on the glasses. Behind the text block the world is attenuated a little, with
 * soft edges, to stand in for that headroom. It is a simulation aid, not a panel the glasses
 * draw (docs/glasses-realism.md).
 */
function headroom(ctx, x, y, w, h, a = 0.3) {
  ctx.save();
  ctx.filter = `blur(${10 * PX}px)`;
  ctx.fillStyle = `rgba(0,0,0,${a})`;
  ctx.fillRect(x, y, w, h);
  ctx.restore();
}

function arrowBadge(ctx, cx, cy, r, ang, color, a = 1) {
  ctx.save();
  ctx.globalAlpha *= a;
  ctx.strokeStyle = hexA(color, 0.75);
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.stroke();
  arrow(ctx, cx, cy, r * 1.1, ang, color, Math.max(2, r * 0.16));
  ctx.restore();
}

export function createCornerMode() {
  let angle = -Math.PI / 2; // smoothed arrow angle
  let lastKey = null;
  const mode = {
    id: 'corner',
    variant: 'rayban',
    get name() {
      return MONOCULAR[mode.variant].name;
    },
    get device() {
      return MONOCULAR[mode.variant].device;
    },
    blur: false,
    render(ctx, view, env) {
      const { anim } = env;
      const spec = MONOCULAR[mode.variant] ?? MONOCULAR.rayban;
      const { x: DX, y: DY, w: DW, h: DH } = spec.rect;
      const k = spec.k;
      const F = fonts(k);
      const PAD = 15 * k;
      const B = DY + DH; // bottom edge
      const compact = DH < 220;

      if (env.chrome) {
        ctx.save();
        rrect(ctx, DX + 0.5, DY + 0.5, DW - 1, DH - 1, spec.radius);
        ctx.strokeStyle = 'rgba(255,255,255,0.14)';
        ctx.lineWidth = 1.2;
        ctx.setLineDash([2, 6]);
        ctx.stroke();
        ctx.restore();
      }
      ctx.save();
      ctx.beginPath();
      ctx.roundRect(DX, DY, DW, DH, spec.radius);
      ctx.clip();
      ctx.globalAlpha = OPACITY;
      ctx.textBaseline = 'middle';

      const urgent = view.alerts.find((a) => a.level === 'urgent' && !a.watch);
      const al = urgent ?? view.alerts.find((a) => !a.watch) ?? null;
      const alertColor = al ? (al.acked ? MINT : al.color) : null;
      const flash = al && !al.acked && al.level === 'urgent' ? t3Flash(al.age) : 0;

      // top: a tiny status line (the rest of the square stays clear)
      const ty = DY + PAD + 6 * k;
      logoMark(ctx, DX + PAD + 7 * k, ty, 7 * k, 1, 0.9);
      const stLabel = { listening: 'Listening', paused: 'Paused', alert: 'Sound alert', connecting: 'Connecting…' }[view.status];
      const stColor = view.status === 'alert' ? alertColor : view.status === 'listening' ? MINT : 'rgba(255,255,255,0.7)';
      ctx.save();
      ctx.globalAlpha *= view.status === 'paused' ? 1 : 0.6 + 0.4 * Math.sin(anim * 4);
      dot(ctx, DX + PAD + 22 * k, ty, 3.5 * k, stColor);
      ctx.restore();
      lit(ctx, stLabel, DX + PAD + 30 * k, ty + 1, F.status, WHITE, 0.75);

      if (view.status === 'connecting') {
        lit(ctx, 'Connecting…', DX + DW / 2, DY + DH / 2 - 8 * k, F.big, WHITE, 0.9, 'center');
        lit(ctx, 'Waiting for the Attune engine', DX + DW / 2, DY + DH / 2 + 24 * k, F.mid, WHITE, 0.65, 'center');
      } else if (view.paused) {
        ctx.fillStyle = 'rgba(255,255,255,0.9)';
        const cx = DX + DW / 2;
        const cy = DY + DH / 2 - 6 * k;
        ctx.fillRect(cx - 52 * k, cy - 13 * k, 6 * k, 26 * k);
        ctx.fillRect(cx - 41 * k, cy - 13 * k, 6 * k, 26 * k);
        lit(ctx, 'Paused', cx - 26 * k, cy + 1, F.big);
        keycap(ctx, cx - 44 * k, cy + 40 * k, 'P', { size: 13 * k });
        lit(ctx, 'to resume', cx - 16 * k, cy + 41 * k, F.small, WHITE, 0.7);
      } else if (al) {
        // the alert takes over the square
        headroom(ctx, DX + 4, DY + 4, DW - 8, DH - 8, 0.28);
        const cx = DX + DW / 2;
        const r = (compact ? 22 : 30) * k;
        const icx = compact ? DX + PAD + r + 4 : cx;
        const icy = compact ? DY + DH / 2 - 4 * k : DY + 88 * k;
        ctx.save();
        ctx.globalAlpha *= al.acked ? 0.9 : 0.55 + 0.45 * (al.level === 'urgent' ? flash : 1);
        ctx.fillStyle = alertColor;
        ctx.beginPath();
        ctx.arc(icx, icy, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
        icon(ctx, al.acked ? 'check' : al.icon, icx - r * 0.55, icy - r * 0.58, r * 1.1, al.level === 'urgent' && !al.acked ? WHITE : '#141414', 2.4);
        const label = al.label + (al.count > 1 && !al.acked ? ` ×${al.count}` : '');
        if (compact) {
          const tx = icx + r + 12 * k;
          lit(ctx, label, tx, icy - 12 * k, F.big);
          lit(ctx, al.acked ? 'Acknowledged' : al.detail, tx, icy + 16 * k, F.mid, WHITE, 0.8);
        } else {
          lit(ctx, label, cx, icy + r + 26 * k, F.big, WHITE, 1, 'center');
          lit(ctx, al.acked ? 'Acknowledged' : al.detail, cx, icy + r + 54 * k, F.mid, WHITE, 0.8, 'center');
        }
        const ang = sideAngle(al.side);
        if (!al.acked) {
          const ay = B - PAD - 14 * k;
          if (ang != null) {
            const nud = Math.sin(anim * 6) * 2.5;
            arrowBadge(ctx, DX + PAD + 14 * k + Math.cos(ang) * nud, ay + Math.sin(ang) * nud, 14 * k, ang, alertColor);
            lit(ctx, { left: 'Left', right: 'Right', behind: 'Behind you' }[al.side] ?? '', DX + PAD + 36 * k, ay + 1, F.small, alertColor);
          }
          const kx = DX + DW - PAD - 108 * k;
          keycap(ctx, kx, ay, 'A', { size: 13 * k });
          lit(ctx, 'acknowledge', kx + 24 * k, ay + 1, F.small, WHITE, 0.7);
        }
      } else {
        // captions at the very bottom: label, speaker + arrow, two lines
        const cap = primaryCaption(view);
        const prop = view.pendingProposal;
        const toast = view.toasts.find((t) => t.kind === 'learned' && t.age < 3);
        const LH = 27 * k;
        const l2 = B - PAD - 6 * k;
        const l1 = l2 - LH;
        const rowY = l1 - 30 * k;
        const labelY = rowY - 24 * k;
        if (cap || prop || toast) headroom(ctx, DX + 4, labelY - 16 * k, DW - 8, B - labelY + 12 * k, 0.3);
        // label row: "Live captions", or a name proposal / a saved toast in its place
        if (prop) {
          lit(ctx, `Is this ${prop.name}?`, DX + PAD, labelY, F.name, ACCENT);
          const kx = DX + DW - PAD - 78 * k;
          keycap(ctx, kx, labelY, 'Y', { size: 12 * k, color: MINT, stroke: hexA(MINT, 0.7), fill: hexA(MINT, 0.14) });
          lit(ctx, 'yes', kx + 22 * k, labelY + 1, F.small, WHITE, 0.7);
          keycap(ctx, kx + 50 * k, labelY, 'N', { size: 12 * k });
        } else if (toast) {
          ctx.save();
          ctx.globalAlpha *= clamp(toast.age / 0.3) * clamp((3 - toast.age) / 0.4);
          icon(ctx, 'check', DX + PAD, labelY - 8 * k, 16 * k, MINT, 2.2);
          lit(ctx, `${toast.text} saved`, DX + PAD + 22 * k, labelY + 1, F.small, WHITE, 0.9);
          ctx.restore();
        } else {
          ctx.save();
          ctx.strokeStyle = 'rgba(255,255,255,0.6)';
          ctx.lineWidth = 1.3;
          rrect(ctx, DX + PAD, labelY - 6 * k, 17 * k, 12 * k, 3 * k);
          ctx.stroke();
          ctx.restore();
          lit(ctx, 'CC', DX + PAD + 8.5 * k, labelY + 0.5, font(700, 7.5 * k, FT), WHITE, 0.7, 'center');
          lit(ctx, 'Live captions', DX + PAD + 24 * k, labelY + 1, F.label, WHITE, 0.62);
        }
        if (cap) {
          const a = cap.alpha;
          ctx.save();
          ctx.globalAlpha *= a;
          dot(ctx, DX + PAD + 5 * k, rowY, 5 * k, cap.color);
          lit(ctx, cap.name, DX + PAD + 16 * k, rowY + 1, F.name);
          let hx = DX + PAD + 16 * k + textW(ctx, cap.name, F.name) + 9 * k;
          const offWord = cap.dir?.off ? { left: 'on your left', right: 'on your right', behind: 'behind you' }[cap.dir.side] : null;
          const sub = cap.translated ? `${cap.lang.toUpperCase()} → EN` : offWord;
          if (sub) {
            lit(ctx, sub, hx, rowY + 1, F.sub, cap.translated ? ACCENT : WHITE, cap.translated ? 1 : 0.65);
            hx += textW(ctx, sub, F.sub) + 9 * k;
          }
          eqBars(ctx, hx + 2, rowY, cap.color, anim, cap.speaking ? 1 : 0.15, 12 * k);
          // direction arrow toward the speaker
          const target = dirAngle(cap.dir);
          if (target != null) {
            if (lastKey !== cap.key) angle = target;
            let d = target - angle;
            while (d > Math.PI) d -= Math.PI * 2;
            while (d < -Math.PI) d += Math.PI * 2;
            angle += d * follow(view.dt, 10);
            arrowBadge(ctx, DX + DW - PAD - 13 * k, rowY, 12 * k, angle, cap.color);
          }
          lastKey = cap.key;
          const lines = wrapPx(ctx, cap.text, DW - PAD * 2, F.body);
          const shown = lines.slice(-2);
          ctx.textBaseline = 'alphabetic';
          shown.forEach((ln, i) => lit(ctx, (i === 0 && lines.length > 2 ? '… ' : '') + ln, DX + PAD, (shown.length === 1 ? l2 : i === 0 ? l1 : l2) + 7 * k, F.body, WHITE, cap.final ? 1 : 0.72));
          ctx.textBaseline = 'middle';
          ctx.restore();
        } else {
          const names = inView(view);
          lit(ctx, names.length ? `In view: ${names.join(', ')}` : 'Listening…', DX + PAD, rowY + 1, F.mid, WHITE, 0.6);
          let x = DX + PAD;
          for (const f of view.faces.filter((q) => q.known).slice(0, 5)) {
            dot(ctx, x + 5 * k, l1, 5 * k, f.color);
            x += 18 * k;
          }
        }
      }
      ctx.restore();
    },
  };
  return mode;
}
