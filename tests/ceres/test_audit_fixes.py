""": the rest of the  bug-audit fixes (findings #3 to #9 and the /find window).

#3 a pending SPS deposit is never "settled" before the bridge confirms; #4 an SPS funding transfer whose wallet answer was lost is
found in the player's own history by its memo; #5 a refund closes a swap only with proof of payment; /find says whether its
window reaches back to the request; #6 automatic planned-card placement re-checks the slot after its read; #7 a malformed problem
code is refused; #8 saved-plan reasons are local wording; #9 malformed review input is a 422 before any upstream read."""
import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ceres import harvest_routing_bridge as bridge, routes_wallet_lp as lp_routes, wallet_lp_operations as operations
from ceres.app import create_app
from ceres.config import CeresConfig
from tests.ceres.vm_context import read_entry_source, run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"
PLAYER = "player0005"


def _app(tmp_path):
    return create_app(CeresConfig(root=tmp_path, player=PLAYER, wallet_lp_signing_enabled=True, card_art_cache_enabled=False))


def _info(tx, payload, effects):
    return {"id": tx, "player": PLAYER, "type": "land_operation", "data": json.dumps(payload), "success": True,
            "result": json.dumps({"success": True, "result": {"success": True, "data": effects}})}


def _funding_row(index, tx, memo, qty, to="player0016", author=PLAYER, timestamp="2026-10-14T08:04:48"):
    payload = {"action": "token_transfer", "params": {"to": to, "qty": qty, "token": "SPS", "memo": memo, "n": "1" * 16}}
    return [index, {"trx_id": tx, "op_in_trx": 0, "timestamp": timestamp,
                    "op": ["custom_json", {"id": "sm_sps", "json": json.dumps(payload), "required_auths": [author], "required_posting_auths": []}]}]


# #3 pending SPS deposit

def _deposit(monkeypatch, settle_result):
    expected = operations.add(PLAYER, "", "SPS", 2, 16)["payload"]
    tx = "c" * 40
    effects = {"pending_deposit_id": 777, "pending_external_amount": 2, "external_transfer_account": "player0016", "resource": "SPS"}
    monkeypatch.setattr(lp_routes, "lookup_trx_info", lambda getter, trx: _info(tx, expected, effects))
    seen = []
    def settle(player, step, older=False):
        seen.append(step)
        return settle_result
    monkeypatch.setattr(bridge, "settle", settle)
    return {"tx_id": tx, "nonce": expected["n"], "kind": "add", "expected_payload": expected}, seen


def test_pending_sps_deposit_is_not_settled_before_the_bridge(monkeypatch, tmp_path):
    body, seen = _deposit(monkeypatch, {"state": "settling", "bridge_next_start": None})
    with TestClient(_app(tmp_path)) as client:
        answer = client.post("/api/wallet/lp/settle", json={**body, "funding_tx": "d" * 40}).json()
    assert answer["status"] == "settling"
    assert len(seen) == 1 and seen[0]["kind"] == "liquidity" and seen[0]["funding"]["state"] == "funded"


def test_pending_sps_deposit_settles_only_with_the_bridge_receipt(monkeypatch, tmp_path):
    receipt = {"transaction_id": "c" * 40, "bridge_transaction_id": "e" * 40, "received": [],
               "deposited": [{"symbol": "SPS", "amount": "2"}, {"symbol": "DEC", "amount": "16"}]}
    body, _ = _deposit(monkeypatch, {"state": "confirmed", "bridge_transaction_id": "e" * 40, "receipt": receipt})
    with TestClient(_app(tmp_path)) as client:
        answer = client.post("/api/wallet/lp/settle", json={**body, "funding_tx": "d" * 40}).json()
    assert answer["status"] == "settled" and answer["deposited"] == receipt["deposited"]


def test_plain_deposit_without_pending_id_is_still_settled_at_once(monkeypatch, tmp_path):
    expected = operations.add(PLAYER, "PR-CEF-65", "GRAIN", 1, 1)["payload"]
    monkeypatch.setattr(lp_routes, "lookup_trx_info", lambda getter, trx: _info("c" * 40, expected, {"resource_amount": 1, "dec_amount": 1}))
    monkeypatch.setattr(bridge, "settle", lambda *a, **k: pytest.fail("no bridge read for an in-game deposit"))
    with TestClient(_app(tmp_path)) as client:
        answer = client.post("/api/wallet/lp/settle", json={"tx_id": "c" * 40, "nonce": expected["n"], "kind": "add", "expected_payload": expected}).json()
    assert answer == {"status": "settled", "received": []}


