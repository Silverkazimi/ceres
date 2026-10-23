""",  (auto reload, auto
close after the final confirmation, total and pay buttons on top) and  (the Credits icon).

The maintainer's two real records (2026-10-15, by-id lookups, third-party names pseudonymised) replayed through the checkout's verify
paths: the purchase 74f9f4d5 (17 listings, DEC, success) -> green "Bought 17 of 17"; the combine e72cfe27 (17 cards -> 115 CC)
-> green "Combined: 115 CC, max level". success:false -> red with the game's text (escaped); no record -> amber; the 3-minute
mark -> amber "Not confirmed yet" with Check again, never red. Nothing is signed or broadcast; no network."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ceres import card_market, combine_evidence, market_purchase as mp, routes_market_purchase as routes
from ceres.app import create_app
from ceres.config import CeresConfig
from tests.ceres.vm_context import run_in_module_context

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"
FIX = Path(__file__).parent / "fixtures"
PURCHASE = json.loads((FIX / "ir9262_purchase_trx.json").read_text())["trx_info"]
COMBINE = json.loads((FIX / "ir9262_combine_trx.json").read_text())["trx_info"]
OWNER = "player0005"
PURCHASE_PAYLOAD = json.loads(PURCHASE["data"])
COMBINE_OP = ["custom_json", {"id": "sm_combine_cards", "json": COMBINE["data"], "required_auths": [], "required_posting_auths": [OWNER]}]


def _copy(info, **changes):
    out = json.loads(json.dumps(info))
    out.update(changes)
    return out


# ── the server verdicts, replayed from the recorded game answers ─────────────────────────────────────────────────────

def test_the_recorded_purchase_is_bought_17_of_17():
    verdict = mp.outcome(PURCHASE, OWNER, PURCHASE["id"], PURCHASE_PAYLOAD)
    assert verdict["status"] == "confirmed" and verdict["fill"] == "all"
    assert (verdict["bought"], verdict["requested"], verdict["num_cards"], verdict["paid_dec"]) == (17, 17, 17, 10095.2)
    # the game lists the bought ids per seller and in its own order (…-23 before …-24), unlike the request: still all bought
    assert PURCHASE_PAYLOAD["items"][1].endswith("-24") and PURCHASE_PAYLOAD["items"][2].endswith("-23")
    assert all(r["outcome"] == "bought" for r in verdict["items"])


def test_the_recorded_combine_is_confirmed_with_115_cc():
    payload = mp.combine_payload(COMBINE_OP, OWNER)
    verdict = mp.combine_outcome(COMBINE, OWNER, COMBINE["id"], payload, 115.0)
    assert verdict == {"status": "confirmed", "bcx": 115.0, "land_base_pp": 1147.826, "count": 17}
    assert "uid" not in json.dumps(verdict) and "C3-" not in json.dumps(verdict)        # no card ids reach the page
    # the record's collection power (2,400 = the survivor's own 60 CC) is not the inputs' sum: it plays no part here
    assert json.loads(COMBINE["result"])["updated_card"]["collection_power"] == 2400


def test_combine_success_false_is_failed_with_the_game_text_and_the_rest_is_never_failed():
    payload = mp.combine_payload(COMBINE_OP, OWNER)
    refused = _copy(COMBINE, success=False, error="Cards are locked <b>", result=json.dumps({"success": False, "error": "Cards are locked <b>"}))
    assert mp.combine_outcome(refused, OWNER, COMBINE["id"], payload) == {"status": "failed", "message": "Cards are locked <b>"}
    assert mp.combine_outcome(None, OWNER, COMBINE["id"], payload)["status"] == "not_found"
    assert mp.combine_outcome(_copy(COMBINE, success=None, result=None), OWNER, COMBINE["id"], payload)["status"] == "pending"
    other_nonce = dict(payload, n="other")
    assert mp.combine_outcome(COMBINE, OWNER, COMBINE["id"], other_nonce)["status"] == "mismatch"
    assert mp.combine_outcome(COMBINE, OWNER, COMBINE["id"], payload, 116.0)["status"] == "mismatch"
    assert mp.combine_outcome(COMBINE, "player_0099", COMBINE["id"], payload)["status"] == "mismatch"
    with pytest.raises(mp.PurchaseError):
        mp.combine_payload(["custom_json", {**COMBINE_OP[1], "required_posting_auths": ["player_0099"]}], OWNER)


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = create_app(CeresConfig(root=tmp_path / ".ceres", player=OWNER))
    if not any(getattr(r, "path", "") == "/api/market/checkout/combine-verify" for r in app.routes):
        app.include_router(routes.router)
    return TestClient(app)


def test_verify_route_confirms_the_recorded_purchase_and_drops_the_market_cache(client, monkeypatch):
    monkeypatch.setattr(routes, "lookup_trx_info", lambda get_json, tx: PURCHASE)
    card_market.CACHE._store["x"] = (0.0, "stale")
    body = client.post(f"/api/market/checkout/verify?player={OWNER}", json={"tx_id": PURCHASE["id"], "nonce": PURCHASE_PAYLOAD["n"],
                                                                         "expected_payload": PURCHASE_PAYLOAD}).json()
    assert body["status"] == "confirmed" and body["bought"] == 17 and body["fill"] == "all"
    assert card_market.CACHE._store == {}          # the next market read shows the bought listings gone


def test_combine_verify_route_on_the_recorded_combine(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "lookup_trx_info", lambda get_json, tx: COMBINE)
    cards = json.loads(COMBINE["data"])["cards"]
    records = [{"uid": c, "bcx": 60 if c == "C3-288-0W5IIU3Q8G" else 40 if c == "C3-288-20ARXY37CW" else 1} for c in cards]
    config = client.app.state.ceres_config
    combine_evidence.save(config, json.loads(COMBINE["data"])["n"], account=OWNER, uids=cards, records=records)
    body = client.post(f"/api/market/checkout/combine-verify?player={OWNER}", json={"tx_id": COMBINE["id"], "operation": COMBINE_OP}).json()
    assert body["status"] == "confirmed" and body["bcx"] == 115.0
    monkeypatch.setattr(routes, "lookup_trx_info", lambda get_json, tx: None)
    assert client.post(f"/api/market/checkout/combine-verify?player={OWNER}", json={"tx_id": COMBINE["id"], "operation": COMBINE_OP}).json()["status"] == "not_found"
    assert client.post(f"/api/market/checkout/combine-verify?player={OWNER}", json={"tx_id": "zz", "operation": COMBINE_OP}).status_code == 422


def test_a_differing_purchase_record_does_not_make_the_combine_step_red(client, monkeypatch):
    monkeypatch.setattr(routes, "lookup_trx_info", lambda get_json, tx: _copy(PURCHASE, player="player_0099"))
    lkeys = [card_market.listing_key({"market_id": m}) for m in PURCHASE_PAYLOAD["items"][1:9]]
    body = client.post(f"/api/market/checkout/combine?player={OWNER}", json={"tx_id": PURCHASE["id"], "nonce": PURCHASE_PAYLOAD["n"],
                                                                           "expected_payload": PURCHASE_PAYLOAD, "lkeys": lkeys}).json()
    assert body["status"] == "pending"


# ── the page: green / amber / red, the reminder, the clock, the auto close, the pay bar ─────────────────────────────

VM = r"""
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw Error(what + ': ' + JSON.stringify(a) + ' != ' + JSON.stringify(b)); };
const ok = (cond, what) => { if (!cond) throw Error(what); };
"""


def _vm(js: str) -> dict:
    out = run_in_module_context("(async () => {\n" + VM + js + "\n})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });",
                                entry=STATIC / "card-checkout.js").stdout
    return json.loads(out.strip().splitlines()[-1])


def test_outcome_lines_green_amber_red():
    purchase = mp.outcome(PURCHASE, OWNER, PURCHASE["id"], PURCHASE_PAYLOAD)
    got = _vm(f"""
