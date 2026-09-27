import assert from 'node:assert/strict';
import { selectFocused, focusedSlots } from '../../web/lens/modes/focused-layout.mjs';

const region = { x: 295, y: 60, w: 1330, h: 960 };
const main = { key: 'center', kind: 'face', face: { cx: 960, isSpeaker: true }, speaking: true, tUpdate: 4 };
const left = { key: 'left', kind: 'offscreen', side: 'left', speaking: true, tUpdate: 5 };
const right = { key: 'right', kind: 'face', face: { cx: 1450, isSpeaker: false }, speaking: false, tUpdate: 3 };
const feed = [left, right, main];

assert.equal(selectFocused(feed), main, 'centered visible talker takes focus over newer side voice');
const slots = focusedSlots(feed, region);
assert.equal(slots.center.bubble, main);
assert.ok(slots.center.y >= 720 && slots.center.y + slots.center.h <= 1020);
assert.equal(slots.sides.find((s) => s.side === 'left').bubble, left);
assert.equal(slots.sides.find((s) => s.side === 'right').bubble, right);
assert.ok(slots.sides.every((s) => s.w < slots.center.w && s.y >= 700));

const noFace = focusedSlots([left, right], region);
assert.equal(noFace.center.bubble, left, 'off-screen main voice keeps a central caption');
assert.equal(noFace.sides[0].side, 'right');

const moreLeft = { key: 'left2', kind: 'offscreen', side: 'left', speaking: false, tUpdate: 2 };
const stacked = focusedSlots([main, left, moreLeft], region);
assert.equal(stacked.sides.length, 2, 'same-side voices remain visible');
assert.equal(stacked.sides[0].bubble, left);
assert.ok(stacked.sides[0].y > stacked.sides[1].y);
assert.ok(stacked.sides.every((s) => s.y >= 720));

const withYou = focusedSlots(feed, region, 1920, { text: 'Thank you', alpha: 1 });
assert.ok(withYou.center.y >= 720 && withYou.center.y + withYou.center.h < withYou.you.y);
assert.ok(withYou.you.y >= 720 && withYou.you.y + withYou.you.h <= 1020);

console.log('focused layout passed');
