/*
 * Mode 3 - Monocular corner HUD (Google Glass / Rokid class).
 *
 * One small full-colour display in the upper right of the right eye (~28% of the width, 16:9).
 * It can't anchor anything to faces, so it shows a compact card: the speaker's colour dot and
 * name, two caption lines and an arrow (← ↖ ↑ ↗ →, or ↓ for behind) toward the speaker.
 * Alerts take over the card; name proposals and the paused state fit in the same space.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-06.
 */

import {
  W, FD, FT, MINT, ACCENT, font, clamp, hexA, rrect, glass, icon, eqBars, dot, keycap, arrow, logoMark, wrapPx, textW,
  follow,
} from '../hud.js';
import { t3Flash } from '../alerts.js';
import { dirAngle, sideAngle, inView, primaryCaption } from './common.js';

const DW = 544;
const DH = 306;
const DX = W - DW - 64;
const DY = 64;
const PAD = 26;
const F = {
  brand: font(620, 17, FD),
  status: font(500, 16, FT),
  name: font(640, 28, FD),
  sub: font(450, 18, FT),
  body: font(450, 29, FT),
  big: font(700, 36, FD),
  mid: font(480, 21, FT),
  small: font(500, 17, FT),
};

function arrowBadge(ctx, cx, cy, r, ang, color, a = 1) {
  ctx.save();
  ctx.globalAlpha *= a;
  ctx.fillStyle = hexA(color, 0.16);
  ctx.strokeStyle = hexA(color, 0.55);
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  arrow(ctx, cx, cy, r * 1.05, ang, color, r * 0.13);
  ctx.restore();
}