const result = {json.dumps(purchase)};
const lkeys = result.items.map((r) => r.lkey);
const confirmed = {{ state: 'confirmed', result, lkeys, currency: 'DEC', price: 10095.2, copy_prices_dec: Object.fromEntries(lkeys.map((k, i) => [k, 100 + i])) }};
const run = {{ txs: [confirmed], groups: [{{ state: 'combined', name: 'Pelacor Bandit', lkeys: lkeys.slice(1, 9), bcx: 115, resulting_bcx: 115 }}] }};
console.log(JSON.stringify({{
  bought: txLineHtml(confirmed), all: outcomeHtml(run), green: runAllGreen(run),
  failed: txLineHtml({{ state: 'failed', message: '<img src=x onerror=alert(1)>', lkeys }}),
  missing: txLineHtml({{ state: 'unknown', lkeys }}),
  timeout: txLineHtml({{ state: 'timeout', lkeys, tx_id: 'a'.repeat(40) }}),
  timeoutChip: listingOutcome({{ state: 'timeout' }}, 'k'),
  ready: groupsHtml({{ groups: [{{ state: 'ready', name: 'X', lkeys: ['a', 'b'] }}] }}),
  decAmounts: [amountText('DEC', 2640), amountText('DEC', 10095.2), amountText('DEC', 1.2349)],
  combineTimeout: groupsHtml({{ groups: [{{ state: 'combine-timeout', name: 'X', lkeys: ['a', 'b'], combine_tx: 'b'.repeat(40) }}] }}),
  combineFailed: groupsHtml({{ groups: [{{ state: 'combine-failed', name: 'X', lkeys: ['a', 'b'], message: 'Cards are <locked>' }}] }}),
  notReady: groupsHtml({{ groups: [{{ state: 'not-ready', name: 'X', lkeys: ['a', 'b'] }}] }}),
  open: runAllGreen({{ txs: [confirmed], groups: [{{ state: 'ready' }}] }}),
  skipped: runAllGreen({{ txs: [confirmed], groups: [{{ state: 'skipped' }}] }}),
  partial: runAllGreen({{ txs: [{{ ...confirmed, result: {{ ...result, fill: 'partial' }} }}] }}),
}}));
""")
    assert 'data-state="ok"' in got["bought"] and "Bought 17 of 17. Paid $4.98 (10,095.2 DEC at the game&#39;s rate)." in got["bought"]
    assert got["decAmounts"] == ["2,640 DEC", "10,095.2 DEC", "1.235 DEC"]
    assert 'class="cm-section co-group" data-state="warn"' in got["ready"] and 'class="cm-note co-group-line" data-state="warn"' in got["ready"]
    assert got["all"].count('class="cm-note co-summary"') == 1
    assert '2 signatures: buy, then combine.' not in got["all"]   # the all-done line owns the complete result
    assert got["all"].count('/static/assets/land/token-dec.png') >= 17
    assert '100 DEC' in got["all"] and '116 DEC' in got["all"]
    assert '<details class="co-outcome-detail">' in got["all"]
    assert "All done: bought 17 of 17, combined into one max-level card." in got["all"] and got["green"] is True
    assert 'data-state="sold"' in got["failed"] and "&lt;img src=x onerror=alert(1)&gt;" in got["failed"] and "<img src=x" not in got["failed"]
    for amber in ("missing", "timeout"):
        assert 'data-state="warn"' in got[amber] and 'data-state="sold"' not in got[amber], amber
    assert "Not confirmed yet" in got["timeout"] and "do not buy these listings again" in got["timeout"]
    assert got["timeoutChip"] == "timeout"
    assert 'class="cm-section co-group" data-state="warn"' in got["combineTimeout"] and "Not confirmed yet" in got["combineTimeout"]
    assert 'data-co-action="combine-check"' in got["combineTimeout"] and "Check again" in got["combineTimeout"]
    assert 'data-state="danger"' in got["combineFailed"] and "Cards are &lt;locked&gt;" in got["combineFailed"]
    assert 'data-state="warn"' in got["notReady"] and 'data-state="danger"' not in got["notReady"]
    assert got["open"] is False and got["skipped"] is False and got["partial"] is False


def test_the_three_minute_mark_is_amber_check_again_not_red():
    got = _vm(r"""
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...rest) => realSetTimeout(fn, 0, ...rest);
let reads = 0;
globalThis.fetch = async (url) => { reads += 1; return { ok: true, status: 200, json: async () => ({ status: 'not_found' }) }; };
_checkoutForTests.player = 'player0005';
const g = { state: 'combine-sent', name: 'X', lkeys: ['a', 'b'], combine_tx: 'b'.repeat(40), operation: ['custom_json', {}], signed_at: new Date(Date.now() - 181000).toISOString() };
const tx = { state: 'sent', tx_id: 'a'.repeat(40), nonce: 'ab'.repeat(8), payload: {}, lkeys: ['a'], signed_at: new Date(Date.now() - 181000).toISOString(), asked_at: new Date(Date.now() - 190000).toISOString() };
_checkoutForTests.run = { token: 't', txs: [tx], groups: [g] };
_checkoutForTests.stage = 'outcome';
verifyCombine(g); verify(tx);
for (let i = 0; i < 50 && (g.state !== 'combine-timeout' || tx.state !== 'timeout'); i++) await new Promise((r) => realSetTimeout(r, 5));
console.log(JSON.stringify({ g: g.state, tx: tx.state, reads, html: outcomeHtml(_checkoutForTests.run), foot: footHtml() }));
""")
    assert got["g"] == "combine-timeout" and got["tx"] == "timeout" and got["reads"] == 3      # state check plus the retained transaction lookup, then amber
    assert 'data-state="sold"' not in got["html"] and 'is-danger' not in got["html"]
    assert got["html"].count("Not confirmed yet") >= 2 and 'data-co-action="check"' in got["foot"]


def test_a_combine_confirmed_by_the_game_turns_green_through_combine_verify():
    got = _vm(r"""
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...rest) => realSetTimeout(fn, 0, ...rest);
const urls = [];
globalThis.fetch = async (url) => { urls.push(String(url)); return { ok: true, status: 200, json: async () => ({ status: 'confirmed', bcx: 115, land_base_pp: 1147.826, count: 17 }) }; };
let combined = 0;
_checkoutForTests.player = 'player0005';
_checkoutForTests.deps = { onCombined: () => { combined += 1; } };
const g = { state: 'combine-sent', name: 'X', lkeys: ['a', 'b'], combine_tx: 'b'.repeat(40), operation: ['custom_json', {}], resulting_bcx: 115 };
_checkoutForTests.run = { token: 't', txs: [{ state: 'confirmed', result: { fill: 'all', bought: 17, requested: 17, items: [] }, lkeys: [] }], groups: [g] };
verifyCombine(g);
for (let i = 0; i < 50 && g.state !== 'combined'; i++) await new Promise((r) => realSetTimeout(r, 5));
console.log(JSON.stringify({ state: g.state, urls, combined, line: groupLineHtml(g), green: runAllGreen(_checkoutForTests.run) }));
""")
    assert got["state"] == "combined" and got["combined"] == 1 and got["green"] is True
    assert got["urls"] == ["/api/market/checkout/combine-verify?player=player0005"]                 # the game's record, not /api/op-verify
    assert "Combined: 115 CC, max level." in got["line"] and 'data-state="ok"' in got["line"]


def test_the_wallet_reminder_is_the_shared_component_with_its_clock():
    got = _vm(r"""