POOL_FLOW = r"""
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...rest) => realSetTimeout(fn, 0, ...rest);
const wallet = { account: { DEC: '100', SPS: '600' }, swap: { symbols: ['SPS'], directions: ['swap_sell'], signing_enabled: true, rates: {}, fees: {}, two_step_sell: ['SPS'] }, regions: [] };
const ctx = { player: 'alice', account: () => 'alice', drawer: () => null, wallet: () => wallet, render: () => {}, setSigning: () => {}, reload: async () => {} };
const target = { closest: () => ({ dataset: { lpSymbol: 'SPS' } }) };
const st = swapState('alice', 'SPS');
"""


def _run_wallet(js, source=None):
    body = "(async () => {\n" + POOL_FLOW + js + "\n})().catch(error => { console.error(error && error.stack || error); process.exit(1); });"
    run_in_module_context(body, entry=STATIC / "wallet-lp.js", **({"source": source} if source else {}))


def test_card_reports_only_settlement_figures_never_the_quote():
    _run_wallet(r"""
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ status: 'settled', received: [] }) });
Object.assign(st, { stage: 'settling', receipt: { kind: 'add', symbol: 'SPS', resource: 2, dec: 16, nonce: 'ab'.repeat(8), tx_id: 'a'.repeat(40), payload: {}, funding_tx: 'c'.repeat(40), funding_at: new Date().toISOString() } });
await swapAction(ctx, 'settle', target);
if (st.receipt.reported) throw Error('an empty settlement was reported as the quoted figures');
""")


# #4 funding transfer with a lost wallet answer

def _sale(monkeypatch, rows, settle_result=None):
    expected = operations.sell(PLAYER, "", "SPS", "600.392", 1, 2.5)["payload"]
    tx = "a" * 40
    effects = {"external_swap_required": True, "pending_swap_id": 115303, "pending_external_amount": 600.392,
               "external_transfer_account": "player0016", "external_swap_token": "SPS"}
    monkeypatch.setattr(lp_routes, "lookup_trx_info", lambda getter, trx: _info(tx, expected, effects))
    monkeypatch.setattr(bridge, "_history", lambda account, start: rows)
    seen = []
    def settle(player, step, older=False):
        seen.append(step)
        return settle_result or {"state": "settling", "bridge_next_start": None}
    monkeypatch.setattr(bridge, "settle", settle)
    return {"tx_id": tx, "nonce": expected["n"], "kind": "swap_sell", "expected_payload": expected, "funding_tx": None}, seen


def test_lost_funding_answer_is_found_in_own_history_by_memo(monkeypatch, tmp_path):
    rows = [_funding_row(5, "f" * 40, "lpsid:115303", 600.392)]
    body, seen = _sale(monkeypatch, rows)
    with TestClient(_app(tmp_path)) as client:
        answer = client.post("/api/wallet/lp/settle", json=body).json()
    assert answer["funding_tx"] == "f" * 40 and answer["status"] == "settling"
    assert seen and seen[0]["funding"]["state"] == "funded"


def test_lost_funding_not_in_history_stays_settling_without_a_bridge_read(monkeypatch, tmp_path):
    rows = [_funding_row(5, "f" * 40, "lpsid:115304", 600.392),          # another swap
            _funding_row(6, "e" * 40, "lpsid:115303", 1),                # wrong amount
            _funding_row(7, "d" * 40, "lpsid:115303", 600.392, to="someone"),
            _funding_row(8, "b" * 40, "lpsid:115303", 600.392, author="player0009")]
    body, seen = _sale(monkeypatch, rows)
    with TestClient(_app(tmp_path)) as client:
        answer = client.post("/api/wallet/lp/settle", json=body).json()
    assert answer == {"status": "settling", "funding": "not_found"} and not seen


def test_card_attaches_a_funding_transfer_found_by_the_server():
    _run_wallet(r"""
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ status: 'settling', funding_tx: 'f'.repeat(40) }) });
Object.assign(st, { stage: 'settling', receipt: { kind: 'swap_sell', pay: 'SPS', receive: 'DEC', input: 600, nonce: 'ab'.repeat(8), tx_id: 'a'.repeat(40), payload: {}, fundingUnknown: true } });
await swapAction(ctx, 'settle', target);
await swapAction(ctx, 'settle-stop', target);
if (st.receipt.funding_tx !== 'f'.repeat(40) || st.receipt.fundingUnknown) throw Error('found funding not attached: ' + JSON.stringify(st.receipt));
const stored = JSON.parse(localStorage.getItem('ceres:wallet-lp-intent:v1:alice:SPS'));
if (stored.receipt.funding_tx !== 'f'.repeat(40)) throw Error('durable intent lacks the found transfer');
""")


