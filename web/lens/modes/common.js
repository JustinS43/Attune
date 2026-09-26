/*
 * Helpers shared by the compact glasses modes (mono waveguide and monocular corner HUD).
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-06.
 */

/**
 * Direction from the wearer to a caption's speaker or an alert, as an angle in radians
 * (0 = right, -PI/2 = up / ahead, PI = left, PI/2 = down / behind), or null when unknown.
 * Faces in view give ← ↖ ↑ ↗ → from where they sit in the frame; off-screen and alert sides
 * give ← → or ↓ (behind).
 */
export function dirAngle(dir) {
  if (!dir) return null;
  if (dir.side === 'behind') return Math.PI / 2;
  if (dir.off) return dir.side === 'left' ? Math.PI : 0;
  const { dx, dy } = dir;
  if (Math.abs(dx) < 0.22) return -Math.PI / 2;
  if (dy < -0.3) return dx < 0 ? (-3 * Math.PI) / 4 : -Math.PI / 4;
  return dx < 0 ? Math.PI : 0;
}

export function sideAngle(side) {
  if (side === 'left') return Math.PI;
  if (side === 'right') return 0;
  if (side === 'behind') return Math.PI / 2;
  return null;
}

/** Names of the people in view, for the idle line ("In view: Mark, Jess"). */
export function inView(view) {
  return view.faces.filter((f) => f.known).map((f) => f.label).slice(0, 3);
}

/** The caption a one-line display should show: the most recently updated one. */
export function primaryCaption(view) {
  return view.feed.find((b) => b.alpha > 0.02) ?? null;
}
