/*
 * Glasses guide: the demo's fourth view. The three looks the lens view can draw (Colour, Mono,
 * Corner), where the text sits in each, and the real glasses that work that way.
 *
 * Section 4 - Pages, Engine & Demo. TODO: P-18.
 *
 * Display regions and device facts come from docs/glasses-realism.md and the makers' own pages
 * (linked on each card). A fact we could not confirm is left out rather than guessed.
 */


// ------------------------------------------------------------------ the three looks
// Regions are in the lens view's 1920x1080 frame (about 75° wide); see docs/glasses-realism.md.
const STYLES = [
  {
    id: 'color',
    name: 'Colour',
    title: 'Full-colour AR · binocular',
    klass: 'Meta Orion class',
    sees: 'Full-colour caption bubbles float next to each speaker, in both eyes, across most of your view. They stay with the person as you move.',
    chips: ['Both eyes', 'Full colour', 'Follows faces', '≈70°'],
    region: 'Display ≈ 1330 × 960 of the 1920 × 1080 view, centred',
    devices: ['orion', 'specs', 'xreal'],
  },
  {
    id: 'mono',
    name: 'Mono',
    title: 'Mono green waveguide',
    klass: 'Even Realities G1 class',
    sees: 'A thin band of green text at or just above eye level: the speaker’s name and four caption lines. The band moves with your head.',
    chips: ['Both eyes', 'Green only', 'Moves with head', '≈25°'],
    region: 'Display ≈ 530 × 165, centred, about 5° above eye level',
    devices: ['g1', 'g2', 'halliday', 'transcribe', 'z100'],
  },
  {
    id: 'corner',
    name: 'Corner',
    title: 'Monocular display · one eye',
    klass: 'Meta Ray-Ban Display class',
    sees: 'A small, translucent colour square just below and right of centre, in your right eye only. Two lines of captions sit at its bottom.',
    chips: ['Right eye only', 'Full colour', 'Moves with head', '≈20°'],
    region: 'Display ≈ 307 × 307 at x 1028–1335, y 452–759',
    devices: ['rayban', 'glass', 'frame'],
  },
];

