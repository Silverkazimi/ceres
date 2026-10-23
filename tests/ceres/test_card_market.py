""": the Cards page's Market mode (the shop, phase 2) -- read only. : read automatically on entering Market mode
and on each settled filter change (the Load button is gone); the Maxed filter.

Server normalisation runs on a recorded, pseudonymised upstream fixture (no live network in any test); the client model
and the automatic, debounced read run in the vm harness."""

from __future__ import annotations

import json
import re
import socket
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ceres import card_market, routes_market
from ceres.app import create_app
from ceres.card_market import (MarketCache, cheapest_max_combo, market_url, maxed_offers, normalise_listing, pp_at, print_card_set,
                                read_market)
from ceres.config import CeresConfig
from tests.ceres.vm_context import run_in_module_context

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ir9182-market" / "market-2026-10-14.json").read_text(encoding="utf-8"))
DETAILS = {int(k): v for k, v in FIXTURE["card_details"].items()}
UIDISH = re.compile(r"\bC-TESTUID\d+|\b[0-9a-f]{40}-\d|\bplayer_0\d{3}\b|\bseller\b|\bmarket_id\b|\"uid\"|card_id\"")


def _getter(calls: list[str] | None = None):
    def get(url: str):
        parsed = urllib.parse.urlsplit(url)
        assert parsed.scheme == "https" and parsed.hostname == "api.splinterlands.com", url
        if calls is not None:
            calls.append(url)
        if parsed.path == "/market/for_sale_grouped":
            return FIXTURE["for_sale_grouped"]
        if parsed.path == "/settings":
            return FIXTURE["settings"]
        if parsed.path == "/market/market_query_by_card":
            raise AssertionError("a deep read without the Maxed filter or a full group")
        if parsed.path == "/market/market_query_grouped":
            query = urllib.parse.parse_qs(parsed.query)
            ids = {int(i) for i in query["card_ids"][0].split(",")}
            groups = FIXTURE["market_query_grouped"].get(query["foil"][0], [])
            return [g for g in groups if g["card_detail_id"] in ids]
        raise AssertionError(f"unexpected upstream path {parsed.path}")
    return get


def _read(filters=None, sort="cost_per_pp", calls=None, cache=None, maxed=False):
    return read_market(DETAILS, filters or {}, sort=sort, player="player0005", getter=_getter(calls), cache=cache or MarketCache(), maxed=maxed)


# server: normalisation on the recorded fixture

def test_print_identity_from_edition_and_tier():
    assert print_card_set(0, DETAILS[1]) == "alpha" and print_card_set(1, DETAILS[1]) == "beta"
    assert print_card_set(3, DETAILS[349]) == "chaos"            # a Chaos reward card carries edition 3
    assert print_card_set(14, DETAILS[731]) == "conclave"
    assert print_card_set(6, {"tier": 4}) == "gladius"
    assert print_card_set(21, {"tier": None}) == "land"


def test_listings_are_normalised_from_the_recorded_fixture():
    body = _read()
    rows = body["listings"]
    assert body["dec_usd"] == FIXTURE["settings"]["dec_price"]
    # 780 has no Land PP and 999999 is not in the card catalogue: neither is ever offered
    assert {r["card_detail_id"] for r in rows} == {1, 349, 456, 731}
    assert body["prints_on_sale"] == 6 and body["prints_read"] == 6 and not body["capped"]
    by_print: dict[tuple, list] = {}
    for r in rows:
        by_print.setdefault((r["card_detail_id"], r["edition"], r["foil"]), []).append(r)
    assert all(len(v) <= card_market.LISTINGS_PER_PRINT for v in by_print.values())
    # the upstream group mixes Alpha and Beta copies of card 1; each print keeps only its own edition
    alpha = by_print[(1, 0, 0)]
    beta = by_print.get((1, 1, 0), [])
    assert alpha and all(r["card_set"] == "alpha" for r in alpha) and all(r["card_set"] == "beta" for r in beta)
    required = {"name", "card_detail_id", "edition", "card_set", "rarity", "foil", "level", "bcx", "bcx_max", "land_base_pp",
                "max_level_pp", "price_usd", "price_dec", "pp_per_dec", "pp_per_dec_max", "market_url", "splinters"}
    for r in rows:
        assert required <= r.keys()
        assert r["price_currency"] == "USD" and r["price_usd"] > 0
        assert r["market_url"] == f"https://splinterlands.com/card-detail/{r['card_detail_id']}/{card_market.FOIL_FRAME[r['foil']]}/{r['edition']}?tab=for-sale"


