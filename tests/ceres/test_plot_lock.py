from pathlib import Path

from tests.ceres.vm_context import run_in_module_context


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
PLOT_LOCK = STATIC / "plot-lock.js"

VM_SETUP = r"""
context.crypto = require('node:crypto').webcrypto;
context.TextEncoder = TextEncoder;
context.AbortController = AbortController;
context.navigator.locks = { request(_name, _options, callback) { return callback(); } };
document.getElementById = id => id === 'board-version' ? { textContent: 'v0.9.835' } : null;
window.hive_keychain = {
  requestSignTx() {},
  requestBroadcast(_account, _operations, _key, callback) {
    callback({ success: true, result: { id: 'mock-tx' }, data: { key: 'Active' } });
  },
};
"""


def run(body: str, entry: Path = PLOT_LOCK) -> None:
    run_in_module_context(
        r"""
queueRpc = async method => {
  if (method.endsWith('get_dynamic_global_properties')) return {
    head_block_number: 100,
    head_block_id: 'd6908f068242802d3791ff650ddcaefbb54782a5',
    time: new Date().toISOString().slice(0, 19),
  };
  if (method.endsWith('broadcast_transaction')) return null;
  if (method.endsWith('get_transaction')) return { block_num: 100 };
  throw Error('unexpected RPC ' + method);
};
""" + body,
        entry=entry,
        extra_context_js=VM_SETUP,
    )


def test_board_lock_cell_and_plot_view_button_markup() -> None:
    run(
        r"""
const row = { deed_uid: 'I-1-aaaaaaaa', plot: '1-1-1', lock_days: 3, unlock_date: '2099-01-02T03:04:05Z' };
const locked = plotLockIconHtml(row);
if (!locked.includes('aria-label="Locked · unlocks ' + new Date(row.unlock_date).toLocaleString())) throw Error('locked label or unlock date missing');
if (!locked.includes('M8 10V7a4 4 0 0 1 8 0v3')) throw Error('locked row did not render a closed-lock icon');
const unlocked = plotLockIconHtml({ ...row, lock_days: null, unlock_date: null });
if (!unlocked.includes('aria-label="Unlocked"')) throw Error('unlocked label missing');
// the maintainer's pair B () -- the open shackle swings clear of the body.
if (!unlocked.includes('M8 10V6.8a4 4 0 0 0-7.1-2.5L.3 5')) throw Error('unlocked row did not render an open-lock icon');
process.exit(0);
""",
    )
    index = (STATIC / "index.html").read_text()
    rows = (STATIC / "rows.js").read_text()
    editor = (STATIC / "editor.js").read_text()
    css = (STATIC / "ceres.css").read_text()
    board = (ROOT / "ceres" / "board.py").read_text()
    sortable = board.split("SORTABLE = [", 1)[1].split("]", 1)[0]
    assert '"lock_state"' in sortable
    assert 'if (!BOARD?.sortable?.includes(key) && key !== \'plot\') return;' in (STATIC / "app.js").read_text()
    assert "th.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');" in (STATIC / "table.js").read_text()
    assert 'table.plot-table thead th[data-key="lock_state"] .plot-lock-icon { display: inline-flex; vertical-align: middle; line-height: 0; }' in css
    assert 'table.plot-table thead th[data-key="lock_state"] .plot-lock-icon svg { inline-size: 1.1rem; block-size: 1.1rem; display: block; stroke-width: 2.6; }' in css
    assert 'table.plot-table tbody tr.plot-row td { block-size: 2.25rem;' in css
    table_width_rule = css.split('html:not(.ceres-lw-le-1279) #plot-table-scroll > table.plot-table {', 1)[1].split('}', 1)[0]
    assert 'inline-size: calc(100% + 8.5px);' in table_width_rule
    headers = index.split('<thead class="table-head-sticky', 1)[1].split('</thead>', 1)[0]
    assert headers.index('data-key="dec_needed"') < headers.index('data-key="lock_state"')
    assert headers.index('data-key="lock_state"') == headers.rindex('data-key=')
    assert rows.index('<td class="col-num"${decStake.tip}>') < rows.index('<td class="lock-cell col-w-lock">')
    lock_markup = editor.split("const splinterAffinityHtml = group.id === 'worker'", 1)[1].split("return `<section class=", 1)[0]
    affinity = lock_markup.index('class="editor-splinter-affinity"')
    first_sep = lock_markup.index('class="editor-splinter-affinity-sep"')
    lock_button = lock_markup.index('class="editor-plot-lock"')
    second_sep = lock_markup.index('class="editor-splinter-affinity-sep"', first_sep + 1)
    assert affinity < first_sep < lock_button < second_sep
    assert lock_markup.count('class="editor-splinter-affinity-sep"') == 2
    assert '${splinterAffinityHtml}${buttons}' in editor
    assert 'plotLockIconHtml(row, { button: true })' in lock_markup
    assert '.plot-lock-button {' in css and 'inline-size: var(--ceres-control-min-block' in css


