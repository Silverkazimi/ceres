"""Guards for the routing dead ends reproduced on player0005 on 2026-09-28.

1. A wallet that refuses before signing (Keychain Cancel, 2-minute signing timeout) left the bundle
 attached to a transaction id that was never signed, so the run could only end "unknown" and every
 later routing run was refused ("Resolve the existing routing run before creating another").
2. After two scheduled chain probes, "Check again" did nothing.
3. Unticking every sale of a bundle left no way past that bundle.
4. A stopped run that never reached a wallet blocked routing the same harvest again.
5. A share waiting for the wallet read "Unknown"; a lookup still being checked read "Outcome unknown".
No wallet, broadcast or live endpoint is used.
"""

import json
from pathlib import Path

import pytest

from ceres import harvest_routing as routing, routing_plan, routing_runs_v2 as runs
from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"
PLAYER = "fixtureowner"


def _preview(preview_id="offline-preview", attempts=("attempt-1",)):
    pool = routing.Pool(1, "GRAIN", routing.amount("1000"), routing.amount("1000"), routing.amount("1000"), False)
    rule = {"id": "sale-1", "kind": "sell", "source_region": "PR-AAA-1", "symbol": "GRAIN",
            "destination_symbol": "DEC", "quantity": "10", "slippage": "2.5"}
    quote = routing.swap_quote("GRAIN", "DEC", "10", {"GRAIN": pool}, "2.5")
    step = {"id": "sale-1", "kind": "sell", "status": "ready", "source_region": "PR-AAA-1",
            "destination_region": routing.INVENTORY, "symbol": "GRAIN", "destination_symbol": "DEC",
            "quantity": "10", "slippage": "2.5", "quote": quote.public()}
    manifest = routing_plan.compile_manifest(rules=[rule], steps=[step], pools={"GRAIN": pool},
                                             account=PLAYER, regions=1, enabled_kinds={"sale_dec"})
    return {"player": PLAYER, "phase": "after", "blocked": False, "manifest": manifest,
            "preview_id": preview_id, "preview_digest": "offline-digest", "rules": [rule],
            "steps": [step], "harvest_attempt_ids": list(attempts),
            "state": {"pools": [{"id": 1, "symbol": "GRAIN", "resource_quantity": "1000",
                                 "dec_quantity": "1000", "total_shares": "1000", "external": False}]}}


