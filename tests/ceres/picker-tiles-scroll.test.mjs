import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const source = readFileSync(new URL('../../ceres/static/picker-tiles-scroll.js', import.meta.url), 'utf8');
const { missingScrollRoom, pickerTilesTop, tilesScrollGoal } = await import(`data:text/javascript,${encodeURIComponent(source)}`);

test('goal puts the tile top on the header line', () => {
  assert.equal(tilesScrollGoal(1000, 420, 96), 1324);
  assert.equal(tilesScrollGoal(1000, -200, 96), 704);
});

test('already aligned gives the current scroll (nothing to move); never negative; no tiles gives null', () => {
  assert.equal(tilesScrollGoal(1324, 96, 96), 1324);
  assert.equal(tilesScrollGoal(10, -500, 96), 0);
  assert.equal(tilesScrollGoal(10, null, 96), null);
  assert.equal(tilesScrollGoal(10, NaN, 96), null);
});

test('re-open is idempotent: goal from the aligned position equals the goal from the first position', () => {
  const first = tilesScrollGoal(300, 520, 96);
  assert.equal(tilesScrollGoal(first, 96, 96), first);
});

test('missing room is what the scroller lacks below its content', () => {
  assert.equal(missingScrollRoom(500, 800, 1500), 0);
  assert.equal(missingScrollRoom(900, 800, 1500), 200);
  assert.equal(missingScrollRoom(0, 800, 700), 100);
});

test('highest painted tile or Power Core socket wins; hidden ones are ignored', () => {
  const box = (top, height = 50, width = 50) => ({ getBoundingClientRect: () => ({ top, height, width }) });
  const host = { querySelectorAll: (sel) => { assert.match(sel, /\.slot-tile/); assert.match(sel, /\.power-satellite/); return [box(300), box(280), box(100, 0), box(310)]; } };
  assert.equal(pickerTilesTop(host), 280);
  assert.equal(pickerTilesTop({ querySelectorAll: () => [] }), null);
  assert.equal(pickerTilesTop(null), null);
});
