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
  assert.equal(build(1 / 30, 12, 'film').bubbles.length, 1); // lost for 2 s: held in place (3 s)
  store.state.clock = 13; // still talking as they go
  store.apply({type: 'caption', utt_id: 'u1', speaker: {kind: 'face', track_id: 7, person_id: 'p1', label: 'Alex'}, text: 'I am leaving the frame.', final: true, lang: 'en'});
  store.state.clock = 13.5;
  store.apply({type: 'scene', faces: [], offscreen: []});
  const view = build(1 / 30, 13.5, 'film');
  assert.equal(view.bubbles.length, 0);
  assert.equal(view.lower?.text, 'I am leaving the frame.');
  assert.equal(view.lower?.side, 'right');
});

test('a face flickering back does not pull its caption up for 3 s', () => {
  const store = createStore();
  const build = createViewBuilder(store);
  const alex = {track_id: 7, box: [900, 160, 160, 200], label: 'Alex', status: 'named', person_id: 'p1'};
  const say = (t, text) => {
    store.state.clock = t;
    store.apply({type: 'caption', utt_id: 'u1', speaker: {kind: 'face', track_id: 7, person_id: 'p1', label: 'Alex'}, text, final: false, lang: 'en'});
  };
  const at = (t, faces) => {
    store.state.clock = t;
    store.apply({type: 'scene', faces, offscreen: []});
    return build(1 / 30, t, 'film');
  };
  at(10, [alex]);
  say(10, 'I am turning');
  assert.equal(at(10.1, [alex]).bubbles.length, 1);
  at(12, []);
  say(12.9, 'I am turning, still talking');
  at(13.5, []); // gone past the hold: the caption is now at the bottom
  say(13.6, 'I am turning away');
  const back = at(14, [alex]); // the face flickers back half a second later
  assert.equal(back.bubbles.length, 0);
  assert.equal(back.lower?.text, 'I am turning away');
  say(16.8, 'I am turning away and back');
  const settled = at(17, [alex]); // 3.5 s at the bottom and the face is really there: back up
  assert.equal(settled.bubbles.length, 1);
  assert.equal(settled.lower, null);
});

test('the off-frame caption stays about 3 s after its words, then eases out', () => {
  const store = createStore();
  const build = createViewBuilder(store);
  const at = (t) => {
    store.state.clock = t;
    store.apply({type: 'scene', faces: [], offscreen: [{person_id: null, label: 'Someone', side: 'left'}]});
    return build(1 / 30, t, 'film');
  };
  at(10);
  store.apply({type: 'caption', utt_id: 'u1', speaker: {kind: 'offscreen', label: 'Someone', side: 'left'}, text: 'Over here.', final: true, lang: 'en'});
  assert.equal(at(10.1).lower?.alpha, 1);
  assert.equal(at(12.9).lower?.alpha, 1);
  const leaving = at(13.45).lower?.alpha;
  assert.ok(leaving > 0 && leaving < 1, `easing out, got ${leaving}`);
  assert.equal(at(14.2).lower, null);
});

test('an unknown direction does not invent an arrow', () => {
  const view = viewFor({kind: 'someone', label: 'Someone', side: 'none'}, {side: 'none'});
  assert.equal(view.lower?.side, 'none');
  assert.equal(view.bubbles.length, 0);
});
