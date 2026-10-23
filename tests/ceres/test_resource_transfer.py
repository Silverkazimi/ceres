""": player-to-player land-resource transfer (the wallet's Transfer tab beside Swap).

The maintainer's proven game transaction (player0005 -> player0004, <tx id>, 2026-10-13, Posting) is the region-to-region
pool swap with from_symbol = to_symbol and `to_player`. Every builder test compares field for field with it; every
upstream read is a fake (no network, nothing is signed or broadcast)."""
import json
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ceres import harvest_routing as routing
from ceres import harvest_routing_operations as harvest_operations
from ceres import routes_wallet_lp as lp_routes
from ceres import routes_wallet_transfer as transfer_routes
from ceres import wallet_lp_operations as operations
from ceres.app import create_app
from ceres.config import CeresConfig
from ceres.harvest_routing import Pool, RoutingError
from ceres.transaction_identity import app_identity
from .vm_context import STATIC, read_entry_source, run_in_module_context

OWNER_TX = "226b1bb3e762785f7d7602b057c05834afc8c2bc"
OWNER_PAYLOAD = {"op": "swap_tokens", "max_slippage": 2.5, "from_region_uid": "PR-CEF-65", "to_region_uid": "PR-BRL-41",
                 "in_amount": 10, "out_amount_1": 0.081, "out_amount_2": 9.062, "from_symbol": "GRAIN", "to_symbol": "GRAIN",
                 "to_player": "player0004", "notify_transport": 1, "app": "splinterlands/0.7.60", "n": "C7HzJy18ZR"}
# The game's answer to that transaction (/transactions/lookup, read by id 2026-10-15).
OWNER_RESULT = {"success": True, "result": {"success": True, "error": "", "balanceHistory": [{"player": "player0005", "token": "DEC", "balance_end": 3852.521}],
                "data": {"resource": "GRAIN", "resource_amount": 8.949, "dec_amount": 0, "region_uid": "PR-BRL-41", "region_name": "Pandon Reach",
                         "to_player": "player0004", "external_withdrawal_id": None, "external_withdrawal_required": False,
                         "external_withdrawal_token": None, "external_withdrawal_amount": None, "external_withdrawal_player": None,
                         "external_transfer_account": None}}}
NONCE = "0123456789abcdef"


def _owner_build(**overrides):
    args = dict(player="player0005", from_region_uid="PR-CEF-65", to_player="player0004", to_region_uid="PR-BRL-41", symbol="GRAIN",
                in_amount=10, out_amount_1="0.081", out_amount_2="9.062", slippage="2.5", nonce=NONCE)
    args.update(overrides)
    return operations.transfer(**args)


def test_transfer_builder_matches_the_owner_transaction_field_for_field():
    built = _owner_build()
    payload = built["payload"]
    # Ceres adds only its own app/n; notify_transport is a game-client notification flag the same-player swap never sends.
    game = {key: value for key, value in OWNER_PAYLOAD.items() if key not in {"app", "n", "notify_transport"}}
    assert {key: value for key, value in payload.items() if key not in {"app", "n"}} == game
    assert payload["app"] == app_identity() and payload["n"] == NONCE
    assert built["authority"] == "Posting"
    [[kind, envelope]] = built["operations"]
    assert kind == "custom_json" and envelope["id"] == "sm_land_operation"
    assert envelope["required_auths"] == [] and envelope["required_posting_auths"] == ["player0005"]
    assert json.loads(envelope["json"]) == payload
    operations.validate_payload(payload)


def test_transfer_shape_is_refused_when_it_is_not_one_resource_to_another_player():
    with pytest.raises(RoutingError):
        _owner_build(to_player="player0005")  # to yourself
    with pytest.raises(RoutingError):
        _owner_build(to_player="Not A Name")
    with pytest.raises(RoutingError):
        _owner_build(symbol="VOUCHER")
    payload = _owner_build()["payload"]
    for wrong in ({**payload, "to_symbol": "WOOD"}, {**payload, "to_player": "x"}, {**payload, "extra": 1}, {**payload, "to_region_uid": "PR-BRL-0"}):
        with pytest.raises(RoutingError):
            operations.validate_payload(wrong)
    # a same-player two-resource swap still validates and still needs two different resources
    same = {key: value for key, value in payload.items() if key != "to_player"}
    with pytest.raises(RoutingError):
        operations.validate_payload(same)
    operations.validate_payload({**same, "to_symbol": "WOOD"})


