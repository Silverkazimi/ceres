// The Runi confirmation copy survives the verify report that follows it (same transaction).
// operations.js is DOM-bound, so the banner functions are cut out of the source and run with stubs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../ceres/static/operations.js', import.meta.url), 'utf8');
function cut(name) {
  const start = source.search(new RegExp(`^(export )?function ${name}\\(`, 'm'));
  assert.ok(start >= 0, name);
  const end = source.indexOf('\n}\n', start);
  return source.slice(start, end + 3).replace(/^export /, '');
}
const code = ['confirmationPhase', 'confirmationPhaseRank', 'confirmationMessage', 'setConfirmationBanner', 'setConfirmationBannerState'].map(cut).join('\n');
const make = new Function('entryFacts', 'renderConfirmationBanner', `let CERES_CONFIRMATION_BANNER = null;\n${code}\nreturn { setConfirmationBanner, setConfirmationBannerState, banner: () => CERES_CONFIRMATION_BANNER };`);

function setup() { return make(() => ({ where: 'Plot 1', what: '1 move applied' }), () => {}); }
const verify = (api) => api.setConfirmationBannerState('Transaction confirmed', 'Confirmed in game', 'success', { state: 'is-confirmed', phase: 'confirmed', txId: 'tx1' });
const runi = [{ group_id: 'runi', action: 'unstake', txId: 'tx1' }];

test('Runi message stays after the verify report', () => {
  const api = setup();
  api.setConfirmationBanner(runi, 'tx1');
  verify(api);
  assert.match(api.banner().detail, /Runi removed from the Power slot/);
  assert.equal(api.banner().title, 'Runi unstaked');
});

test('Runi message holds when the entries carry no txId but the chunk does', () => {
  const api = setup();
  api.setConfirmationBanner([{ group_id: 'runi', action: 'unstake' }], 'tx1');
  verify(api);
  assert.match(api.banner().detail, /Runi removed/);
});

test('ordinary moves still get the verify report', () => {
  const api = setup();
  api.setConfirmationBanner([{ group_id: 'x', action: 'stake', txId: 'tx1' }], 'tx1');
  verify(api);
  assert.equal(api.banner().detail, 'Confirmed in game');
});
