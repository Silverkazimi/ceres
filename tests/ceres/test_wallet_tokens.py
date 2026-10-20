"""Voucher and SPS get the wallet pool swap. Every payload is compared field for field
with synthetic transactions in the game's own shape."""
import json
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ceres import routes_wallet as wallet
from ceres import routes_wallet_lp as lp_routes
from ceres import wallet_lp_operations as lp
from ceres.app import create_app
from ceres.config import CeresConfig
from ceres.harvest_routing import Pool, RoutingError
from ceres.transaction_identity import app_identity
from .vm_context import STATIC, read_entry_source, run_in_module_context

# Synthetic transactions in the shape of the game's (sm_land_operation, Posting) -- app/n are the game client's own.
OWNER = {
    "dec_to_sps": ("dec798815095d4e9e40bda47880c9a1bcfa4858c",
                   {"op": "swap_tokens", "region_uid": "", "dec_amount": 10, "shares_out": 1.219, "max_slippage": 2.5, "resource_symbol": "SPS"}),
    "dec_to_voucher": ("91c8bb4867a21dc12a239dc0d33f2ed7301ee24d",
                       {"op": "swap_tokens", "region_uid": "", "dec_amount": 10, "shares_out": 0.251, "max_slippage": 2.5, "resource_symbol": "VOUCHER"}),
    "voucher_to_dec": ("f7d356a4f524b22bf0019b2f6d11721da2e5a747",
                       {"op": "swap_tokens", "region_uid": "", "resource_amount": 0.1, "shares_out": 3.23, "max_slippage": 2.5, "resource_symbol": "VOUCHER"}),
    "sps_to_dec": ("68e3dac9c9281037a5a4fd14cf78618e38064a0e",
                   {"op": "swap_tokens", "region_uid": "", "resource_amount": 1, "shares_out": 8.124, "max_slippage": 2.5, "resource_symbol": "SPS"}),
}
# Step 2 of the SPS sale: ACTIVE custom_json id sm_sps (tx 9f708b6a9b079312900596d03c866e88e2782974).
OWNER_TRANSFER = {"action": "token_transfer", "params": {"to": "player0015", "qty": 1, "token": "SPS", "memo": "lpsid:90001"}}
# What transactions/lookup returned for tx <tx id> (result.data).
OWNER_SPS_SALE_RESULT = {"pending_swap_id": 90001, "pending_external_amount": 1, "external_swap_required": True,
                         "external_swap_token": "SPS", "external_transfer_account": "player0015"}


@pytest.mark.parametrize("name,build", [
    ("dec_to_sps", lambda: lp.buy("player0005", "", "SPS", 10, "1.219", "2.5")),
    ("dec_to_voucher", lambda: lp.buy("player0005", "", "VOUCHER", 10, "0.251", "2.5")),
    ("voucher_to_dec", lambda: lp.sell("player0005", "", "VOUCHER", "0.1", "3.23", "2.5")),
    ("sps_to_dec", lambda: lp.sell("player0005", "", "SPS", 1, "8.124", "2.5")),
])
def test_posting_swap_matches_the_owners_game_transaction_field_for_field(name, build):
    _, game = OWNER[name]
    built = build()
    envelope = built["operations"][0][1]
    assert envelope["id"] == "sm_land_operation" and envelope["required_auths"] == [] and envelope["required_posting_auths"] == ["player0005"]
    ours = json.loads(envelope["json"])
    assert set(ours) == set(game) | {"app", "n"}  # the game client carries its own app/n the same way
    for key, value in game.items():
        assert ours[key] == value, key  # region_uid "" for both wallet tokens; amounts equal as numbers
    assert ours["app"] == app_identity()
    lp.validate_payload(ours)


def test_wallet_tokens_carry_no_region_and_land_resources_keep_theirs():
    with pytest.raises(RoutingError):
        lp.sell("player0005", "PR-CEF-65", "SPS", 1, 1, "2.5")
    with pytest.raises(RoutingError):
        lp.buy("player0005", "PR-CEF-65", "VOUCHER", 1, 1, "2.5")
    with pytest.raises(RoutingError):
        lp.sell("player0005", "", "GRAIN", 1, 1, "2.5")
    for soulbound in ("RESEARCH", "AURA"):
        with pytest.raises(RoutingError):
            lp.sell("player0005", "", soulbound, 1, 1, "2.5")