def test_routing_builder_is_unchanged_by_the_shared_field_builder():
    step = {"kind": "swap", "source_region": "PR-CEF-65", "destination_region": "PR-BRL-41",
            "quote": {"source_symbol": "GRAIN", "destination_symbol": "WOOD", "input": "10", "intermediate_dec": "0.081", "output": "1.5"}}
    payload = harvest_operations.build("player0005", step, "2.5")["payload"]
    assert list(payload) == ["app", "n", "op", "max_slippage", "from_symbol", "to_symbol", "from_region_uid", "to_region_uid",
                             "in_amount", "out_amount_1", "out_amount_2"]
    assert {key: payload[key] for key in list(payload)[2:]} == {
        "op": "swap_tokens", "max_slippage": 2.5, "from_symbol": "GRAIN", "to_symbol": "WOOD", "from_region_uid": "PR-CEF-65",
        "to_region_uid": "PR-BRL-41", "in_amount": 10.0, "out_amount_1": 0.081, "out_amount_2": 1.5}
    assert "to_player" not in payload


POOLS = {"GRAIN": Pool(1, "GRAIN", Decimal("1000000"), Decimal("8000"), Decimal("1000"))}


def test_quote_is_the_routing_quote_with_two_five_percent_legs():
    view = transfer_routes.transfer_view("GRAIN", "10", POOLS)
    quote = routing.swap_quote("GRAIN", "GRAIN", "10", POOLS, "2.5")
    assert view["input"] == "10" and view["output"] == routing.text(quote.output) and view["minimum"] == routing.text(quote.minimum)
    assert view["intermediate_dec"] == routing.text(quote.intermediate_dec)
    one, two = view["legs"]
    assert (one["pay"], one["receive"], one["fee_percent"], one["fee"], one["fee_symbol"]) == ("GRAIN", "DEC", "5", "0.500", "GRAIN")
    assert (two["pay"], two["receive"], two["fee_percent"], two["fee_symbol"]) == ("DEC", "GRAIN", "5", "DEC")
    assert two["fee"] == routing.text(routing.floor(quote.intermediate_dec * Decimal("0.05")))
    assert view["fee_percent"] == "10" and view["slippage"] == "2.5"
    # the maintainer's figures: 10 Grain in -> about 9.06 out at a 10 % total fee
    assert Decimal("8.9") < Decimal(view["output"]) < Decimal("9.1")
    with pytest.raises(RoutingError):
        transfer_routes.transfer_view("VOUCHER", "1", POOLS)


def _regions(rows):
    return {"status": "success", "data": [{"uid": uid, "name": name, "plots_owned": plots, "grain": grain, "wood": 0, "stone": 0, "iron": 0, "research": 0, "aura": 0}
                                          for uid, name, plots, grain in rows]}


def test_recipient_check_is_three_reads_and_names_each_failure(monkeypatch):
    calls = []
    def hive(name): calls.append("hive"); return name != "nohive"
    def game(name): calls.append("game"); return name != "nogame"
    def regions(name):
        calls.append("regions")
        return _regions([] if name == "noland" else [("PR-BRL-41", "041 - Pandon Reach", 0, 8.949), ("PR-CEF-65", "065 - Mandar Camden", 25, 0)])
    monkeypatch.setattr(transfer_routes, "_hive_exists", hive)
    monkeypatch.setattr(transfer_routes, "_splinterlands_exists", game)
    monkeypatch.setattr(transfer_routes, "_regions", regions)
    found = transfer_routes.read_recipient("player0004")
    assert sorted(calls) == ["game", "hive", "regions"]
    assert found["status"] == "ok" and [row["region_uid"] for row in found["regions"]] == ["PR-CEF-65", "PR-BRL-41"]  # most plots first
    assert "grain" not in json.dumps(found).lower()  # the recipient's balances are not shown
    assert transfer_routes.read_recipient("nohive")["status"] == "no_hive_account"
    assert transfer_routes.read_recipient("nogame")["status"] == "no_splinterlands_account"
    # no land -> every Praetoria region from the static list, no extra read
    calls.clear()
    noland = transfer_routes.read_recipient("noland")
    assert sorted(calls) == ["game", "hive", "regions"]
    assert noland["status"] == "ok" and noland["no_land"] is True and len(noland["regions"]) == 143
    assert noland["regions"][0] == {"region_uid": "PR-PNW-1", "name": "001 - Viritas", "plots_owned": 0}
    assert {"region_uid": "PR-BRL-41", "name": "041 - Pandon Reach", "plots_owned": 0} in noland["regions"]