_checkoutForTests.player = 'player0005';
const asked = new Date(Date.now() - 30000).toISOString();
console.log(JSON.stringify({
  buy: txLineHtml({ state: 'asked', asked_at: asked, lkeys: [] }),
  combine: groupLineHtml({ state: 'combine-asked', asked_at: asked, lkeys: ['a', 'b'] }),
  checking: txLineHtml({ state: 'sent', signed_at: new Date(Date.now() - 42000).toISOString(), lkeys: [] }),
  clock: [walletClockText(0, 120000, 30000), walletClockText(0, 110000, 110000), elapsedText(0, 42000)],
  plain: walletWaitHtml('player0005', 'Active'),
  ms: WALLET_REQUEST_MS,
}));
""")
    for key, keytext in (("buy", "(Active key)"), ("combine", "")):
        html = got[key]
        assert 'class="wallet-wait is-' in html and "has-clock" in html and "data-wallet-clock" in html, key
        assert "Approve the request in the Keychain extension now" in html or "Check the Keychain app on your phone now" in html
        assert keytext in html and "If the time runs out, Ceres asks the game whether it went through. Do not sign it again." in html
    # the wallet's own request lifetime, 30 s gone: Keychain 120 s -> 1:30, HiveAuth 110 s -> 1:20 (this sandbox has no extension)
    ttl = int(re.search(r'data-ttl="(\d+)"', got["buy"]).group(1))
    assert ttl in (120000, 110000) and re.search(r"1:[23]\d left", got["buy"])
    assert 'data-state="warn"' in got["checking"] and "Checking with the game…" in got["checking"] and "0:42" in got["checking"]
    assert got["clock"] == ["1:30 left", "Time is up", "0:42"]
    assert "has-clock" not in got["plain"] and "data-wallet-clock" not in got["plain"]          # other callers unchanged
    assert got["ms"] == {"keychain": 120000, "hiveauth": 110000}
    gate = (STATIC / "broadcast-gate.js").read_text(encoding="utf-8")
    wallet = (STATIC / "wallet.js").read_text(encoding="utf-8")
    assert "KEYCHAIN_RESPONSE_TIMEOUT_MS = 120000" in gate and "reason: action === 'connect' ? 'expired' : 'watchdog' }), 110000)" in wallet


def test_pay_bar_on_top_and_credits_icon():
    got = _vm(r"""