def test_late_funding_answer_reaches_the_durable_intent():
    source = read_entry_source(STATIC / "wallet-lp.js") \
        .replace("await requestBroadcastThroughGate(ctx.player, st.funding.operations", "await globalThis.__fundGate(ctx.player, st.funding.operations") \
        .replace("    if (!walletReady(ctx.player)) return;\n    if (ctx.account()", "    if (false) return;\n    if (ctx.account()")
    _run_wallet(r"""
let late = null, prompts = 0;
globalThis.fetch = async (url) => {
  const path = String(url);
  if (path.includes('/sps-funding')) return { ok: true, status: 200, json: async () => ({ status: 'ready', operations: [['custom_json', { id: 'sm_sps', json: '{}' }]] }) };
  return { ok: true, status: 200, json: async () => ({ status: 'settling' }) };
};
globalThis.__fundGate = async (player, ops, onResponse) => { prompts += 1; late = onResponse; const e = new Error('lost'); e.broadcasted = undefined; throw e; };
Object.assign(st, { stage: 'funding', token: 'tok', receipt: { kind: 'swap_sell', pay: 'SPS', receive: 'DEC', input: 600, nonce: 'ab'.repeat(8), tx_id: 'a'.repeat(40), payload: {}, twoStep: true } });
syncStoredIntent('alice', 'SPS', st);
await swapAction(ctx, 'fund', target);
if (st.stage !== 'settling' || !st.receipt.fundingUnknown) throw Error('ambiguous funding must settle-only: ' + st.stage);
late({ result: { id: 'e'.repeat(40) } });
st.settleStopped = true; st.settleWake?.();
if (st.receipt.funding_tx !== 'e'.repeat(40) || st.receipt.fundingUnknown) throw Error('late answer lost: ' + JSON.stringify(st.receipt));
if (JSON.parse(localStorage.getItem('ceres:wallet-lp-intent:v1:alice:SPS')).receipt.funding_tx !== 'e'.repeat(40)) throw Error('not durable');
if (prompts !== 1) throw Error('a second transfer was requested');
""", source=source)


# #5 refund only with proof of payment

def _swap_step():
    effects = {"external_swap_required": True, "pending_swap_id": 115303, "pending_external_amount": 2,
               "external_transfer_account": "player0016", "external_swap_token": "SPS", "external_swap_player": PLAYER}
    return lp_routes.settlement_step("a" * 40, "swap_sell", "SPS", effects, True, None)


def _bridge_row(index, tx, op_id, payload):
    return [index, {"trx_id": tx, "op_in_trx": 0, "timestamp": "2026-10-14T08:05:39",
                    "op": ["custom_json", {"id": op_id, "json": json.dumps(payload), "required_auths": ["player0016"], "required_posting_auths": []}]}]


def _refund(qty):
    return _bridge_row(2, "e" * 40, "sm_token_transfer", {"to": PLAYER, "qty": qty, "memo": "LP swap refund 115303", "token": "SPS"})


_SWAP_OP = {"op": "external_swap", "player": PLAYER, "pending_swap_id": "115303", "external_token_amount": 2}


def _game_says_incomplete(monkeypatch):
    class Transport:
        def __init__(self, **_):
            pass
        def get_json(self, url):
            envelope = {"success": True, "result": {"success": True, "data": {"pending_swap_id": 115303, "external_swap_completed": False}}}
            return type("R", (), {"data": {"trx_info": {"id": "d" * 40, "player": "player0016", "type": "land_operation", "data": json.dumps(_SWAP_OP),
                                                        "success": True, "result": json.dumps(envelope)}}})()
    monkeypatch.setattr(bridge, "FixedGetTransport", Transport)


def test_partial_refund_without_a_game_verdict_keeps_settling(monkeypatch):
    monkeypatch.setattr(bridge, "_history", lambda account, start: [_refund(0.001)])
    assert bridge.settle(PLAYER, _swap_step())["state"] == "settling"