def _sale(result=OWNER_SPS_SALE_RESULT, amount=1):
    expected = lp.sell("player0005", "", "SPS", amount, "8.124", "2.5")["payload"]
    tx = "68e3dac9c9281037a5a4fd14cf78618e38064a0e"
    info = {"id": tx, "player": "player0005", "type": "land_operation", "data": json.dumps(expected),
            "success": True, "result": json.dumps({"data": result})}
    return info, tx, {"tx_id": tx, "nonce": expected["n"], "expected_payload": expected}


def test_sps_sale_step_two_is_the_owners_active_transfer_field_for_field():
    info, tx, body = _sale()
    ready = lp_routes.sps_funding_operation("player0005", info, tx, body)
    assert ready["status"] == "ready" and ready["authority"] == "Active" and ready["pending_swap_id"] == 90001
    [[kind, envelope]] = ready["operations"]
    assert kind == "custom_json" and envelope["id"] == "sm_sps"
    assert envelope["required_auths"] == ["player0005"] and envelope["required_posting_auths"] == []  # Active
    ours = json.loads(envelope["json"])
    assert ours["action"] == OWNER_TRANSFER["action"]
    assert set(ours["params"]) == set(OWNER_TRANSFER["params"]) | {"app", "n"}  # Ceres identity () + the gate's nonce
    for key, value in OWNER_TRANSFER["params"].items():
        assert ours["params"][key] == value, key
    # The page-memory recovery: the same lookup builds the same transfer again (same pending id, new nonce).
    again = lp_routes.sps_funding_operation("player0005", info, tx, body)
    assert json.loads(again["operations"][0][1]["json"])["params"]["memo"] == "lpsid:90001"


def test_sps_sale_step_two_refuses_anything_but_the_reviewed_sale():
    info, tx, body = _sale()
    assert lp_routes.sps_funding_operation("player0005", None, tx, body) == {"status": "not_found", "operations": None}
    for bad in ({**OWNER_SPS_SALE_RESULT, "external_transfer_account": "someoneelse"},
                {**OWNER_SPS_SALE_RESULT, "pending_external_amount": 2},
                {**OWNER_SPS_SALE_RESULT, "external_swap_required": False},
                {**OWNER_SPS_SALE_RESULT, "pending_swap_id": 0}):
        info, tx, body = _sale(bad)
        with pytest.raises(HTTPException):
            lp_routes.sps_funding_operation("player0005", info, tx, body)
    # A Voucher sale never asks for a transfer.
    expected = lp.sell("player0005", "", "VOUCHER", "0.1", "3.23", "2.5")["payload"]
    info = {"id": "c" * 40, "player": "player0005", "type": "land_operation", "data": json.dumps(expected), "success": True,
            "result": json.dumps({"data": {"dec_amount": 3.23}})}
    with pytest.raises(HTTPException):
        lp_routes.sps_funding_operation("player0005", info, "c" * 40, {"nonce": expected["n"], "expected_payload": expected})


def _pools():
    return {"GRAIN": Pool(1, "GRAIN", Decimal("1000000"), Decimal("100000"), Decimal("100")),
            "VOUCHER": Pool(34, "VOUCHER", Decimal("1000"), Decimal("36000"), Decimal("100")),
            "SPS": Pool(100, "SPS", Decimal("100000"), Decimal("816500"), Decimal("100"), True)}


def test_wallet_swap_section_lists_voucher_and_sps_with_their_own_fee():
    rows = [{"player": "player0005", "token": t, "balance": b} for t, b in (("DEC", 100), ("SPS", 5), ("VOUCHER", 2))]
    data = wallet.build_wallet("player0005", lambda p: rows, lambda p: {"status": "success", "data": []}, _pools)
    swap = data["swap"]
    assert swap["symbols"] == ["GRAIN", "VOUCHER", "SPS"]
    assert swap["account_symbols"] == ["VOUCHER", "SPS"] and swap["two_step_sell"] == ["SPS"]
    assert swap["fees"] == {"GRAIN": "10", "VOUCHER": "10", "SPS": "0.5"}  # the SPS pool takes 0.5 %
    assert set(swap["rates"]["SPS"]) == {"swap_sell", "swap_buy"}


def _fakes(monkeypatch):
    monkeypatch.setattr(lp_routes, "_pools", _pools)
    monkeypatch.setattr(lp_routes, "_balances", lambda p: [{"player": p, "token": t, "balance": b}
                                                           for t, b in (("DEC", 100), ("SPS", 5), ("VOUCHER", 2))])

    class Land:
        def __init__(self, **_):
            pass

        def regional_balances(self, player):
            return type("R", (), {"data": {"status": "success", "data": []}})()
    monkeypatch.setattr(lp_routes, "LandClient", Land)