def test_listing_pp_agrees_with_the_cards_page_rules():
    # Every regular / gold listing reports land_base_pp; the formula (max-level PP x min(CC, max CC) / max CC) agrees with it.
    for r in _read()["listings"]:
        assert r["pp_source"] == "listing"
        if r["foil"] in (0, 1):
            assert r["max_level_pp"] is not None
            derived = pp_at(r["max_level_pp"], r["bcx"], r["bcx_max"])
            assert derived == pytest.approx(r["land_base_pp"], rel=0.002, abs=0.01), r["name"]
    black = [r for r in _read()["listings"] if r["foil"] == 3]
    assert black and all(r["maxed"] and r["max_level_pp"] == r["land_base_pp"] for r in black)  # always max level


def test_no_uid_seller_or_market_id_leaves_the_server():
    text = json.dumps(_read())
    assert not UIDISH.search(text), UIDISH.search(text).group(0)
    own = [r for r in _read()["listings"] if r["own"]]
    assert len(own) == 1  # the maintainer's own listing is marked, not named


def test_a_press_costs_a_fixed_number_of_upstream_reads_and_is_cached_60_s():
    cache = MarketCache()
    calls: list[str] = []
    first = _read(calls=calls, cache=cache)
    paths = [urllib.parse.urlsplit(u).path for u in calls]
    assert paths.count("/market/for_sale_grouped") == 1 and paths.count("/settings") == 1
    assert paths.count("/market/market_query_grouped") == 3  # one per foil class read (regular, gold, black)
    assert first["upstream_calls"] == paths
    again: list[str] = []
    second = _read(calls=again, cache=cache)
    assert again == [] and second["upstream_calls"] == [] and second["listings"] == first["listings"]


def test_server_filters_choose_which_prints_are_read():
    calls: list[str] = []
    body = _read({"foil": "3"}, calls=calls)
    assert {r["foil"] for r in body["listings"]} == {3}
    assert [urllib.parse.urlsplit(u).path for u in calls].count("/market/market_query_grouped") == 1
    assert {r["card_set"] for r in _read({"set": "alpha"})["listings"]} == {"alpha"}
    assert {r["rarity"] for r in _read({"rarity": "rare"})["listings"]} == {"rare"}
    assert _read({"splinter": "dragon"})["listings"] == []


# price / PP math

ITEM = {"card_detail_id": 349, "name": "Gargoya Lion", "edition": 3, "card_set": "chaos", "rarity": "common", "foil": 0,
        "gold": False, "splinters": ["Neutral"], "max_level": 10, "bcx_max": 400, "max_level_pp": 1000.0,
        "low_price_per_cc_usd": 0.003}


def test_price_and_pp_math():
    row = {"bcx": 100, "buy_price": 1.0, "price_bcx": 0.01, "level": 6, "currency": "USD", "land_base_pp": "250.000", "seller": "x"}
    out = normalise_listing(row, ITEM, dec_usd=0.0005, player="player0005")
    assert out["price_dec"] == pytest.approx(2000.0)
    assert out["land_base_pp"] == 250.0 and out["pp_per_dec"] == pytest.approx(0.125) and out["dec_per_pp"] == pytest.approx(8.0)
    # max level: the 300 CC still missing at today's cheapest 0.003 USD per CC -> 1.90 USD = 3,800 DEC for 1,000 PP
    assert out["missing_cc"] == 300 and out["max_cost_usd"] == pytest.approx(1.9) and out["max_cost_dec"] == pytest.approx(3800.0)
    assert out["pp_per_dec_max"] == pytest.approx(1000 / 3800) and out["dec_per_pp_max"] == pytest.approx(3.8)
    # no PP reported -> derived from the same rules as the Cards page
    derived = normalise_listing({**row, "land_base_pp": None}, ITEM, dec_usd=0.0005, player=None)
    assert derived["land_base_pp"] == pytest.approx(250.0) and derived["pp_source"] == "formula"
    # overcombined: CC past max adds no PP, nothing is missing
    over = normalise_listing({**row, "bcx": 450, "land_base_pp": None}, ITEM, dec_usd=0.0005, player=None)
    assert over["land_base_pp"] == 1000.0 and over["maxed"] and over["max_cost_usd"] == 1.0
    # unknown PP stays null, never zero
    unknown = normalise_listing({**row, "land_base_pp": None}, {**ITEM, "max_level_pp": None}, dec_usd=0.0005, player=None)
    assert unknown["land_base_pp"] is None and unknown["pp_per_dec"] is None and unknown["pp_per_dec_max"] is None
    # no DEC price -> USD still there, DEC figures null
    no_dec = normalise_listing(row, ITEM, dec_usd=None, player=None)
    assert no_dec["price_dec"] is None and no_dec["pp_per_usd"] == 250.0
    assert normalise_listing({**row, "currency": "DEC"}, ITEM, dec_usd=0.0005, player=None) is None