def _app(monkeypatch, tmp_path, *, signing=True, pools=None):
    calls = {"pools": 0, "regions": []}
    def fake_pools():
        calls["pools"] += 1
        return pools or POOLS
    def regions(name):
        calls["regions"].append(name)
        if name == "player0005":
            return _regions([("PR-CEF-65", "065 - Mandar Camden", 101, 50)])
        return _regions([("PR-BRL-41", "041 - Pandon Reach", 0, 8.949)])
    monkeypatch.setattr(transfer_routes, "_pools", fake_pools)
    monkeypatch.setattr(transfer_routes, "_regions", regions)
    monkeypatch.setattr(transfer_routes, "_hive_exists", lambda name: True)
    monkeypatch.setattr(transfer_routes, "_splinterlands_exists", lambda name: True)
    app = create_app(CeresConfig(root=tmp_path, player="player0005", wallet_lp_signing_enabled=signing, card_art_cache_enabled=False))
    return app, calls


BODY = {"resource_symbol": "GRAIN", "from_region_uid": "PR-CEF-65", "to_player": "player0004", "to_region_uid": "PR-BRL-41", "in_amount": "10"}


def test_routes_read_on_demand_with_counted_calls_and_sign_needs_the_review(monkeypatch, tmp_path):
    app, calls = _app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        who = client.post("/api/wallet/transfer/recipient", json={"to_player": "@player0004"}).json()
        assert who["status"] == "ok" and who["account"] == "player0004" and who["upstream_calls"] == {"hive_account": 1, "player_details": 1, "regions": 1}
        assert client.post("/api/wallet/transfer/recipient", json={"to_player": "player0005"}).status_code == 422  # yourself
        quote = client.post("/api/wallet/transfer/quote", json={"resource_symbol": "GRAIN", "amount": "10"}).json()
        assert quote["upstream_calls"] == {"pools": 1} and calls["pools"] == 1 and len(quote["legs"]) == 2
        review = client.post("/api/wallet/transfer/preview", json=BODY).json()
        assert calls["pools"] == 2 and calls["regions"][-2:] in (["player0005", "player0004"], ["player0004", "player0005"])
        assert review["kind"] == "transfer" and review["authority"] == "Posting" and review["to_player"] == "player0004"
        assert review["payload"]["to_player"] == "player0004" and review["payload"]["from_symbol"] == review["payload"]["to_symbol"] == "GRAIN"
        assert review["projected"] == {"region_after": "40.000"}
        assert client.post("/api/wallet/transfer/preview", json={**BODY, "sign": True}).status_code == 422  # never without the review
        signed = client.post("/api/wallet/transfer/preview", json={**BODY, "sign": True, "reviewed_payload": review["payload"]}).json()
        assert signed["nonce"] == review["nonce"] and signed["payload"] == review["payload"]
        # another recipient than the reviewed one is refused
        other = client.post("/api/wallet/transfer/preview", json={**BODY, "to_player": "someoneelse", "sign": True, "reviewed_payload": review["payload"]})
        assert other.status_code == 409
        # the recipient has no land in that region; more than the region holds
        assert client.post("/api/wallet/transfer/preview", json={**BODY, "to_region_uid": "PR-CEF-65"}).status_code == 422
        assert client.post("/api/wallet/transfer/preview", json={**BODY, "in_amount": "51"}).status_code == 422