_checkoutForTests.player = 'player0005';
_checkoutForTests.currency = 'CREDITS';
const quote = { quote_id: 'q', dec_price: 0.0005, max_per_transaction: 150, signing: { enabled: true },
  items: [{ lkey: 'aaaaaaaaaaaaaaa1', pid: '349-3-0', status: 'ok', price_usd: 0.01, seen_usd: 0.01 }],
  totals: { count: 1, usd: 0.01, DEC: 20, CREDITS: 10, transactions: [{ count: 1 }] },
  currencies: { DEC: { total: 20, balance: 5000, enough: true }, CREDITS: { total: 10, balance: 1000, enough: true } }, changes: {} };
console.log(JSON.stringify({ review: reviewHtml(quote), credits: amountHtml('CREDITS', 10), dec: amountHtml('DEC', 20), foot: (_checkoutForTests.stage = 'review', _checkoutForTests.quote = quote, footHtml()) }));
""")
    review = got["review"]
    assert review.index('class="co-paybar"') < review.index('class="co-rows"')                  # total + pay buttons above the list
    bar = review[review.index('class="co-paybar"'):review.index('class="co-rows"')]
    assert "Total ·" in bar and 'data-co-action="continue"' in bar and 'data-co-currency="CREDITS"' in bar and 'data-co-currency="DEC"' in bar
    assert "/static/assets/land/token-credits.png" in got["credits"] and "10 credits" in got["credits"]
    assert "/static/assets/land/token-dec.png" in got["dec"]
    assert 'data-co-action="continue"' not in got["foot"]
    # The public tree ships no game artwork: the icon must be on the recorded download list (game image
    # host, pinned SHA-256), and once downloaded it is a PNG.
    source_manifest = STATIC / "assets" / "sources.json"
    local = STATIC / "assets" / "land" / "token-credits.png"
    listed = False
    if source_manifest.is_file():
        sources = json.loads(source_manifest.read_text(encoding="utf-8"))
        icon = next(row for row in sources if row["path"] == "land/token-credits.png")
        assert icon["source_url"].startswith("https://d36mxiodymuqjm.cloudfront.net/") and icon["source_url"].endswith(".png")
        assert re.fullmatch(r"[0-9a-f]{64}", icon["sha256"]) and icon["byte_size"] > 0
        listed = True
    # on disk (a checkout that has downloaded the artwork) or on the download list -- never neither
    assert local.is_file() or listed
    if local.is_file():
        assert local.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    css = (STATIC / "card-checkout.css").read_text(encoding="utf-8")
    assert re.search(r"\.co-paybar \{ position: sticky;", css)


def test_auto_close_reload_and_cart_pay_bar_contracts():
    js = (STATIC / "card-checkout.js").read_text(encoding="utf-8")
    assert "AUTO_CLOSE_MS = 1500" in js
    settle = js[js.index("function settle()"):js.index("let tickTimer")]
    assert "runAllGreen(co.run)" in settle and "closeCheckout()" in settle and "AUTO_CLOSE_MS" in settle
    verify_combine = js[js.index("async function verifyCombine"):js.index("function footHtml")]
    assert "fetch(" not in verify_combine and "post('combine-verify'" in verify_combine
    cart = (STATIC / "card-cart.js").read_text(encoding="utf-8")
    assert "onCombined: () => cartAfterPurchase({ combined: true })" in cart and "cartAfterPurchase(); }," in cart
    assert "cart.deps?.reloadMarket?.()" in cart and "/api/capture/refresh?" in cart and "wait_for=settled" in cart
    market = (STATIC / "card-market.js").read_text(encoding="utf-8")
    assert "reloadMarket: () => cmReload(), reloadCards: deps.reloadCards" in market and "market.stale = true" in market
    assert "reloadCards: () => load()" in (STATIC / "card-browser.js").read_text(encoding="utf-8")


def test_cart_panel_pay_bar_sits_above_the_listings():
    out = run_in_module_context(r"""