def test_pp_at_is_linear_to_max_cc_then_flat():
    assert pp_at(1000.0, 200, 400) == 500.0
    assert pp_at(1000.0, 500, 400) == 1000.0
    assert pp_at(5000.0, 38, 0) == 5000.0     # black foil: always max level
    assert pp_at(None, 10, 400) is None and pp_at(1000.0, 10, None) is None


def test_market_url_is_the_official_card_page():
    assert market_url(349, 0, 3) == "https://splinterlands.com/card-detail/349/regular/3?tab=for-sale"
    assert market_url(1, 1, 0).endswith("/1/gold/0?tab=for-sale") and market_url(731, 4, 14).endswith("/731/black/14?tab=for-sale")
    assert market_url(None, 0, 3) is None


# route

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    monkeypatch.setattr(card_market, "_default_get", _getter())
    monkeypatch.setattr(card_market, "CACHE", MarketCache())
    monkeypatch.setattr(routes_market, "load_details_by_id", lambda: DETAILS)
    return TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player="player0005")))


def test_route_shape_and_rate_limit(client):
    response = client.get("/api/market?player=player0005&rarity=common&sort=cost_per_pp")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["player"] == "player0005" and body["filters"] == {"rarity": "common"} and body["read_at"].endswith("Z")
    assert body["count"] == len(body["listings"]) > 0 and {r["rarity"] for r in body["listings"]} == {"common"}
    assert not UIDISH.search(response.text)
    assert client.get("/api/market?player=player0005&foil=gold").status_code == 400
    statuses = [client.get("/api/market?player=player0005").status_code for _ in range(30)]
    assert statuses[:28] == [200] * 28 and statuses[-1] == 429  # bucket "market" 30 reads a minute per client


def test_route_upstream_failure_is_502(client, monkeypatch):
    monkeypatch.setattr(card_market, "_default_get", lambda url: (_ for _ in ()).throw(RuntimeError("down")))
    assert client.get("/api/market?player=player0005").status_code == 502


def test_cards_page_has_no_load_button_and_a_maxed_filter(client):
    page = client.get("/cards?player=player0005")
    assert page.status_code == 200 and "/api/market" not in page.text  # the read is made by card-market.js, not the HTML
    assert 'id="cm-load"' not in page.text and 'Load market listings' not in page.text and 'id="cb-mode-market"' in page.text
    # removes this pill from My cards; the Level dropdown covers it. Market retains Max.
    assert re.search(r'<label class="[^"]*cm-only"[^>]*><span class="ceres-btn__check" aria-hidden="true"></span><input type="checkbox" id="cb-maxed" aria-label="Maxed cards"><span>Max</span></label>', page.text)
    assert 'id="cb-level"' in page.text


# client: model (vm)

MODEL = STATIC / "card-market-model.js"
ROWS_JS = r"""
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw Error(what + ': ' + JSON.stringify(a) + ' != ' + JSON.stringify(b)); };
const r = (name, over) => ({ name, card_set: 'chaos', rarity: 'common', foil: 0, splinters: ['Fire'], land_base_pp: 100, max_level_pp: 1000,
  price_dec: 100, max_cost_dec: 2000, ...over, get price_usd() { return this.price_dec * 0.0005; },
  get max_cost_usd() { return this.max_cost_dec === null ? null : this.max_cost_dec * 0.0005; } });
const rows = [
  r('Cheap now', { price_dec: 50 }),                         // 0.5 DEC/PP now, 2.0 at max
  r('Cheap at max', { price_dec: 200, max_cost_dec: 800 }),   // 2.0 now, 0.8 at max
  r('Big', { land_base_pp: 1000, price_dec: 1500, max_cost_dec: 1500, card_set: 'alpha', rarity: 'rare' }),  // 1.5 / 1.5
  r('Unknown PP', { land_base_pp: null, max_level_pp: null, max_cost_dec: null, price_dec: 1 }),
];
"""


