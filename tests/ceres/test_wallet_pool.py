""": the position card under the swap on Grain, Wood, Stone, Iron, Voucher and
SPS, never on DEC, Research or Aura. Withdraw follows the game's own client maths (Manage view, shares_out = percent/100,
10 % early-withdrawal fee on shares still inside 30 days). The wallet is a stub; nothing is broadcast."""
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from ceres import routes_wallet as wallet
from ceres import routes_wallet_lp as lp_routes
from ceres import wallet_lp_operations as lp
from ceres.app import create_app
from ceres.config import CeresConfig
from ceres.harvest_routing import Pool, RoutingError
from .vm_context import STATIC, read_entry_source, run_in_module_context

POOLS = {"GRAIN": Pool(1, "GRAIN", Decimal("1000"), Decimal("500"), Decimal("100")),
         "VOUCHER": Pool(34, "VOUCHER", Decimal("10"), Decimal("400"), Decimal("50")),
         "SPS": Pool(100, "SPS", Decimal("100"), Decimal("800"), Decimal("200"), True)}


def _rows(*pairs):
    return [{"player": "player0005", "token": token, "balance": balance} for token, balance in pairs]


def test_wallet_carries_every_pool_position_from_the_balances_read_it_already_makes():
    data = wallet.build_wallet("player0005", lambda p: _rows(("DEC", 10), ("VOUCHER", 2), ("DEC-GRAIN", 25), ("DEC-B", 0)),
                               lambda p: {"status": "success", "data": []}, lambda: POOLS)
    pool = data["pool"]
    assert pool["symbols"] == ["GRAIN", "VOUCHER", "SPS"] and pool["account_symbols"] == ["VOUCHER", "SPS"] and pool["external"] == ["SPS"]
    assert pool["positions"]["GRAIN"] == {"shares": "25.000", "share_percent": "25", "resource_amount": "250.000", "dec_amount": "125.000"}
    assert pool["positions"]["SPS"]["shares"] == "0.000"
    assert pool["reserves"]["GRAIN"] == {"resource": "1000.000", "dec": "500.000", "shares": "100.000"}
    # A failed balances read is "unavailable", never a zero position.
    def broken(player):
        raise OSError("down")
    assert wallet.build_wallet("player0005", broken, lambda p: {"status": "success", "data": []}, lambda: POOLS)["pool"]["positions"] is None


def test_position_row_reads_the_games_vesting_row_and_null_is_zero():
    payload = {"status": "success", "data": {"single": [{"player": "player0005", "token": "DEC-GRAIN", "balance": 8953.124},
                                                         {"player": "player0005", "token": "VESTING-GRAIN", "balance": "1000.5"}]}}
    assert lp_routes.position_row(payload, "GRAIN") == (Decimal("8953.124"), Decimal("1000.5"))
    assert lp_routes.position_row({"status": "success", "data": {"single": [{"token": "VESTING-VOUCHER", "balance": None}]}}, "VOUCHER") == (0, 0)
    with pytest.raises(RoutingError):
        lp_routes.position_row({"status": "error"}, "GRAIN")


def test_withdraw_view_matches_the_games_client_maths():
    pool = POOLS["GRAIN"]
    free = lp_routes.withdraw_view(pool, Decimal(20), Decimal(0), "0.5")    # 10 of 100 shares: 100 GRAIN + 50 DEC
    assert (free["resource_amount"], free["dec_amount"], free["early_fee"], free["fraction"]) == ("100.000", "50.000", False, "0.5")
    locked = lp_routes.withdraw_view(pool, Decimal(20), Decimal(20), "0.5")  # nothing unlocked: x 0.9
    assert (locked["resource_amount"], locked["dec_amount"], locked["fee_percent"]) == ("90.000", "45.000", "10")
    part = lp_routes.withdraw_view(pool, Decimal(20), Decimal(15), "0.5")    # 5 unlocked of 10 taken: 1 - 0.5 x 0.1
    assert (part["resource_amount"], part["fee_percent"]) == ("95.000", "5")
    with pytest.raises(RoutingError):
        lp_routes.withdraw_view(pool, Decimal(0), Decimal(0), "0.5")
    with pytest.raises(RoutingError):
        lp_routes.withdraw_view(pool, Decimal(20), Decimal(0), "1.5")