def test_a_recipient_without_land_may_receive_in_any_existing_region(monkeypatch, tmp_path):
    app, calls = _app(monkeypatch, tmp_path)
    monkeypatch.setattr(transfer_routes, "_regions", lambda name: _regions([("PR-CEF-65", "065 - Mandar Camden", 101, 50)]) if name == "player0005" else _regions([]))
    with TestClient(app) as client:
        who = client.post("/api/wallet/transfer/recipient", json={"to_player": "player0004"}).json()
        assert who["no_land"] is True and len(who["regions"]) == 143
        review = client.post("/api/wallet/transfer/preview", json={**BODY, "to_region_uid": "PR-SHC-143"})
        assert review.status_code == 200 and review.json()["payload"]["to_region_uid"] == "PR-SHC-143"
        assert client.post("/api/wallet/transfer/preview", json={**BODY, "to_region_uid": "PR-SHC-144"}).status_code == 422  # does not exist
        assert client.post("/api/wallet/transfer/preview", json={**BODY, "to_region_uid": "PR-BRL-143"}).status_code == 422  # wrong territory


def test_sign_is_refused_when_the_pool_moved_past_the_reviewed_slippage(monkeypatch, tmp_path):
    app, _ = _app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        review = client.post("/api/wallet/transfer/preview", json=BODY).json()
    moved = {"GRAIN": Pool(1, "GRAIN", Decimal("1300000"), Decimal("8000"), Decimal("1000"))}  # Grain 30 % cheaper
    app, _ = _app(monkeypatch, tmp_path, pools=moved)
    with TestClient(app) as client:
        response = client.post("/api/wallet/transfer/preview", json={**BODY, "sign": True, "reviewed_payload": review["payload"]})
        assert response.status_code == 409


def test_sign_keeps_both_reviewed_output_floors_when_the_quote_moves_within_tolerance(monkeypatch, tmp_path):
    app, _ = _app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        review = client.post("/api/wallet/transfer/preview", json=BODY).json()
    moved = {"GRAIN": Pool(1, "GRAIN", Decimal("1000000"), Decimal("7800"), Decimal("1000"))}
    monkeypatch.setattr(transfer_routes, "_pools", lambda: moved)
    with TestClient(app) as client:
        signed = client.post("/api/wallet/transfer/preview", json={**BODY, "sign": True,
                                   "reviewed_payload": review["payload"]})
    assert signed.status_code == 200
    result = signed.json()
    payload = result["payload"]
    assert Decimal(result["quote"]["output"]) != Decimal(review["quote"]["output"])
    displayed_minimum = Decimal(review["quote"]["minimum"])
    # Before the fix, Sign used this fresh expected output and the game applied
    # max_slippage to it again, dropping below the review's displayed minimum.
    old_sign_floor = routing.floor(Decimal(result["quote"]["output"]) * Decimal("0.975"))
    assert old_sign_floor < displayed_minimum
    signed_final_floor = routing.floor(Decimal(str(payload["out_amount_2"])) * Decimal("0.975"))
    assert signed_final_floor == displayed_minimum
    for field in ("out_amount_1", "out_amount_2"):
        assert payload[field] == review["payload"][field]


def test_signing_switched_off_still_reviews_but_never_builds_for_the_wallet(monkeypatch, tmp_path):
    app, _ = _app(monkeypatch, tmp_path, signing=False)
    with TestClient(app) as client:
        review = client.post("/api/wallet/transfer/preview", json=BODY).json()
        assert review["signing_enabled"] is False
        assert client.post("/api/wallet/transfer/preview", json={**BODY, "sign": True, "reviewed_payload": review["payload"]}).status_code == 409


def _info(payload, *, success=True, result=OWNER_RESULT, error=None):
    return {"id": "c" * 40, "player": "player0005", "type": "land_operation", "data": json.dumps(payload), "success": success,
            "error": error, "result": json.dumps(result) if result is not None else None}


def test_the_swap_verify_confirms_a_transfer_with_the_amount_the_recipient_got():
    payload = _owner_build()["payload"]
    body = {"nonce": NONCE, "kind": "transfer", "expected_payload": payload}
    verdict = lp_routes._verify(_info(payload), "player0005", "c" * 40, body)
    assert verdict["status"] == "confirmed" and verdict["game_result"]["resource_amount"] == 8.949
    # the game's refusal is reported verbatim
    refusal = "Insufficient GRAIN balance in region PR-CEF-65"
    assert lp_routes._verify(_info(payload, success=False, result=None, error=refusal), "player0005", "c" * 40, body) == {"status": "failed", "message": refusal}
    inner = {"success": True, "result": {"success": False, "error": "Slippage exceeded"}}
    assert lp_routes._verify(_info(payload, result=inner), "player0005", "c" * 40, body)["message"] == "Slippage exceeded"
    # a transfer is never confirmed as a same-player swap, nor a swap as a transfer
    with pytest.raises(HTTPException):
        lp_routes._verify(_info(payload), "player0005", "c" * 40, {**body, "kind": "swap_resource"})
    same = operations.resource_swap("player0005", "PR-CEF-65", "PR-BRL-41", "GRAIN", "WOOD", 10, "0.081", "1.5", "2.5", nonce=NONCE)["payload"]
    with pytest.raises(HTTPException):
        lp_routes._verify(_info(same), "player0005", "c" * 40, {"nonce": NONCE, "kind": "transfer", "expected_payload": same})