// ------------------------------------------------------------------ the glasses
// status.kind: shipping | preorder | prototype | discontinued | replaced
const DEVICES = {
  orion: {
    name: 'Orion',
    maker: 'Meta',
    status: { kind: 'prototype', text: 'Prototype · not sold' },
    facts: [
      ['Display', 'Full colour, silicon-carbide lenses'],
      ['Field of view', '≈ 70°'],
      ['Eyes', 'Both'],
      ['Text sits', 'Anywhere in a wide view: next to each speaker'],
    ],
    note: 'Runs with a wireless compute puck and an EMG wristband.',
    model: 'Attune’s Colour look is modelled on this',
    url: 'https://www.meta.com/emerging-tech/orion/',
    alt: 'Meta Orion: thick black frame with cameras at the outer corners; a full-colour glow fills both lenses.',
  },
  specs: {
    name: 'Specs',
    maker: 'Snap',
    status: { kind: 'preorder', text: 'Pre-order' },
    facts: [
      ['Display', 'Full colour (16 million colours), see-through waveguide'],
      ['Field of view', '51°'],
      ['Eyes', 'Both'],
    ],
    note: 'The consumer successor to the 2024 Spectacles developer kit.',
    url: 'https://www.specs.com/',
    alt: 'Snap Specs: chunky dark frame with large cameras at both outer corners; a colour glow in both lenses.',
  },
  xreal: {
    name: 'One Pro',
    maker: 'XREAL',
    status: { kind: 'shipping', text: 'Shipping' },
    facts: [
      ['Display', 'Full colour, 0.55″ Sony micro-OLED, X-Prism optics'],
      ['Field of view', '57°'],
      ['Eyes', 'Both'],
      ['Text sits', 'On a virtual screen: head-locked (Follow mode) or pinned in space'],
    ],
    note: 'Tethered over USB-C to a phone, PC or console. A screen in front of you, not labels on the world.',
    url: 'https://www.xreal.com/us/one-pro',
    alt: 'XREAL One Pro: black sunglasses-style frame with dark lenses, a virtual screen in both lenses and a USB-C cable.',
  },
  g1: {
    name: 'G1',
    maker: 'Even Realities',
    status: { kind: 'shipping', text: 'Shipping' },
    facts: [
      ['Display', 'Green micro-LED, 640 × 200, 1000 nits'],
      ['Field of view', '25°'],
      ['Eyes', 'Both'],
      ['Text sits', 'A band at or just above eye level; height adjustable'],
    ],
    note: 'Panto and rectangular frames in magnesium. No camera.',
    model: 'Attune’s Mono look is modelled on this',
    url: 'https://www.evenrealities.com/g1',
    alt: 'Even Realities G1: thin round silver frame; a green text band glows in both lenses.',
  },
  g2: {
    name: 'G2',
    maker: 'Even Realities',
    status: { kind: 'shipping', text: 'Shipping' },
    facts: [
      ['Display', 'Green waveguide, 640 × 350, 1200 nits'],
      ['Field of view', '27.5°'],
      ['Eyes', 'Both'],
    ],
    note: 'Sold as subtitle glasses: live captions in front of your eyes.',
    url: 'https://www.evenrealities.com/subtitle-glasses',
    alt: 'Even Realities G2: slim dark rectangular frame; a green text band glows in both lenses.',
  },
  halliday: {
    name: 'Halliday G2',
    maker: 'Halliday',
    status: { kind: 'shipping', text: 'On sale' },
    facts: [
      ['Display', 'Green, dual microLED engines, 600 × 300 per eye'],
      ['Field of view', '25.2°'],
      ['Eyes', 'Both'],
    ],
    url: 'https://hallidayglobal.com/products/halliday-glasses',
    alt: 'Halliday G2: ordinary-looking black frame; a green text band glows in both lenses.',
  },
  transcribe: {
    name: 'TranscribeGlass',
    maker: 'TranscribeGlass',
    status: { kind: 'shipping', text: 'On sale' },
    facts: [
      ['Display', 'Green, 640 × 480, 2000 nits'],
      ['Field of view', '30°'],
      ['Eyes', 'Right eye only'],
      ['Fits', 'Clips onto your own glasses (38 g)'],
    ],
    oneEye: true,
    note: 'Made for captions. Green like Mono, but in one eye only.',
    url: 'https://www.transcribeglass.com/',
    alt: 'TranscribeGlass: a small white clip-on module on the right temple of a brown frame; green text glows in the right lens only.',
  },
  z100: {
    name: 'Z100',
    maker: 'Vuzix',
    status: { kind: 'shipping', text: 'Released Nov 2024' },
    facts: [
      ['Display', 'Monochrome waveguide'],
      ['Eyes', 'One eye (monocular)'],
      ['Weight', '38 g'],
    ],
    oneEye: true,
    note: 'Monochrome like Mono, but in one eye only. Vuzix lists closed captioning as a use.',
    url: 'https://ir.vuzix.com/news-events/press-releases/detail/2104/vuzix-announces-general-availability-of-z100-smart-glasses',
    linkText: 'Vuzix announcement',
    alt: 'Vuzix Z100: thin ordinary black frame; green text glows in one lens only.',
  },
  rayban: {
    name: 'Ray-Ban Display',
    maker: 'Meta',
    status: { kind: 'shipping', text: 'Shipping' },
    facts: [
      ['Display', 'Full colour, 600 × 600, translucent'],
      ['Field of view', '≈ 20°'],
      ['Eyes', 'Right eye only'],
      ['Text sits', 'Slightly below and right of centre'],
    ],
    note: 'Black or Sand frames; controlled with the Meta Neural Band.',
    model: 'Attune’s Corner look is modelled on this',
    url: 'https://www.meta.com/ai-glasses/meta-ray-ban-display-glasses-and-neural-band/',
    alt: 'Meta Ray-Ban Display: thick black Wayfarer-style frame with a corner camera; a small colour square glows low in the right lens.',
  },
  glass: {
    name: 'Glass Enterprise Edition 2',
    maker: 'Google',
    status: { kind: 'discontinued', text: 'Discontinued 2023' },
    facts: [
      ['Eyes', 'Right eye only'],
      ['Text sits', 'Just above your right eye, not in front of it'],
    ],
    note: 'Press G in the Glasses view for Attune’s Glass placement.',
    variant: 'glass',
    url: 'https://support.google.com/glass-enterprise/customer/answer/9220199',
    linkText: 'Google support page',
    alt: 'Google Glass Enterprise Edition 2: a thin titanium band with the Glass pod on the right side and a small glowing prism above the right eye.',
  },
  frame: {
    name: 'Frame',
    maker: 'Brilliant Labs',
    status: { kind: 'replaced', text: 'Succeeded by Halo' },
    facts: [
      ['Display', 'Colour micro-OLED, 640 × 400, up to 16 colours at once'],
      ['Field of view', '20°'],
      ['Eyes', 'One eye'],
    ],
    note: 'A prism in the lens floats a small transparent screen in view.',
    url: 'https://docs.brilliant.xyz/frame/hardware/',
    linkText: 'Brilliant Labs docs',
    alt: 'Brilliant Labs Frame: round black frame with a camera in the bridge; a small colour screen glows in one lens.',
  },
};

