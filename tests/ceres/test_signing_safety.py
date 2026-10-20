""" S3 (v1 clean-up, audit finding L2-01): an unclear wallet outcome is never permission to sign a second swap."""
from pathlib import Path

from tests.ceres.vm_context import read_entry_source, run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"
ENTRY = STATIC / "wallet-lp.js"
# the real module graph, with only the wallet broadcast gate swapped for a scripted one so each outcome can be produced exactly
SOURCE = read_entry_source(ENTRY).replace("await requestBroadcastThroughGate(ctx.player, fresh.operations", "await globalThis.__gate(ctx.player, fresh.operations").replace("|| !walletReady(ctx.player)) return;", "|| false) return;")

FLOW = r"""
const calls = [];
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...rest) => realSetTimeout(fn, 0, ...rest); // the verify schedule (about a minute) runs instantly
globalThis.fetch = async (url, init) => {
 const path = String(url);
 calls.push(path);
 if (path.includes('/verify')) return { ok: true, status: 200, json: async () => ({ status: 'pending' }) };
 return { ok: true, status: 200, json: async () => ({ operations: [['custom_json', { id: 'x', json: '{}' }]], quote: { input: 10, output: 5, two_step: false } }) };
};
const symbol = 'WOOD';
const st = swapState('alice', symbol);
Object.assign(st, { direction: 'sell', amount: '10', quoted: '10', region: 'PR-AAA-1',
 quote: { kind: 'swap_sell', input: 10, output: 5, minimum: 4.9, fee_percent: '1', pool_cost: 0.1 } });
const wallet = { account: { DEC: '100' }, swap: { symbols: ['WOOD'], directions: ['swap_sell', 'swap_buy'], signing_enabled: true, rates: {}, fees: {} },
 regions: [{ region_uid: 'PR-AAA-1', balances: { WOOD: '50' } }] };
const target = { closest: () => ({ dataset: { lpSymbol: 'WOOD' } }) };
const ctx = { player: 'alice', account: () => 'alice', drawer: () => null, wallet: () => wallet, render: () => {}, setSigning: () => {}, reload: async () => {} };
"""


def _run(outcome_js, expectation_js):
    body = "(async () => {\n" + FLOW + outcome_js + expectation_js + "\n})().catch(error => { console.error(error && error.stack || error); process.exit(1); });"
    run_in_module_context(body, entry=ENTRY, source=SOURCE)


def test_l2_01_the_button_does_not_come_back_when_the_wallet_outcome_is_unknown():
    # the wallet timed out: no transaction id, and nothing proves it was not sent
    _run("globalThis.__gate = async () => { const e = new Error('The wallet did not answer'); e.broadcasted = undefined; throw e; };\n"
         "try { await swapAction(ctx, 'swap', target); } catch (_) {}\n",
         "if (st.stage === 'edit') throw Error('the swap button is ready again after an unknown outcome (stage ' + st.stage + ')');\n"
         "if (st.stage !== 'confirming' || !st.receipt || st.receipt.unknownOutcome !== true) throw Error('expected a held receipt, got ' + st.stage);\n")


def test_l2_01_a_provably_unsent_swap_returns_the_button():
    _run("globalThis.__gate = async () => { const e = new Error('Declined in the wallet'); e.broadcasted = false; throw e; };\n"
         "try { await swapAction(ctx, 'swap', target); } catch (_) {}\n",
         "if (st.stage !== 'edit' || st.receipt) throw Error('a declined swap must be ready again, got ' + st.stage);\n")


# L2-06: a refusal inside the game's nested answer must never read as confirmed
import json

import pytest

from ceres import routes_wallet_lp as lp_routes, wallet_lp_operations as operations

TX = "c" * 40


