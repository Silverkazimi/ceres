""" / : the Market cart's checkout ("Buy from Ceres").

Payload shape and the price rules (DEC, CREDITS), the 150-listing split, sold / price-rise re-confirmation (409), the
listing key -> market id resolution kept on the server, the outcome per listing (all, partial, none, failed, unknown), no
second signature on an unknown outcome, the balance gate, the Active allowlist entry and the maintainer-only test buy.
No network: every upstream read is a stub. Nothing is signed or broadcast."""
from __future__ import annotations

import json
import re
import socket
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ceres import card_market, market_purchase as mp, routes_market_purchase as routes
from ceres.app import create_app
from ceres.card_market import MarketCache, listing_key
from ceres.config import CeresConfig
from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"
PID = "349-3-0"
DEC_PRICE = 0.0005


def mid(n: int) -> str:
    return f"{n:040x}-0"


def deep_row(n: int, price: float, seller: str = "seller_a") -> dict:
    return {"market_id": mid(n), "uid": f"C3-349-FAKEUID{n:04d}", "bcx": 1, "buy_price": price, "price_bcx": price, "level": 1,
            "currency": "USD", "seller": seller, "type": "SELL", "card_detail_id": 349, "edition": 3, "foil": 0}


def status_row(n: int, price: float, *, status: int = 0, seller: str = "seller_a", purchaser=None, card=(349, 3, 0),
               expires: str = "2099-01-01T00:00:00.000Z") -> dict:
    return {"id": 1000 + n, "sell_trx_id": mid(n), "seller": seller, "type": "SELL", "currency": "USD", "buy_price": f"{price:.3f}",
            "status": status, "purchaser": purchaser, "expiration_date": expires, "lock_block": None, "locked_by": None,
            "cards": [{"uid": f"C3-349-FAKEUID{n:04d}", "card_detail_id": card[0], "edition": card[1], "foil": card[2], "gold": False}]}


class Market:
    """A stub of the public market: deep reads per print, by-id status, settings and balances."""

    def __init__(self, n: int = 3, price: float = 0.01) -> None:
        self.deep = [deep_row(i, price) for i in range(1, n + 1)]
        self.status = {mid(i): status_row(i, price) for i in range(1, n + 1)}
        self.dec_price = DEC_PRICE
        self.balances = [{"player": "player0005", "token": "DEC", "balance": 5000.0}, {"player": "player0005", "token": "CREDITS", "balance": 1000}]
        self.calls: list[str] = []

    def __call__(self, url: str):
        parsed = urllib.parse.urlsplit(url)
        assert parsed.hostname == "api.splinterlands.com"
        query = urllib.parse.parse_qs(parsed.query)
        self.calls.append(parsed.path)
        if parsed.path == card_market.BY_CARD_PATH:
            return self.deep
        if parsed.path == mp.STATUS_PATH:
            ids = query["ids"][0].split(",")
            assert len(ids) <= mp.STATUS_IDS_PER_CALL
            return [self.status[i] for i in ids if i in self.status]
        if parsed.path == "/settings":
            return {"dec_price": self.dec_price}
        if parsed.path == "/players/balances":
            return self.balances
        if parsed.path == card_market.GROUPED_PATH:
            return [{"card_detail_id": 349, "edition": 3, "gold": False, "qty": len(self.deep), "low_price": 0.01, "low_price_bcx": 0.01}]
        raise AssertionError(parsed.path)


def cart(market: Market, n: int | None = None, seen: float = 0.01) -> list[dict]:
    rows = market.deep[:n] if n else market.deep
    return [{"lkey": listing_key(r), "pid": PID, "seen_usd": seen} for r in rows]


@pytest.fixture(autouse=True)
def fresh_state():
    mp.KEYS.clear()
    mp.QUOTES.clear()
    yield
    mp.KEYS.clear()
    mp.QUOTES.clear()


@pytest.fixture
def market(monkeypatch):
    stub = Market()
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    monkeypatch.setattr(mp, "_default_get", stub)
    monkeypatch.setattr(card_market, "CACHE", MarketCache())
    monkeypatch.setattr(mp, "app_identity", lambda: "ceres/0.29.0")
    return stub


@pytest.fixture
def client(tmp_path, market, monkeypatch):
    monkeypatch.delenv(routes.ENV_SIGNING, raising=False)
    app = create_app(CeresConfig(root=tmp_path / ".ceres", player="player0005"))
    if not any(getattr(r, "path", "") == "/api/market/checkout/quote" for r in app.routes):
        app.include_router(routes.router)  # app.py registration is the integrator's (routes_market_purchase.router)
    return TestClient(app)