// ------------------------------------------------------------------ "where the text appears"
// A 16:9 mini view in the frame's own 1920x1080 coordinates, with the display region at its true
// position and size, drawn in that style's look.
function scene(id) {
  return `
    <defs>
      <linearGradient id="${id}-sky" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#1b2433"/><stop offset="0.62" stop-color="#121822"/><stop offset="1" stop-color="#0c1017"/>
      </linearGradient>
      <filter id="${id}-soft" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="6"/></filter>
    </defs>
    <rect width="1920" height="1080" fill="url(#${id}-sky)"/>
    <path d="M0,720 L1920,700 L1920,1080 L0,1080 Z" fill="#0f141c"/>
    <rect x="1480" y="170" width="300" height="360" rx="10" fill="#1d2735" opacity="0.8"/>
    <g fill="#2c3646">
      <circle cx="700" cy="450" r="78"/>
      <path d="M540,760 Q548,560 700,548 Q852,560 860,760 Z"/>
      <circle cx="1250" cy="420" r="72"/>
      <path d="M1100,740 Q1110,535 1250,522 Q1392,535 1400,740 Z"/>
    </g>
    <line x1="0" y1="540" x2="1920" y2="540" stroke="#ffffff" stroke-opacity="0.2" stroke-width="3" stroke-dasharray="14 16"/>
    <path d="M945,540 h30 M960,525 v30" stroke="#ffffff" stroke-opacity="0.35" stroke-width="4"/>`;
}

function dim(id, x, y, w, h) {
  return `<path d="M0,0 H1920 V1080 H0 Z M${x},${y} V${y + h} H${x + w} V${y} Z" fill="#05070a" fill-opacity="0.55" fill-rule="evenodd"/>`;
}

function bars(x, y, widths, gap, h, fill, op = 1) {
  return widths.map((w, i) => `<rect x="${x}" y="${y + i * gap}" width="${w}" height="${h}" rx="${h / 2}" fill="${fill}" opacity="${i === 0 ? op : op * 0.9}"/>`).join('');
}