def _fakes(monkeypatch, calls, vesting="0"):
    def pools():
        calls["pools"] += 1
        return POOLS
    def balances(player):
        calls["balances"] += 1
        return _rows(("DEC", 1000), ("VOUCHER", 5), ("SPS", 50), ("DEC-GRAIN", 20), ("DEC-VOUCHER", 10))
    class Land:
        def __init__(self, **_):
            pass
        def regional_balances(self, player):
            calls["regions"] += 1
            return type("Response", (), {"data": {"status": "success", "data": [
                {"uid": "PR-CEF-65", "grain": 300, "wood": 0, "stone": 0, "iron": 0, "research": 0, "aura": 0}]}})()
    def position(player, symbol):
        calls["position"] += 1
        return {"GRAIN": Decimal(20), "VOUCHER": Decimal(10)}.get(symbol, Decimal(0)), Decimal(vesting)
    monkeypatch.setattr(lp_routes, "_pools", pools)
    monkeypatch.setattr(lp_routes, "_balances", balances)
    monkeypatch.setattr(lp_routes, "LandClient", Land)
    monkeypatch.setattr(lp_routes, "_position", position)


def test_one_click_add_and_withdraw_build_the_games_shapes_and_refuse_worse_figures(monkeypatch, tmp_path):
    calls = {"pools": 0, "balances": 0, "regions": 0, "position": 0}
    _fakes(monkeypatch, calls)
    app = create_app(CeresConfig(root=tmp_path, player="player0005", card_art_cache_enabled=False, wallet_lp_signing_enabled=True))
    with TestClient(app) as client:
        add = {"kind": "add", "region_uid": "PR-CEF-65", "resource_symbol": "GRAIN", "resource_amount": "100", "sign": True}
        ok = client.post("/api/wallet/lp/preview", json={**add, "seen_dec": "50"})
        assert ok.status_code == 200, ok.text
        payload = ok.json()["payload"]
        assert {k: payload[k] for k in ("op", "region_uid", "resource_amount", "dec_amount", "shares_out", "resource_symbol")} == \
            {"op": "add_liquidity", "region_uid": "PR-CEF-65", "resource_amount": 100.0, "dec_amount": 50.0, "shares_out": 0, "resource_symbol": "GRAIN"}
        assert ok.json()["operations"][0][1]["required_posting_auths"] == ["player0005"]
        assert client.post("/api/wallet/lp/preview", json={**add, "seen_dec": "40"}).status_code == 409   # pool needs more DEC than shown
        assert client.post("/api/wallet/lp/preview", json=add).status_code == 422                          # no shown figure, no build
        assert calls["position"] == 0  # Add never reads the position
        voucher = client.post("/api/wallet/lp/preview", json={"kind": "add", "region_uid": "", "resource_symbol": "VOUCHER",
                                                                "resource_amount": "1", "sign": True, "seen_dec": "40"})
        assert voucher.status_code == 200 and voucher.json()["payload"]["region_uid"] == ""

        remove = {"kind": "remove", "region_uid": "PR-CEF-65", "resource_symbol": "GRAIN", "shares_out": "0.5", "sign": True}
        good = client.post("/api/wallet/lp/preview", json={**remove, "seen": {"resource_amount": "100", "dec_amount": "50"}})
        assert good.status_code == 200, good.text
        assert {k: good.json()["payload"][k] for k in ("op", "region_uid", "resource_amount", "dec_amount", "shares_out")} == \
            {"op": "remove_liquidity", "region_uid": "PR-CEF-65", "resource_amount": 0, "dec_amount": 0, "shares_out": 0.5}
        assert good.json()["withdraw"]["resource_amount"] == "100.000" and calls["position"] == 1
        assert client.post("/api/wallet/lp/preview", json={**remove, "seen": {"resource_amount": "120", "dec_amount": "50"}}).status_code == 409