export function createCornerMode() {
  let angle = -Math.PI / 2; // smoothed arrow angle
  let lastKey = null;
  return {
    id: 'corner',
    name: 'Monocular corner HUD',
    device: 'Small full-colour display, right eye · Google Glass / Rokid class',
    blur: true,
    render(ctx, view, env) {
      const { anim, blur } = env;
      const urgent = view.alerts.find((a) => a.level === 'urgent' && !a.watch);
      const al = urgent ?? view.alerts.find((a) => !a.watch) ?? null;
      const alertColor = al ? (al.acked ? MINT : al.color) : null;
      const flash = al && !al.acked && al.level === 'urgent' ? t3Flash(al.age) : 0;

      glass(ctx, blur, DX, DY, DW, DH, 22, {
        tint: al ? hexA(al.acked ? '#0C1412' : '#1A0E10', 0.7) : 'rgba(10,12,17,0.62)',
        glow: alertColor,
        border: al ? hexA(alertColor, 0.4 + 0.5 * flash) : 'rgba(255,255,255,0.18)',
      });
      ctx.save();
      ctx.beginPath();
      ctx.roundRect(DX, DY, DW, DH, 22);
      ctx.clip();
      ctx.textBaseline = 'middle';

      // top row: brand + status
      const ty = DY + 30;
      logoMark(ctx, DX + PAD + 9, ty, 9.5, 1, 0.95);
      ctx.font = F.brand;
      ctx.fillStyle = 'rgba(255,255,255,0.9)';
      ctx.fillText('Attune', DX + PAD + 26, ty + 1);
      const stLabel = { listening: 'Listening', paused: 'Paused', alert: 'Sound alert', connecting: 'Connecting…' }[view.status];
      const stColor = view.status === 'alert' ? alertColor : view.status === 'listening' ? MINT : 'rgba(255,255,255,0.6)';
      ctx.font = F.status;
      ctx.textAlign = 'right';
      ctx.fillStyle = 'rgba(255,255,255,0.72)';
      ctx.fillText(stLabel, DX + DW - PAD, ty + 1);
      const sw = textW(ctx, stLabel, F.status);
      ctx.textAlign = 'left';
      ctx.fillStyle = stColor;
      ctx.globalAlpha = view.status === 'paused' ? 1 : 0.6 + 0.4 * Math.sin(anim * 4);
      ctx.beginPath();
      ctx.arc(DX + DW - PAD - sw - 12, ty, 4.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.fillStyle = 'rgba(255,255,255,0.12)';
      ctx.fillRect(DX + PAD, DY + 54, DW - PAD * 2, 1);

      const cy0 = DY + 54;
      if (view.status === 'connecting') {
        ctx.font = F.name;
        ctx.textAlign = 'center';
        ctx.fillStyle = 'rgba(255,255,255,0.9)';
        ctx.fillText('Connecting…', DX + DW / 2, cy0 + 96);
        ctx.font = F.mid;
        ctx.fillStyle = 'rgba(255,255,255,0.58)';
        ctx.fillText('Waiting for the Attune engine', DX + DW / 2, cy0 + 138);
        ctx.textAlign = 'left';
      } else if (view.paused) {
        ctx.fillStyle = 'rgba(255,255,255,0.85)';
        ctx.fillRect(DX + DW / 2 - 70, cy0 + 80, 7, 30);
        ctx.fillRect(DX + DW / 2 - 57, cy0 + 80, 7, 30);
        ctx.font = F.big;
        ctx.fillStyle = '#FFFFFF';
        ctx.fillText('Paused', DX + DW / 2 - 36, cy0 + 97);
        ctx.font = F.mid;
        ctx.textAlign = 'center';
        ctx.fillStyle = 'rgba(255,255,255,0.6)';
        ctx.fillText('Nothing is being recognised', DX + DW / 2, cy0 + 150);
        ctx.textAlign = 'left';
        keycap(ctx, DX + DW / 2 - 60, cy0 + 198, 'P', { size: 16 });
        ctx.font = F.small;
        ctx.fillStyle = 'rgba(255,255,255,0.6)';
        ctx.fillText('to resume', DX + DW / 2 - 28, cy0 + 199);
      } else if (al) {
        // the alert takes over the card
        const icx = DX + PAD + 40;
        const icy = cy0 + 82;
        ctx.save();
        ctx.fillStyle = alertColor;
        ctx.shadowColor = alertColor;
        ctx.shadowBlur = 20 * flash * env.px;
        ctx.beginPath();
        ctx.arc(icx, icy, 38, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
        icon(ctx, al.acked ? 'check' : al.icon, icx - 20, icy - 21, 40, al.level === 'urgent' && !al.acked ? '#FFFFFF' : '#141414', 2.4);
        ctx.font = F.big;
        ctx.fillStyle = '#FFFFFF';
        ctx.fillText(al.label + (al.count > 1 && !al.acked ? ` ×${al.count}` : ''), icx + 58, icy - 14);
        ctx.font = F.mid;
        ctx.fillStyle = 'rgba(255,255,255,0.75)';
        ctx.fillText(al.acked ? 'Acknowledged' : al.detail, icx + 60, icy + 24);
        const ang = sideAngle(al.side);
        if (ang != null && !al.acked) {
          const nud = Math.sin(anim * 6) * 3;
          arrowBadge(ctx, DX + DW / 2 + Math.cos(ang) * nud, cy0 + 186 + Math.sin(ang) * nud, 30, ang, alertColor);
          ctx.font = F.small;
          ctx.fillStyle = hexA(alertColor, 0.95);
          ctx.textAlign = 'center';
          ctx.fillText({ left: 'Left', right: 'Right', behind: 'Behind you' }[al.side] ?? '', DX + DW / 2, cy0 + 234);
          ctx.textAlign = 'left';
        }
        if (!al.acked) {
          const kx = DX + DW - PAD - 150;
          keycap(ctx, kx, cy0 + 222, 'A', { size: 15 });
          ctx.font = F.small;
          ctx.fillStyle = 'rgba(255,255,255,0.6)';
          ctx.fillText('acknowledge', kx + 32, cy0 + 223);
        }
      } else {
        const cap = primaryCaption(view);
        const prop = view.pendingProposal;
        if (cap) {
          const ny = cy0 + 44;
          const a = cap.alpha;
          ctx.globalAlpha = a;
          dot(ctx, DX + PAD + 8, ny, 8, cap.color);
          ctx.font = F.name;
          ctx.fillStyle = '#FFFFFF';
          ctx.fillText(cap.name, DX + PAD + 28, ny + 1);
          let hx = DX + PAD + 28 + textW(ctx, cap.name, F.name) + 12;
          const sub = cap.translated ? `${cap.lang.toUpperCase()} → EN` : cap.relation;
          if (sub) {
            ctx.font = F.sub;
            ctx.fillStyle = cap.translated ? ACCENT : 'rgba(255,255,255,0.58)';
            ctx.fillText(sub, hx, ny + 2);
            hx += textW(ctx, sub, F.sub) + 12;
          }
          eqBars(ctx, hx + 2, ny, cap.color, anim, cap.speaking ? 1 : 0.15, 16);
          // direction arrow toward the speaker
          const target = dirAngle(cap.dir);
          if (target != null) {
            if (lastKey !== cap.key) angle = target;
            let d = target - angle;
            while (d > Math.PI) d -= Math.PI * 2;
            while (d < -Math.PI) d += Math.PI * 2;
            angle += d * follow(view.dt, 10);
            arrowBadge(ctx, DX + DW - PAD - 26, ny, 25, angle, cap.color, a);
          }
          lastKey = cap.key;
          const lines = wrapPx(ctx, cap.text, DW - PAD * 2, F.body);
          const shown = lines.slice(-2);
          ctx.font = F.body;
          ctx.textBaseline = 'alphabetic';
          ctx.fillStyle = '#FFFFFF';
          ctx.globalAlpha = a * (cap.final ? 1 : 0.72);
          shown.forEach((ln, i) => ctx.fillText((i === 0 && lines.length > 2 ? '… ' : '') + ln, DX + PAD, cy0 + 118 + i * 40));
          ctx.globalAlpha = 1;
          ctx.textBaseline = 'middle';
          if (cap.orig && !prop) {
            ctx.font = font('italic 400', 18, FT);
            ctx.fillStyle = 'rgba(255,255,255,0.5)';
            const o = cap.orig.length > 46 ? `…${cap.orig.slice(-46)}` : cap.orig;
            ctx.fillText(o, DX + PAD, cy0 + 214);
          }
        } else {
          const names = inView(view);
          ctx.font = F.mid;
          ctx.fillStyle = 'rgba(255,255,255,0.5)';
          ctx.fillText(names.length ? `In view: ${names.join(', ')}` : 'Listening…', DX + PAD, cy0 + 50);
          let x = DX + PAD;
          for (const f of view.faces.filter((q) => q.known).slice(0, 4)) {
            dot(ctx, x + 7, cy0 + 92, 7, f.color);
            x += 24;
          }
        }
        if (prop) {
          const py = cy0 + 216;
          rrect(ctx, DX + PAD - 8, py - 24, DW - PAD * 2 + 16, 48, 14);
          ctx.fillStyle = hexA(ACCENT, 0.12);
          ctx.fill();
          ctx.font = font(620, 22, FD);
          ctx.fillStyle = '#FFFFFF';
          ctx.fillText(`Is this ${prop.name}?`, DX + PAD + 6, py + 1);
          const kx = DX + DW - PAD - 128;
          keycap(ctx, kx, py, 'Y', { size: 15, color: MINT, stroke: hexA(MINT, 0.6), fill: hexA(MINT, 0.14) });
          ctx.font = F.small;
          ctx.fillStyle = 'rgba(255,255,255,0.6)';
          ctx.fillText('yes', kx + 30, py + 1);
          keycap(ctx, kx + 66, py, 'N', { size: 15 });
          ctx.fillText('no', kx + 96, py + 1);
        } else {
          const toast = view.toasts.find((t) => t.kind === 'learned' && t.age < 3);
          if (toast) {
            ctx.globalAlpha = clamp(toast.age / 0.3) * clamp((3 - toast.age) / 0.4);
            icon(ctx, 'check', DX + PAD, cy0 + 204, 20, MINT, 2.4);
            ctx.font = F.small;
            ctx.fillStyle = 'rgba(255,255,255,0.8)';
            ctx.fillText(`${toast.text} saved to your people`, DX + PAD + 28, cy0 + 215);
            ctx.globalAlpha = 1;
          }
        }
      }
      ctx.restore();
    },
  };
}
