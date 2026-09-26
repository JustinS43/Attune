/*
 * Mode 1 - Full-colour AR (Meta Orion / XREAL class, binocular waveguide), the launch film's look.
 * Face-anchored name tags and speech bubbles, docked off-screen speakers, the You bar,
 * alert chips/cards with direction, name proposals, the status pill and paused state.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-06, P-07, P-08.
 */

import { createBubbleLayer } from '../bubbles.js';
import { drawAlerts, drawStatus, drawToasts, drawPaused } from '../alerts.js';

export function createColorMode() {
  const bubbles = createBubbleLayer();
  let obstacles = [];
  return {
    id: 'color',
    name: 'Full-colour AR',
    device: 'Binocular waveguide · Meta Orion / XREAL class',
    blur: true,
    render(ctx, view, env) {
      bubbles.render(ctx, view, { ...env, obstacles });
      obstacles = drawAlerts(ctx, env.blur, view, env.anim).map((r) => ({ ...r, x: r.x - 10, w: r.w + 20 }));
      drawToasts(ctx, env.blur, view, env.anim, 40);
      drawPaused(ctx, env.blur, view, env.anim);
      drawStatus(ctx, env.blur, view, env.anim);
    },
  };
}