def _vm(body: str) -> None:
    run_in_module_context(ROWS_JS + body, entry=MODEL)


def test_sorts_by_cost_per_pp_price_and_pp():
    _vm(r"""
const names = (sort) => cmSort(rows, sort).map((x) => x.name);
eq(names('cost_per_pp'), ['Cheap now', 'Big', 'Cheap at max', 'Unknown PP'], 'cost per PP, unknown last');
eq(Object.keys(CM_SORTS), ['cost_per_pp', 'price', 'pp'], ': no "Cost per PP at max level" sort');
eq(names('price'), ['Unknown PP', 'Cheap now', 'Cheap at max', 'Big'], 'price');
eq(names('pp'), ['Big', 'Cheap now', 'Cheap at max', 'Unknown PP'], 'PP, most first; : equal PP, cheaper first');
eq([cmPpPerDec(rows[0]), cmPpPerDecMax(rows[1]), cmDecPerPp(rows[3])], [2, 1.25, null], 'ratios');
const s = cmSummary(rows);
eq([s.listings, s.cheapest.name, s.cheapest.rate, s.cheapest.dec], [4, 'Cheap now', 4000, 0.5], 'summary: listings shown and best PP per $');
eq([cmPpPerUsd(rows[0]), cmPpPerUsdMax(rows[1])], [4000, 2500], 'PP per $');
""")


def test_filters_reuse_the_cards_row_and_the_query_follows_server_filters():
    _vm(r"""
const base = { q: '', set: '', rarity: '', foil: '', splinter: '', status: 'staked', level: 'maxed', min_pp: '', max_pp: '', ability: true };
eq(cmView(rows, base, 'cost_per_pp').tiles.length, 4, 'status / level / ability do not apply to listings');
eq(cmView(rows, { ...base, set: 'alpha' }).tiles.map((t) => t.card.name), ['Big'], 'set');
eq(cmView(rows, { ...base, min_pp: '500' }).tiles.map((t) => t.card.name), ['Big'], 'PP range');
const t = cmView(rows, base).tiles[0];
eq([t.status, t.plot, t.unavailable, t.card.no_pp, Array.isArray(t.card.land_abilities)], ['idle', null, '', false, true], 'a listing tile');
eq(cmFiltersChanged({ rarity: 'rare' }, { ...base, rarity: 'rare', min_pp: '9' }), true, ': the PP range is a server filter');
eq(cmFiltersChanged({ min_pp: '3,000' }, { ...base, min_pp: '3000' }), false, 'the same number written two ways');
eq(cmQuery('player0005', { ...base, min_pp: '3,000', max_pp: 'x' }, 'cost_per_pp'), '/api/market?player=player0005&min_pp=3000&sort=cost_per_pp', 'PP query');
eq(cmFiltersChanged({ rarity: 'rare' }, { ...base, rarity: 'epic' }), true, 'server filter changed');
eq(cmQuery('player0005', { ...base, rarity: 'rare', foil: '1' }, 'price'), '/api/market?player=player0005&rarity=rare&foil=1&sort=price', 'query');
eq(cmQuery('player0005', { ...base, maxed: true }, 'cost_per_pp'), '/api/market?player=player0005&maxed=1&sort=cost_per_pp', 'maxed query');
eq(cmFiltersChanged({ maxed: false }, { ...base, maxed: true }), true, 'maxed is a server filter');
eq(cmView([...rows, r('Maxed one', { maxed: true })], { ...base, maxed: true }).tiles.map((t) => t.card.name), ['Maxed one'], 'maxed only');
eq([cmFormatRatio(0.3698), cmFormatRatio(0.0823), cmFormatRatio(12.149), cmFormatRatio(2284.79), cmFormatRatio(null)],
   ['0.37', '0.082', '12.1', '2,285', '—'], 'ratio format');
eq([cmFormatDec(2284.79), cmFormatUsd(1.128), cmFormatUsd(0.013)], ['2,285', '$1.13', '$0.013'], 'money format');
""")