def test_withdraw_inside_30_days_shows_the_fee_and_a_shown_unfeed_figure_is_refused(monkeypatch, tmp_path):
    calls = {"pools": 0, "balances": 0, "regions": 0, "position": 0}
    _fakes(monkeypatch, calls, vesting="20")
    app = create_app(CeresConfig(root=tmp_path, player="player0005", card_art_cache_enabled=False, wallet_lp_signing_enabled=True))
    with TestClient(app) as client:
        position = client.post("/api/wallet/lp/position", json={"resource_symbol": "GRAIN"}).json()
        assert position["shares"] == "20" and position["locked_shares"] == "20"
        remove = {"kind": "remove", "region_uid": "PR-CEF-65", "resource_symbol": "GRAIN", "shares_out": "0.5", "sign": True}
        assert client.post("/api/wallet/lp/preview", json={**remove, "seen": {"resource_amount": "100", "dec_amount": "50"}}).status_code == 409
        feed = client.post("/api/wallet/lp/preview", json={**remove, "seen": {"resource_amount": "90", "dec_amount": "45"}})
        assert feed.status_code == 200 and feed.json()["withdraw"]["early_fee"] is True


def test_sps_deposit_funding_pays_the_pending_deposit_with_the_active_key():
    expected = lp.add("player0005", "", "SPS", 2, 16)["payload"]
    tx = "c" * 40
    effects = {"pending_deposit_id": 777, "pending_external_amount": 2, "external_transfer_account": lp_routes.SPS_BRIDGE_ACCOUNT, "resource": "SPS"}
    info = {"id": tx, "player": "player0005", "type": "land_operation", "data": json.dumps(expected), "success": True,
            "result": json.dumps({"success": True, "result": {"success": True, "data": effects}})}
    built = lp_routes.sps_funding_operation("player0005", info, tx, {"tx_id": tx, "nonce": expected["n"], "kind": "add", "expected_payload": expected})
    assert built["status"] == "ready" and built["authority"] == "Active" and built["pending_deposit_id"] == 777
    params = json.loads(built["operations"][0][1]["json"])["params"]
    assert (params["to"], params["qty"], params["token"], params["memo"]) == (lp_routes.SPS_BRIDGE_ACCOUNT, 2.0, "SPS", "lpdid:777")


CARD_JS = """
const wallet = { account: { DEC: 1000, SPS: 50, VOUCHER: 5 }, soulbound: ['AURA', 'RESEARCH'],
  regions: [{ region_uid: 'PR-CEF-65', plots_owned: 3, balances: { GRAIN: 300, WOOD: 0, STONE: 0, IRON: 0, RESEARCH: 1, AURA: 1 } }],
  swap: { symbols: ['GRAIN', 'VOUCHER', 'SPS'], directions: ['swap_sell', 'swap_buy'], account_symbols: ['VOUCHER', 'SPS'], two_step_sell: ['SPS'], signing_enabled: true, fees: {}, rates: {} },
  pool: { symbols: ['GRAIN', 'VOUCHER', 'SPS'], account_symbols: ['VOUCHER', 'SPS'], external: ['SPS'],
    reserves: { GRAIN: { resource: '1000', dec: '500', shares: '100' }, VOUCHER: { resource: '10', dec: '400', shares: '50' }, SPS: { resource: '100', dec: '800', shares: '200' } },
    positions: { GRAIN: { shares: '20.000', share_percent: '20', resource_amount: '200.000', dec_amount: '100.000' },
                 VOUCHER: { shares: '0.000', share_percent: '0', resource_amount: '0.000', dec_amount: '0.000' },
                 SPS: { shares: '0.000', share_percent: '0', resource_amount: '0.000', dec_amount: '0.000' } } } };
"""