@pytest.mark.parametrize("symbol,kind,amount,field", [
    ("VOUCHER", "swap_sell", "0.1", "resource_amount"), ("VOUCHER", "swap_buy", "10", "dec_amount"),
    ("SPS", "swap_sell", "1", "resource_amount"), ("SPS", "swap_buy", "10", "dec_amount"),
])
def test_quote_and_preview_both_directions_per_wallet_token(monkeypatch, tmp_path, symbol, kind, amount, field):
    _fakes(monkeypatch)
    app = create_app(CeresConfig(root=tmp_path, player="player0005", card_art_cache_enabled=False))
    with TestClient(app) as client:
        quote = client.post("/api/wallet/lp/quote", json={"kind": kind, "resource_symbol": symbol, "amount": amount}).json()
        assert quote["fee_percent"] == ("0.5" if symbol == "SPS" else "10")
        assert quote["two_step"] is (symbol == "SPS" and kind == "swap_sell")
        review = client.post("/api/wallet/lp/preview", json={"kind": kind, "resource_symbol": symbol, "region_uid": "", field: amount})
        assert review.status_code == 200, review.text
        data = review.json()
        payload = data["payload"]
        assert payload["region_uid"] == "" and payload["resource_symbol"] == symbol and payload[field] == float(amount)
        assert payload["shares_out"] == float(data["quote"]["output"]) and payload["max_slippage"] == 2.5
        held = {"SPS": Decimal(5), "VOUCHER": Decimal(2)}[symbol]
        spent, got = Decimal(data["quote"]["input"]), Decimal(data["quote"]["output"])
        after = held - spent if kind == "swap_sell" else held + got
        assert Decimal(data["projected"]["region_after"]) == after.quantize(Decimal("0.001"))
        # A region is refused for a wallet token; more than the wallet holds is refused.
        assert client.post("/api/wallet/lp/preview", json={"kind": kind, "resource_symbol": symbol, "region_uid": "PR-CEF-65", field: amount}).status_code == 422
    too_much = {"swap_sell": "6", "swap_buy": "101"}[kind]
    with TestClient(app) as client:
        assert client.post("/api/wallet/lp/preview", json={"kind": kind, "resource_symbol": symbol, "region_uid": "", field: too_much}).status_code == 422


def test_owner_sps_swaps_are_reproduced_by_the_pool_maths():
    # 1 SPS -> 8.124 DEC and 10 DEC -> 1.219 SPS in the same minute only fit a 0.5 % SPS pool fee
    # with 10 % the buy would have returned 0.997 SPS.
    from ceres import harvest_routing as routing
    pools = {"SPS": Pool(100, "SPS", Decimal("1000000"), Decimal("8165000"), Decimal("1"), True)}
    assert routing.swap_quote("SPS", "DEC", 1, pools, "2.5").output == Decimal("8.124")
    assert routing.swap_quote("DEC", "SPS", 10, pools, "2.5").output == Decimal("1.219")


CARD_JS = """
const wallet = { account: { DEC: 100, SPS: 5, SPSP: 0, VOUCHER: 2 },
 swap: { symbols: ['GRAIN', 'VOUCHER', 'SPS'], account_symbols: ['VOUCHER', 'SPS'], two_step_sell: ['SPS'], directions: ['swap_sell', 'swap_buy'],
 slippage: '2.5', signing_enabled: true, fees: { GRAIN: '10', VOUCHER: '10', SPS: '0.5' },
 rates: { SPS: { swap_sell: { unit: '8.124', inverse: '0.123' }, swap_buy: { unit: '0.122', inverse: '8.2' } },
 VOUCHER: { swap_sell: { unit: '32.3', inverse: '0.031' }, swap_buy: { unit: '0.025', inverse: '40' } } } },
 regions: [{ region_uid: 'PR-CEF-65', plots_owned: 3, balances: { GRAIN: 1, WOOD: 0, STONE: 0, IRON: 0, RESEARCH: 5, AURA: 5 } }] };
"""


