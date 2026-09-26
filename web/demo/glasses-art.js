/*
 * Hand-drawn vector illustrations of real glasses for the demo's glasses guide.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-18.
 *
 * Each drawing is a front view, as if you were facing the wearer. The wearer's RIGHT lens is
 * therefore on the LEFT of the drawing. The display area shows as a soft glow on the lens (or
 * lenses) that have one. These are our own simplified drawings, not product photos.
 */

const W = 300;
const H = 132;
const CY = 64;
const XR = 88; // wearer's right lens (left in a front view)
const XL = 212; // wearer's left lens

// ------------------------------------------------------------------ shapes
function lensPath(kind, cx, cy, w, h) {
  const x0 = cx - w / 2;
  const x1 = cx + w / 2;
  const y0 = cy - h / 2;
  const y1 = cy + h / 2;
  switch (kind) {
    case 'round':
      return `M${x0},${cy} a${w / 2},${h / 2} 0 1 0 ${w},0 a${w / 2},${h / 2} 0 1 0 ${-w},0 Z`;
    case 'panto': // round lens with a flatter top (Even G1 "panto")
      return `M${x0 + 16},${y0} H${x1 - 16} C${x1 + 3},${y0} ${x1 + 2},${cy + h * 0.1} ${x1 - 8},${y1 - 10}
        C${x1 - 22},${y1 + 3} ${x0 + 22},${y1 + 3} ${x0 + 8},${y1 - 10} C${x0 - 2},${cy + h * 0.1} ${x0 - 3},${y0} ${x0 + 16},${y0} Z`;
    case 'wayfarer': // wider at the top, tapering down
      return `M${x0 - 5},${y0 + 4} Q${x0 - 5},${y0} ${x0},${y0} H${x1} Q${x1 + 5},${y0} ${x1 + 5},${y0 + 4}
        L${x1 - 3},${y1 - 14} Q${x1 - 6},${y1} ${x1 - 20},${y1} H${x0 + 20} Q${x0 + 6},${y1} ${x0 + 3},${y1 - 14} Z`;
    case 'goggle': // Magic-Leap-like teardrop, not used for any card yet
    case 'rect':
    default: {
      const r = Math.min(18, h / 2.6);
      return `M${x0 + r},${y0} H${x1 - r} Q${x1},${y0} ${x1},${y0 + r} V${y1 - r} Q${x1},${y1} ${x1 - r},${y1}
        H${x0 + r} Q${x0},${y1} ${x0},${y1 - r} V${y0 + r} Q${x0},${y0} ${x0 + r},${y0} Z`;
    }
  }
}

const GLOWS = {
  color: ['#7cf5d6', '#6ab8ff'],
  green: ['#3dff66', '#3dff66'],
  white: ['#ffffff', '#cfe7ff'],
};

function defs(id, glow) {
  const [a, b] = GLOWS[glow] || GLOWS.color;
  return `<defs>
    <radialGradient id="${id}-glow" cx="50%" cy="50%" r="50%">
      <stop offset="0" stop-color="${a}" stop-opacity="0.95"/>
      <stop offset="0.45" stop-color="${b}" stop-opacity="0.45"/>
      <stop offset="1" stop-color="${b}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="${id}-shine" x1="0" y1="0" x2="0.35" y2="1">
      <stop offset="0" stop-color="#fff" stop-opacity="0.22"/>
      <stop offset="0.5" stop-color="#fff" stop-opacity="0.03"/>
      <stop offset="1" stop-color="#fff" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="${id}-frame" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#fff" stop-opacity="0.16"/>
      <stop offset="0.4" stop-color="#fff" stop-opacity="0"/>
    </linearGradient>
  </defs>`;
}

/** A glow patch (the display region) inside one lens, clipped to the lens. */
function glowIn(id, clip, cx, cy, rx, ry, extra = '') {
  return `<g clip-path="url(#${clip})"><ellipse cx="${cx}" cy="${cy}" rx="${rx}" ry="${ry}" fill="url(#${id}-glow)" ${extra}/></g>`;
}