def test_position_card_closes_every_pool_tab_and_never_dec_or_soulbound():
    body_js = CARD_JS + """
for (const tab of ['GRAIN', 'VOUCHER', 'SPS']) {
  const html = positionHtml(tab, wallet, 'player0005');
  if (!html.includes('data-lp-pool') || !html.includes('lp:pool-add') || !html.includes('lp:pool-remove')) throw new Error(tab + ': ' + html);
}
for (const tab of ['DEC', 'RESEARCH', 'AURA']) if (positionHtml(tab, wallet, 'player0005') !== '') throw new Error('no pool card on ' + tab);
const grain = positionHtml('GRAIN', wallet, 'player0005');
if (!grain.includes('20%') || !grain.includes('200.000') || !grain.includes('100.000')) throw new Error('position figures: ' + grain);
if (!/data-dec="lp:pool-remove"[^>]*disabled/.test(positionHtml('VOUCHER', wallet, 'player0005'))) throw new Error('nothing to withdraw');
if (!positionHtml('GRAIN', { ...wallet, pool: { ...wallet.pool, positions: null } }, 'player0005').includes('unavailable')) throw new Error('failed read is not zero');
// The game's withdrawal maths: 50 % of 20 shares = 10 of 100 -> 100 GRAIN + 50 DEC; all locked -> x 0.9.
const free = withdrawEstimate(wallet.pool.reserves.GRAIN, 20, 0, '50');
const locked = withdrawEstimate(wallet.pool.reserves.GRAIN, 20, 20, '50');
if (free.fraction !== 0.5 || free.resource !== 100 || free.dec !== 50 || free.fee !== 0) throw new Error(JSON.stringify(free));
if (locked.resource !== 90 || locked.dec !== 45 || locked.fee !== 10) throw new Error(JSON.stringify(locked));
if (withdrawEstimate(wallet.pool.reserves.GRAIN, 20, 0, '33.3').fraction !== 0.333) throw new Error('three decimals');
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-lp.js")


def test_one_add_click_signs_the_deposit_and_a_withdraw_click_signs_the_removal():
    source = read_entry_source(STATIC / "wallet-lp.js")
    source = source.replace("requestBroadcastThroughGate(ctx.player", "__stubGate(ctx.player").replace("walletReady(ctx.player)", "true").replace("walletReady(player)", "true")
    extra = """
context.__stubGate = async (player, operations, onResponse, authority) => {
  context.__signed.push({ authority, json: JSON.parse(operations[0][1].json) });
  return { txId: 'a'.repeat(40) };
};
context.__signed = [];
"""
    added = lp.add("player0005", "PR-CEF-65", "GRAIN", 100, 50, nonce="0123456789abcdef")
    removed = lp.remove("player0005", "PR-CEF-65", "GRAIN", "0.5", nonce="fedcba9876543210")
    body_js = CARD_JS + f"""
VERIFY_SCHEDULE_MS.length = 1;
const order = [];
const builds = {{ add: {{ ...{json.dumps(added)}, deposit: {{ resource_amount: '100.000', dec_amount: '50.000' }} }},
                 remove: {{ ...{json.dumps(removed)}, withdraw: {{ resource_amount: '90.000', dec_amount: '45.000' }} }} }};
