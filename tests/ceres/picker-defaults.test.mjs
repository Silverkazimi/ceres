import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const source = readFileSync(new URL('../../ceres/static/picker-defaults.js', import.meta.url), 'utf8');
const { pickerOnLandAtOpen } = await import(`data:text/javascript,${encodeURIComponent(source)}`);

test('planning opens the existing On land filter on, regardless of the saved Live preference', () => {
  assert.equal(pickerOnLandAtOpen(true, undefined), true);
  assert.equal(pickerOnLandAtOpen(true, false), true);
  assert.equal(pickerOnLandAtOpen(true, true), true);
});

test('Live keeps its On land default and saved preference', () => {
  assert.equal(pickerOnLandAtOpen(false, undefined), false);
  assert.equal(pickerOnLandAtOpen(false, false), false);
  assert.equal(pickerOnLandAtOpen(false, true), true);
});
