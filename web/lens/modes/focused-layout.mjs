/** Choose a centered, visibly speaking face and place other captions by direction. */

export function selectFocused(feed, width = 1920) {
  const captions = feed.filter((b) => b.kind !== 'you' && b.kind !== 'you_typed');
  const centered = captions.filter((b) =>
    b.face && !b.face.ghost && (b.face.isSpeaker || b.speaking)
    && Math.abs(b.face.cx - width / 2) <= width * 0.22);
  centered.sort((a, b) =>
    Number(b.face.isSpeaker) - Number(a.face.isSpeaker)
    || Math.abs(a.face.cx - width / 2) - Math.abs(b.face.cx - width / 2)
    || b.tUpdate - a.tUpdate);
  return centered[0]
    ?? captions.find((b) => b.current && b.speaking)
    ?? captions.find((b) => b.speaking)
    ?? captions[0]
    ?? null;
}

export function captionSide(b, width = 1920) {
  const side = b.dir?.side ?? b.side;
  if (side === 'left' || side === 'right') return side;
  if (b.face) return b.face.cx < width / 2 ? 'left' : 'right';
  return 'right';
}

export function focusedSlots(feed, region, width = 1920, you = null) {
  const focus = selectFocused(feed, width);
  const others = feed.filter((b) => b !== focus && b.kind !== 'you' && b.kind !== 'you_typed');
  const left = others.filter((b) => captionSide(b, width) === 'left').sort((a, b) => b.tUpdate - a.tUpdate);
  const right = others.filter((b) => captionSide(b, width) === 'right').sort((a, b) => b.tUpdate - a.tUpdate);
  const gap = 18;
  const sideW = Math.round(region.w * 0.23);
  const centerW = region.w - 2 * sideW - 4 * gap;
  const bottom = region.y + region.h - 26;
  const centerH = you ? 160 : 214;
  const center = focus ? {
    bubble: focus, x: region.x + (region.w - centerW) / 2,
    y: bottom - centerH - (you ? 62 : 0), w: centerW, h: centerH, size: 'main',
  } : null;
  const youSlot = you ? { bubble: you, x: region.x + (region.w - centerW) / 2, y: bottom - 54, w: centerW, h: 54 } : null;
  const sideH = 110;
  const slots = [];
  for (const [side, group] of [['left', left], ['right', right]]) {
    group.slice(0, 2).forEach((bubble, i) => {
      slots.push({
        bubble, side, size: 'secondary',
        x: side === 'left' ? region.x + gap : region.x + region.w - gap - sideW,
        y: bottom - sideH - i * (sideH + 8), w: sideW, h: sideH,
      });
    });
  }
  return { center, sides: slots, you: youSlot };
}