globalThis.fetch = async (url, options) => {{
  const path = url.split('?')[0].split('/').pop(); const body = JSON.parse(options.body); order.push([path, body]);
  const reply = path === 'preview' ? builds[body.kind]
    : path === 'position' ? {{ shares: '20.000', locked_shares: '20.000' }}
    : {{ status: 'confirmed', game_result: body.kind === 'add' ? {{ resource_amount: 100, dec_amount: 50 }} : {{ resource_amount: 89.9, dec_amount: 44.9 }} }};
  return {{ ok: true, status: 200, json: async () => reply }};
}};
const ctx = {{ player: 'player0005', account: () => 'player0005', drawer: () => null, wallet: () => wallet, render: () => {{}}, reload: async () => {{}}, setSigning: () => {{}} }};
const target = {{ closest: () => ({{ dataset: {{ lpSymbol: 'GRAIN' }} }}) }};
const st = swapState('player0005', 'GRAIN');
(async () => {{
  await swapAction(ctx, 'pool-add', target);
  await swapAction(ctx, 'pool-pct:50', target);   // 50 % of what 300 GRAIN and 1000 DEC can pair: 150 GRAIN
  if (st.pool.amount !== '150') throw new Error('add amount: ' + st.pool.amount);
  st.pool.amount = '100';
  let card = positionHtml('GRAIN', wallet, 'player0005');
  if (!card.includes('Add 100.000 Grain + 50.000 DEC')) throw new Error('add button: ' + card);
  await swapAction(ctx, 'pool-sign', target);
  const preview = order.find(o => o[0] === 'preview')[1];
  if (preview.kind !== 'add' || preview.seen_dec !== '50' || preview.resource_amount !== '100' || preview.region_uid !== 'PR-CEF-65') throw new Error('add build: ' + JSON.stringify(preview));
  if (__signed.length !== 1 || __signed[0].authority !== 'Posting' || __signed[0].json.op !== 'add_liquidity') throw new Error('signed: ' + JSON.stringify(__signed));
  if (st.stage !== 'done' || st.receipt.resource !== 100 || st.receipt.dec !== 50) throw new Error('add receipt: ' + st.stage);
  // the result shows in the pool box itself; the swap card stays (and waits), so nothing above it moves.
  const poolBox = positionHtml('GRAIN', wallet, 'player0005'), swapBox = swapHtml('GRAIN', wallet, 'player0005');
  if (!poolBox.includes('Liquidity added') || !poolBox.includes('data-lp-active') || swapBox.includes('Liquidity added') || !/data-dec="lp:swap"[^>]*disabled/.test(swapBox)) throw new Error('receipt in the pool box: ' + poolBox);
  await swapAction(ctx, 'done', target);
  // Withdraw: opening it reads the position (the early-withdrawal fee), then one click signs.
  await swapAction(ctx, 'pool-remove', target);
  if (order.filter(o => o[0] === 'position').length !== 1 || st.pool.locked !== 20) throw new Error('position read');
  await swapAction(ctx, 'pool-pct:50', target);
  card = positionHtml('GRAIN', wallet, 'player0005');
  if (!card.includes('Withdraw 50% → 90.000 Grain + 45.000 DEC') || !card.includes('data-lp-early-fee')) throw new Error('withdraw card: ' + card);
  await swapAction(ctx, 'pool-sign', target);
  const removal = order.filter(o => o[0] === 'preview')[1][1];
  if (removal.shares_out !== '0.500' || removal.seen.resource_amount !== '90.000' || removal.seen.dec_amount !== '45.000') throw new Error('remove build: ' + JSON.stringify(removal));
  if (__signed[1].json.op !== 'remove_liquidity' || st.stage !== 'done' || st.receipt.resource !== 89.9) throw new Error('remove: ' + st.stage);
  if (!positionHtml('GRAIN', wallet, 'player0005').includes('Liquidity withdrawn')) throw new Error('withdraw receipt');
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-lp.js", source=source, extra_context_js=extra)


def test_typing_the_dec_works_out_the_resource_it_pairs_with():
    body_js = CARD_JS + """
const ctx = { player: 'player0005', account: () => 'player0005', drawer: () => null, wallet: () => wallet, render: () => {}, reload: async () => {}, setSigning: () => {} };
const st = swapState('player0005', 'GRAIN');
st.pool.mode = 'add';
const input = (attr, value) => ({ value, dataset: { lpPoolDec: attr === 'data-lp-pool-dec' ? 'GRAIN' : undefined, lpPoolAmount: attr === 'data-lp-pool-amount' ? 'GRAIN' : undefined }, hasAttribute: a => a === attr });
poolInput(ctx, input('data-lp-pool-dec', '40'));        // pool 1000 GRAIN : 500 DEC -> 80 GRAIN
if (st.pool.amount !== '80' || st.pool.dec !== '40' || !st.pool.decTyped) throw new Error('dec typed: ' + JSON.stringify(st.pool));
let card = positionHtml('GRAIN', wallet, 'player0005');
if (!card.includes('data-lp-pool-dec') || !card.includes('value="40"') || !card.includes('Add 80.000 Grain + 40.000 DEC')) throw new Error('card: ' + card);
const big = input('data-lp-pool-dec', '99999');          // capped at what 300 GRAIN (and 1000 DEC) can pair: 150 DEC
poolInput(ctx, big);
if (big.value !== '150' || st.pool.amount !== '300') throw new Error('cap: ' + big.value + ' ' + st.pool.amount);
poolInput(ctx, input('data-lp-pool-amount', '10'));      // typing the resource again: the DEC follows it
if (st.pool.decTyped || !positionHtml('GRAIN', wallet, 'player0005').includes('value="5"')) throw new Error('resource typed again');
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-lp.js")