# price rules and payload

def test_price_rules_match_the_official_client():
    # DEC: parseFloat((S / dec_price).toFixed(3)); CREDITS: S * 1000 (whole credits)
    assert mp.price_for("DEC", 0.739, 0.0004868) == float(f"{0.739 / 0.0004868:.3f}")
    assert mp.price_for("DEC", 0.214, 0.000234198) == 913.757
    assert mp.price_for("CREDITS", 91.0, None) == 91000 and isinstance(mp.price_for("CREDITS", 91.0, None), int)
    assert mp.price_for("CREDITS", 0.739, None) == 739   # 0.739 * 1000 = 739.0000000000001 in doubles
    assert mp.price_for("CREDITS", 19.9, None) == 19900
    assert mp._to_fixed_3(0.0625) == 0.063             # ties up, as Number.prototype.toFixed
    assert mp.usd_sum([0.1, 0.2]) == 0.1 + 0.2          # the same float sum the official reduce makes
    with pytest.raises(mp.PurchaseError):
        mp.price_for("DEC", 1.0, None)
    with pytest.raises(mp.PurchaseError):
        mp.price_for("SPS", 1.0, 1.0)


def test_payload_shape_active_and_key_order(market):
    quote = mp.build_quote("player0005", cart(market), market)
    txs = mp.build_transactions("player0005", quote, "DEC", nonces=["a" * 16])
    assert len(txs) == 1
    op = txs[0]["operations"][0]
    assert op[0] == "custom_json" and op[1]["id"] == "sm_market_purchase"
    assert op[1]["required_auths"] == ["player0005"] and op[1]["required_posting_auths"] == []
    payload = json.loads(op[1]["json"])
    assert list(payload) == ["currency", "items", "price", "app", "n"]
    assert payload == {"currency": "DEC", "items": [mid(1), mid(2), mid(3)], "price": 60.0, "app": "ceres/0.29.0", "n": "a" * 16}
    assert txs[0]["authority"] == "Active" and txs[0]["lkeys"] == [listing_key(r) for r in market.deep]
    credits = mp.build_transactions("player0005", quote, "CREDITS")[0]["payload"]
    assert credits["currency"] == "CREDITS" and credits["price"] == 30 and re.fullmatch(r"[a-f0-9]{16}", credits["n"])


def test_split_into_transactions_of_150():
    stub = Market(n=170)
    quote = mp.build_quote("player0005", cart(stub), stub)
    assert quote["totals"]["count"] == 170 and [t["count"] for t in quote["totals"]["transactions"]] == [150, 20]
    txs = mp.build_transactions("player0005", quote, "CREDITS")
    assert [len(t["payload"]["items"]) for t in txs] == [150, 20]
    assert [t["payload"]["price"] for t in txs] == [1500, 200]              # each transaction priced on its own listings
    assert len({t["nonce"] for t in txs}) == 2
    assert quote["totals"]["CREDITS"] == 1700
    # status reads go out in chunks of at most 40 ids
    assert stub.calls.count(mp.STATUS_PATH) == 5


def test_lkey_resolves_to_market_id_only_on_the_server(client, market):
    response = client.post("/api/market/checkout/quote?player=player0005", json={"items": cart(market)})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    text = response.text
    assert not any(mid(i) in text or mid(i)[:20] in text for i in (1, 2, 3)) and "FAKEUID" not in text and "seller_a" not in text
    body = response.json()
    assert [r["lkey"] for r in body["items"]] == [c["lkey"] for c in cart(market)]
    assert body["totals"]["count"] == 3 and body["totals"]["DEC"] == 60.0 and body["totals"]["CREDITS"] == 30
    assert body["currencies"]["DEC"]["enough"] and body["currencies"]["CREDITS"]["enough"]
    assert body["balances"] == {"DEC": 5000.0, "CREDITS": 1000.0} and body["dec_price"] == DEC_PRICE
    assert body["signing"]["enabled"] and body["max_per_transaction"] == 150
    # the market ids are kept server-side: in the registry and the stored quote, never in the page's view
    assert mp.KEYS.get(cart(market)[0]["lkey"], PID) == mid(1)
    assert "_ids" not in body and mp.QUOTES.get(body["quote_id"], "player0005")["_ids"][cart(market)[0]["lkey"]] == mid(1)