CARD_JS = """
const wallet = { account: { DEC: 12, SPS: 0, SPSP: 0, VOUCHER: 0 },
  swap: { symbols: ['GRAIN', 'WOOD', 'STONE', 'IRON'], directions: ['swap_sell', 'swap_buy'], slippage: '2.5', signing_enabled: true },
  regions: [{ region_uid: 'PR-CEF-65', plots_owned: 101, balances: { GRAIN: 50, WOOD: 0, STONE: 0, IRON: 0, RESEARCH: 0, AURA: 0 } }] };
const visible = html => html.replace(/<[^>]*>/g, ' ');
const QUOTE = { resource_symbol: 'GRAIN', input: '10', intermediate_dec: '0.080', output: '9.030', minimum: '8.804', slippage: '2.5', fee_percent: '10',
  legs: [{ pay: 'GRAIN', receive: 'DEC', fee_percent: '5', fee: '0.500', fee_symbol: 'GRAIN' }, { pay: 'DEC', receive: 'GRAIN', fee_percent: '5', fee: '0.004', fee_symbol: 'DEC' }] };
"""


def test_transfer_tab_renders_empty_found_quoted_review_and_every_outcome_colour():
    body_js = CARD_JS + """
const swap = swapHtml('GRAIN', wallet, 'player0005');
if (!swap.includes('class="ceres-tabs wallet-swap-tabs" role="tablist"') || !swap.includes('data-dec="lp:tab:transfer"') || !swap.includes('data-dec="lp:tab:swap"') || !/lp:tab:swap"[^>]*role="tab" aria-selected="true"/.test(swap)) throw new Error('Swap | Transfer tabs: ' + swap);
for (const tab of ['VOUCHER', 'SPS', 'DEC', 'WOOD']) if (swapHtml(tab, wallet, 'player0005').includes('lp:tab:transfer')) throw new Error(tab + ' has no transfer');
const st = swapState('player0005', 'GRAIN');
st.tab = 'transfer';
let html = swapHtml('GRAIN', wallet, 'player0005');
if (!html.includes('data-lp-tab="transfer"') || !html.includes('To player') || !html.includes('data-lp-transfer-to') || !html.includes('You send')) throw new Error('empty form: ' + html);
if (!/data-dec="lp:transfer-find"[^>]*disabled/.test(html) || !/data-dec="lp:transfer-review"[^>]*disabled/.test(html) || !html.includes('Find the player first')) throw new Error('nothing can be reviewed yet: ' + html);
st.xfer.to = 'player0004';
st.xfer.recipient = { status: 'ok', account: 'player0004', regions: [{ region_uid: 'PR-BRL-41', name: '041 - Pandon Reach', plots_owned: 0 }, { region_uid: 'PR-CEF-65', name: '065 - Mandar Camden', plots_owned: 3 }] };
st.xfer.toRegion = 'PR-BRL-41';
st.xfer.amount = '10'; st.xfer.quoted = '10'; st.xfer.quote = QUOTE;
html = swapHtml('GRAIN', wallet, 'player0005');
for (const text of ['✓ @player0004 receives in', 'Region 41', 'data-lp-transfer-region', 'Fee Grain → DEC · 5%', '0.500 Grain', 'Fee DEC → Grain · 5%', '0.004 DEC',
                    'Total fee', '10%', 'At least · slippage 2.5%', '8.804 Grain', '9.030', 'Review transfer of 10.000 Grain'])
  if (!html.includes(text)) throw new Error('quoted form lacks ' + text + ': ' + html);
if (/data-dec="lp:transfer-review"[^>]*disabled/.test(html)) throw new Error('Review is ready');
if (/PR-[A-Z]{3}-\\d/.test(visible(html))) throw new Error('no region uid on screen: ' + visible(html));
for (const [status, text] of [['no_hive_account', 'no Hive account'], ['no_splinterlands_account', 'no Splinterlands account'], ['invalid', 'cannot receive a transfer']]) {
  st.xfer.recipient = { status, account: 'player0004' };
  const card = swapHtml('GRAIN', wallet, 'player0005');
  if (!card.includes(text) || !card.includes('is-danger') || !/lp:transfer-review"[^>]*disabled/.test(card)) throw new Error(status + ': ' + card);
}
// a recipient with no land picks any region, and the card says they own no land there.
st.xfer.recipient = { status: 'ok', account: 'player0004', no_land: true, regions: [{ region_uid: 'PR-PNW-1', name: '001 - Viritas', plots_owned: 0 }, { region_uid: 'PR-BRL-41', name: '041 - Pandon Reach', plots_owned: 0 }] };
html = swapHtml('GRAIN', wallet, 'player0005');
if (!html.includes('They own no land in this region.') || !html.includes('Region 1 · Viritas') || /lp:transfer-review"[^>]*disabled/.test(html)) throw new Error('no land: ' + html);
st.xfer.recipient = { status: 'ok', account: 'player0004', regions: [{ region_uid: 'PR-BRL-41', name: '041 - Pandon Reach', plots_owned: 0 }] };
if (swapHtml('GRAIN', wallet, 'player0005').includes('own no land')) throw new Error('land maintainer gets no note');
const payload = { op: 'swap_tokens', from_region_uid: 'PR-CEF-65', to_region_uid: 'PR-BRL-41', to_player: 'player0004', from_symbol: 'GRAIN', to_symbol: 'GRAIN' };
st.xfer.review = { quote: QUOTE, payload, to_player: 'player0004', projected: { region_after: '40' }, nonce: 'n' };
html = swapHtml('GRAIN', wallet, 'player0005');
for (const text of ['Review transfer', 'You send', '10.000 Grain', '@player0004', 'into their', 'cannot be undone', 'data-dec="lp:transfer-edit"', 'data-dec="lp:transfer-sign"', 'Region 65 Grain after', '40.000'])
  if (!html.includes(text)) throw new Error('review lacks ' + text + ': ' + html);
if (!html.includes('data-lp-stage="review"') || /PR-[A-Z]{3}-\\d/.test(visible(html))) throw new Error('review stage: ' + html);
// Outcomes: amber while the game has not answered, green when confirmed, red with the game's own reason when refused.
st.stage = 'confirming'; st.checking = true;
st.receipt = { kind: 'transfer', pay: 'GRAIN', receive: 'GRAIN', region: 'PR-CEF-65', to_player: 'player0004', to_region: 'PR-BRL-41', input: 10, output: null, estimate: 9.03, nonce: 'abc', tx_id: 'c'.repeat(40) };
html = swapHtml('GRAIN', wallet, 'player0005');
if (!html.includes('is-waiting') || !html.includes('Sending') || !html.includes('≈ 9.030') || !html.includes('Confirming with the game') || html.includes('lp:transfer-sign')) throw new Error('amber: ' + html);
st.checking = false; st.stage = 'done'; st.receipt.output = 8.949;
html = swapHtml('GRAIN', wallet, 'player0005');
if (!html.includes('is-confirmed') || !html.includes('Transfer confirmed') || !html.includes('8.949') || !html.includes('@player0004') || !html.includes('Region 41')) throw new Error('green: ' + html);
st.stage = 'refused'; st.refusal = 'Insufficient GRAIN balance <b>x</b>';
html = swapHtml('GRAIN', wallet, 'player0005');
if (!html.includes('is-failed') || !html.includes('The game refused this transfer') || !html.includes('Insufficient GRAIN balance &lt;b&gt;x&lt;/b&gt;') || !html.includes('Close')) throw new Error('red: ' + html);
if (/PR-[A-Z]{3}-\\d/.test(visible(html))) throw new Error('no uid in the receipt: ' + visible(html));
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-lp.js")


def test_review_then_sign_uses_the_swap_signing_and_confirm_path_and_a_refusal_turns_red():
    source = read_entry_source(STATIC / "wallet-lp.js").replace("requestBroadcastThroughGate(ctx.player", "__stubGate(ctx.player").replace("walletReady(ctx.player)", "true").replace("walletReady(player)", "true")
    extra = """