const DIAGRAMS = {
  color: (id) => `${scene(id)}
    ${dim(id, 295, 60, 1330, 960)}
    <rect x="295" y="60" width="1330" height="960" rx="18" fill="#7cf5d6" fill-opacity="0.04" stroke="#7cf5d6" stroke-opacity="0.8" stroke-width="5" stroke-dasharray="18 14"/>
    <rect x="335" y="96" width="250" height="52" rx="26" fill="#0e1016" fill-opacity="0.6" stroke="#ffffff" stroke-opacity="0.3" stroke-width="3"/>
    <circle cx="368" cy="122" r="11" fill="#7cf5d6"/>
    <rect x="392" y="114" width="160" height="16" rx="8" fill="#ffffff" opacity="0.7"/>
    <g>
      <path d="M640,338 L680,378 L700,338 Z" fill="#141a24" fill-opacity="0.85" stroke="#7cf5d6" stroke-width="4"/>
      <rect x="430" y="210" width="470" height="132" rx="30" fill="#141a24" fill-opacity="0.85" stroke="#7cf5d6" stroke-width="5"/>
      <circle cx="470" cy="245" r="12" fill="#7cf5d6"/>
      <rect x="494" y="236" width="120" height="18" rx="9" fill="#7cf5d6"/>
      ${bars(462, 274, [400, 300], 34, 20, '#ffffff', 0.95)}
    </g>
    <g>
      <path d="M1220,318 L1250,352 L1275,318 Z" fill="#141a24" fill-opacity="0.85" stroke="#6ab8ff" stroke-width="4"/>
      <rect x="1060" y="186" width="430" height="136" rx="30" fill="#141a24" fill-opacity="0.85" stroke="#6ab8ff" stroke-width="5"/>
      <circle cx="1100" cy="222" r="12" fill="#6ab8ff"/>
      <rect x="1124" y="213" width="100" height="18" rx="9" fill="#6ab8ff"/>
      ${bars(1092, 252, [360, 250], 34, 20, '#ffffff', 0.95)}
    </g>`,
  mono: (id) => `${scene(id)}
    ${dim(id, 695, 347, 530, 165)}
    <rect x="695" y="347" width="530" height="165" rx="10" fill="none" stroke="#3dff66" stroke-opacity="0.55" stroke-width="4" stroke-dasharray="14 12"/>
    <g filter="url(#${id}-soft)" opacity="0.9">
      ${bars(722, 364, [150], 0, 20, '#28ff46')}
      ${bars(722, 398, [470, 430, 452, 300], 27, 13, '#28ff46')}
    </g>
    ${bars(722, 364, [150], 0, 20, '#8dff9f')}
    ${bars(722, 398, [470, 430, 452, 300], 27, 13, '#8dff9f')}
    <path d="M708,420 l-9,10 l9,10 M1212,420 l9,10 l-9,10" fill="none" stroke="#8dff9f" stroke-width="4" opacity="0.8"/>`,
  corner: (id) => `${scene(id)}
    ${dim(id, 1028, 452, 307, 307)}
    <rect x="1028" y="452" width="307" height="307" rx="8" fill="#ffffff" fill-opacity="0.06" stroke="#ffffff" stroke-opacity="0.75" stroke-width="4" stroke-dasharray="12 10"/>
    <rect x="1048" y="636" width="118" height="12" rx="6" fill="#ffffff" opacity="0.55"/>
    <circle cx="1056" cy="672" r="8" fill="#7cf5d6"/>
    <rect x="1072" y="665" width="90" height="14" rx="7" fill="#7cf5d6"/>
    <path d="M1296,662 l14,10 l-14,10" fill="none" stroke="#ffffff" stroke-width="4" opacity="0.8"/>
    ${bars(1048, 698, [266, 190], 28, 16, '#ffffff', 0.95)}`,
};

function diagramSvg(style) {
  const id = `dg-${style.id}`;
  return `<svg class="where-svg" viewBox="0 0 1920 1080" role="img" aria-labelledby="${id}-t">
    <title id="${id}-t">Where the text appears in ${style.name}: ${style.region.replace('Display ≈', 'a display of about')}.</title>
    ${DIAGRAMS[style.id](id)}
  </svg>`;
}

// ------------------------------------------------------------------ markup
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

const ICON_EXT = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3H3.5A1.5 1.5 0 0 0 2 4.5v8A1.5 1.5 0 0 0 3.5 14h8a1.5 1.5 0 0 0 1.5-1.5V10M9 2h5v5M14 2 7.5 8.5"/></svg>';
const ICON_PLAY = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 3.2v9.6L12.8 8z"/></svg>';
const ICON_STAR = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.8l1.8 3.9 4.2.4-3.2 2.8 1 4.2L8 10.9l-3.8 2.2 1-4.2L2 6.1l4.2-.4z"/></svg>';