def test_full_refund_closes_the_swap(monkeypatch):
    monkeypatch.setattr(bridge, "_history", lambda account, start: [_refund(2)])
    settled = bridge.settle(PLAYER, _swap_step())
    assert settled["state"] == "refunded" and settled["full"] is True and settled["refunded"] == [{"symbol": "SPS", "amount": "2"}]
    assert lp_routes.settlement_view(settled)["full"] is True


def test_incomplete_swap_without_its_refund_transfer_is_not_refunded(monkeypatch):
    _game_says_incomplete(monkeypatch)
    monkeypatch.setattr(bridge, "_history", lambda account, start: [_bridge_row(1, "d" * 40, "sm_land_operation", _SWAP_OP)])
    settled = bridge.settle(PLAYER, _swap_step())
    assert settled["state"] == "settling" and settled["refund_pending"] is True
    assert lp_routes.settlement_view(settled)["refund_pending"] is True


def test_incomplete_swap_with_a_partial_refund_is_refunded_but_not_full(monkeypatch):
    _game_says_incomplete(monkeypatch)
    monkeypatch.setattr(bridge, "_history", lambda account, start: [_refund(1.5), _bridge_row(1, "d" * 40, "sm_land_operation", _SWAP_OP)])
    settled = bridge.settle(PLAYER, _swap_step())
    assert settled["state"] == "refunded" and settled["full"] is False and settled["refunded"][0]["amount"] == "1.5"


def test_card_says_nothing_was_lost_only_for_a_full_refund():
    _run_wallet(r"""
for (const [full, expectLost] of [[false, false], [true, true]]) {
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ status: 'refunded', full, expected: '2', refunded: [{ symbol: 'SPS', amount: full ? '2' : '1.5' }] }) });
  Object.assign(st, { stage: 'settling', receipt: { kind: 'swap_sell', pay: 'SPS', receive: 'DEC', input: 2, nonce: 'ab'.repeat(8), tx_id: 'a'.repeat(40), payload: {}, funding_tx: 'c'.repeat(40) } });
  await swapAction(ctx, 'settle', target);
  if (st.stage !== 'edit') throw Error('refund did not close');
  if (st.lpNote.includes('Nothing was lost') !== expectLost) throw Error('wrong note for full=' + full + ': ' + st.lpNote);
}
""")


# find window

def test_history_covers_only_when_the_window_reaches_back():
    rows = [[500, {"timestamp": "2026-10-14T10:00:00"}], [599, {"timestamp": "2026-10-14T10:30:00"}]]
    assert lp_routes.history_covers(rows, "2026-10-14T10:05:00Z") is True
    assert lp_routes.history_covers(rows, "2026-10-14T09:59:00.000Z") is False
    assert lp_routes.history_covers(rows, None) is False
    assert lp_routes.history_covers([[0, {"timestamp": "2026-10-14T10:20:00"}]], "2026-10-14T09:00:00Z") is True  # whole history read
    assert lp_routes.history_covers([], "2026-10-14T09:00:00Z") is False


def test_find_route_reports_coverage(monkeypatch, tmp_path):
    rows = [[500, {"timestamp": "2026-10-14T10:00:00", "op": ["vote", {}], "trx_id": "0" * 40}]]
    monkeypatch.setattr(bridge, "_history", lambda account, start: rows)
    with TestClient(_app(tmp_path)) as client:
        short = client.post("/api/wallet/lp/find", json={"nonce": "ab" * 8, "since": "2026-10-14T09:00:00.000Z"}).json()
        long = client.post("/api/wallet/lp/find", json={"nonce": "ab" * 8, "since": "2026-10-14T11:00:00.000Z"}).json()
        bad = client.post("/api/wallet/lp/find", json={"nonce": "ab" * 8, "since": ["x"]})
    assert short == {"status": "not_found", "covered": False} and long == {"status": "not_found", "covered": True}
    assert bad.status_code == 422