def _verdict(result):
    expected = operations.sell("player0005", "PR-CEF-65", "GRAIN", 2, 1, 2.5)["payload"]
    info = {"id": TX, "player": "player0005", "type": "land_operation", "data": json.dumps(expected), "success": True, "result": result}
    body = {"tx_id": TX, "nonce": expected["n"], "kind": "swap_sell", "resource_symbol": "GRAIN", "region_uid": "PR-CEF-65", "expected_payload": expected}
    return lp_routes._verify(info, "player0005", TX, body)


@pytest.mark.parametrize("result", [
    {"success": True, "result": {"success": False, "error": "pool moved"}},                                   # the case the first fix handled
    {"success": True, "data": {"dec_amount": "9"}, "result": {"success": False, "error": "pool moved"}},      # a data block does not hide a refusal
    {"success": False, "data": {"dec_amount": "9"}},                                                           # outer refusal with data
    {"result": {"success": False, "error": "pool moved"}, "data": {"dec_amount": "9"}}])
def test_l2_06_a_refusal_anywhere_in_the_answer_is_a_failure(result):
    assert _verdict(result)["status"] == "failed"


def test_l2_06_a_real_confirmation_still_confirms():
    assert _verdict({"data": {"dec_amount": "9"}}) == {"status": "confirmed", "game_result": {"dec_amount": "9"}}
    assert _verdict({"success": True, "result": {"success": True, "data": {"dec_amount": "9"}}})["status"] == "confirmed"


# S4 / L2-05: the swap signed for what the card promised, not for a lower fresh figure (slippage was counted twice)
from decimal import Decimal

from ceres.harvest_routing import Pool


def _built(body_extra, pool_dec="100"):
    pools = {"GRAIN": Pool(1, "GRAIN", Decimal("1000"), Decimal(pool_dec), Decimal("100"))}
    balances = [{"player": "player0005", "token": "DEC", "balance": 100}]
    regional = {"status": "success", "data": [{"uid": "PR-CEF-65", "grain": 50, "wood": 0, "stone": 0, "iron": 0, "research": 0, "aura": 0}]}
    body = {"kind": "swap_sell", "region_uid": "PR-CEF-65", "resource_symbol": "GRAIN", "resource_amount": "10", **body_extra}
    return lp_routes._build("player0005", body, pools, balances, regional), pools


def test_l2_05_the_signed_expected_output_is_the_figure_the_owner_saw():
    fresh, pools = _built({})
    fresh_output = Decimal(str(fresh["payload"]["shares_out"]))
    seen = (fresh_output * Decimal("1.01")).quantize(Decimal("0.001"))   # the card showed a slightly higher figure than the fresh pool now gives
    signed, _ = _built({"sign": True, "seen_output": str(seen)})
    assert float(signed["payload"]["shares_out"]) == float(seen) != float(fresh_output)


def test_l2_05_a_fresh_quote_that_cannot_meet_the_promise_is_not_papered_over():
    fresh, _ = _built({})
    fresh_output = Decimal(str(fresh["payload"]["shares_out"]))
    far_above = (fresh_output * Decimal("2")).quantize(Decimal("0.001"))                    # fresh output is below seen x (1 - 2.5%): the caller refuses; the builder keeps the fresh figure
    signed, _ = _built({"sign": True, "seen_output": str(far_above)})
    assert float(signed["payload"]["shares_out"]) == float(fresh_output)


def test_l2_05_without_a_seen_figure_or_when_not_signing_nothing_changes():
    fresh, _ = _built({})
    for extra in ({"seen_output": "999"}, {"sign": True}, {"sign": True, "seen_output": "x"}, {"sign": True, "seen_output": "5.123456"}, {"sign": True, "seen_output": "5", "reviewed_payload": {}}):
        built, _ = _built(extra)
        assert built["payload"]["shares_out"] == fresh["payload"]["shares_out"], extra


# S4 / L2-04: a wallet-funded LP deposit is never repriced beyond what the maintainer reviewed
from types import SimpleNamespace

from ceres import harvest_routing as routing_mod, routing_runs_v2 as runs_mod
from tests.ceres import test_r5_lp_wallet as lp_fixture