def test_card_on_voucher_and_sps_tabs_has_no_region_and_no_two_step_copy():
    body_js = CARD_JS + """
for (const symbol of ['VOUCHER', 'SPS']) {
 const html = swapHtml(symbol, wallet, 'player0005', true);
 if (!html.includes(`data-lp-symbol="${symbol}"`) || !html.includes('data-dec="lp:flip"')) throw new Error(symbol + ' card: ' + html);
 if (html.includes('data-lp-region') || html.includes('ch-plot-badge')) throw new Error(symbol + ' must have no region: ' + html);
 if (!html.includes('in your wallet')) throw new Error(symbol + ' pays from the wallet: ' + html);
 // the game does an SPS sale in one click -- no "two signatures" / "Step 2" copy anywhere.
 if (/two signatures|Step 2|Active key/i.test(html)) throw new Error(symbol + ' two-step copy: ' + html);
}
swapState('player0005', 'SPS').direction = 'buy';
const buy = swapHtml('SPS', wallet, 'player0005', true);
if (!buy.includes('DEC</span>→<span>')) throw new Error('SPS buy: ' + buy);
for (const tab of ['RESEARCH', 'AURA', 'DEC']) if (swapHtml(tab, wallet, 'player0005', true) !== '') throw new Error(tab + ' never swaps');
// No SPS in the wallet: the card still offers the buy side only.
if (swapHtml('SPS', { ...wallet, account: { ...wallet.account, SPS: 0 } }, 'player0005', true).includes('lp:flip')) throw new Error('nothing to sell');
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-lp.js")


def test_one_swap_click_signs_the_sale_then_the_sps_transfer_and_a_declined_transfer_can_be_retried():
    """one "Swap SPS -> DEC" click builds the sale (no Review), opens the wallet for the Posting
 swap, reads the game's pending swap and opens the wallet again for the Active transfer -- no second click.
 A declined transfer leaves one "Send SPS to the pool" button (pending id in page memory), no error text.
 The wallet is a stub; nothing is broadcast."""
    source = read_entry_source(STATIC / "wallet-lp.js")
    for name in ("requestBroadcastThroughGate(ctx.player", ):
        assert source.count(name) == 2, name
    source = source.replace("requestBroadcastThroughGate(ctx.player", "__stubGate(ctx.player").replace("walletReady(ctx.player)", "true").replace("walletReady(player)", "true")
    extra = """
context.__stubGate = async (player, operations, onResponse, authority) => {
 context.__signed.push({ authority, json: JSON.parse(operations[0][1].json), id: operations[0][1].id });
 if (authority === 'Active' && context.__declineActive) { context.__declineActive = false; const e = new Error('user cancel'); e.broadcasted = false; throw e; }
 return { txId: (authority === 'Active' ? 'b' : 'a').repeat(40) };
};
context.__signed = [];
context.__declineActive = true;
"""
    posting = json.dumps(lp.sell("player0005", "", "SPS", 1, "8.124", "2.5", nonce="0123456789abcdef"))
    info = {"status": "confirmed", "game_result": OWNER_SPS_SALE_RESULT}
    funding = {"status": "ready", "authority": "Active", "pending_swap_id": 90001, "amount": "1.000",
               "operations": [["custom_json", {"id": "sm_sps", "json": json.dumps({"action": "token_transfer", "params": {**OWNER_TRANSFER["params"], "app": app_identity(), "n": "fedcba9876543210"}}),
                                                "required_auths": ["player0005"], "required_posting_auths": []}]]}
    body_js = CARD_JS + f"""