def test_short_window_never_releases_an_unanswered_swap():
    _run_wallet(r"""
  const old = new Date(Date.now() - 10 * 60000).toISOString();
let bodies = [];
for (const covered of [false, true]) {
  globalThis.fetch = async (url, init) => {
    if (String(url).includes('/find')) { bodies.push(JSON.parse(init.body)); if (bodies.length > 3 && !covered) await swapAction(ctx, 'stop', { closest: () => ({ dataset: { lpSymbol: 'WOOD' } }) }); return { ok: true, status: 200, json: async () => ({ status: 'not_found', covered }) }; }
    throw Error('unexpected ' + url);
  };
  const w = swapState('alice', 'WOOD');
  Object.assign(w, { stage: 'confirming', stopped: false, failed: null, receipt: { kind: 'swap_sell', pay: 'WOOD', receive: 'DEC', input: 1, nonce: 'cd'.repeat(8), tx_id: null, payload: {}, signed_at: old, asked_at: old } });
  bodies = [];
  await swapAction(ctx, 'verify', { closest: () => ({ dataset: { lpSymbol: 'WOOD' } }) });
  if (bodies[0].since !== old) throw Error('find did not send since');
  if (!covered && (w.stage !== 'confirming' || !w.historyUnreadable)) throw Error('short window released the swap: ' + w.stage);
  if (covered && w.stage !== 'confirming') throw Error('a covered history miss before Hive expiry released the request: ' + w.stage);
}
""")


# #6 automatic placement re-checks after its read

def test_planned_card_placement_rechecks_the_slot_after_the_read():
    text = (STATIC / "combine-chain-ui.js").read_text()
    body = text[text.index("async function stageBackPlannedCard"):]
    body = body[:body.index("\n}\n")]
    after = body[body.index("await fetch("):]
    assert "CURRENT_PLAYER !== player || serial !== chainRefreshSerial" in after
    assert "entry.deed_uid === source.deed_uid && entry.group_id === 'worker' && entry.slot_index === source.slot_index" in after
    assert "editorGroups(liveRow)" in after and after.index("editorGroups(liveRow)") < after.index("stageForSlot(")


# #7 malformed problem code

def test_malformed_problem_code_is_refused_and_derivation_never_throws():
    run_in_module_context(r"""
const plan = { version: 2, state: 'planned', card: {}, expected: {}, origin: {},
  inputs: [{ uid: 'a', cc: 1, status: 'active', kind: 'free' }, { uid: 'b', cc: 1, status: 'active', kind: 'free' }], problem: { code: 7 } };
if (validChainPlan(plan) !== false) throw Error('numeric problem code accepted');
const inner = { ...plan, problem: null, inputs: [{ ...plan.inputs[0], problem: { code: {} } }, plan.inputs[1]] };
if (validChainPlan(inner) !== false) throw Error('input problem code accepted');
deriveChain(plan);
if (validChainPlan({ ...plan, problem: { code: 'combine_mismatch' } }) !== true) throw Error('valid plan refused');
""", entry=STATIC / "combine-chain.js")


# #8 saved-plan reasons are local wording

def test_saved_plan_reasons_never_show_server_prose():
    run_in_module_context(r"""
const cases = [['card_unavailable', 'arbitrary server wording'], ['weird_code', null], ['card_unavailable', 'delegated to <b>x</b> and more'], ['card_unavailable', 'locked until soon-ish']];
for (const [reason, detail] of cases) {
  const text = dropReasonText(reason, detail);
  if (/arbitrary|weird_code|<b>|soon-ish/.test(text)) throw Error('server text shown: ' + text);
}
if (dropReasonText('card_unavailable', 'listed on market') !== 'this card is listed on the market now') throw Error('listed');
if (dropReasonText('card_unavailable', 'rented (season)') !== 'this card is rented out now') throw Error('rented');
if (dropReasonText('card_unavailable', 'locked until 2026-11-01T00:00:00Z') !== 'this card is locked until 2026-11-01T00:00:00Z') throw Error('locked');
""", entry=STATIC / "stage-plans.js")
    assert "DROP_TEXT[item.reason] || item.reason" not in (STATIC / "stage-plans.js").read_text()


# #9 malformed review input

@pytest.mark.parametrize("extra", [{"seen": [1]}, {"seen": {"resource_amount": [1], "dec_amount": "1"}}, {"seen_output": {"x": 1}},
                                   {"reviewed_payload": "x"}, {"resource_amount": True}, {"region_uid": 5}])
def test_malformed_review_input_is_422_before_any_upstream_read(monkeypatch, tmp_path, extra):
    calls = []
    monkeypatch.setattr(lp_routes, "_fresh_state", lambda player: calls.append(player) or ({}, [], []))
    monkeypatch.setattr(lp_routes, "_position", lambda *a: calls.append(a) or (Decimal(1), Decimal(0)))
    body = {"kind": "remove", "region_uid": "PR-CEF-65", "resource_symbol": "GRAIN", "shares_out": "0.5", "sign": True, **extra}
    with TestClient(_app(tmp_path)) as client:
        answer = client.post("/api/wallet/lp/preview", json=body)
    assert answer.status_code == 422 and calls == []