function card(key, style) {
  const d = DEVICES[key];
  const full = `${d.maker} ${d.name}`.replace(/^TranscribeGlass TranscribeGlass$/, 'TranscribeGlass');
  const title = d.maker === d.name ? d.name : `${d.name}`;
  const facts = d.facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('');
  return `<li class="gcard${d.model ? ' is-model' : ''}${d.oneEye ? ' one-eye' : ''}">
    <article aria-labelledby="gc-${key}">
      <div class="gbody">
        <div class="ghead">
          <div>
            ${d.maker !== d.name ? `<p class="gmaker">${esc(d.maker)}</p>` : ''}
            <h4 id="gc-${key}">${esc(title)}${d.oneEye ? '<span class="gone">ONE EYE</span>' : ''}</h4>
          </div>
          <span class="gstatus s-${d.status.kind}">${esc(d.status.text)}</span>
        </div>
        ${d.model ? `<p class="gmodel">${ICON_STAR}<span>${esc(d.model)}</span></p>` : ''}
        <dl class="gfacts">${facts}</dl>
        ${d.note ? `<p class="gnote">${esc(d.note)}</p>` : ''}
        <div class="gactions">
          <a class="glink" href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">${esc(d.linkText || 'Official page')}${ICON_EXT}<span class="sr-only"> for ${esc(full)} (opens in a new tab)</span></a>
          ${d.variant ? `<button type="button" class="gtry-mini" data-try="${style.id}" data-variant="${d.variant}">${ICON_PLAY}Try this placement</button>` : ''}
        </div>
      </div>
    </article>
  </li>`;
}

function column(style, i) {
  return `<section class="gcol gcol-${style.id}" aria-labelledby="gh-${style.id}" style="--i:${i}">
    <header class="gcol-head">
      <p class="geyebrow"><span class="gswatch" aria-hidden="true"></span>Look ${i + 1} · ${esc(style.klass)}</p>
      <h2 id="gh-${style.id}">${esc(style.name)}</h2>
      <p class="gtitle">${esc(style.title)}</p>
      <p class="gsees">${esc(style.sees)}</p>
      <figure class="where">
        ${diagramSvg(style)}
        <figcaption><span class="where-k">Where the text appears</span><span class="where-v">${esc(style.region)}</span></figcaption>
      </figure>
      <ul class="gchips" aria-label="At a glance">${style.chips.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>
      <button type="button" class="gtry" data-try="${style.id}" data-variant="rayban">
        ${ICON_PLAY}<span>Try this look</span><span class="sr-only"> (${esc(style.name)} in the Glasses view)</span>
      </button>
    </header>
    <h3 class="glist-h">Glasses that work this way</h3>
    <ul class="glist">${style.devices.map((k) => card(k, style)).join('')}</ul>
  </section>`;
}

/**
 * Draw the guide into `root`. `onTry(modeId, variant)` is called by the "Try this look" buttons.
 */
export function mountGuide(root, { onTry }) {
  root.innerHTML = `<div class="guide-inner">
    <header class="guide-hero">
      <p class="attune-eyebrow">Glasses guide</p>
      <h1 id="guide-title">Three ways captions reach your eyes</h1>
      <p class="guide-lede">Attune draws the same live captions in three display styles, one for each kind of real
        caption-capable glasses. Each mini view below is the wearer’s view (about 75° wide) with the display drawn
        at its true size and position.</p>
      <ul class="guide-legend" aria-label="How to read the mini views">
        <li><span class="lg lg-eye" aria-hidden="true"></span>eye level</li>
        <li><span class="lg lg-region" aria-hidden="true"></span>display area</li>
        <li><span class="lg lg-dim" aria-hidden="true"></span>no display: the plain world</li>
      </ul>
    </header>
    <div class="guide-cols">${STYLES.map(column).join('')}</div>
    <footer class="guide-foot">
      <p>Drawings are our own simplified illustrations, not product photos. Front views: the wearer’s right lens is on the left.
        Specs are from each maker’s page and <code>docs/glasses-realism.md</code>; anything we couldn’t confirm is left out.</p>
    </footer>
  </div>`;

  root.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-try]');
    if (!b) return;
    onTry(b.dataset.try, b.dataset.variant || 'rayban');
  });
}