function camera(x, y, r = 5.2) {
  return `<circle cx="${x}" cy="${y}" r="${r}" fill="#0a0c10" stroke="#5c6470" stroke-width="1.4"/>
    <circle cx="${x - r * 0.3}" cy="${y - r * 0.3}" r="${r * 0.3}" fill="#9fb4d0" opacity="0.7"/>`;
}

function led(x, y) {
  return `<circle cx="${x}" cy="${y}" r="2.6" fill="#2a2f38" stroke="#5c6470" stroke-width="1"/>`;
}

function eyeTag(x, y, text = 'R') {
  return `<text x="${x}" y="${y}" text-anchor="middle" font-family="Segoe UI, system-ui, sans-serif" font-size="10"
    font-weight="700" letter-spacing="1.5" fill="rgba(255,255,255,0.55)">${text}</text>`;
}

/**
 * A pair of glasses from a few parameters.
 *   shape, lw/lh     lens shape and size
 *   rim, color       frame thickness and colour
 *   tint, tintA      lens tint
 *   brow             a thick top bar joining both lenses
 *   bridge           'keyhole' | 'straight' | 'thick'
 *   glow             'both' | 'right' | 'none', glowColor 'color' | 'green' | 'white'
 *   glowAt           {dx, dy, rx, ry} offset and size of the glow inside a lens
 *   extras           svg drawn on top
 */
function pair(id, o) {
  const lw = o.lw ?? 96;
  const lh = o.lh ?? 62;
  const rim = o.rim ?? 5;
  const color = o.color ?? '#1c1f25';
  const pR = lensPath(o.shape, XR, CY, lw, lh);
  const pL = lensPath(o.shape, XL, CY, lw, lh);
  const g = o.glowAt ?? { dx: 0, dy: -4, rx: lw * 0.36, ry: lh * 0.3 };
  const glowR = o.glow === 'both' || o.glow === 'right';
  const glowL = o.glow === 'both';
  const top = CY - lh / 2;
  const bridge = {
    keyhole: `<path d="M${XR + lw / 2 - 2},${CY - 8} Q${W / 2},${CY - 26} ${XL - lw / 2 + 2},${CY - 8}" fill="none" stroke="${color}" stroke-width="${Math.max(3, rim * 0.8)}" stroke-linecap="round"/>`,
    straight: `<path d="M${XR + lw / 2 - 1},${top + 8} Q${W / 2},${top + 2} ${XL - lw / 2 + 1},${top + 8}" fill="none" stroke="${color}" stroke-width="${rim}" stroke-linecap="round"/>`,
    thick: `<path d="M${XR + lw / 2 - 2},${top + 3} H${XL - lw / 2 + 2} V${top + 20} Q${W / 2},${top + 8} ${XR + lw / 2 - 2},${top + 20} Z" fill="${color}"/>`,
  }[o.bridge ?? 'keyhole'];
  const endW = o.endW ?? Math.max(8, rim * 1.6);
  const ends = `
    <path d="M${XR - lw / 2 - rim / 2 + 1},${top + 6} h${-endW} q-4,0 -4,5 v${o.endH ?? 12} q0,4 4,4 h${endW}" fill="${color}"/>
    <path d="M${XL + lw / 2 + rim / 2 - 1},${top + 6} h${endW} q4,0 4,5 v${o.endH ?? 12} q0,4 -4,4 h${-endW}" fill="${color}"/>`;
  const brow = o.brow
    ? `<path d="M${XR - lw / 2 - rim / 2 - 2},${top - rim / 2 - 2} H${XL + lw / 2 + rim / 2 + 2} V${top + (o.browH ?? 12)} H${XR - lw / 2 - rim / 2 - 2} Z" fill="${color}"/>`
    : '';
  return `${defs(id, o.glowColor)}
    <clipPath id="${id}-cr"><path d="${pR}"/></clipPath>
    <clipPath id="${id}-cl"><path d="${pL}"/></clipPath>
    ${o.under ?? ''}
    <path d="${pR}" fill="${o.tint ?? '#9cc3d6'}" fill-opacity="${o.tintA ?? 0.08}"/>
    <path d="${pL}" fill="${o.tint ?? '#9cc3d6'}" fill-opacity="${o.tintA ?? 0.08}"/>
    ${glowR ? glowIn(id, `${id}-cr`, XR + g.dx, CY + g.dy, g.rx, g.ry) : ''}
    ${glowL ? glowIn(id, `${id}-cl`, XL - g.dx, CY + g.dy, g.rx, g.ry) : ''}
    <path d="${pR}" fill="url(#${id}-shine)"/>
    <path d="${pL}" fill="url(#${id}-shine)"/>
    ${ends}
    ${brow}
    <path d="${pR}" fill="none" stroke="${color}" stroke-width="${rim}" stroke-linejoin="round"/>
    <path d="${pL}" fill="none" stroke="${color}" stroke-width="${rim}" stroke-linejoin="round"/>
    <path d="${pR}" fill="none" stroke="url(#${id}-frame)" stroke-width="${rim}" stroke-linejoin="round"/>
    <path d="${pL}" fill="none" stroke="url(#${id}-frame)" stroke-width="${rim}" stroke-linejoin="round"/>
    ${bridge}
    ${o.extras ?? ''}
    ${o.glow === 'right' ? eyeTag(XR, CY + lh / 2 + rim / 2 + 16, o.eyeLabel ?? 'RIGHT EYE') : ''}`;
}

