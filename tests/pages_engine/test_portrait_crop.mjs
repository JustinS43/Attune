import assert from 'node:assert/strict';
import test from 'node:test';
import {portraitCrop} from '../../web/phone/portrait.js';

test('keeps the entire face and more context than the old tight crop', () => {
  const crop = portraitCrop([540, 210, 180, 180], 1280, 720);
  assert.ok(crop);
  assert.ok(crop.left < 540 && crop.left + crop.width > 720);
  assert.ok(crop.top < 210 && crop.top + crop.height > 390);
  assert.ok(crop.height >= 540);
  assert.equal(crop.width / crop.height, .75);
});

test('moves an edge face crop inside the frame without cutting the face', () => {
  const crop = portraitCrop([15, 10, 100, 120], 1280, 720);
  assert.ok(crop);
  assert.equal(crop.left, 0);
  assert.equal(crop.top, 0);
  assert.ok(crop.left + crop.width >= 115);
  assert.ok(crop.top + crop.height >= 130);
});

test('asks for more distance when the camera cannot fit the full face', () => {
  assert.equal(portraitCrop([300, 40, 620, 650], 1280, 720), null);
  assert.equal(portraitCrop([100, 100, -1, 150], 1280, 720), null);
});