def test_preview_builds_the_exact_operation(client, market):
    quote = client.post("/api/market/checkout/quote?player=player0005", json={"items": cart(market)}).json()
    response = client.post("/api/market/checkout/preview?player=player0005",
                           json={"quote_id": quote["quote_id"], "currency": "DEC", "confirmed_total": quote["totals"]["DEC"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authority"] == "Active" and body["currency"] == "DEC" and body["total"] == 60.0
    tx = body["transactions"][0]
    assert json.loads(tx["operations"][0][1]["json"]) == tx["payload"] and tx["payload"]["items"] == [mid(1), mid(2), mid(3)]


def test_sold_and_price_rise_refuse_with_the_new_quote(client, market):
    quote = client.post("/api/market/checkout/quote?player=player0005", json={"items": cart(market)}).json()
    market.status[mid(2)] = status_row(2, 0.01, status=1, purchaser="player_0002")   # sold in the meantime
    market.status[mid(3)] = status_row(3, 0.02)                                       # dearer
    response = client.post("/api/market/checkout/preview?player=player0005",
                           json={"quote_id": quote["quote_id"], "currency": "DEC", "confirmed_total": quote["totals"]["DEC"]})
    assert response.status_code == 409
    body = response.json()
    states = {r["lkey"]: r for r in body["items"]}
    keys = [c["lkey"] for c in cart(market)]
    assert states[keys[1]]["status"] == "sold" and states[keys[2]]["status"] == "price_changed" and states[keys[2]]["rise"]
    assert states[keys[2]]["was_usd"] == 0.01 and states[keys[2]]["price_usd"] == 0.02
    assert body["changes"] == {"sold": 1, "price_rise": 1, "price_drop": 0} and body["totals"]["count"] == 2
    assert body["totals"]["DEC"] == 60.0 and body["quote_id"] != quote["quote_id"] and body["reasons"]
    # confirming the NEW quote goes through
    again = client.post("/api/market/checkout/preview?player=player0005",
                        json={"quote_id": body["quote_id"], "currency": "CREDITS", "confirmed_total": body["totals"]["CREDITS"]})
    assert again.status_code == 200 and again.json()["transactions"][0]["payload"]["items"] == [mid(1), mid(3)]


def test_dec_rate_rise_alone_needs_reconfirmation(client, market):
    quote = client.post("/api/market/checkout/quote?player=player0005", json={"items": cart(market)}).json()
    market.dec_price = 0.0004   # the same dollars now cost more DEC
    response = client.post("/api/market/checkout/preview?player=player0005",
                           json={"quote_id": quote["quote_id"], "currency": "DEC", "confirmed_total": quote["totals"]["DEC"]})
    assert response.status_code == 409 and response.json()["totals"]["DEC"] == 75.0
    # a rise in the cart since adding is amber in the first quote already (seen 0.005, now 0.01)
    first = client.post("/api/market/checkout/quote?player=player0005", json={"items": cart(market, seen=0.005)}).json()
    assert first["changes"]["price_rise"] == 3 and all(r["status"] == "price_changed" and r["rise"] for r in first["items"])


def test_balance_gate(client, market):
    market.balances = [{"player": "player0005", "token": "DEC", "balance": 10.0}]   # no CREDITS row = 0
    quote = client.post("/api/market/checkout/quote?player=player0005", json={"items": cart(market)}).json()
    assert quote["currencies"]["DEC"] == {"total": 60.0, "balance": 10.0, "enough": False, "short": 50.0, "reason": "Not enough DEC."}
    assert not quote["currencies"]["CREDITS"]["enough"] and quote["currencies"]["CREDITS"]["balance"] == 0.0
    response = client.post("/api/market/checkout/preview?player=player0005",
                           json={"quote_id": quote["quote_id"], "currency": "DEC", "confirmed_total": 60.0})
    assert response.status_code == 409 and "your DEC balance is not enough" in response.json()["reasons"]


def test_classify_unavailable_listings():
    item = {"lkey": "a" * 16, "pid": PID, "seen_usd": 0.01}
    assert mp.classify(item, None, None, "player0005")["status"] == "unresolved"
    assert mp.classify(item, mid(1), None, "player0005")["status"] == "gone"
    assert mp.classify(item, mid(1), status_row(1, 0.01, status=1, purchaser="x"), "player0005")["status"] == "sold"
    assert mp.classify(item, mid(1), status_row(1, 0.01, expires="2020-01-01T00:00:00.000Z"), "player0005")["status"] == "gone"
    assert mp.classify(item, mid(1), status_row(1, 0.01, seller="player0005"), "player0005")["status"] == "own"
    assert mp.classify(item, mid(1), status_row(1, 0.01, card=(350, 3, 0)), "player0005")["status"] == "gone"
    drop = mp.classify(item, mid(1), status_row(1, 0.008), "player0005")
    assert drop["status"] == "price_changed" and drop["rise"] is False
    assert mp.classify(item, mid(1), status_row(1, 0.01), "player0005")["status"] == "ok"


def test_quote_refuses_bad_items(client):
    assert client.post("/api/market/checkout/quote?player=player0005", json={"items": []}).status_code == 422
    assert client.post("/api/market/checkout/quote?player=player0005", json={"items": [{"lkey": "x", "pid": PID}]}).status_code == 422
    assert client.post("/api/market/checkout/preview?player=player0005", json={"quote_id": "0" * 24, "currency": "DEC"}).status_code == 410
    assert client.post("/api/market/checkout/preview?player=player0005", json={"quote_id": "0" * 24, "currency": "SPS"}).status_code == 422


# outcome

TX = "b" * 40


def _expected():
    return {"currency": "DEC", "items": [mid(1), mid(2), mid(3)], "price": 60.0, "app": "ceres/0.29.0", "n": "c" * 16}


def _info(result, success=True, error=None, **over):
    data = json.dumps(_expected())
    return {"id": TX, "type": "market_purchase", "player": "player0005", "data": data, "success": success, "error": error,
            "result": json.dumps(result) if result is not None else None, **over}


def test_outcome_all_partial_none_failed_and_unknown():
    keys = [listing_key({"market_id": mid(i)}) for i in (1, 2, 3)]
    full = mp.outcome(_info({"success": True, "num_cards": 3, "total_usd": 0.03, "total_dec": 60.0,
                             "by_seller": [{"seller": "a", "items": [mid(1), mid(2)]}, {"seller": "b", "items": [mid(3)]}]}), "player0005", TX, _expected())
    assert full["status"] == "confirmed" and full["fill"] == "all" and full["paid_dec"] == 60.0
    assert [r["outcome"] for r in full["items"]] == ["bought"] * 3 and [r["lkey"] for r in full["items"]] == keys
    part = mp.outcome(_info({"success": True, "num_cards": 1, "by_seller": [{"items": [mid(2)]}]}), "player0005", TX, _expected())
    assert part["fill"] == "partial" and [r["outcome"] for r in part["items"]] == ["not_bought", "bought", "not_bought"]
    none = mp.outcome(_info({"success": True, "num_cards": 0, "by_seller": []}), "player0005", TX, _expected())
    assert none["fill"] == "none"
    failed = mp.outcome(_info(None, success=False, error="<b>Card is no longer for sale</b>"), "player0005", TX, _expected())
    assert failed["status"] == "failed" and failed["message"] == "<b>Card is no longer for sale</b>"  # verbatim; the page escapes
    assert all(r["outcome"] == "not_bought" for r in failed["items"])
    nested = mp.outcome(_info({"success": True, "result": {"success": False, "error": "Insufficient DEC"}}), "player0005", TX, _expected())
    assert nested["status"] == "failed" and nested["message"] == "Insufficient DEC"
    assert mp.outcome(None, "player0005", TX, _expected()) == {"status": "not_found"}
    assert mp.outcome(_info(None, success=None), "player0005", TX, _expected())["status"] == "pending"
    assert mp.outcome(_info({"success": True}, player="player_0002"), "player0005", TX, _expected())["status"] == "mismatch"
    other = {**_expected(), "n": "d" * 16}
    assert mp.outcome(_info({"success": True}), "player0005", TX, other)["status"] == "mismatch"


def test_verify_route_by_id(client, monkeypatch):
    seen = {}

    def lookup(get_json, tx):
        seen["tx"] = tx
        return _info({"success": True, "num_cards": 3, "by_seller": [{"items": [mid(1), mid(2), mid(3)]}]})

    monkeypatch.setattr(routes, "lookup_trx_info", lookup)
    body = {"tx_id": TX, "nonce": "c" * 16, "expected_payload": _expected()}
    response = client.post("/api/market/checkout/verify?player=player0005", json=body)
    assert response.status_code == 200 and response.json()["fill"] == "all" and seen["tx"] == TX
    assert mid(1) not in response.text
    bad = client.post("/api/market/checkout/verify?player=player0005", json={**body, "nonce": "e" * 16})
    assert bad.status_code == 422
    monkeypatch.setattr(routes, "lookup_trx_info", lambda g, t: (_ for _ in ()).throw(RuntimeError("down")))
    down = client.post("/api/market/checkout/verify?player=player0005", json=body)
    assert down.status_code == 502 and "without buying again" in down.json()["detail"]


def test_find_in_own_history_by_nonce():
    def row(i, nonce, op_id="sm_market_purchase", auths=("player0005",)):
        return [i, {"trx_id": f"{i:040x}", "timestamp": "2026-10-15T10:00:00",
                    "op": ["custom_json", {"id": op_id, "required_auths": list(auths), "required_posting_auths": [],
                                           "json": json.dumps({"currency": "DEC", "items": [mid(1)], "price": 1, "app": "ceres/x", "n": nonce})}]}]
    rows = [row(5, "f" * 16, op_id="sm_land_operation"), row(6, "f" * 16, auths=("player_0002",)), row(7, "f" * 16)]
    assert mp.find_in_history(rows, "player0005", "f" * 16) == f"{7:040x}"
    assert mp.find_in_history(rows[:2], "player0005", "f" * 16) is None


# signing gate and the test buy

def test_signing_is_owner_only_by_default(client, market, monkeypatch):
    other = [{"player": "someone-else", "token": "DEC", "balance": 5000.0}]
    market.balances = other
    quote = client.post("/api/market/checkout/quote?player=someone-else", json={"items": cart(market)}).json()
    assert quote["signing"]["enabled"] is False and "test purchases" in quote["signing"]["reason"]
    refused = client.post("/api/market/checkout/preview?player=someone-else",
                          json={"quote_id": quote["quote_id"], "currency": "DEC", "confirmed_total": 60.0})
    assert refused.status_code == 409
    monkeypatch.setenv(routes.ENV_SIGNING, "on")
    assert client.post("/api/market/checkout/quote?player=someone-else", json={"items": cart(market)}).json()["signing"]["enabled"]
    monkeypatch.setenv(routes.ENV_SIGNING, "off")
    assert not client.post("/api/market/checkout/quote?player=player0005", json={"items": cart(market)}).json()["signing"]["enabled"]


def test_test_buy_is_the_cheapest_listing_for_the_owner_on_a_local_build(client, market, monkeypatch):
    from ceres import board
    from tests.ceres.test_shop_cart import DETAILS

    monkeypatch.setattr(board, "load_details_by_id", lambda: DETAILS)
    market.deep = [deep_row(1, 0.02), deep_row(2, 0.004), deep_row(3, 0.003, seller="player0005")]   # the own one never
    market.status = {mid(i): status_row(i, p) for i, p in ((1, 0.02), (2, 0.004))}
    monkeypatch.setattr(routes, "dev_watch_enabled", lambda: True)
    response = client.post("/api/market/checkout/test-buy?player=player0005", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["test_item"]["lkey"] == listing_key(deep_row(2, 0.004)) and body["totals"]["count"] == 1
    assert body["totals"]["CREDITS"] == 4 and body["test_buy"] is True and "FAKEUID" not in response.text
    assert client.post("/api/market/checkout/test-buy?player=someone-else", json={}).status_code == 404
    monkeypatch.setattr(routes, "dev_watch_enabled", lambda: False)
    assert client.post("/api/market/checkout/test-buy?player=player0005", json={}).status_code == 404


def test_checkout_reads_have_their_own_bucket(client, market):
    statuses = [client.post("/api/market/checkout/quote?player=player0005", json={"items": cart(market)}).status_code for _ in range(21)]
    assert statuses[:20] == [200] * 20 and statuses[20] == 429


# client

def test_active_allowlist_admits_only_the_reviewed_purchase():
    out = run_in_module_context(r"""
const p = (over = {}) => ({ currency: 'DEC', items: ['%s'], price: 1.5, app: 'ceres/0.29.0', n: 'aaaaaaaaaaaaaaaa', ...over });
const op = (payload, over = {}) => [['custom_json', { id: 'sm_market_purchase', json: JSON.stringify(payload), required_auths: ['player0005'], required_posting_auths: [], ...over }]];
const r = {
  ok: reviewedMarketPurchase('player0005', op(p())),
  credits: reviewedMarketPurchase('player0005', op(p({ currency: 'CREDITS', price: 1500 }))),
  other_account: reviewedMarketPurchase('player_0001', op(p())),
  posting: reviewedMarketPurchase('player0005', op(p(), { required_auths: [], required_posting_auths: ['player0005'] })),
  sps: reviewedMarketPurchase('player0005', op(p({ currency: 'SPS' }))),
  extra_field: reviewedMarketPurchase('player0005', op({ ...p(), memo: 'x' })),
  zero: reviewedMarketPurchase('player0005', op(p({ price: 0 }))),
  uid_item: reviewedMarketPurchase('player0005', op(p({ items: ['C3-349-ABC'] }))),
  too_many: reviewedMarketPurchase('player0005', op(p({ items: Array.from({ length: 151 }, (_, i) => i.toString(16).padStart(40, '0') + '-0') }))),
  two_ops: reviewedMarketPurchase('player0005', [...op(p()), ...op(p())]),
};
console.log(JSON.stringify(r));
""" % mid(1), entry=STATIC / "broadcast-gate.js").stdout
    got = json.loads(out.strip().splitlines()[-1])
    assert got.pop("ok") is True and got.pop("credits") is True
    assert not any(got.values()), got
    gate = (STATIC / "broadcast-gate.js").read_text(encoding="utf-8")
    assert "reviewedMarketPurchase(account, operations) ||" in gate


CHECKOUT_VM = r"""
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw Error(what + ': ' + JSON.stringify(a) + ' != ' + JSON.stringify(b)); };
"""


def test_checkout_view_model_and_unknown_outcome_locks():
    out = run_in_module_context(CHECKOUT_VM + r"""
const quote = {
  quote_id: 'q', dec_price: 0.0005, max_per_transaction: 150, signing: { enabled: true },
  items: [
    { lkey: 'aaaaaaaaaaaaaaa1', pid: '349-3-0', status: 'ok', price_usd: 0.01, seen_usd: 0.01 },
    { lkey: 'aaaaaaaaaaaaaaa2', pid: '349-3-0', status: 'sold', price_usd: null, seen_usd: 0.01, reason: 'Sold to another player.' },
    { lkey: 'aaaaaaaaaaaaaaa3', pid: '349-3-0', status: 'price_changed', rise: true, price_usd: 0.02, was_usd: 0.01, seen_usd: 0.01 },
  ],
  totals: { count: 2, usd: 0.03, DEC: 60, CREDITS: 30, transactions: [{ count: 2 }] },
  currencies: { DEC: { total: 60, balance: 5000, enough: true }, CREDITS: { total: 30, balance: 10, enough: false, short: 20, reason: 'Not enough credits.' } },
  changes: { sold: 1, price_rise: 1, price_drop: 0 },
};
const html = reviewHtml(quote);
eq(quote.items.map(rowState), ['ok', 'sold', 'rise'], 'row states');
eq(currencyChoice(quote, 'CREDITS').disabled, true, 'credits disabled');
eq(currencyChoice(quote, 'DEC').disabled, false, 'dec enabled');
eq(changeNotes(quote).map((n) => n.tone), ['danger', 'warn'], 'notes');
// an unknown outcome keeps its listings locked and the run open (no second purchase is offered)
const run = { token: 't', txs: [{ nonce: 'n1', lkeys: ['aaaaaaaaaaaaaaa1'], state: 'unknown' }, { nonce: 'n2', lkeys: ['aaaaaaaaaaaaaaa3'], state: 'skipped' }] };
eq(runIsOpen(run), true, 'unknown is open');
eq(writeRun('player0005', run), true, 'stored');
eq([...checkoutLockedKeys('player0005')], ['aaaaaaaaaaaaaaa1'], 'locked keys');
eq(writeRun('player0005', { token: 'other', txs: [{ state: 'asked', lkeys: [] }] }), false, 'another purchase cannot overwrite an open one');
eq(listingOutcome({ state: 'confirmed', result: { items: [{ lkey: 'k1', outcome: 'bought' }, { lkey: 'k2', outcome: 'not_bought' }] } }, 'k2'), 'not_bought', 'partial');
eq(listingOutcome({ state: 'unknown' }, 'k1'), 'unknown', 'unknown');
eq(runIsOpen({ txs: [{ state: 'confirmed' }, { state: 'not_sent' }, { state: 'declined' }] }), false, 'all resolved');
const out = outcomeHtml({ txs: [{ state: 'failed', message: '<img src=x onerror=alert(1)>', lkeys: ['k1'], currency: 'DEC', price: 1 }] });
console.log(JSON.stringify({ html, out, undo: UNDO_TEXT }));
""", entry=STATIC / "card-checkout.js").stdout
    got = json.loads(out.strip().splitlines()[-1])
    html = got["html"]
    assert 'data-state="sold"' in html and "Sold" in html and 'data-state="rise"' in html and "Price rose · was $0.010" in html
    assert 'aria-pressed="false"' in html and 'aria-pressed="true"' not in html            # no currency preselected ()
    assert re.search(r'data-co-currency="CREDITS" aria-pressed="false" disabled', html) and "Not enough credits. Short by 20 credits." in html
    assert "struck out" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in got["out"] and "<img src=x" not in got["out"]   # the game's text, escaped
    assert "cannot be undone" in got["undo"]


def test_checkout_source_contracts():
    js = (STATIC / "card-checkout.js").read_text(encoding="utf-8")
    # Active, one sign-and-send prompt per transaction, through the shared gate
    assert "requestBroadcastThroughGate(signingPlayer, tx.operations" in js and "'Active', { direct: true }" in js
    sign = js[js.index("async function sign()"):js.index("function markSkipped")]
    # the intent is stored before the wallet is asked; an unknown or declined answer stops the rest of the checkout
    assert sign.index("writeRun(co.player, co.run))") < sign.index("requestBroadcastThroughGate")
    assert "tx.state = 'unknown'; markSkipped(i + 1)" in sign and "tx.state = 'declined'; markSkipped(i + 1)" in sign
    assert "if (runIsOpen(open)) return 'taken'" in sign and "await adoptLate()" in sign
    # clocks
    # An unknown wallet answer stays reserved through Hive expiry plus the inclusion grace.
    assert "CONFIRM_TTL_MS = 3 * 60 * 1000" in js and "NEVER_SENT_AFTER_MS = 3900 * 1000" in js and "RECHECK_MS = 10000" in js
    assert "Date.now() - Date.parse(tx.asked_at || '') >= NEVER_SENT_AFTER_MS" in js
    # The fresh combine witness uses copy UIDs internally; checkout UI must not expose market IDs or seller data.
    assert not re.search(r"market_id|seller", js.replace("// No market id, uid or seller is ever shown", ""))
    cart_js = (STATIC / "card-cart.js").read_text(encoding="utf-8")
    assert "import('./card-checkout.js')" in cart_js and "onBought: (keys) => { if (cartRemove(cart.state, keys)) save();" in cart_js


# Buy & max

def test_split_keeps_a_maxed_group_in_one_transaction():
    singles = [{"lkey": f"s{i}", "group": None} for i in range(149)]
    group = [{"lkey": f"g{i}", "group": "max-a"} for i in range(3)]
    chunks = mp.split(singles[:100] + group + singles[100:])
    assert [len(c) for c in chunks] == [150, 2] and all(x in chunks[0] for x in group)
    chunks = mp.split(singles + group)
    assert [len(c) for c in chunks] == [149, 3] and chunks[1] == group          # never cut apart
    big = [{"lkey": f"b{i}", "group": "max-b"} for i in range(160)]
    assert [len(c) for c in mp.split(big)] == [150, 10]                         # too big for one purchase: split as singles


def _bought_status(n, purchaser="player0005", status=1):
    row = status_row(n, 0.01, status=status, purchaser=purchaser)
    row["cards"][0]["player"] = purchaser
    return row


@pytest.fixture
def combine_client(client, market, monkeypatch):
    from ceres import routes_combine
    built = {}

    def fake_build(body, request, player=None):
        built["uids"] = body["uids"]
        return JSONResponse(content={"operation": ["custom_json", {"id": "sm_combine_cards", "required_posting_auths": [player],
                                                                    "required_auths": [], "json": json.dumps({"cards": body["uids"]})}],
                                     "preview": {"resulting_bcx": 3, "uids": body["uids"]}})

    from fastapi.responses import JSONResponse
    monkeypatch.setattr(routes_combine, "combine_build", fake_build)
    return client, built


def _combine(client, lkeys, info, monkeypatch):
    monkeypatch.setattr(routes, "lookup_trx_info", lambda get_json, tx: info)
    return client.post("/api/market/checkout/combine?player=player0005",
                       json={"tx_id": TX, "nonce": "c" * 16, "expected_payload": _expected(), "lkeys": lkeys})


def test_buy_and_max_all_bought_offers_the_combine_of_exactly_those_cards(combine_client, market, monkeypatch):
    client, built = combine_client
    market.status = {mid(i): _bought_status(i) for i in (1, 2, 3)}
    keys = [listing_key({"market_id": mid(i)}) for i in (1, 2, 3)]
    info = _info({"success": True, "num_cards": 3, "by_seller": [{"items": [mid(1), mid(2), mid(3)]}]})
    response = _combine(client, keys[:2], info, monkeypatch)
    assert response.status_code == 200 and response.json()["status"] == "ready"
    assert built["uids"] == ["C3-349-FAKEUID0001", "C3-349-FAKEUID0002"]          # the group only, from the by-id listing status
    assert response.json()["operation"][1]["id"] == "sm_combine_cards" and response.json()["authority"] == "Posting"


def test_buy_and_max_partial_or_unknown_never_combines(combine_client, market, monkeypatch):
    client, built = combine_client
    market.status = {mid(i): _bought_status(i) for i in (1, 2, 3)}
    keys = [listing_key({"market_id": mid(i)}) for i in (1, 2, 3)]
    partial = _info({"success": True, "num_cards": 2, "by_seller": [{"items": [mid(1), mid(3)]}]})
    body = _combine(client, keys, partial, monkeypatch).json()
    assert body["status"] == "partial" and body["missing"] == [keys[1]] and "uids" not in built
    assert _combine(client, keys, None, monkeypatch).json()["status"] == "pending" and "uids" not in built        # unknown
    refused = _info(None, success=False, error="no")
    assert _combine(client, keys, refused, monkeypatch).json()["status"] == "failed" and "uids" not in built
    # bought, but the listings do not show the hand-over yet: wait (the page asks again each minute)
    market.status = {mid(i): status_row(i, 0.01) for i in (1, 2, 3)}
    full = _info({"success": True, "by_seller": [{"items": [mid(1), mid(2), mid(3)]}]})
    assert _combine(client, keys, full, monkeypatch).json()["status"] == "not_ready" and "uids" not in built
    assert _combine(client, keys[:1], full, monkeypatch).status_code == 422                  # a group is two or more listings
    assert _combine(client, ["f" * 16, keys[0]], full, monkeypatch).status_code == 422       # only listings of this purchase


def test_buy_and_max_client_verdicts():
    out = run_in_module_context(CHECKOUT_VM + r"""
const g = { group: 'max-a', lkeys: ['k1', 'k2'] };
const tx = (items, state = 'confirmed') => ({ state, result: { items } });
eq(groupVerdict(g, tx([{ lkey: 'k1', outcome: 'bought' }, { lkey: 'k2', outcome: 'bought' }])), 'building', 'all bought -> combine');
eq(groupVerdict(g, tx([{ lkey: 'k1', outcome: 'bought' }, { lkey: 'k2', outcome: 'not_bought' }])), 'partial', 'partial');
eq(groupVerdict(g, { state: 'unknown' }), 'waiting', 'unknown -> nothing yet');
eq(groupVerdict(g, { state: 'failed' }), 'none', 'failed -> nothing');
eq(maxGroupsOf([{ lkey: 'a', group: 'max-1', name: 'X' }, { lkey: 'b', group: 'max-1' }, { lkey: 'c', group: 'lv-2' }, { lkey: 'd', group: 'max-2' }]).map((x) => x.lkeys), [['a', 'b']], 'groups');
eq(runIsOpen({ txs: [{ state: 'confirmed' }], groups: [{ state: 'ready' }] }), true, 'a combine to sign keeps the run');
eq(runIsOpen({ txs: [{ state: 'confirmed' }], groups: [{ state: 'combined' }] }), false, 'done');
const html = groupsHtml({ groups: [{ state: 'ready', name: 'Gargoya Lion', lkeys: ['k1', 'k2'] }, { state: 'partial', missing: 1, name: 'Gargoya Lion', lkeys: ['k1', 'k2'] }] });
console.log(JSON.stringify({ html, two: TWO_SIGNATURES }));
""", entry=STATIC / "card-checkout.js").stdout
    got = json.loads(out.strip().splitlines()[-1])
    assert got["two"] == "2 signatures: buy, then combine."
    assert 'data-co-action="combine-sign"' in got["html"] and "Sign the combine" in got["html"]
    assert "1 of 2 listings were not bought" in got["html"] and got["html"].count('data-co-action="combine-sign"') == 1
    js = (STATIC / "card-checkout.js").read_text(encoding="utf-8")
    sign = js[js.index("async function signCombine"):js.index("async function verifyCombine")]
    assert "'Posting'" in sign and "g.state = g.combine_tx ? 'combine-sent' : 'combine-unknown'" in sign
    build = js[js.index("async function buildCombine"):js.index("async function signCombine")]
    assert "requestBroadcastThroughGate" not in build                     # building never signs; the maintainer presses "Sign the combine"