def _fresh_with_pool_price(monkeypatch, dec_quantity):
    class Transport:
        def __init__(self, *, timeout, retries):
            pass

        def get_json(self, url):
            if "/land/liquidity/pools" in url:
                return SimpleNamespace(data={"status": "success", "data": [{"id": 1, "token_symbol": "GRAIN", "resource_quantity": 1000,
                                                                             "dec_quantity": dec_quantity, "total_shares": 1000, "is_external_resource": False}]})
            return SimpleNamespace(data=[{"player": lp_fixture.PLAYER, "token": "DEC", "balance": "5000"}])
    monkeypatch.setattr(runs_mod, "FixedGetTransport", Transport)


def test_l2_04_a_repriced_deposit_far_above_the_reviewed_dec_is_refused(tmp_path, monkeypatch):
    with runs_mod.locked(tmp_path, lp_fixture.PLAYER) as folder:
        record = runs_mod.create(folder, lp_fixture.PLAYER, lp_fixture._preview(), {"lp_wallet"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        _fresh_with_pool_price(monkeypatch, 100000)               # DEC became 100x more expensive: 10 DEC reviewed -> about 1,000 DEC now
        with pytest.raises(routing_mod.RoutingError, match="Review the route again"):
            runs_mod.claim(folder, record, bundle_id, {"lp_wallet"}, True)


def test_l2_04_a_small_move_within_the_tolerance_still_signs(tmp_path, monkeypatch):
    with runs_mod.locked(tmp_path, lp_fixture.PLAYER) as folder:
        record = runs_mod.create(folder, lp_fixture.PLAYER, lp_fixture._preview(), {"lp_wallet"})
        bundle_id = record["manifest"]["bundles"][0]["id"]
        _fresh_with_pool_price(monkeypatch, 1010)
        assert runs_mod.claim(folder, record, bundle_id, {"lp_wallet"}, True)["operations"]


# S4 / L2-08: an SPS sale says up front that it also needs the Active key
def test_l2_08_two_step_sale_is_disclosed_before_the_first_signature():
    run_in_module_context(r"""
const wallet = { account: { DEC: '100', SPS: '500' }, swap: { symbols: ['SPS'], account_symbols: ['SPS'], two_step_sell: ['SPS'], directions: ['swap_sell'],
 signing_enabled: true, rates: {}, fees: {} }, regions: [] };
const html = swapHtml('SPS', wallet, 'alice');
if (!html.includes('data-lp-two-step') || !html.includes('Active') || !html.includes('two wallet approvals')) throw Error('no disclosure: ' + html.slice(0, 200));
const single = { ...wallet, swap: { ...wallet.swap, two_step_sell: [] } };
if (swapHtml('SPS', single, 'alice').includes('data-lp-two-step')) throw Error('a one-step sale must not show the two-step note');
""", entry=ENTRY)


# S10 / L2-01: an unclassified wallet failure is an unknown outcome, never "not sent"
def test_l2_01_only_stated_outcomes_release_a_claim():
    run_in_module_context(r"""
const unknown = [walletFailure(null), walletFailure(undefined), walletFailure({ success: false, error: 'network timeout' }), walletFailure({ success: false })];
for (const error of unknown) {
 if (!error.ambiguousBroadcast || !error.broadcasted || error.retryable) throw Error('an unknown failure was treated as unsent: ' + error.message);
}
const stated = [walletFailure({ declined: true }), walletFailure({ notSent: true }), walletFailure({ success: false, error: 'user_cancel' }),
 walletFailure({ success: false, error: 'Assert Exception: tx rejected' })];
for (const error of stated) {
 if (error.ambiguousBroadcast || error.broadcasted !== false) throw Error('a stated not-sent outcome was kept as unknown: ' + error.message);
}
if (!walletFailure({ success: false, ambiguousBroadcast: true }).ambiguousBroadcast) throw Error('an explicit ambiguous flag must survive');
""", entry=STATIC / "broadcast-gate.js")


# S10 / L2-02: an unresolved swap survives a reload
def test_l2_02_an_open_swap_is_restored_by_a_new_page_and_cleared_when_settled():
    run_in_module_context(r"""
const first = swapState('alice', 'WOOD');
Object.assign(first, { stage: 'confirming', receipt: { kind: 'swap_sell', pay: 'WOOD', receive: 'DEC', nonce: 'abcdef0123456789', input: 10, unknownOutcome: true } });
syncStoredIntent('alice', 'WOOD', first);
states.clear(); // a reload / second tab: the page memory is gone
const again = swapState('alice', 'WOOD');
if (again.stage !== 'confirming' || !again.receipt || again.receipt.nonce !== 'abcdef0123456789' || again.receipt.unknownOutcome !== true) throw Error('the open swap was not restored: ' + again.stage);
if (swapState('alice', 'STONE').stage !== 'edit' || swapState('bob', 'WOOD').stage !== 'edit') throw Error('another symbol or player must not inherit it');
again.stage = 'done'; syncStoredIntent('alice', 'WOOD', again); // settled: nothing is kept
states.clear();
if (swapState('alice', 'WOOD').stage !== 'edit') throw Error('a settled swap came back');
localStorage.setItem('ceres:wallet-lp-intent:v1:alice:IRON', '{damaged');
states.clear();
if (swapState('alice', 'IRON').stage !== 'edit') throw Error('a damaged record must be ignored');
localStorage.setItem('ceres:wallet-lp-intent:v1:alice:GRAIN', JSON.stringify({ stage: 'confirming', receipt: { nonce: 'x' }, savedAt: Date.now() - 40 * 24 * 3600 * 1000 }));
states.clear();
if (swapState('alice', 'GRAIN').stage !== 'confirming') throw Error('elapsed time must never release an unresolved swap (only the maintainer can)');
""", entry=ENTRY)


# round 2 audit: residual bypasses
def test_r2_l2_01_a_duplicate_transaction_assertion_is_not_proof_of_not_sent():
    run_in_module_context(r"""
for (const text of ['assert_exception: duplicate transaction', 'Assert Exception: transaction already applied']) {
 const error = walletFailure({ success: false, error: text });
 if (!error.ambiguousBroadcast || error.broadcasted !== true) throw Error('a duplicate assertion must stay unknown: ' + text);
}
const plain = walletFailure({ success: false, error: 'Assert Exception: tx rejected' });
if (plain.ambiguousBroadcast) throw Error('an ordinary chain rejection is still a stated not-sent outcome');
""", entry=STATIC / "broadcast-gate.js")


def test_r2_l2_02_a_swap_still_waiting_for_the_wallet_when_the_page_ended_comes_back_as_unknown():
    run_in_module_context(r"""
const first = swapState('alice', 'WOOD');
Object.assign(first, { stage: 'signing', receipt: { kind: 'swap_sell', pay: 'WOOD', receive: 'DEC', nonce: 'abcdef0123456789', input: 10 } });
syncStoredIntent('alice', 'WOOD', first);
states.clear();
const again = swapState('alice', 'WOOD');
if (again.stage !== 'confirming' || again.receipt.unknownOutcome !== true) throw Error('a swap interrupted at the wallet prompt must be restored as unknown: ' + again.stage);
""", entry=ENTRY)


def test_r2_l2_04_a_request_without_a_transaction_id_is_not_judged_before_the_longest_expiration():
    from ceres import routes_op_preview
    assert routes_op_preview.NULL_ID_PROBE_SETTLE_SECONDS >= 3600 and routes_op_preview.TX_ABSENCE_GRACE_SECONDS >= 3600


def test_r2_l4_02_03_an_unknown_sps_transfer_is_not_offered_again_and_close_keeps_an_unsettled_swap():
    source = SOURCE
    assert "ambiguous = failure?.broadcasted !== false;" in source and "fundingUnknown: true" in source
    assert "an unsettled swap is never discarded by a Close" in source
    assert "if (name === 'done' && st.stage === 'done') {" in source


def test_r3_l2_01_02_03_funding_prompt_cross_tab_reservation_and_owner_discard():
    run_in_module_context(r"""
01: an Active transfer interrupted at the wallet prompt comes back as an unknown settlement, never as "send it again"
const f = swapState('alice', 'SPS');
Object.assign(f, { stage: 'funding-signing', receipt: { kind: 'swap_sell', pay: 'SPS', receive: 'DEC', nonce: 'abcdef0123456789', twoStep: true } });
syncStoredIntent('alice', 'SPS', f);
states.clear();
const back = swapState('alice', 'SPS');
if (back.stage !== 'settling' || back.receipt.fundingUnknown !== true) throw Error('funding prompt not restored as unknown: ' + back.stage);
// 02: the reservation written when the click starts is visible to another tab at once
localStorage.setItem('ceres:wallet-lp-intent:v1:alice:WOOD', JSON.stringify({ stage: 'signing', receipt: { kind: 'swap_sell', pay: 'WOOD', receive: 'DEC', reserved: true }, savedAt: Date.now() }));
states.clear();
const other = swapState('alice', 'WOOD');
if (other.stage !== 'confirming' || other.receipt.unknownOutcome !== true) throw Error('a second tab must see the reservation: ' + other.stage);
""", entry=ENTRY)


def test_r3_l2_03_only_the_owner_can_discard_an_unresolved_swap():
    source = SOURCE
    # there is no discard action; a successful covering miss releases only after Hive expiry plus grace.
    assert "name === 'discard'" not in source and "NEVER_SENT_AFTER_MS = 3900 * 1000" in source
    assert "seen === 'not_found' && Date.now() - Date.parse(st.receipt.asked_at || st.receipt.signed_at || '') >= NEVER_SENT_AFTER_MS" in source
    assert "INTENT_MAX_AGE_MS" not in source          # no time-based release
    assert "if (intentIsOpen(open)) { restoreIntent(ctx.player, symbol, st); return 'taken'; }" in source   # a swap click first looks at the shared record


# round 4 (A-01, A-02, A-04, A-03, A-06)
SOURCE_FUND = SOURCE.replace("await requestBroadcastThroughGate(ctx.player, st.funding.operations", "await globalThis.__gate(ctx.player, st.funding.operations").replace("if (!walletReady(ctx.player)) return;", "")


def _run_fund(js):
    run_in_module_context("(async () => {\n" + FLOW + js + "\n})().catch(error => { console.error(error && error.stack || error); process.exit(1); });", entry=ENTRY, source=SOURCE_FUND)


def test_r4_a02_no_wallet_prompt_when_the_swap_note_cannot_be_stored():
    _run_fund("""
let prompts = 0; globalThis.__gate = async () => { prompts += 1; return { txId: 'a'.repeat(40) }; };
const realSet = localStorage.setItem.bind(localStorage);
localStorage.setItem = (key, value) => { if (String(key).includes('wallet-lp-intent')) throw new Error('quota'); return realSet(key, value); };
await swapAction(ctx, 'swap', target);
if (prompts !== 0) throw Error('the wallet was opened without a stored swap note: ' + prompts);
if (st.stage !== 'edit' || st.storageBlocked !== true) throw Error('expected a blocked edit card, got ' + st.stage);
""")


def test_r4_a01_a_reservation_lost_to_another_tab_never_reaches_the_wallet():
    _run_fund("""
let prompts = 0; globalThis.__gate = async () => { prompts += 1; return { txId: 'a'.repeat(40) }; };
const realGet = localStorage.getItem.bind(localStorage);
let wrote = false;
const realSet = localStorage.setItem.bind(localStorage);
localStorage.setItem = (key, value) => { realSet(key, value); if (String(key).includes('wallet-lp-intent')) wrote = true; };
localStorage.getItem = (key) => {
 const value = realGet(key);
 if (wrote && String(key).includes('wallet-lp-intent') && value) return JSON.stringify({ ...JSON.parse(value), token: 'another-tab' }); // the other tab's token won the race
 return value;
};
await swapAction(ctx, 'swap', target);
if (prompts !== 0) throw Error('two tabs both reached the wallet: ' + prompts);
""")


def test_r4_a04_a_second_tab_does_not_send_the_funding_transfer_again():
    _run_fund("""
let prompts = 0; globalThis.__gate = async () => { prompts += 1; return { txId: 'b'.repeat(40) }; };
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ status: 'ready', operations: [['custom_json', { id: 'sm_sps', json: '{}' }]] }) });
Object.assign(st, { stage: 'funding', receipt: { kind: 'swap_sell', pay: 'WOOD', receive: 'DEC', twoStep: true, tx_id: 'c'.repeat(40), nonce: 'n1', payload: {} } });
localStorage.setItem('ceres:wallet-lp-intent:v1:alice:WOOD', JSON.stringify({ stage: 'funding-signing', receipt: st.receipt, token: 'other', savedAt: Date.now() }));
await swapAction(ctx, 'fund', target);
if (prompts !== 0) throw Error('the funding transfer was requested a second time: ' + prompts);
if (st.stage === 'edit') throw Error('the sale was dropped instead of restored');
""")


def test_r4_a03_a06_the_owner_can_name_a_swap_id_or_say_the_funding_was_not_sent():
    source = SOURCE
    assert "name === 'enter-id'" not in source and "prompt(" not in source   # the transaction is found automatically
    assert "name === 'funding-not-sent'" in source and "the SPS transfer was not sent" in source


def test_r5_a01_a_stale_verifier_never_funds_a_sale_another_tab_settled():
    _run_fund("""
let prompts = 0; globalThis.__gate = async () => { prompts += 1; return { txId: 'b'.repeat(40) }; };
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ status: 'ready', operations: [['custom_json', { id: 'sm_sps', json: '{}' }]] }) });
Object.assign(st, { stage: 'funding', token: 'mine', receipt: { kind: 'swap_sell', pay: 'WOOD', receive: 'DEC', twoStep: true, tx_id: 'c'.repeat(40), nonce: 'n1', payload: {} } });
localStorage.removeItem('ceres:wallet-lp-intent:v1:alice:WOOD'); // the other tab settled the sale and removed the record
await swapAction(ctx, 'fund', target);
if (prompts !== 0) throw Error('a stale result funded a settled sale: ' + prompts);
if (st.stage !== 'edit') throw Error('a settled sale must leave this tab on the card, got ' + st.stage);
""")


def test_r5_a01_a_late_result_never_removes_another_swaps_record():
    _run_fund("""
localStorage.setItem('ceres:wallet-lp-intent:v1:alice:WOOD', JSON.stringify({ stage: 'signing', token: 'newer', receipt: { kind: 'swap_sell', pay: 'WOOD', receive: 'DEC', reserved: true }, savedAt: Date.now() }));
Object.assign(st, { stage: 'edit', token: 'older', receipt: null });
if (syncStoredIntent('alice', 'WOOD', st) !== false) throw Error('a foreign record was touched');
if (!localStorage.getItem('ceres:wallet-lp-intent:v1:alice:WOOD')) throw Error('the newer swap record was removed by an older tab');
""")


def test_r5_a02_a03_a04_wrong_id_can_be_changed_drawer_redraws_and_regional_dec_reserves_under_a_lock():
    lp = ENTRY.read_text(encoding="utf-8")
    assert "Change the transaction id" not in lp and "post('find'" in lp
    assert "export function onIntentChange" in lp and "intentListener?.(match[1], match[2])" in lp
    dec = (STATIC / "regional-dec.js").read_text(encoding="utf-8")
    assert "withDecLock" in dec and "A DEC change from another tab is still unfinished" in dec and "onIntentChange(" in dec