VERIFY_SCHEDULE_MS.length = 1;
const built = {posting};
const order = [];
globalThis.fetch = async (url, options) => {{
 const path = url.split('?')[0].split('/').pop(); const body = JSON.parse(options.body); order.push([path, body]);
 const reply = path === 'preview' ? {{ ...built, signing_enabled: true, quote: {{ kind: 'swap_sell', input: '1', output: '8.124', minimum: '7.920', pool_cost: '0.005', slippage: '2.5', fee_percent: '0.5', two_step: true }}, projected: {{ dec_after: '108.124', region_after: '4' }} }}
 : path === 'verify' ? {json.dumps(info)} : {json.dumps(funding)};
 return {{ ok: true, status: 200, json: async () => reply }};
}};
const st = swapState('player0005', 'SPS');
Object.assign(st, {{ direction: 'sell', amount: '1', quoted: '1', stage: 'edit', quote: {{ kind: 'swap_sell', input: '1', output: '8.124', minimum: '7.920', pool_cost: '0.005', fee_percent: '0.5', two_step: true }} }});
let signing = false;
const ctx = {{ player: 'player0005', account: () => 'player0005', drawer: () => null, wallet: () => wallet, render: () => {{}}, reload: async () => {{}}, setSigning: v => {{ signing = v; }} }};
const target = {{ closest: () => ({{ dataset: {{ lpSymbol: 'SPS' }} }}) }};
(async () => {{
 const card = swapHtml('SPS', wallet, 'player0005', true);
 if (!card.includes('data-dec="lp:swap"') || !card.includes('Swap 1.000 SPS → 8.124 DEC') || /two signatures|Step 2/.test(card)) throw new Error('one Swap button: ' + card);
 // ONE click.
 await swapAction(ctx, 'swap', target);
 for (let i = 0; i < 400 && !(st.stage === 'funding' && !signing); i++) await new Promise(r => setTimeout(r, 2)); // the click returns at once; the check loop runs on
 const paths = order.map(o => o[0]).join(',');
 if (paths !== 'preview,verify,sps-funding') throw new Error('requests in order: ' + paths);
 const preview = order[0][1];
 if (preview.sign !== true || preview.seen_output !== '8.124' || preview.region_uid !== '' || preview.resource_amount !== '1' || 'reviewed_payload' in preview) throw new Error('one-click build: ' + JSON.stringify(preview));
 if (__signed.length !== 2 || __signed[0].authority !== 'Posting' || __signed[1].authority !== 'Active') throw new Error('both wallet requests from one click: ' + JSON.stringify(__signed));
 if (__signed[0].json.resource_symbol !== 'SPS' || __signed[1].id !== 'sm_sps' || __signed[1].json.params.memo !== 'lpsid:90001') throw new Error('ops: ' + JSON.stringify(__signed));
 // The Active prompt was declined: one retry button, no error text, pending swap kept in memory.
 if (st.stage !== 'funding' || !st.funding || st.funding.pending_swap_id !== 90001 || signing) throw new Error('declined: ' + st.stage);
 let retry = swapHtml('SPS', wallet, 'player0005', true);
 if ((retry.match(/data-dec="lp:fund"/g) || []).length !== 1 || !retry.includes('Send SPS to the pool') || /error|failed|rejected|cancel|two signatures|Step 2/i.test(retry)) throw new Error('retry card: ' + retry);
 await swapAction(ctx, 'fund', target);
 if (order.filter(o => o[0] === 'sps-funding').length !== 1) throw new Error('the kept transfer is signed again, not rebuilt');
 // (stuck states): the sale is not "done" at the transfer -- it waits for the pool to settle, no quote shown.
 if (__signed.length !== 3 || __signed[2].authority !== 'Active' || st.stage !== 'settling' || st.receipt.funding_tx !== 'b'.repeat(40) || st.receipt.output !== null) throw new Error('retry settling: ' + st.stage);
 if (!swapHtml('SPS', wallet, 'player0005', true).includes('Waiting for the pool to settle')) throw new Error('receipt');
 process.exit(0); // the settlement loop keeps real timers alive; the checks are done
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-lp.js", source=source, extra_context_js=extra)


def test_a_moved_quote_or_a_declined_wallet_leaves_the_card_ready_without_text():
    """a 409 (quote moved past the shown figure) or a declined Posting prompt returns to the card with the
 Swap button ready again; the only visible change is a fresh "You receive"."""
    source = read_entry_source(STATIC / "wallet-lp.js").replace("requestBroadcastThroughGate(ctx.player", "__stubGate(ctx.player").replace("walletReady(ctx.player)", "true").replace("walletReady(player)", "true")
    extra = """
context.__stubGate = async () => { const e = new Error('user cancel'); e.broadcasted = false; throw e; };
"""
    posting = json.dumps(lp.sell("player0005", "", "VOUCHER", "0.1", "3.23", "2.5", nonce="0123456789abcdef"))
    body_js = CARD_JS + f"""
let mode = 'moved';
const built = {posting};
globalThis.fetch = async (url) => {{
 const path = url.split('?')[0].split('/').pop();
 if (path === 'preview' && mode === 'moved') return {{ ok: false, status: 409, json: async () => ({{ detail: 'moved' }}) }};
 const q = {{ kind: 'swap_sell', resource_symbol: 'VOUCHER', input: '0.1', output: '3.100', minimum: '3.022', pool_cost: '0.01', fee_percent: '10', two_step: false }};
 return {{ ok: true, status: 200, json: async () => path === 'quote' ? q : {{ ...built, signing_enabled: true, quote: q }} }};
}};
const st = swapState('player0005', 'VOUCHER');
Object.assign(st, {{ direction: 'sell', amount: '0.1', quoted: '0.1', stage: 'edit', quote: {{ kind: 'swap_sell', resource_symbol: 'VOUCHER', input: '0.1', output: '3.230', minimum: '3.149', pool_cost: '0.01' }} }});
const ctx = {{ player: 'player0005', account: () => 'player0005', drawer: () => null, wallet: () => wallet, render: () => {{}}, reload: async () => {{}}, setSigning: () => {{}} }};
const target = {{ closest: () => ({{ dataset: {{ lpSymbol: 'VOUCHER' }} }}) }};
(async () => {{
 await swapAction(ctx, 'swap', target);
 await new Promise(r => setTimeout(r, 20));
 if (st.stage !== 'edit' || st.quote.output !== '3.100') throw new Error('moved: stays on the card with a fresh figure ' + st.stage + ' ' + st.quote.output);
 let html = swapHtml('VOUCHER', wallet, 'player0005', true);
 if (/moved|error|failed/i.test(html) || !html.includes('3.100 DEC')) throw new Error('moved card: ' + html);
 mode = 'ok';
 await swapAction(ctx, 'swap', target);
 if (st.stage !== 'edit' || st.receipt) throw new Error('declined wallet: back to the card ' + st.stage);
 html = swapHtml('VOUCHER', wallet, 'player0005', true);
 if (/data-dec="lp:swap"[^>]*disabled/.test(html) || /cancel|error|failed/i.test(html)) throw new Error('Swap ready again: ' + html);
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-lp.js", source=source, extra_context_js=extra)


def test_the_games_nested_answer_confirms_and_builds_the_sps_transfer():
    # The maintainer's SPS sale <tx id> came back as {"success", "result": {"success", "error", "data"}} -- one level deeper
    # than the tests assumed, so every swap stayed "pending" forever and the Active transfer was never asked for.
    from ceres import routes_wallet_lp as lp
    tx = "8ea5e5e51b0a29a44965cbcd07998170cc818cf1"
    payload = {"op": "swap_tokens", "region_uid": "", "resource_amount": 1.0, "shares_out": 8.115, "max_slippage": 2.5,
               "resource_symbol": "SPS", "app": "ceres/0.9.933", "n": "5a5614c286d9fe86"}
    data = {"resource": "SPS", "resource_amount": 1, "dec_amount": 8.115, "region_uid": "", "pending_swap_id": 90002,
            "pending_external_amount": 1, "pending_external_fee_amount": 0.005, "pending_external_swap_amount": 0.995,
            "external_swap_required": True, "external_swap_token": "SPS", "external_swap_player": "player0005",
            "external_transfer_account": "player0015", "to_player": "player0005", "region_name": None}
    info = {"id": tx, "type": "land_operation", "player": "player0005", "data": json.dumps(payload), "success": True,
            "result": json.dumps({"success": True, "result": {"success": True, "error": "", "data": data}})}
    body = {"nonce": payload["n"], "kind": "swap_sell", "expected_payload": payload}
    assert lp._verify(info, "player0005", tx, body) == {"status": "confirmed", "game_result": data}
    step2 = lp.sps_funding_operation("player0005", info, tx, {"nonce": payload["n"], "expected_payload": payload})
    assert step2["status"] == "ready" and step2["authority"] == "Active"
    assert json.loads(step2["operations"][0][1]["json"])["params"]["memo"] == "lpsid:90002"
    refused = {**info, "result": json.dumps({"success": True, "result": {"success": False, "error": "Pool busy", "data": None}})}
    assert lp._verify(refused, "player0005", tx, body)["status"] == "failed"


def test_the_active_sps_transfer_is_one_keychain_sign_and_send_request():
    # Reproduced in the browser: a sign-only Active request waited behind the swap in Ceres' send queue and the maintainer's
    # Keychain showed no prompt. The game asks Keychain to sign AND send; so does Ceres now.
    lp = (STATIC / "wallet-lp.js").read_text(encoding="utf-8")
    gate = (STATIC / "broadcast-gate.js").read_text(encoding="utf-8")
    assert "}, 'Active', { direct: true });" in lp
    assert "if (!walletCanSignTransaction(account) || metadata?.direct === true) {" in gate