def test_staging_toggle_review_envelopes_and_active_allowlist() -> None:
    run(
        r"""
(async () => {
const expectedApp = 'ceres/' + document.getElementById('board-version').textContent.slice(1);
const row = { deed_uid: 'I-1-aaaaaaaa', plot: '1-1-1', lock_days: null, unlock_date: null };
reconcilePlotLockPlans({ player: 'alice', plots: [row] });
if (!togglePlotLock(row)) throw Error('first click did not stage');
const staged = STAGED_PLOT_LOCKS.get(row.deed_uid);
if (!staged || staged.action !== 'lock' || staged.lockDays !== 3) throw Error('staged lock plan is wrong');
if (!plotLockIconHtml(row, { button: true }).includes('is-staged')) throw Error('staged lock button lacks staged pulse class');
if (!togglePlotLock(row) || STAGED_PLOT_LOCKS.has(row.deed_uid)) throw Error('second click did not unstage');

togglePlotLock(row);
const lockReview = await preparePlotLockReview(row);
const lockOperation = lockReview.operations[0];
const lockPayload = JSON.parse(lockOperation[1].json);
if (lockReview.lockDays !== 3 || lockPayload.lock_days !== 3) throw Error('lock period is not exactly three days');
if (JSON.stringify(Object.keys(lockPayload)) !== JSON.stringify(['type', 'lock_days', 'asset_ids', 'app', 'n'])) throw Error('lock payload key order is wrong');
if (lockOperation[1].json !== JSON.stringify({ type: 'item', lock_days: 3, asset_ids: [row.deed_uid], app: expectedApp, n: lockPayload.n })
  || JSON.stringify(Object.keys(lockOperation[1]).sort()) !== JSON.stringify(['id', 'json', 'required_auths', 'required_posting_auths'])) throw Error('lock envelope fields differ from the built operation');
if (lockOperation[0] !== 'custom_json' || lockOperation[1].id !== 'sm_lock_assets'
  || JSON.stringify(lockPayload.asset_ids) !== JSON.stringify([row.deed_uid]) || lockPayload.type !== 'item'
  || !lockPayload.n || JSON.stringify(lockOperation[1].required_auths) !== '["alice"]'
  || JSON.stringify(lockOperation[1].required_posting_auths) !== '[]' || lockReview.prepared.authority !== 'Active') throw Error('lock envelope differs from the reviewed Active operation');
clearPlotLockPlans();
const unlockingRow = { ...row, lock_days: 3, unlock_date: '2099-01-02T03:04:05Z' };
reconcilePlotLockPlans({ player: 'alice', plots: [unlockingRow] });
if (togglePlotLock(unlockingRow)) throw Error('a deed already unlocking must not stage another unlock');
const unlockRow = { ...row, lock_days: 3, unlock_date: null };
reconcilePlotLockPlans({ player: 'alice', plots: [unlockRow] });
if (!togglePlotLock(unlockRow)) throw Error('unlock plan did not stage');
const unlockReview = await preparePlotLockReview(unlockRow);
const unlockOperation = unlockReview.operations[0];
const unlockPayload = JSON.parse(unlockOperation[1].json);
if (JSON.stringify(Object.keys(unlockPayload)) !== JSON.stringify(['type', 'asset_ids', 'app', 'n'])) throw Error('unlock payload key order is wrong');
if (unlockOperation[1].json !== JSON.stringify({ type: 'item', asset_ids: [row.deed_uid], app: expectedApp, n: unlockPayload.n })
  || JSON.stringify(Object.keys(unlockOperation[1]).sort()) !== JSON.stringify(['id', 'json', 'required_auths', 'required_posting_auths'])) throw Error('unlock envelope fields differ from the built operation');
if (unlockOperation[1].id !== 'sm_unlock_assets' || JSON.stringify(unlockPayload.asset_ids) !== JSON.stringify([row.deed_uid])
  || unlockPayload.type !== 'item' || !unlockPayload.n || JSON.stringify(unlockOperation[1].required_auths) !== '["alice"]'
  || JSON.stringify(unlockOperation[1].required_posting_auths) !== '[]' || unlockReview.prepared.authority !== 'Active') throw Error('unlock envelope differs from the reviewed Active operation');

const malformed = ['custom_json', { id: 'sm_other', required_auths: ['alice'], required_posting_auths: [], json: JSON.stringify({ n: crypto.randomUUID() }) }];
const lockWitness = { id: lockOperation[1].id, json: lockOperation[1].json };
for (const [id, json, witness] of [
  ['sm_other', lockOperation[1].json, lockWitness],
  [lockOperation[1].id, JSON.stringify({ ...lockPayload, lock_days: 4 }), lockWitness],
  [lockOperation[1].id, JSON.stringify(Object.fromEntries(Object.entries(lockPayload).filter(([key]) => key !== 'app'))), null],
  [lockOperation[1].id, JSON.stringify({ ...lockPayload, app: 'ceres/9.9.9' }), null],
]) {
  const activeWitness = witness || { id, json };
  const op = ['custom_json', { ...malformed[1], id, json }];
  await prepareSigningBatch('alice', [[op]], 'Active', { reviewedActiveLock: activeWitness }).then(
    () => { throw Error('transaction queue accepted an unreviewed Active payload'); },
    () => {},
  );
  await BroadcastGate.broadcast('alice', [op], null, 'Active', { reviewedActiveLock: activeWitness }).then(
    () => { throw Error('broadcast gate accepted an unreviewed Active payload'); },
    () => {},
  );
}
const sps = ['custom_json', { id: 'sm_sps', required_auths: ['alice'], required_posting_auths: [],
  json: JSON.stringify({ action: 'token_transfer', params: { token: 'SPS', memo: 'lpsid:123', qty: 1, to: 'alice', n: crypto.randomUUID() } }) }];
const spsPrepared = await prepareSigningBatch('alice', [[sps]], 'Active');
if (!spsPrepared?.length) throw Error('transaction queue no longer allows the SPS Active exception');
await BroadcastGate.broadcast('alice', [sps], null, 'Active');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=PLOT_LOCK,
    )


def test_three_day_review_and_hiveauth_refusal_are_visible_without_inputs() -> None:
    checkout = (STATIC / "checkout.js").read_text()
    assert '<span>Unlock delay: 3 days</span>' in checkout
    assert 'Locking needs Hive Keychain (Active key).' in checkout
    assert 'Unlocking needs Hive Keychain (Active key).' in checkout
    assert 'Deed lock changes need Hive Keychain (Active key).' in checkout
    signing = checkout[checkout.index('const lockSigningHtml'):checkout.index('\n  return `', checkout.index('const lockSigningHtml'))]
    assert (
        "'Keychain shows the exact operation before approval.' : lockGuidance" in signing
        and '<h3 class="hm-sec-h">Signing</h3>' in signing
        and '${lockSigningHtml}' in signing
    )
    # the separate review/row-sign buttons were removed (); their disable checks went with them.
    review = checkout[checkout.index('function plotLockPlanHtml'):checkout.index('function statusHtml', checkout.index('function plotLockPlanHtml'))]
    assert '<input' not in review
    assert "lockDays: 3" in PLOT_LOCK.read_text()
    assert 'review.prepared.transaction.operations' in PLOT_LOCK.read_text()


def test_lock_fields_flow_from_deeds_bundle_without_new_lock_request() -> None:
    analysis = (ROOT / "praetoria" / "land_analysis.py").read_text()
    board = (ROOT / "ceres" / "board.py").read_text()
    assert '"lock_days": deed.get("lock_days")' in analysis
    assert '"unlock_date": deed.get("unlock_date")' in analysis
    assert '"lock_days": facts.get("lock_days")' in board
    assert '"unlock_date": facts.get("unlock_date")' in board
    assert "land_client.bundle(player)" in analysis
    assert "sm_lock_assets" not in analysis and "sm_unlock_assets" not in analysis
    assert "sm_lock_assets" not in board and "sm_unlock_assets" not in board