context.__stubGate = async (player, operations, onResponse, authority) => { context.__signed.push({ authority, json: JSON.parse(operations[0][1].json) }); return { txId: 'c'.repeat(40) }; };
context.__signed = [];
"""
    built = _owner_build()
    review = {**built, "kind": "transfer", "signing_enabled": True, "to_player": "player0004", "projected": {"region_after": "40"},
              "quote": {"resource_symbol": "GRAIN", "input": "10", "intermediate_dec": "0.081", "output": "9.062", "minimum": "8.835", "slippage": "2.5",
                        "fee_percent": "10", "legs": []}}
    body_js = CARD_JS + f"""
VERIFY_SCHEDULE_MS.length = 1;
const review = {json.dumps(review)};
const order = [];
let verdict = {{ status: 'failed', message: 'Slippage exceeded: received less than the minimum' }};
globalThis.fetch = async (url, options) => {{
  const path = url.split('?')[0]; const body = JSON.parse(options.body); order.push([path, body]);
  const reply = path === '/api/wallet/transfer/preview' ? review : path === '/api/wallet/lp/verify' ? verdict : {{}};
  return {{ ok: true, status: 200, json: async () => reply }};
}};
const st = swapState('player0005', 'GRAIN');
Object.assign(st, {{ tab: 'transfer' }});
Object.assign(st.xfer, {{ to: 'player0004', recipient: {{ status: 'ok', account: 'player0004', regions: [{{ region_uid: 'PR-BRL-41', name: '041 - Pandon Reach', plots_owned: 0 }}] }},
  toRegion: 'PR-BRL-41', fromRegion: 'PR-CEF-65', amount: '10', quoted: '10', quote: QUOTE }});