_cartForTests.state = { items: [{ lkey: 'k1', pid: '349-3-0', name: 'Card', card_set: 'chaos', rarity: 1, foil: 0, level: 1, bcx: 1, price_usd: 1, seen_usd: 1, status: 'ok', added_at: 1 }], removed: [] };
console.log(JSON.stringify({ html: cartPanelHtml() }));
""", entry=STATIC / "card-cart.js").stdout
    html = json.loads(out.strip().splitlines()[-1])["html"]
    assert html.index('id="cm-checkout"') < html.index('cm-cart-item') and html.index("Total ·") < html.index("cm-cart-item")


def test_owned_cards_refresh_waits_for_the_board_read_then_reloads():
    out = run_in_module_context(r"""
(async () => {
  const calls = []; let reads = 0; let reloaded = 0;
  const fetcher = async (url, opts) => { calls.push((opts?.method || 'GET') + ' ' + url); if (String(url).includes('/status')) { reads += 1; return { ok: true, json: async () => ({ state: reads < 3 ? 'running' : 'idle' }) }; } return { ok: true, json: async () => ({}) }; };
  await cartRefreshOwned('player0005', () => { reloaded += 1; }, { fetcher });
  console.log(JSON.stringify({ calls, reloaded }));
})().catch((e) => { console.error(e); process.exit(1); });
""", entry=STATIC / "card-cart.js").stdout
    got = json.loads(out.strip().splitlines()[-1])
    assert got["calls"][0] == "POST /api/capture/refresh?player=player0005" and len(got["calls"]) == 4 and got["reloaded"] == 1