def test_unsigned_claim_returns_to_planned_and_signs_the_same_payload(tmp_path):
    with runs.locked(tmp_path, PLAYER) as folder:
        record = runs.create(folder, PLAYER, _preview(), {"sale_dec"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        first = runs.claim(folder, record, bundle_id, {"sale_dec"}, True)
        runs.release(folder, record, bundle_id)
        saved = runs.read(folder, PLAYER, record["run_id"])
        assert saved["bundles"][bundle_id]["state"] == "planned"
        assert saved["operations"]["sale-1"]["state"] == "planned"
        assert {item["state"] for item in saved["reservations"].values()} == {"held"}
        again = runs.claim(folder, saved, bundle_id, {"sale_dec"}, True)
        assert again["operations"] == first["operations"] and again["member_nonces"] == first["member_nonces"]


def test_release_is_refused_once_a_transaction_is_attached(tmp_path):
    with runs.locked(tmp_path, PLAYER) as folder:
        record = runs.create(folder, PLAYER, _preview(), {"sale_dec"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        runs.claim(folder, record, bundle_id, {"sale_dec"}, True)
        runs.attach(folder, record, bundle_id, "a" * 40)
        with pytest.raises(routing.RoutingError):
            runs.release(folder, record, bundle_id)
        assert runs.read(folder, PLAYER, record["run_id"])["bundles"][bundle_id]["state"] == "attached"


def test_manual_check_can_probe_after_the_scheduled_probes(tmp_path):
    with runs.locked(tmp_path, PLAYER) as folder:
        record = runs.create(folder, PLAYER, _preview(), {"sale_dec"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        claim = runs.claim(folder, record, bundle_id, {"sale_dec"}, True)
        runs.attach(folder, record, bundle_id, "a" * 40)
        missing = lambda _tx: (None, "not_found")  # noqa: E731
        runs.reconcile(folder, record, bundle_id, "chain", chain_get=missing)
        runs.reconcile(folder, record, bundle_id, "chain", chain_get=missing)
        with pytest.raises(routing.RoutingError, match="allowance"):
            runs.reconcile(folder, record, bundle_id, "chain", chain_get=missing)
        runs.reconcile(folder, record, bundle_id, "chain", manual=True,
                       chain_get=lambda _tx: ({"operations": claim["operations"]}, None))
        saved = runs.read(folder, PLAYER, record["run_id"])
        assert saved["bundles"][bundle_id]["chain"] == "included"
        assert saved["call_budget"]["actions"] == {"chain_probe": 2, "manual_retry": 1}


def test_expired_and_definitively_absent_transaction_stops_instead_of_bricking(tmp_path):
    """An attached transaction the node says it does not know, once past Hive's one-hour expiry,
 can never be included: the bundle stops, reservations are released and the account can route again.
 A lookup failure or a recent miss stays unknown."""
    with runs.locked(tmp_path, PLAYER) as folder:
        record = runs.create(folder, PLAYER, _preview("p1"), {"sale_dec"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        runs.claim(folder, record, bundle_id, {"sale_dec"}, True)
        runs.attach(folder, record, bundle_id, "a" * 40)
        runs.reconcile(folder, record, bundle_id, "chain", chain_get=lambda _tx: (None, "not_found"))
        assert record["bundles"][bundle_id]["state"] == "outcome_unknown"
        record["bundles"][bundle_id]["attached_at"] -= runs.TX_EXPIRY_SECONDS + 1
        runs.reconcile(folder, record, bundle_id, "chain", chain_get=lambda _tx: (None, "lookup_failed"))
        assert record["bundles"][bundle_id]["state"] == "outcome_unknown"
        runs.reconcile(folder, record, bundle_id, "chain", manual=True, chain_get=lambda _tx: (None, "not_found"))
        saved = record   # a run that is stopped with nothing in doubt is deleted in the same step
        assert (PLAYER, record["run_id"]) not in runs._RUNS
        assert saved["bundles"][bundle_id]["state"] == "stopped"
        assert {item["state"] for item in saved["reservations"].values()} == {"released"}
        assert saved["state"] == "stopped"
        assert runs.create(folder, PLAYER, _preview("p2", attempts=("attempt-2",)), {"sale_dec"})["state"] == "ready"


def test_all_sales_unticked_skips_the_bundle_without_a_wallet(tmp_path):
    with runs.locked(tmp_path, PLAYER) as folder:
        record = runs.create(folder, PLAYER, _preview(), {"sale_dec"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        route = record["manifest"]["bundles"][0]["operations"][0]["members"][0]["route_id"]
        result = runs.claim(folder, record, bundle_id, {"sale_dec"}, True, [route])
        assert result["skipped"] is True and result["operations"] == []
        saved = record   # deleted in the same step
        assert (PLAYER, record["run_id"]) not in runs._RUNS
        assert saved["bundles"][bundle_id]["state"] == "skipped"
        assert {item["state"] for item in saved["reservations"].values()} == {"released"}
        assert saved["state"] == "stopped"


def test_stopped_never_sent_run_frees_its_harvest_but_a_sent_one_does_not(tmp_path):
    with runs.locked(tmp_path, PLAYER) as folder:
        first = runs.create(folder, PLAYER, _preview("p1"), {"sale_dec"})
        runs.stop(folder, first)
        second = runs.create(folder, PLAYER, _preview("p2"), {"sale_dec"})
        assert second["run_id"] != first["run_id"]
        bundle_id = second["manifest"]["bundles"][0]["id"]
        runs.claim(folder, second, bundle_id, {"sale_dec"}, True)
        runs.attach(folder, second, bundle_id, "a" * 40)
        runs.reconcile(folder, second, bundle_id, "chain", chain_get=lambda _tx: (None, "not_found"))
        runs.stop(folder, second)
        with pytest.raises(routing.RoutingError):
            runs.create(folder, PLAYER, _preview("p3"), {"sale_dec"})


def test_wallet_refusal_releases_the_claim_and_allows_signing_again():
    run_in_module_context(r"""
walletReady = () => true;
walletCanSignTransaction = () => true;
navigator.locks = { request: async (_name, fn) => fn() };
const bundle = { id: 'bundle-1', wave: 0, approval: 1, operations: [{ id: 'op', members: [{ nonce: 'member-1', kind: 'sale_dec', route_id: 'r1' }] }] };
const record = { run_id: 'a'.repeat(32), state: 'ready', manifest: { bundles: [bundle], approval_counts: { harvest: 1 } },
 bundles: { 'bundle-1': { state: 'planned' } }, operations: {}, call_budget: { stopped: false, total: 0 } };
const calls = [];
const api = async (_player, path, body) => {
 calls.push(path);
 if (path === 'run' || path === 'state') return record;
 if (path === 'claim') { record.bundles['bundle-1'] = { state: 'claimed' }; return {
 authority: 'Posting', member_nonces: ['member-1'], operations: [['custom_json', { id: 'sm_land_operation', json: '{"n":"member-1"}' }]] }; }
 if (path === 'head') return { head_block_number: 1, head_block_id: '0'.repeat(40), time: '2026-09-28T00:00:00' };
 if (path === 'release') { record.bundles['bundle-1'] = { state: 'planned' }; return record; }
 if (path === 'attach') throw Error('an unsigned transaction was attached');
 throw Error('unexpected endpoint ' + path);
};
let signatures = 0;
const run = createRoutingRun({ player: 'alice', api, prepare: async () => [{ txId: 'b'.repeat(40) }],
 sign: async () => {
 signatures += 1;
 const error = Error('The wallet did not approve this signature. Nothing was sent.');
 error.txId = 'b'.repeat(40); error.broadcasted = false; error.declined = true; error.signingRejected = true; throw error;
 } });
(async () => {
 await run.create({ preview_id: 'preview', preview_digest: 'digest' });
 await run.action('sign');
 if (record.bundles['bundle-1'].state !== 'planned') throw Error('refused bundle stayed claimed');
 if (!calls.includes('release')) throw Error('release not requested');
 if (JSON.parse(localStorage.getItem('ceres:routing-v2-members:alice') || '[]').length) throw Error('member stayed reserved');
 if (run.html().includes('did not approve')) throw Error('an error text was shown');
 await run.action('sign');
 if (signatures !== 2) throw Error('the refused bundle could not be signed again');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=STATIC / "routing-run.js")


def test_wallet_failure_other_than_cancel_shows_its_reason():
    run_in_module_context(r"""
walletReady = () => true;
walletCanSignTransaction = () => true;
navigator.locks = { request: async (_name, fn) => fn() };
const bundle = { id: 'bundle-1', wave: 0, approval: 1, operations: [{ id: 'op', members: [{ nonce: 'member-1', kind: 'sale_dec', route_id: 'r1' }] }] };
const record = { run_id: 'a'.repeat(32), state: 'ready', manifest: { bundles: [bundle], approval_counts: { harvest: 1 } },
 bundles: { 'bundle-1': { state: 'planned' } }, operations: {}, call_budget: { stopped: false, total: 0 } };
const calls = [];
const api = async (_player, path, body) => {
 calls.push(path);
 if (path === 'run' || path === 'state') return record;
 if (path === 'claim') { record.bundles['bundle-1'] = { state: 'claimed' }; return {
 authority: 'Posting', member_nonces: ['member-1'], operations: [['custom_json', { id: 'sm_land_operation', json: '{"n":"member-1"}' }]] }; }
 if (path === 'head') return { head_block_number: 1, head_block_id: '0'.repeat(40), time: '2026-09-28T00:00:00' };
 if (path === 'release') { record.bundles['bundle-1'] = { state: 'planned' }; return record; }
 if (path === 'attach') throw Error('an unsigned transaction was attached');
 throw Error('unexpected endpoint ' + path);
};
let signatures = 0;
const run = createRoutingRun({ player: 'alice', api, prepare: async () => [{ txId: 'b'.repeat(40) }],
 sign: async () => {
 signatures += 1;
 const error = Error('This transaction exceeds Hive five custom-operation limit. Nothing was sent.');
 error.broadcasted = false; throw error;
 } });
(async () => {
 await run.create({ preview_id: 'preview', preview_digest: 'digest' });
 await run.action('sign');
 if (record.bundles['bundle-1'].state !== 'planned') throw Error('refused bundle stayed claimed');
 if (!calls.includes('release')) throw Error('release not requested');
 if (JSON.parse(localStorage.getItem('ceres:routing-v2-members:alice') || '[]').length) throw Error('member stayed reserved');
 if (!run.html().includes('custom-operation limit')) throw Error('the failure reason was hidden');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=STATIC / "routing-run.js")


def test_window_labels_follow_the_bundle_until_a_share_is_final():
    run_in_module_context(r"""
const operation = { id: 'op', kind: 'sell', source_region: 'PR-BRL-41', source_symbol: 'WOOD', destination_symbol: 'DEC',
 members: [{ nonce: 'n1', kind: 'sale_dec', route_id: 'r1', source_amount: '10', source_percent: '25', protected_minimum: '1' }] };
const bundle = { id: 'b1', wave: 0, approval: 1, operations: [operation] };
const base = state => ({ run_id: 'x', state: 'ready', manifest: { bundles: [bundle], approval_counts: { routing: 1 } },
 bundles: { b1: { state, tx_id: state === 'claimed' ? undefined : 'a'.repeat(40) } },
 operations: { op: { state: 'outcome_unknown', members: { n1: { state: 'outcome_unknown' } } } }, call_budget: { total: 2 } });
const claimed = routingWindowHtml(base('claimed'), { player: 'alice', busy: true });
if (claimed.includes('· Unknown')) throw Error('a share waiting for the wallet reads Unknown');
const checking = routingWindowHtml(base('outcome_unknown'), { player: 'alice', busy: true });
if (checking.includes('Outcome unknown') || checking.includes('· Unknown')) throw Error('a running check reads unknown');
const settled = routingWindowHtml(base('outcome_unknown'), { player: 'alice', busy: false });
if (!settled.includes('Outcome unknown')) throw Error('a settled unknown outcome is hidden');
const planned = { ...base('planned'), bundles: { b1: { state: 'planned' } }, operations: { op: { state: 'planned', members: { n1: { state: 'planned' } } } } };
const skip = routingWindowHtml(planned, { player: 'alice', excludedRouteIds: new Set(['r1']) });
if (!skip.includes('>Skip this bundle<') || skip.includes('data-r3="sign" disabled')) throw Error('an all-unticked bundle has no way forward');
""", entry=STATIC / "routing-window.js")


def test_every_releasable_v2_step_signs_with_posting_only(tmp_path, monkeypatch):
    """the Active SPS funding transfer (harvest_routing_operations.build_sps_funding) is only
 reachable from the hidden v1 flow. With every local routing switch on, no v2 kind that could need
 it is enabled, and a claimed v2 bundle carries no Active authority, so the run window's single
 "Posting" label is true for every step it can show."""
    from ceres.config import CeresConfig
    for name in ("CERES_ROUTING_SALE_DEC", "CERES_ROUTING_LP_WALLET", "CERES_ROUTING_LP_SELL_HALF"):
        monkeypatch.setenv(name, "1")
    enabled = set(CeresConfig(root=tmp_path, player=PLAYER).routing_kinds)
    assert enabled == {"sale_dec", "lp_wallet", "lp_sell_half"}
    sps_rules = [{"kind": "sell", "symbol": "SPS", "destination_symbol": "DEC"},
                 {"kind": "sell", "symbol": "GRAIN", "destination_symbol": "SPS"}]
    assert {routing_plan.rule_kind(rule) for rule in sps_rules}.isdisjoint(enabled)
    with runs.locked(tmp_path, PLAYER) as folder:
        record = runs.create(folder, PLAYER, _preview(), {"sale_dec"})
        claim = runs.claim(folder, record, record["manifest"]["bundles"][0]["id"], {"sale_dec"}, True)
    assert claim["authority"] == "Posting"
    assert all(body["required_auths"] == [] and body["required_posting_auths"] == [PLAYER]
               and body["id"] == "sm_land_operation" for _kind, body in claim["operations"])


def test_setup_shows_routes_already_bundled_before_sign():
    run_in_module_context(r"""
const __seed1 = { rules: [
 { id: 'r1', kind: 'sell', destination_symbol: 'DEC', source_region: 'PR-BRL-41', symbol: 'WOOD', quantity_mode: 'percent', quantity: '25', slippage: '2.5' },
 { id: 'r2', kind: 'liquidity', dec_source: 'wallet', source_region: 'PR-BRL-41', symbol: 'WOOD', quantity_mode: 'percent', quantity: '25', slippage: '2.5' },
 { id: 'r3', kind: 'sell', destination_symbol: 'DEC', source_region: 'PR-BRL-41', symbol: 'STONE', quantity_mode: 'percent', quantity: '25', slippage: '2.5' }] };
const member = id => ({ route_id: id, kind: id === 'r2' ? 'lp_wallet' : 'sale_dec' });
const manifest = { bundles: [
 { id: 'b1', approval: 1, operations: [{ kind: 'sell', members: [member('r1')] }, { kind: 'sell', members: [member('r3')] }] },
 { id: 'b2', approval: 2, operations: [{ kind: 'liquidity', members: [member('r2')] }] }] };
let previews = 0;
fetch = async (url) => { if (String(url).includes('/last-rules') || String(url).includes('/safety')) return { ok: true, json: async () => ({ rules: [] }) }; previews += 1; return { ok: true, json: async () => ({ manifest, steps: [], blocked: false }) }; };
let renders = 0;
const setup = createRoutingSetup({ initialRules: __seed1.rules, player: 'alice', scope: { kind: 'all' }, regions: [{ region_uid: 'PR-BRL-41' }],
 receipts: ['attempt-1'], enabledKinds: ['sale_dec', 'lp_wallet'], signingEnabled: true, onRender: () => { renders += 1; }, isCurrent: () => true });
(async () => {
 const before = setup.html();
 // the very first paint already shows the final bundled layout; the preview never regroups it.
 if (!before.includes('Approval 1 of 2 \u00b7 Sales \u00b7 2 routes') || !before.includes('Approval 2 of 2 \u00b7 LP deposits \u00b7 1 route')) throw Error('first paint is not bundled: ' + before.slice(0, 500));
 if (before.includes('Grouping routes')) throw Error('a grouping placeholder was shown');
 setup.load();
 await new Promise(resolve => setTimeout(resolve, 900));
 const after = setup.html();
 if (previews !== 1) throw Error('expected one automatic preview, got ' + previews);
 const first = after.indexOf('Approval 1 of 2 · Sales · 2 routes');
 const second = after.indexOf('Approval 2 of 2 · LP deposits · 1 route');
 if (first < 0 || second < first) throw Error('routes are not shown in their bundles: ' + after.slice(0, 600));
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=STATIC / "routing-setup.js")


_RUN_HARNESS = r"""
walletReady = () => true;
walletCanSignTransaction = () => true;
navigator.locks = { request: async (_name, fn) => fn() };
const bundle = { id: 'bundle-1', wave: 0, approval: 1, operations: [{ id: 'op', members: [{ nonce: 'member-1', kind: 'sale_dec', route_id: 'r1' }] }] };
const record = { run_id: 'a'.repeat(32), state: 'ready', manifest: { bundles: [bundle], approval_counts: { harvest: 1 } },
 bundles: { 'bundle-1': { state: 'planned' } }, operations: {}, call_budget: { stopped: false, total: %(total)s, ceiling: %(ceiling)s } };
const calls = [];
const api = async (_player, path, body) => {
 calls.push(path);
 if (path === 'run' || path === 'state') return record;
 if (path === 'claim') { record.bundles['bundle-1'] = { state: 'claimed' }; return {
 authority: 'Posting', member_nonces: ['member-1'], operations: [['custom_json', { id: 'sm_land_operation', json: '{"n":"member-1"}' }]] }; }
 if (path === 'head') return { head_block_number: 1, head_block_id: '0'.repeat(40), time: '2026-09-28T00:00:00' };
 if (path === 'release') { record.bundles['bundle-1'] = { state: 'planned' }; return record; }
 if (path === 'client-report') return {};
 throw Error('unexpected endpoint ' + path);
};
"""


def test_browser_guard_follows_the_plan_sized_budget_r02():
    run_in_module_context(_RUN_HARNESS % {"total": 130, "ceiling": 500} + r"""
const run = createRoutingRun({ player: 'alice', api, prepare: async () => [{ txId: 'b'.repeat(40) }],
 sign: async () => { const error = Error('wallet-said-no-xyz'); error.broadcasted = false; throw error; } });
(async () => {
 await run.create({ preview_id: 'preview', preview_digest: 'digest' });
 await run.action('sign');
 const html = run.html();
 if (html.includes('call budget')) throw Error('guard stopped at the old fixed 120');
 if (!html.includes('wallet-said-no-xyz')) throw Error('signing did not reach the wallet: ' + html.slice(0, 300));
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=STATIC / "routing-run.js")


def test_full_browser_storage_still_releases_an_unsigned_claim_r08():
    run_in_module_context(_RUN_HARNESS % {"total": 0, "ceiling": 120} + r"""
const realSet = localStorage.setItem.bind(localStorage);
const run = createRoutingRun({ player: 'alice', api, prepare: async () => [{ txId: 'b'.repeat(40) }],
 sign: async () => { throw Error('never reached'); } });
(async () => {
 await run.create({ preview_id: 'preview', preview_digest: 'digest' });
 localStorage.setItem = (key, value) => { if (String(key).startsWith('ceres:routing-v2-members')) throw Error('QuotaExceededError'); return realSet(key, value); };
 await run.action('sign');
 if (!calls.includes('release')) throw Error('the claimed bundle was left claimed');
 if (record.bundles['bundle-1'].state !== 'planned') throw Error('bundle not back to planned');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=STATIC / "routing-run.js")


def test_sign_stops_when_the_routes_changed_and_shows_a_repriced_plan_first_r06_r07():
    run_in_module_context(r"""
const __seed2 = { rules: [
 { id: 'r1', kind: 'sell', destination_symbol: 'DEC', source_region: 'PR-BRL-41', symbol: 'WOOD', quantity_mode: 'percent', quantity: '25', slippage: '2.5' }] };
navigator.locks = { request: async (_name, fn) => fn() };
walletReady = () => true;
const member = id => ({ route_id: id, kind: 'sale_dec' });
const manifest = { bundles: [{ id: 'b1', approval: 1, operations: [{ kind: 'sell', members: [member('r1')] }] }] };
let previews = 0;
let resolveCreate;
fetch = async (url) => {
 const path = String(url).split('?')[0];
 if (path.endsWith('/routing/preview')) { previews += 1; return { ok: true, json: async () => ({ manifest, steps: [], blocked: false, preview_id: 'p' + previews, preview_digest: 'd' }) }; }
 if (path.endsWith('/v2/state')) return { ok: true, json: async () => ({ run_id: 'a'.repeat(32), state: 'ready', manifest, bundles: { b1: { state: 'planned' } }, operations: {}, call_budget: { total: 0, ceiling: 120 } }) };
 if (path.endsWith('/v2/run')) return { ok: false, status: 409, json: async () => ({ detail: 'Routing preview expired or changed' }) };
 return { ok: true, json: async () => ({}) };
};
const setup = createRoutingSetup({ initialRules: __seed2.rules, player: 'alice', scope: { kind: 'all' }, regions: [{ region_uid: 'PR-BRL-41' }],
 receipts: ['attempt-1'], enabledKinds: ['sale_dec'], signingEnabled: true, onRender: () => {}, isCurrent: () => true });
(async () => {
 setup.load();
 await new Promise(resolve => setTimeout(resolve, 700));
 const before = previews;
 await setup.action('start');
 if (setup.record) throw Error('a run was created from an expired plan');
 if (previews !== before + 1) throw Error('the expired plan was not re-priced once: ' + (previews - before));
 if (!setup.html().includes('Prices moved while this stayed open')) throw Error('the re-priced plan was not shown before signing: ' + setup.html().slice(0, 200));
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=STATIC / "routing-setup.js")


def test_queue_evidence_is_posted_once_per_state():
    run_in_module_context(r"""
const tx = '1'.repeat(40);
localStorage.setItem('ceres:signed-queue:v1:alice', JSON.stringify([
 { txId: tx, account: 'alice', state: 'included', updatedAt: new Date().toISOString(), context: { harvestAttemptId: 'att-1' } }]));
let posts = 0;
fetch = async () => { posts += 1; return { ok: true }; };
(async () => {
 await syncQueueHarvestEvidence('alice');
 await syncQueueHarvestEvidence('alice');
 await syncQueueHarvestEvidence('alice');
 if (posts !== 1) throw Error('expected one post, got ' + posts);
 const rows = JSON.parse(localStorage.getItem('ceres:signed-queue:v1:alice'));
 rows[0].state = 'rejected';
 localStorage.setItem('ceres:signed-queue:v1:alice', JSON.stringify(rows));
 await syncQueueHarvestEvidence('alice');
 if (posts !== 2) throw Error('a new state must be posted once more, got ' + posts);
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=STATIC / "transaction-queue.js")


def test_rule_count_is_no_longer_capped_at_one_hundred_r01():
    pools = {}
    rules = [{"id": f"r{i}"} for i in range(101)]
    with pytest.raises(routing.RoutingError) as caught:
        routing.validate_rules(rules, routing.Ledger({}), pools, set())
    assert str(caught.value) != "Invalid routing rules"
    with pytest.raises(routing.RoutingError) as caught:
        routing.validate_rules([{"id": f"r{i}"} for i in range(routing.MAX_RULES + 1)], routing.Ledger({}), pools, set())
    assert str(caught.value) == "Invalid routing rules"


def test_setup_prices_by_itself_even_when_an_old_finished_run_is_stored():
    run_in_module_context(r"""
const __seed3 = { rules: [
 { id: 'r1', kind: 'sell', destination_symbol: 'DEC', source_region: 'PR-BRL-41', symbol: 'WOOD', quantity_mode: 'percent', quantity: '25', slippage: '2.5' }] };
localStorage.setItem('ceres:routing-v2-run:alice', 'a'.repeat(32));
navigator.locks = { request: async (_name, fn) => fn() };
const member = id => ({ route_id: id, kind: 'sale_dec' });
const manifest = { bundles: [{ id: 'b1', approval: 1, operations: [{ kind: 'sell', members: [member('r1')] }] }] };
const old = { run_id: 'a'.repeat(32), state: 'complete', harvest_attempt_ids: ['an-older-harvest'], manifest: { bundles: [] },
 bundles: {}, operations: {}, call_budget: { total: 0, ceiling: 120 } };
let previews = 0;
fetch = async (url) => {
 const path = String(url).split('?')[0];
 if (path.endsWith('/routing/preview')) { previews += 1; return { ok: true, json: async () => ({ manifest, steps: [], blocked: false, preview_id: 'p', preview_digest: 'd' }) }; }
 if (path.endsWith('/v2/state')) return { ok: true, json: async () => old };
 return { ok: true, json: async () => ({}) };
};
const setup = createRoutingSetup({ initialRules: __seed3.rules, player: 'alice', scope: { kind: 'all' }, regions: [{ region_uid: 'PR-BRL-41' }],
 receipts: ['this-harvest'], enabledKinds: ['sale_dec'], signingEnabled: true, onRender: () => {}, isCurrent: () => true });
(async () => {
 await setup.load();
 await new Promise(resolve => setTimeout(resolve, 900));
 if (previews !== 1) throw Error('no automatic preview after an old run was discarded: ' + previews);
 if (!setup.html().includes('Approval 1 of 1')) throw Error('not grouped');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=STATIC / "routing-setup.js")


def test_sell_to_dec_box_is_two_rows():
    run_in_module_context(r"""
const operation = { id: 'op', kind: 'sell', source_region: 'PR-BRL-41', source_symbol: 'WOOD', destination_symbol: 'DEC', protected_minimum: '120.43',
 members: [{ nonce: 'n1', kind: 'sale_dec', route_id: 'r1', source_amount: '5746.22', source_percent: '25', protected_minimum: '120.43' }] };
const bundle = { id: 'b1', wave: 0, approval: 1, operations: [operation] };
const record = { run_id: 'x', state: 'ready', manifest: { bundles: [bundle], approval_counts: { routing: 1 } },
 bundles: { b1: { state: 'planned' } }, operations: {}, call_budget: { total: 0 } };
const html = routingWindowHtml(record, { player: 'alice' });
const box = html.match(/<div class="r3-destination r3-destination--two">.*?<\/div>/);
if (!box) throw Error('no two-row sale destination box');
if (!box[0].includes('<span>Minimum</span><strong>120.43 DEC</strong>')) throw Error('rows are not "Minimum" over the DEC amount: ' + box[0]);
if (box[0].includes('<b>')) throw Error('a third row is back');
""", entry=STATIC / "routing-window.js")


def test_lp_outcome_box_is_two_rows():
    run_in_module_context(r"""
const operation = { id: 'op', kind: 'liquidity', source_region: 'PR-BRL-60', source_symbol: 'WOOD', pool: 'WOOD', destination_symbol: 'DEC',
 members: [{ nonce: 'n1', kind: 'lp_wallet', route_id: 'r1', source_amount: '647.23', source_percent: '25', protected_minimum: '631.05', required_wallet_dec: '15.45' }] };
const bundle = { id: 'b1', wave: 1, approval: 1, operations: [operation] };
const record = { run_id: 'x', state: 'ready', manifest: { bundles: [bundle], approval_counts: { routing: 1 } },
 bundles: { b1: { state: 'planned' } }, operations: {}, call_budget: { total: 0 } };
const html = routingWindowHtml(record, { player: 'alice' });
const box = html.match(/<div class="r3-destination r3-destination--two">.*?<\/div>/);
if (!box) throw Error('no two-row LP outcome box');
if (!box[0].includes('<span>LP shares · minimum</span><strong>631.05 Wood</strong>')) throw Error('rows are wrong: ' + box[0]);
if (box[0].includes('<b>')) throw Error('a third row is back');
""", entry=STATIC / "routing-window.js")