// ------------------------------------------------------------------ one drawing per model
const ART = {
  // Meta Orion: thick black magnesium frame, cameras at the outer corners, full-colour
  // binocular display across most of each lens.
  orion: (id) => pair(id, {
    shape: 'rect', lw: 98, lh: 66, rim: 10, color: '#17191e', brow: true, browH: 10, bridge: 'thick',
    tint: '#b7d4e6', tintA: 0.14, glow: 'both', glowColor: 'color', glowAt: { dx: 0, dy: 0, rx: 46, ry: 34 },
    endW: 16, endH: 16,
    extras: camera(XR - 44, CY - 34, 4) + camera(XL + 44, CY - 34, 4),
  }),
  // Snap Specs: chunky angular frame with big cameras at both outer corners.
  specs: (id) => pair(id, {
    shape: 'rect', lw: 94, lh: 60, rim: 13, color: '#23262d', brow: true, browH: 14, bridge: 'thick',
    tint: '#5f7385', tintA: 0.3, glow: 'both', glowColor: 'color', glowAt: { dx: 0, dy: 2, rx: 38, ry: 26 },
    endW: 22, endH: 20,
    extras: camera(XR - 52, CY - 28, 7) + camera(XL + 52, CY - 28, 7)
      + `<rect x="${W / 2 - 6}" y="${CY - 40}" width="12" height="4" rx="2" fill="#39404b"/>`,
  }),
  // XREAL One Pro: sunglasses shape with dark lenses; a virtual screen in both eyes; USB-C cable.
  xreal: (id) => pair(id, {
    shape: 'wayfarer', lw: 100, lh: 56, rim: 7, color: '#0f1013', bridge: 'straight',
    tint: '#1a1d24', tintA: 0.82, glow: 'both', glowColor: 'white', glowAt: { dx: 0, dy: 0, rx: 34, ry: 20 },
    endW: 12,
    extras: `<rect x="${XR - 24}" y="${CY - 13}" width="48" height="27" rx="3" fill="none" stroke="#dff1ff" stroke-opacity="0.5" stroke-width="1.2"/>
      <rect x="${XL - 24}" y="${CY - 13}" width="48" height="27" rx="3" fill="none" stroke="#dff1ff" stroke-opacity="0.5" stroke-width="1.2"/>
      <path d="M${XR - 60},${CY - 14} C${XR - 76},${CY + 6} ${XR - 70},${CY + 40} ${XR - 58},${H - 6}" fill="none" stroke="#2b2f37" stroke-width="4" stroke-linecap="round"/>
      <rect x="${XR - 63}" y="${H - 14}" width="10" height="12" rx="2" fill="#3a404a"/>`,
  }),
  // Even Realities G1: thin round "panto" magnesium frame, no camera, green band in both eyes.
  g1: (id) => pair(id, {
    shape: 'panto', lw: 90, lh: 76, rim: 3.2, color: '#b8bcc2', bridge: 'keyhole',
    tint: '#cfe6f0', tintA: 0.05, glow: 'both', glowColor: 'green', glowAt: { dx: 2, dy: -8, rx: 30, ry: 11 },
    endW: 7, endH: 8,
    extras: bandLines(XR + 2, CY - 8) + bandLines(XL - 2, CY - 8),
  }),
  // Even Realities G2: slimmer rectangular frame, dark, green band in both eyes.
  g2: (id) => pair(id, {
    shape: 'rect', lw: 96, lh: 58, rim: 4, color: '#3c4148', bridge: 'straight',
    tint: '#cfe6f0', tintA: 0.05, glow: 'both', glowColor: 'green', glowAt: { dx: 2, dy: -6, rx: 32, ry: 12 },
    endW: 8, endH: 9,
    extras: bandLines(XR + 2, CY - 6) + bandLines(XL - 2, CY - 6),
  }),
  // Halliday G2: ordinary-looking black frame with two green microLED engines.
  halliday: (id) => pair(id, {
    shape: 'wayfarer', lw: 94, lh: 58, rim: 6.5, color: '#141619', bridge: 'straight',
    tint: '#cfe6f0', tintA: 0.06, glow: 'both', glowColor: 'green', glowAt: { dx: 0, dy: -6, rx: 30, ry: 11 },
    endW: 11,
    extras: bandLines(XR, CY - 6) + bandLines(XL, CY - 6),
  }),
  // TranscribeGlass: a clip-on module on the right temple of the wearer's own glasses, projecting
  // a green caption display into the right lens.
  transcribe: (id) => pair(id, {
    shape: 'rect', lw: 92, lh: 60, rim: 5, color: '#6b4a35', bridge: 'keyhole',
    tint: '#cfe6f0', tintA: 0.05, glow: 'right', glowColor: 'green', glowAt: { dx: -4, dy: -2, rx: 28, ry: 14 },
    extras: bandLines(XR - 4, CY - 2, 3)
      + `<rect x="${XR - 72}" y="${CY - 30}" width="26" height="40" rx="7" fill="#e9edf1" stroke="#aab4bf" stroke-width="1.2"/>
      <rect x="${XR - 50}" y="${CY - 24}" width="18" height="16" rx="3" fill="#dfe6ec" fill-opacity="0.9" stroke="#aab4bf" stroke-width="1.2"/>
      <circle cx="${XR - 59}" cy="${CY + 2}" r="2" fill="#7cf5d6"/>`,
  }),
  // Vuzix Z100: thin, ordinary-looking black frame; monochrome display in one eye.
  z100: (id) => pair(id, {
    shape: 'rect', lw: 94, lh: 56, rim: 5, color: '#101215', bridge: 'straight',
    tint: '#cfe6f0', tintA: 0.05, glow: 'right', glowColor: 'green', glowAt: { dx: 0, dy: -2, rx: 30, ry: 14 },
    endW: 12, endH: 10, eyeLabel: 'ONE EYE',
    extras: bandLines(XR, CY - 2, 3),
  }),
  // Meta Ray-Ban Display: thick Wayfarer-style frame, camera and LED at the corners, a small
  // translucent full-colour square low in the right lens.
  rayban: (id) => pair(id, {
    shape: 'wayfarer', lw: 98, lh: 60, rim: 11, color: '#121316', bridge: 'straight',
    tint: '#b6c7d2', tintA: 0.12, glow: 'right', glowColor: 'color', glowAt: { dx: -6, dy: 8, rx: 22, ry: 20 },
    endW: 16, endH: 16,
    extras: camera(XR - 50, CY - 26, 4.2) + led(XL + 50, CY - 26)
      + `<rect x="${XR - 18}" y="${CY - 5}" width="24" height="24" rx="3" fill="#ffffff" fill-opacity="0.16" stroke="#ffffff" stroke-opacity="0.55" stroke-width="1"/>
      <rect x="${XR - 14}" y="${CY + 11}" width="16" height="2" rx="1" fill="#fff" opacity="0.85"/>
      <rect x="${XR - 14}" y="${CY + 15}" width="11" height="2" rx="1" fill="#fff" opacity="0.6"/>`,
  }),
  // Google Glass Enterprise Edition 2: a titanium band (no lenses), the Glass pod on the right
  // side and a small prism just above the right eye.
  glass: (id) => `${defs(id, 'white')}
    <path d="M26,${CY - 20} Q${W / 2},${CY - 38} ${W - 26},${CY - 20}" fill="none" stroke="#a9b0b8" stroke-width="5" stroke-linecap="round"/>
    <path d="M${W / 2 - 10},${CY - 26} q10,14 20,0" fill="none" stroke="#a9b0b8" stroke-width="3" stroke-linecap="round"/>
    <path d="M${W / 2 - 13},${CY - 20} q-6,18 -14,26 M${W / 2 + 13},${CY - 20} q6,18 14,26" fill="none" stroke="#a9b0b8" stroke-width="2.4" stroke-linecap="round"/>
    <ellipse cx="${W / 2 - 27}" cy="${CY + 8}" rx="4" ry="6" fill="#d7dde3" opacity="0.8"/>
    <ellipse cx="${W / 2 + 27}" cy="${CY + 8}" rx="4" ry="6" fill="#d7dde3" opacity="0.8"/>
    <rect x="14" y="${CY - 34}" width="58" height="26" rx="12" fill="#2a2e35" stroke="#4d5560" stroke-width="1.2"/>
    <rect x="70" y="${CY - 31}" width="30" height="20" rx="4" fill="#ffffff" fill-opacity="0.2" stroke="#dfe8f0" stroke-opacity="0.8" stroke-width="1.3"/>
    <ellipse cx="86" cy="${CY - 21}" rx="16" ry="11" fill="url(#${id}-glow)"/>
    <rect x="80" y="${CY - 25}" width="13" height="2" rx="1" fill="#fff" opacity="0.8"/>
    <rect x="80" y="${CY - 20}" width="9" height="2" rx="1" fill="#fff" opacity="0.6"/>
    ${camera(22, CY - 21, 3.2)}
    <circle cx="${XR}" cy="${CY + 14}" r="17" fill="none" stroke="#fff" stroke-opacity="0.12" stroke-dasharray="3 4"/>
    ${eyeTag(XR, CY + 50, 'RIGHT EYE')}`,
  // Brilliant Labs Frame: round lenses, a camera in the bridge, a small colour display in one eye.
  frame: (id) => pair(id, {
    shape: 'round', lw: 86, lh: 78, rim: 6, color: '#202329', bridge: 'thick',
    tint: '#cfe6f0', tintA: 0.07, glow: 'right', glowColor: 'color', glowAt: { dx: 0, dy: -2, rx: 22, ry: 16 },
    endW: 10, eyeLabel: 'ONE EYE',
    extras: camera(W / 2, CY - 30, 4) + `<rect x="${XR - 13}" y="${CY - 11}" width="26" height="17" rx="2" fill="#fff" fill-opacity="0.12" stroke="#fff" stroke-opacity="0.45" stroke-width="1"/>`,
  }),
};

// Three to four faint green caption lines, like text on a waveguide band.
function bandLines(cx, cy, n = 3) {
  let s = '';
  const widths = [34, 46, 40, 28];
  for (let i = 0; i < n; i++) {
    const w = widths[i];
    s += `<rect x="${cx - 23}" y="${cy - 7 + i * 5}" width="${w}" height="1.8" rx="0.9" fill="#8dff9f" opacity="${i === 0 ? 0.95 : 0.75}"/>`;
  }
  return s;
}

let uid = 0;

/** The SVG markup for a model, with an accessible label. */
export function glassesSvg(model, label) {
  const draw = ART[model];
  if (!draw) return '';
  const id = `ga${++uid}`;
  return `<svg class="glasses-art" viewBox="0 0 ${W} ${H}" role="img" aria-label="${label.replace(/"/g, '&quot;')}">${draw(id)}</svg>`;
}