# client: the market is read only on the button (vm, the real card-market.js)

FAKE_DOM = r"""
context.__fetches = [];
context.__els = {};
function __el(id) {
  if (!context.__els[id]) {
    const listeners = {};
    context.__els[id] = {
      id, hidden: false, innerHTML: '', textContent: '', value: '', dataset: {}, attrs: {},
      addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
      fire(type) { return Promise.all((listeners[type] || []).map((fn) => fn({ type }))); },
      setAttribute(k, v) { this.attrs[k] = String(v); }, removeAttribute(k) { delete this.attrs[k]; },
      insertAdjacentHTML(_where, html) { this.innerHTML += html; },
      getBoundingClientRect() { return { top: 99999, height: 0 }; },
    };
  }
  return context.__els[id];
}
context.document = { ...context.document, getElementById: __el, querySelectorAll() { return []; }, body: { dataset: {} } };
context.__signals = [];
context.__state = { hold: false };
context.__fakeFetch = (url, init) => {
  context.__fetches.push(String(url));
  context.__signals.push(init && init.signal);
  if (context.__state.hold) return new Promise((_, reject) => init.signal.addEventListener('abort', () => reject(Object.assign(Error('aborted'), { name: 'AbortError' }))));
  return Promise.resolve({ ok: true, status: 200, json: async () => ({ read_at: '2026-10-14T12:00:00Z', prints_read: 1, prints_matching: 1, dec_usd: 0.0005,
    listings: [{ name: 'Gargoya Lion', card_detail_id: 349, edition: 3, card_set: 'chaos', rarity: 'common', foil: 0, level: 9, max_level: 10, bcx: 338,
      bcx_max: 400, land_base_pp: 845, max_level_pp: 1000, price_usd: 1.128, price_dec: 2256, max_cost_dec: 2628, max_cost_usd: 1.314, missing_cc: 62,
      splinters: ['Neutral'], market_url: 'https://splinterlands.com/card-detail/349/regular/3?tab=for-sale', pp_source: 'listing' }] }) });
};
context.fetch = context.__fakeFetch;
context.window.fetch = context.__fakeFetch;
"""

AUTO_JS = r"""
(async () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const filters = { q: '', set: '', rarity: 'common', foil: '', splinter: '', min_pp: '', max_pp: '', maxed: false };
  const drawn = [];
  initMarket({ tileHtml: (tile) => { if (!tile.skeleton) drawn.push(tile); return '<article class="cb-tile"></article>'; }, filters: () => filters,
    player: () => 'player0005', renderCards() {} });
  await wait(10);
  if (__fetches.length) throw Error('read while in My cards mode: ' + __fetches.join(', '));
  // entering Market mode reads at once
  await __els['cb-mode-market'].fire('click');
  await wait(20);
  if (__fetches.length !== 1 || !__fetches[0].startsWith('/api/market?player=player0005&rarity=common&sort=cost_per_pp')) throw Error('entry: ' + __fetches.join(', '));
  // the tile has the one-click maxed button, no market link
  if (drawn.length !== 1 || !drawn[0].extra || /View on market/.test(drawn[0].extra.after) || /<a /.test(drawn[0].extra.after) || !/data-cm-action="add-max"/.test(drawn[0].extra.after)) throw Error('tile: ' + (drawn[0] && drawn[0].extra.after));
  // the price badge is drawn at the art's bottom edge (extra.bottom), no longer in the foot
  if (!/2,256/.test(drawn[0].extra.bottom) || !/token-dec\.png/.test(drawn[0].extra.bottom) || !/slot-wait-badge cm-price-badge/.test(drawn[0].extra.bottom) || drawn[0].extra.foot) throw Error('price badge: ' + drawn[0].extra.bottom);
  if (!/PP\/\$/.test(drawn[0].extra.metrics) || /at max level/.test(drawn[0].extra.metrics) || /cm-metric-max/.test(drawn[0].extra.metrics)) throw Error('metrics: ' + drawn[0].extra.metrics);
  if (!/listing/.test(__els['cm-summary'].innerHTML) || !/Best .* PP\/\$/.test(__els['cm-summary'].innerHTML) || /Filters changed/.test(__els['cm-summary'].innerHTML)) throw Error('summary: ' + __els['cm-summary'].innerHTML);
  // leaving and re-entering with the same filters re-uses the read
  await __els['cb-mode-cards'].fire('click'); await __els['cb-mode-market'].fire('click');
  await wait(20);
  if (__fetches.length !== 1) throw Error('same filters read again: ' + __fetches.join(', '));
  // the PP range is a server filter now -- a change reads again after the debounce, with min_pp in the query
  filters.min_pp = '100';
  await __els['cm-sort'].fire('change');
  await wait(450);
  if (__fetches.length !== 2 || !/rarity=common&min_pp=100&sort=/.test(__fetches[1])) throw Error('a PP change did not read: ' + __fetches.join(', '));
  filters.min_pp = '';
  await wait(20);
  // three quick server-filter changes: one read, after the debounce, with the settled filters
  filters.rarity = 'rare'; await __els['cm-sort'].fire('change');
  await wait(100);
  filters.rarity = 'epic'; await __els['cm-sort'].fire('change');
  await wait(100);
  filters.maxed = true; await __els['cm-sort'].fire('change');
  await wait(200);
  if (__fetches.length !== 2) throw Error('read before the debounce settled: ' + __fetches.join(', '));
  if (__els['cm-loading'].hidden) throw Error('no spinner while a read is pending');
  await wait(300);
  if (__fetches.length !== 3 || !/rarity=epic&maxed=1&sort=/.test(__fetches[2])) throw Error('settled read: ' + __fetches.join(', '));
  await wait(20);
  // a read in flight is aborted when the filters change again
  __state.hold = true;
  filters.rarity = 'legendary'; await __els['cm-sort'].fire('change');
  await wait(450);
  if (__fetches.length !== 4) throw Error('fourth read: ' + __fetches.join(', '));
  filters.rarity = 'rare'; await __els['cm-sort'].fire('change');
  await wait(20);
  if (!__signals[3].aborted) throw Error('the stale read was not aborted');
  __state.hold = false;
  await wait(450);
  if (__fetches.length !== 5 || !/rarity=rare&maxed=1/.test(__fetches[4])) throw Error('after abort: ' + __fetches.join(', '));
})().catch((error) => { console.error(error); process.exit(1); });
"""


