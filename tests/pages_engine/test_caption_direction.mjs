import assert from 'node:assert/strict';
import test from 'node:test';

globalThis.Path2D = class Path2D {};
const {createStore, createViewBuilder} = await import('../../web/lens/store.js');

function viewFor(speaker, {face = null, side = 'left'} = {}) {
  const store = createStore();
  const build = createViewBuilder(store);
  store.state.clock = 10;
  store.apply({
    type: 'scene',
    faces: face ? [face] : [],
    offscreen: face ? [] : [{person_id: speaker.person_id ?? null, label: speaker.label, side}],
  });
  store.apply({type: 'caption', utt_id: 'u1', speaker, text: 'The meeting starts now.', final: true, lang: 'en'});
  return build(1 / 30, 10, 'film');
}

test('off-frame speech stays in the bottom caption with its direction', () => {
  for (const side of ['left', 'right', 'behind']) {
    const view = viewFor({kind: 'offscreen', person_id: 'p1', label: 'Alex', side}, {side});
    assert.equal(view.lower?.text, 'The meeting starts now.');
    assert.equal(view.lower?.side, side);
    assert.equal(view.bubbles.length, 0);
  }
});

test('speech from a visible person stays attached to their face', () => {
  const face = {track_id: 7, box: [400, 160, 160, 200], label: 'Alex', status: 'named', person_id: 'p1', is_speaker: true};
  const view = viewFor({kind: 'face', track_id: 7, person_id: 'p1', label: 'Alex'}, {face});
  assert.equal(view.lower, null);
  assert.equal(view.bubbles.length, 1);
  assert.equal(view.bubbles[0].face.track_id, 7);
});

test('a continuing caption moves to the bottom after its face leaves', () => {
  const store = createStore();
  const build = createViewBuilder(store);
  store.state.clock = 10;
  store.apply({type: 'scene', faces: [{track_id: 7, box: [900, 160, 160, 200], label: 'Alex', status: 'named', person_id: 'p1'}], offscreen: []});
  store.apply({type: 'caption', utt_id: 'u1', speaker: {kind: 'face', track_id: 7, person_id: 'p1', label: 'Alex'}, text: 'I am leaving the frame.', final: true, lang: 'en'});
  assert.equal(build(1 / 30, 10, 'film').bubbles.length, 1);
  store.state.clock = 12;
  store.apply({type: 'scene', faces: [], offscreen: []});
  const view = build(1 / 30, 12, 'film');
  assert.equal(view.bubbles.length, 0);
  assert.equal(view.lower?.text, 'I am leaving the frame.');
  assert.equal(view.lower?.side, 'right');
});

test('an unknown direction does not invent an arrow', () => {
  const view = viewFor({kind: 'someone', label: 'Someone', side: 'none'}, {side: 'none'});
  assert.equal(view.lower?.side, 'none');
  assert.equal(view.bubbles.length, 0);
});