let signing = false;
const ctx = {{ player: 'player0005', account: () => 'player0005', drawer: () => null, wallet: () => wallet, render: () => {{}}, reload: async () => {{}}, setSigning: v => {{ signing = v; }} }};
const target = {{ closest: () => ({{ dataset: {{ lpSymbol: 'GRAIN' }} }}) }};
(async () => {{
  await swapAction(ctx, 'transfer-review', target);
  if (order.length !== 1 || order[0][0] !== '/api/wallet/transfer/preview' || order[0][1].sign !== false || order[0][1].to_player !== 'player0004' || order[0][1].in_amount !== '10') throw new Error('review read: ' + JSON.stringify(order));
  if (!st.xfer.review || __signed.length) throw new Error('Review signs nothing');
  await swapAction(ctx, 'transfer-sign', target);
  for (let i = 0; i < 400 && st.stage !== 'refused'; i++) await new Promise(r => setTimeout(r, 2));
  const sign = order[1];
  if (sign[0] !== '/api/wallet/transfer/preview' || sign[1].sign !== true || sign[1].reviewed_payload.n !== review.nonce) throw new Error('sign build: ' + JSON.stringify(sign));
  if (__signed.length !== 1 || __signed[0].authority !== 'Posting' || __signed[0].json.to_player !== 'player0004') throw new Error('one Posting request: ' + JSON.stringify(__signed));
  const check = order[2];
  if (check[0] !== '/api/wallet/lp/verify' || check[1].kind !== 'transfer' || check[1].tx_id !== 'c'.repeat(40) || check[1].expected_payload.to_player !== 'player0004') throw new Error('confirm: ' + JSON.stringify(check));
  if (st.stage !== 'refused' || st.refusal !== 'Slippage exceeded: received less than the minimum') throw new Error('refused: ' + st.stage);
  const red = swapHtml('GRAIN', wallet, 'player0005');
  if (!red.includes('is-failed') || !red.includes('Slippage exceeded: received less than the minimum')) throw new Error('red card: ' + red);
  // Close returns to the Transfer tab with the player kept and the amount cleared; nothing is signed again.
  await swapAction(ctx, 'done', target);
  if (st.stage !== 'edit' || st.tab !== 'transfer' || st.xfer.recipient?.account !== 'player0004' || st.xfer.amount !== '' || st.xfer.review) throw new Error('closed: ' + JSON.stringify(st.xfer));
  if (__signed.length !== 1) throw new Error('never signed again');
  // A confirmed transfer ends green with the amount the game reported.
  verdict = {{ status: 'confirmed', game_result: {json.dumps(OWNER_RESULT["result"]["data"])} }};
  Object.assign(st.xfer, {{ amount: '10', quoted: '10', quote: QUOTE }});
  await swapAction(ctx, 'transfer-review', target);
  await swapAction(ctx, 'transfer-sign', target);
  for (let i = 0; i < 400 && st.stage !== 'done'; i++) await new Promise(r => setTimeout(r, 2));
  if (st.stage !== 'done' || st.receipt.output !== 8.949) throw new Error('green: ' + st.stage + ' ' + JSON.stringify(st.receipt));
  if (!swapHtml('GRAIN', wallet, 'player0005').includes('Transfer confirmed')) throw new Error('green card');
  process.exit(0);
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-lp.js", source=source, extra_context_js=extra)


def test_an_unknown_wallet_outcome_never_offers_the_transfer_again():
    """ S3: no transaction id and not provably unsent -> the card stays on its receipt (amber), no Sign button."""
    source = read_entry_source(STATIC / "wallet-lp.js").replace("requestBroadcastThroughGate(ctx.player", "__stubGate(ctx.player").replace("walletReady(ctx.player)", "true").replace("walletReady(player)", "true")
    extra = """
context.__stubGate = async () => { const e = new Error('timeout'); throw e; };
"""
    built = _owner_build()
    review = {**built, "kind": "transfer", "signing_enabled": True, "to_player": "player0004", "projected": {"region_after": "40"},
              "quote": {"resource_symbol": "GRAIN", "input": "10", "intermediate_dec": "0.081", "output": "9.062", "minimum": "8.835", "slippage": "2.5", "fee_percent": "10", "legs": []}}
    body_js = CARD_JS + f"""
const review = {json.dumps(review)};
const paths = [];
globalThis.fetch = async (url) => {{
  const path = url.split('?')[0]; paths.push(path);
  if (path === '/api/wallet/lp/find') return {{ ok: false, status: 502, json: async () => ({{}}) }};
  return {{ ok: true, status: 200, json: async () => review }};
}};
const st = swapState('player0005', 'GRAIN');
st.tab = 'transfer';
Object.assign(st.xfer, {{ to: 'player0004', recipient: {{ status: 'ok', account: 'player0004', regions: [{{ region_uid: 'PR-BRL-41', name: '', plots_owned: 0 }}] }}, toRegion: 'PR-BRL-41', fromRegion: 'PR-CEF-65', review }});
const ctx = {{ player: 'player0005', account: () => 'player0005', drawer: () => null, wallet: () => wallet, render: () => {{}}, reload: async () => {{}}, setSigning: () => {{}} }};
const target = {{ closest: () => ({{ dataset: {{ lpSymbol: 'GRAIN' }} }}) }};
(async () => {{
  await swapAction(ctx, 'transfer-sign', target);
  await new Promise(r => setTimeout(r, 20));
  if (st.stage !== 'confirming' || !st.receipt.unknownOutcome) throw new Error('unknown outcome stays open: ' + st.stage);
  const html = swapHtml('GRAIN', wallet, 'player0005');
  if (html.includes('lp:transfer-sign') || html.includes('lp:tab:') || !html.includes('is-waiting')) throw new Error('no second signature: ' + html);
  await swapAction(ctx, 'transfer-sign', target);
  if (paths.filter(p => p === '/api/wallet/transfer/preview').length !== 1) throw new Error('never built again: ' + paths);
  process.exit(0);
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-lp.js", source=source, extra_context_js=extra)


def test_regional_dec_binds_the_transfer_fields_and_css_uses_tokens_only():
    regional = (STATIC / "regional-dec.js").read_text(encoding="utf-8")
    assert "transferInput(swapContext(), event.target)" in regional and "transferSelect(swapContext(), event.target)" in regional
    css = (STATIC / "wallet.css").read_text(encoding="utf-8")
    block = css[css.index(".wallet-transfer"):]
    import re
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|\bwhite\b", block)