def test_market_reads_on_entry_and_on_each_settled_filter_change():
    run_in_module_context(AUTO_JS, entry=STATIC / "card-market.js", extra_context_js=FAKE_DOM)


def test_page_wiring_and_no_buy_code():
    page = (STATIC / "cards.html").read_text(encoding="utf-8")
    browser = (STATIC / "card-browser.js").read_text(encoding="utf-8")
    js = (STATIC / "card-market.js").read_text(encoding="utf-8")
    assert "import { initMarket, setMode } from './card-market.js';" in browser  # setMode for the purchases mode and "initMarket({" in browser
    assert "/api/market" not in browser  # the Cards module never reads the market
    # two reads -- the filter read and Load more (the next page of the same query)
    assert js.count("fetch(") == 2 and re.search(r"async function loadMarket\(url\)[\s\S]*?fetch\(url", js)
    # Load more names the print where the last page's window ended (start=next_start)
    assert re.search(r"export async function cmLoadMore\(\)[\s\S]*?fetch\(`\$\{market\.url\}&page=\$\{next\}\$\{from\}`", js)
    assert "'cm-load'" not in js and "Load market listings" not in js and "Filters changed" not in js
    assert "export const CM_DEBOUNCE_MS = 400;" in js and "new AbortController()" in js
    for source in (js, (STATIC / "card-market-model.js").read_text(encoding="utf-8"), (ROOT / "ceres" / "card_market.py").read_text(encoding="utf-8"),
                   (ROOT / "ceres" / "routes_market.py").read_text(encoding="utf-8")):
        assert not re.search(r"sm_market_purchase|market_purchase|requestCustomJson|requestTransfer|broadcast|keychain|sign\(", source, re.I)
    # the two modes are icon buttons with accessible names
    assert 'data-header-icon="myCards" aria-label="My cards"' in page and 'data-header-icon="market" aria-label="Market"' in page and 'Load market listings' not in page
    contract = (Path(__file__).parent / "fixtures" / "ceres-button-contract.json").read_text(encoding="utf-8")
    assert '"selector": "#cb-mode-cards, #cb-mode-market' in contract and '"#cm-load"' not in contract and '"selector": "#cb-maxed"' in contract
