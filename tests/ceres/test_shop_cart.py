""" the Market's card menu, the cart, the price badge at the bottom of the art,
the tile hover (fade and border, no zoom) and loading without layout shift.

. . (maintainer decision): checkout buys from Ceres in a later queue; Checkout is present but disabled.
No live network: the upstream is a synthetic getter; the client runs in the vm harness."""

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
from ceres.card_market import (MarketCache, check_listings, cheapest_covers, cheapest_max_combo, level_bundles, listing_key,
                                normalise_listing, parse_print_ids, read_print)
from ceres.config import CeresConfig
from tests.ceres.test_card_market import FAKE_DOM, UIDISH

# the card menu of one print names each listing's seller (and only the menu); ids stay out everywhere
MENU_IDISH = re.compile(UIDISH.pattern.replace(r"|\bseller\b", "").replace(r"|\bplayer_0\d{3}\b", ""))
from tests.ceres.vm_context import run_in_module_context

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ir9182-market" / "market-2026-10-14.json").read_text(encoding="utf-8"))
DETAILS = {int(k): v for k, v in FIXTURE["card_details"].items()}

# Gargoya Lion, Chaos common regular: level table [1, 5, 14, 30, 60, 100, 150, 220, 300, 400] (ceres/data/level_thresholds.json)
ITEM = {"card_detail_id": 349, "name": "Gargoya Lion", "edition": 3, "card_set": "chaos", "rarity": "common", "foil": 0, "gold": False,
        "splinters": ["Neutral"], "max_level": 10, "bcx_max": 400, "max_level_pp": 1000.0, "low_price_per_cc_usd": 0.003, "qty": 7}


def _row(n, bcx, price, seller="x", **over):
    return {"market_id": f"{n:040x}-0", "uid": f"C-TESTUID{n:04d}", "bcx": bcx, "buy_price": price, "price_bcx": price / bcx, "level": 1,
            "currency": "USD", "seller": seller, "land_base_pp": None, "type": "SELL", "card_detail_id": 349, "edition": 3, "foil": 0, **over}


ROWS = [_row(1, 338, 1.0), _row(2, 60, 0.3), _row(3, 1, 0.01), _row(4, 1, 0.01), _row(5, 420, 1.9), _row(6, 300, 0.1, seller="player0005"),
        _row(7, 20, 0.5)]


def _listings(rows=ROWS, player="player0005"):
    out = [normalise_listing(r, ITEM, 0.0005, player) for r in rows]
    card_market.over_cheapest(out)
    return out


# server: bundles per level

def test_listing_key_is_opaque_and_stable():
    a, b = listing_key(ROWS[0]), listing_key(dict(ROWS[0]))
    assert a == b and re.fullmatch(r"[0-9a-f]{16}", a) and listing_key(ROWS[1]) != a
    assert listing_key({"uid": "C-1"}) is None  # no market id: no key, never the uid
    text = json.dumps(_listings())
    assert not UIDISH.search(text) and "C-TESTUID" not in text and "seller" not in text


def test_cheapest_covers_agree_with_the_maxed_combination():
    offers = [(300, 0.9), (100, 0.5), (250, 0.7), (150, 0.45), (60, 0.2), (1, 0.01)]
    thresholds = [1, 5, 14, 30, 60, 100, 150, 220, 300, 400]
    covers = cheapest_covers(offers, thresholds, 400)
    assert sorted(covers[-1]) == sorted(cheapest_max_combo(offers, 400))
    assert covers[0] == [5]  # one CC: the cheapest single offer
    for threshold, chosen in zip(thresholds, covers):
        assert sum(offers[i][0] for i in chosen) >= threshold
    assert cheapest_covers([(10, 1.0)], [1, 400], 400) == [[0], None]


def test_level_bundles_max_first_own_listing_excluded_and_folded():
    bundles = level_bundles(ITEM, _listings(), 0.0005)
    levels = [b["level"] for b in bundles]
    assert levels == sorted(levels, reverse=True) and levels[0] == 10
    top = bundles[0]
    # max level: 338 + 60 + 1 + 1 = 400 CC for 1.32 beats the single 420 CC listing at 1.90; the maintainer's 300 CC listing at 0.10 is never used
    assert top["max"] and top["reachable"] and top["bcx"] == 400 and top["price_usd"] == pytest.approx(1.32) and top["count"] == 4
    assert top["land_base_pp"] == 1000.0 and top["pp_per_usd"] == pytest.approx(1000 / 1.32) and top["price_dec"] == pytest.approx(1.32 / 0.0005)
    own_key = listing_key(ROWS[5])
    assert all(own_key not in b.get("keys", []) for b in bundles)
    # every listed set appears once: a level whose cheapest set reaches a higher level is folded into that level
    signatures = [tuple(sorted(b["keys"])) for b in bundles if b["reachable"]]
    assert len(signatures) == len(set(signatures))
    for b in bundles:
        assert b["reached_level"] >= b["level"]
    assert {"level": 1} and any(b["level"] == 1 and b["price_usd"] == pytest.approx(0.01) for b in bundles)


def test_unreachable_levels_say_what_is_missing():
    bundles = level_bundles(ITEM, _listings([_row(1, 60, 0.3), _row(2, 20, 0.1)]), 0.0005)
    top = [b for b in bundles if not b["reachable"]]
    assert top and top[0]["level"] == 10 and top[0]["cc_needed"] == 400 and top[0]["cc_available"] == 80
    assert all(b["level"] > 5 for b in top)  # 80 CC reach level 5 (60 CC), not 6 (100 CC)


def test_black_foil_bundle_is_the_cheapest_single_copy():
    black = {**ITEM, "foil": 3, "bcx_max": 0, "max_level_pp": None}
    rows = [_row(1, 38, 9.0, foil=3, land_base_pp="5000"), _row(2, 40, 7.0, foil=3, land_base_pp="5000")]
    bundles = level_bundles(black, [normalise_listing(r, black, 0.0005, None) for r in rows], 0.0005)
    assert len(bundles) == 1 and bundles[0]["price_usd"] == 7.0 and bundles[0]["count"] == 1 and bundles[0]["max"]


def test_over_cheapest_flag():
    rows = _listings()
    pricey = next(r for r in rows if r["bcx"] == 20)  # 0.025 per CC vs the cheapest 0.000333 (the maintainer's own)
    assert pricey["over_flag"] and pricey["over_cheapest"] > 1
    assert not next(r for r in rows if r["bcx"] == 338)["over_flag"] is None


def _getter(calls, rows=ROWS):
    def get(url):
        parsed = urllib.parse.urlsplit(url)
        assert parsed.hostname == "api.splinterlands.com"
        calls.append(url)
        if parsed.path == "/market/for_sale_grouped":
            return FIXTURE["for_sale_grouped"]
        if parsed.path == "/settings":
            return FIXTURE["settings"]
        if parsed.path == "/market/market_query_by_card":
            query = urllib.parse.parse_qs(parsed.query)
            assert query["limit"] == [str(card_market.MAXED_DEEP_LISTINGS)]
            if (query["card_detail_id"], query["edition"], query["foil"]) == (["349"], ["3"], ["0"]):
                return rows
            return []
        raise AssertionError(parsed.path)
    return get


def test_read_print_menu_body():
    calls: list[str] = []
    cache = MarketCache()
    body = read_print(DETAILS, 349, 3, 0, player="player0005", getter=_getter(calls), cache=cache)
    assert body["print"]["id"] == "349-3-0" and body["print"]["name"] == "Gargoya Lion" and body["print"]["max_level"] == 10
    assert body["count"] == len(ROWS) and body["on_sale"] == 639 and body["truncated"]  # upstream gives at most 60 per print
    assert all(r["lkey"] for r in body["listings"]) and sum(r["own"] for r in body["listings"]) == 1
    assert body["bundles"][0]["max"] and body["bundles"][0]["price_usd"] == pytest.approx(1.32)
    assert not MENU_IDISH.search(json.dumps(body)) and "C-TESTUID" not in json.dumps(body)
    # the deep read is the Maxed deep read's own URL: one cache entry for both
    assert card_market.deep_url({"card_detail_id": 349, "foil": 0, "edition": 3}) in calls
    again: list[str] = []
    read_print(DETAILS, 349, 3, 0, player="player0005", getter=_getter(again), cache=cache)
    assert again == []
    assert read_print(DETAILS, 999, 3, 0, getter=_getter([]), cache=MarketCache()) is None


def test_check_listings_reports_live_keys():
    body = check_listings(DETAILS, [(349, 3, 0), (456, 3, 0)], player="player0005", getter=_getter([], rows=ROWS[1:]), cache=MarketCache())
    live = body["prints"]["349-3-0"]["listings"]
    assert listing_key(ROWS[0]) not in live and listing_key(ROWS[1]) in live and live[listing_key(ROWS[1])]["price_usd"] == 0.3
    assert body["prints"]["349-3-0"]["complete"] is False  # 639 on sale, 6 read
    assert parse_print_ids("349-3-0,bad,1-0-1,349-3-0") == [(349, 3, 0), (1, 0, 1)]
    assert len(parse_print_ids(",".join(f"{i}-3-0" for i in range(40)))) == card_market.CHECK_PRINTS


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    monkeypatch.setattr(card_market, "_default_get", _getter([]))
    monkeypatch.setattr(card_market, "CACHE", MarketCache())
    monkeypatch.setattr(routes_market, "load_details_by_id", lambda: DETAILS)
    return TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player="player0005")))


def test_routes_print_and_check(client):
    response = client.get("/api/market/print?card_detail_id=349&edition=3&foil=0&player=player0005")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["print"]["id"] == "349-3-0" and body["bundles"] and not MENU_IDISH.search(response.text)
    assert client.get("/api/market/print?card_detail_id=349&edition=3&foil=9").status_code == 400
    assert client.get("/api/market/print?card_detail_id=999&edition=3&foil=0").status_code == 404
    check = client.get("/api/market/check?prints=349-3-0&player=player0005")
    assert check.status_code == 200 and "349-3-0" in check.json()["prints"]
    assert client.get("/api/market/check?prints=nonsense").status_code == 400
    # the same "market" bucket as /api/market (30 a minute per client)
    statuses = [client.get("/api/market/print?card_detail_id=349&edition=3&foil=0").status_code for _ in range(30)]
    assert statuses[-1] == 429 and client.get("/api/market?player=player0005").status_code == 429


# client: the cart model (vm)

MODEL = STATIC / "card-cart-model.js"
CART_JS = r"""
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw Error(what + ': ' + JSON.stringify(a) + ' != ' + JSON.stringify(b)); };
const L = (k, bcx, usd, over = {}) => ({ lkey: k, name: 'Gargoya Lion', card_detail_id: 349, edition: 3, foil: 0, card_set: 'chaos', rarity: 'common',
 level: 1, bcx, land_base_pp: bcx * 2.5, price_usd: usd, price_dec: usd / 0.0005, ...over });
const listings = [L('aaaaaaaaaaaaaaa1', 338, 1.0), L('aaaaaaaaaaaaaaa2', 60, 0.3), L('aaaaaaaaaaaaaaa3', 1, 0.01), L('aaaaaaaaaaaaaaa4', 1, 0.01),
 L('aaaaaaaaaaaaaaa5', 420, 1.9), L('aaaaaaaaaaaaaaa6', 300, 0.1, { own: true }), L('aaaaaaaaaaaaaaa7', 400, 2.5)];
const data = { print: { id: '349-3-0', bcx_max: 400, max_level: 10 }, truncated: false, listings,
 bundles: [{ level: 10, max: true, reachable: true, keys: ['aaaaaaaaaaaaaaa1', 'aaaaaaaaaaaaaaa2', 'aaaaaaaaaaaaaaa3', 'aaaaaaaaaaaaaaa4'] }] };
"""


def _cart_vm(body: str) -> None:
    run_in_module_context(CART_JS + body, entry=MODEL)


def test_cart_add_remove_totals_groups_and_persistence():
    _cart_vm(r"""
const cart = { items: [], removed: [] };
eq(cartAdd(cart, [listings[0], listings[5], listings[0], { name: 'no key' }]), 1, 'own, duplicate and key-less listings are never added');
eq(cartAdd(cart, [listings[1]], { bundle: 'Lv 10' }), 1, 'bundle');
const t = cartTotals(cart);
eq([t.count, Math.round(t.usd * 100), Math.round(t.dec)], [2, 130, 2600], 'totals in $ and DEC');
eq(cartGroups(cart).map((g) => [g.pid, g.items.length, g.cc]), [['349-3-0', 2, 398]], 'grouped by card');
// per player, wrapped storage
const store = {}; const storage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };
eq(cartStore(storage, 'player0005', cart), true, 'stored');
eq(Object.keys(store), ['ceres:cards:cart:player0005'], 'per-player key');
eq(cartLoad(storage, 'player0005').items.map((i) => i.lkey), ['aaaaaaaaaaaaaaa1', 'aaaaaaaaaaaaaaa2'], 'reloaded');
eq(cartLoad(storage, 'someone').items, [], 'another player has their own cart');
const broken = { getItem() { throw Error('blocked'); }, setItem() { throw Error('full'); } };
eq([cartLoad(broken, 'player0005').items.length, cartStore(broken, 'player0005', cart)], [0, false], 'a blocked storage never throws');
eq(cartParse('{"v":1,"items":[{"lkey":"C3-334-F8PTJ387HC","pid":"1-1-1"}]}').items, [], 'a uid-shaped key is refused');
const stored = JSON.parse(store['ceres:cards:cart:player0005']);
if (/uid|seller|market_id|C-TESTUID/.test(JSON.stringify(stored))) throw Error('uid in storage');
eq(stored.items[0].seen_usd, 1.0, 'the price seen is kept');
eq(cartRemove(cart, 'aaaaaaaaaaaaaaa1'), 1, 'remove');
cartClear(cart); eq(cart.items.length, 0, 'clear');
""")


def test_cart_recheck_marks_sold_changed_and_unverified():
    _cart_vm(r"""
const cart = { items: [], removed: [] };
cartAdd(cart, [listings[0], listings[1], listings[2]]);
const marks = cartApplyCheck(cart, { '349-3-0': { complete: true, listings: { aaaaaaaaaaaaaaa1: { price_usd: 1.2, price_dec: 2400 }, aaaaaaaaaaaaaaa2: { price_usd: 0.3, price_dec: 600 } } } });
eq(marks, { sold: 1, changed: 1, unverified: 0 }, 'marks');
eq(cart.items.map((i) => [i.lkey.slice(-1), i.status, i.price_usd, i.was_usd]), [['1', 'changed', 1.2, 1.0], ['2', 'ok', 0.3, null]], 'changed price');
eq(cart.removed.map((i) => [i.lkey.slice(-1), i.status]), [['3', 'sold']], 'sold: removed from the cart, kept for the note');
const partial = { items: [], removed: [] };
cartAdd(partial, [listings[0]]);
cartApplyCheck(partial, { '349-3-0': { complete: false, listings: {} } });
eq([partial.items[0].status, partial.removed.length], ['unverified', 0], 'missing from a partial read: not re-checked, never called sold');
""")


def test_maxed_pick_one_click_and_a_second_press():
    _cart_vm(r"""
const cart = { items: [], removed: [] };
const first = cmMaxedPick(data, cart);
eq(first.listings.map((l) => l.lkey.slice(-1)).sort(), ['1', '2', '3', '4'], 'the cheapest maxed card (the same set as the server bundle)');
cartAdd(cart, first.listings);
const second = cmMaxedPick(data, cart);
eq(second.listings.map((l) => l.lkey.slice(-1)), ['5'], 'the next cheapest maxed card from what is not in the cart (own never)');
cartAdd(cart, second.listings);
eq(cmMaxedPick(data, cart).listings.map((l) => l.lkey.slice(-1)), ['7'], 'third');
cartAdd(cart, cmMaxedPick(data, cart).listings);
const none = cmMaxedPick(data, cart);
eq([none.listings.length, /Not enough copies on sale for another maxed card: 0 of 400 CC/.test(none.reason)], [0, true], 'nothing left: a reason');
eq(cmCheapestCover([[300, 0.9], [100, 0.5], [250, 0.7], [150, 0.45], [60, 0.2]], 400).sort(), [2, 3], 'the same min-cost cover as the server');
eq(cmCheapestCover([[10, 1]], 400), null, 'unreachable');
// the menu's bundles are priced from the copies not in the cart; one press = one group, removed as one ()
const d2 = { ...data, print: { ...data.print, max_level_pp: 1000, thresholds: [1, 5, 14, 30, 60, 100, 150, 220, 300, 400] } };
const c2 = { items: [], removed: [] };
const b0 = cmLevelBundles(d2, c2);
eq([b0[0].level, b0[0].keys.slice().sort().map((k) => k.slice(-1)), Math.round(b0[0].price_usd * 100)], [10, ['1', '2', '3', '4'], 132], 'max bundle, empty cart');
cartAdd(c2, cmMaxedPick(d2, c2).listings, { group: 'max-a', via: 'aaaaaaaaaaaaaaa1', bundle: 'Lv 10' });
const b1 = cmLevelBundles(d2, c2);
eq([b1[0].level, b1[0].keys.map((k) => k.slice(-1)), b1[0].price_usd], [10, ['5'], 1.9], '2nd maxed card: priced after the 1st is taken out, and dearer');
if (b1.some((b) => (b.keys || []).some((k) => cartHas(c2, k)))) throw Error('a bundle uses a listing in the cart');
eq(cartMaxGroups(c2, '349-3-0').map((g) => [g.group, g.via, g.keys.length]), [['max-a', 'aaaaaaaaaaaaaaa1', 4]], 'max group');
eq(cartPrintParts(c2, '349-3-0').map((p) => [p.group, p.items.length]), [['max-a', 4]], 'in your cart, as added');
eq(cartRemoveGroup(c2, 'max-a'), 4, 'remove exactly that maxed card');
eq([cmRowState(listings[5], cart), cmRowState(listings[0], cart), cmRowState(L('bbbbbbbbbbbbbbb1', 1, 9, { over_flag: true }), cart)], ['own', 'in-cart', 'over'], 'row states');

""")


MENU_JS = r"""
const L = (k, bcx, usd, over = {}) => ({ lkey: k, name: 'Gargoya Lion', card_detail_id: 349, edition: 3, foil: 0, card_set: 'chaos', rarity: 'common',
 level: 1, bcx, land_base_pp: bcx * 2.5, pp_per_usd: bcx * 2.5 / usd, price_usd: usd, price_dec: usd / 0.0005, over_cheapest: 0, over_flag: false, ...over });
const listings = [L('aaaaaaaaaaaaaaa1', 338, 1.0), L('aaaaaaaaaaaaaaa2', 60, 0.3), L('aaaaaaaaaaaaaaa6', 300, 0.1, { own: true }),
 L('aaaaaaaaaaaaaaa7', 20, 0.5, { over_cheapest: 2.4, over_flag: true })];
const data = { print: { id: '349-3-0', name: 'Gargoya Lion', card_set: 'chaos', foil: 0, rarity: 'common', bcx_max: 400, max_level: 10, max_level_pp: 1000,
 thresholds: [1, 5, 14, 30, 60, 100, 150, 220, 300, 400], market_url: 'https://splinterlands.com/card-detail/349/regular/3?tab=for-sale' },
 truncated: true, count: 4, on_sale: 639, listings,
 bundles: [{ level: 10, max: true, reachable: false, cc_needed: 400, cc_available: 418 },
 { level: 9, max: false, reachable: true, reached_level: 9, keys: ['aaaaaaaaaaaaaaa1'], parts_cc: [338], count: 1, bcx: 338, price_usd: 1, price_dec: 2000, land_base_pp: 845, pp_per_usd: 845 }] };
cartAdd(cartState(), [listings[1]]);
const menu = cartMenuHtml(data, 'https://example.invalid/a.png', null);
const panel = cartPanelHtml();
console.log(JSON.stringify({ menu, panel, tip: CHECKOUT_TIP }));
"""


def test_menu_and_cart_panel_colour_coding_and_disabled_checkout():
    out = run_in_module_context(MENU_JS, entry=STATIC / "card-cart.js").stdout
    got = json.loads(out.strip().splitlines()[-1])
    menu, panel = got["menu"], got["panel"]
    assert "4 cheapest per CC of 639 on sale" in menu and "Chaos · Regular · Common" in menu and "Max-level PP 1,000" in menu
    assert 'data-state="in-cart"' in menu and '<span class="cm-chip is-ok">In cart</span>' in menu           # green
    assert 'data-state="own"' in menu and 'class="cm-chip is-warn"' in menu and ">Your listing<" in menu    # amber, not addable
    assert "+240% over cheapest" in menu                                                                     # amber
    # bundles are equal buttons -- level, $ and DEC, "N cards"; an unreachable level is disabled, its reason in the tooltip
    assert re.search(r'<span class="cm-bundle-slot"[^>]*Not enough copies on sale: 358 of 400 CC\.[^>]*><button[^>]*cm-bundle-btn[^>]*disabled><span class="cm-bb-level">Max level</span>', menu)
    assert re.search(r'cm-bundle-btn" data-control-density="comfortable" aria-pressed="false" data-cm-action="add-bundle" data-cm-level="9"[^>]*><span class="cm-bb-level">Level 9</span><span class="cm-bb-usd">\$1\.00</span><span class="cm-bb-dec">2,000<img class="cm-dec-icon"[^>]*></span><span class="cm-bb-n">1 card</span>', menu)
    # the copy list is one aligned table with a header row
    assert 'class="cm-copy-table" role="table"' in menu and 'class="cm-copy cm-copy-head"' in menu and '<span class="cm-cell cm-c-decicon" role="cell"><img class="cm-dec-icon"' in menu
    assert 'class="cm-menu-link" href="https://splinterlands.com/card-detail/349/regular/3?tab=for-sale"' in menu  # in the menu only
    # The unavailable max action lives in the Max-level tile, with its reason directly below the grid.
    assert menu.count('<span class="cm-bb-level">Max level</span>') == 1
    assert "Not enough copies on sale for another maxed card: 358 of 400 CC." in menu
    assert "token-dec.png" in menu and "$1.00" in menu and "2,000" in menu
    assert not MENU_IDISH.search(menu) and "C-" not in menu.replace("CC", "")
    assert ">Remove</button>" in panel and "Gargoya Lion" in panel
    # Checkout is live now (card-checkout.js); disabled only while the cart is empty. The cart itself still never signs.
    assert got["tip"].startswith("Buy these listings from Ceres")
    js = (STATIC / "card-cart.js").read_text(encoding="utf-8")
    assert re.search(r'id="cm-checkout" data-cm-action="checkout"\$\{t\.count \? \'\' : \' disabled\'\}', js) and "CHECKOUT_TIP" in js
    for source in (js, (STATIC / "card-cart-model.js").read_text(encoding="utf-8")):
        assert not re.search(r"sm_market_purchase|market_purchase|requestCustomJson|requestTransfer|broadcast|keychain|sign\(", source, re.I)


def test_market_tile_hooks_cart_state_and_skeleton():
    out = run_in_module_context(r"""
const row = { name: 'Gargoya Lion', card_detail_id: 349, edition: 3, card_set: 'chaos', rarity: 'common', foil: 0, level: 9, max_level: 10, bcx: 338,
 bcx_max: 400, land_base_pp: 845, max_level_pp: 1000, price_usd: 1, price_dec: 2000, splinters: ['Neutral'], lkey: 'aaaaaaaaaaaaaaa1',
 market_url: 'https://splinterlands.com/card-detail/349/regular/3?tab=for-sale' };
const before = cmTileHtml(cmTiles([row])[0], tileHtml);
cartAdd(cartState(), [row]);
const after = cmTileHtml(cmTiles([row])[0], tileHtml, { fade: true });
// the maxed card this tile's button added -> green tile, "Remove from cart" in the destructive treatment
const other = { ...row, lkey: 'aaaaaaaaaaaaaaa9', bcx: 1 };
cartAdd(cartState(), [{ ...row, lkey: 'aaaaaaaaaaaaaaa8' }], { group: 'max-x', via: 'aaaaaaaaaaaaaaa9', bundle: 'Lv 10' });
const added = cmTileHtml(cmTiles([other])[0], tileHtml);
const second = cmTileHtml(cmTiles([{ ...row, lkey: 'aaaaaaaaaaaaaaa7', maxed_seq: 2 }])[0], tileHtml);
console.log(JSON.stringify({ before, after, added, second, skeleton: cmSkeletonHtml(tileHtml) }));
""", entry=STATIC / "card-browser.js").stdout
    got = json.loads(out.strip().splitlines()[-1])
    before, after, skeleton = got["before"], got["after"], got["skeleton"]
    assert 'tabindex="0" aria-haspopup="dialog" data-cm-index="0" data-cm-keys="aaaaaaaaaaaaaaa1"' in before
    assert 'data-cm-action="add-max"' in before and ">Add maxed card</button>" in before
    assert "View on market" not in before and "cm-market-link" not in before
    assert "is-in-cart" not in before and '<span class="cm-tag cm-incart" hidden>In cart</span>' in before
    assert "cm-tile is-in-cart cm-fade-in" in after and '<span class="cm-tag cm-incart">In cart</span>' in after
    added = got["added"]
    assert "is-in-cart" in added and 'data-control-case="destructive" data-cm-action="remove-max" data-cm-group="max-x"' in added and ">Remove from cart</button>" in added
    assert 'class="cm-tag cm-seq"' in got["second"] and "2nd maxed copy" in got["second"]
    assert "cm-skeleton" in skeleton and 'aria-hidden="true"' in skeleton and "data-cb-art" not in skeleton and "data-cm-index" not in skeleton


# no layout shift while loading

RELOAD_JS = r"""
(async () => {
 const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
 const filters = { q: '', set: '', rarity: 'common', foil: '', splinter: '', min_pp: '', max_pp: '', maxed: false };
 initMarket({ tileHtml: (tile) => `<article class="cb-tile${tile.extra.className ? ' ' + tile.extra.className : ''}"></article>`, filters: () => filters,
 player: () => 'player0005', renderCards() {} });
 __state.hold = true;
 await __els['cb-mode-market'].fire('click');
 await wait(10);
 const first = __els['cm-grid'].innerHTML;
 if ((first.match(/cm-skeleton/g) || []).length < 1) throw Error('no placeholders on first entry: ' + first);
 if (!/Reading the market/.test(__els['cm-summary'].innerHTML) || __els['cm-summary'].hidden) throw Error('summary not reserved');
 if (__els['cm-grid'].dataset.cmLoading) throw Error('placeholders are not dimmed');
 __state.hold = false;
 filters.foil = '0'; await __els['cm-sort'].fire('change');
 await wait(450);
 const loaded = __els['cm-grid'].innerHTML;
 if (!/cm-fade-in/.test(loaded) || /cm-skeleton/.test(loaded)) throw Error('results do not fade in: ' + loaded);
 // a re-read keeps the tiles in place, dimmed, until the new answer is in
 __state.hold = true;
 filters.foil = ''; await __els['cm-sort'].fire('change');
 await wait(450);
 if (__els['cm-grid'].innerHTML !== loaded) throw Error('the grid changed while a read was pending');
 if (__els['cm-grid'].dataset.cmLoading !== 'true' || __els['cm-loading'].hidden) throw Error('no soft dim / floating spinner');
 if (!/listing shown/.test(__els['cm-summary'].innerHTML) || /Reading the market/.test(__els['cm-summary'].innerHTML)) throw Error('summary cleared');
})().catch((error) => { console.error(error); process.exit(1); });
"""


def test_loading_keeps_layout_and_fades():
    run_in_module_context(RELOAD_JS, entry=STATIC / "card-market.js", extra_context_js=FAKE_DOM)
    page = (STATIC / "cards.html").read_text(encoding="utf-8")
    # never hidden; : drawn with its dashed placeholder from the first paint (card-market.js CM_SUMMARY_PLACEHOLDER)
    assert '<div class="cb-summary" id="cm-summary" role="status" aria-live="polite"><span class="cb-summary-item"><strong>—</strong> listings shown</span>' in page
    assert re.search(r'<div class="cm-stage">\s*<div class="cb-loading cm-loading" id="cm-loading"', page)
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    for rule in (".card-browser #cm-summary.cb-summary { inline-size: 100%; min-block-size",
                 ".card-browser .cm-stage { position: relative;",
                 ".card-browser .cm-stage > .cm-loading { position: absolute;",
                 ".card-browser .cm-stage > .cm-message { position: absolute;",
                 ".card-browser .cm-grid { transition: opacity 180ms ease; }",
                 ".card-browser .cm-grid[data-cm-loading] { opacity: 0.55; }",
                 ".card-browser.cb-reduce-motion .cm-grid { transition: none; }",
                 ".card-browser.cb-reduce-motion .cb-grid > .cm-fade-out { transition: none; }"):
        assert rule in css, rule
    assert "@keyframes" not in css and "@media" not in css and "@container" not in css  # plain .card-browser rules (test_ir9173)
    js = (STATIC / "card-market.js").read_text(encoding="utf-8")
    assert "if (!market.loaded) renderMarket();" in js and "renderMarket({ fade: true });" in js
    # the fade-in (200 ms, staggered) and the shimmer are Web Animations, skipped under prefers-reduced-motion
    assert "el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200, delay: Math.min(index, 30) * CM_STAGGER_MS" in js
    assert "if (cmReduceMotion() || typeof el?.animate !== 'function') return;" in js and "'cb-reduce-motion'" in js


# price badge at the bottom; hover fade and border, no zoom

def test_tile_hover_and_price_badge_css():
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    assert ".card-browser .cb-grid > .cb-tile { transition: border-color 0.16s ease, box-shadow 0.16s ease, background-color 0.16s ease, opacity 140ms ease-out; }" in css
    # no other tile dims
    assert ".cb-tile:not(:hover) { opacity: .45" not in css and ".cb-tile:not(.cb-held) { opacity: .45" not in css
    assert ".card-browser.cb-reduce-motion .cb-grid > .cb-tile { transition: none; }" in css
    assert ".card-browser .cb-grid > .cb-tile:hover .cb-art > img { transform: none; }" in css  # no zoom
    assert "border-color: color-mix(in srgb, var(--text) 60%, transparent); background: var(--cb-glass-hover); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--text) 60%, transparent)" in css
    assert ":is(" not in css.split("the card menu and the cart")[-1]
    for rule in re.findall(r"^([^@/\s][^{]*)\{", css, re.M):
        for selector in rule.split(","):
            assert selector.strip().startswith((".card-browser", "html")), selector
    for colour in re.findall(r"var\(--(cyan|info)\)", css.split("the card menu and the cart")[-1]):
        raise AssertionError(colour)


def test_cards_page_cart_button_and_manifest():
    page = (STATIC / "cards.html").read_text(encoding="utf-8")
    assert re.search(r'<button type="button" class="ceres-btn ceres-btn--primary cm-cart-btn" data-control-density="header" id="cm-cart-btn"', page)
    assert '<span class="cm-cart-count" id="cm-cart-count" data-state="empty">0</span>' in page
    contract = json.loads((Path(__file__).parent / "fixtures" / "ceres-button-contract.json").read_text(encoding="utf-8"))
    selectors = {c["selector"] for c in contract["controls"]}
    assert {"#cm-cart-btn", ".cm-add-max", ".cm-row-btn", "#cm-checkout", ".cm-dialog-panel .hazard-close"} <= selectors


# the PP range picks the prints in scope before the cap

def test_min_pp_narrows_the_scope_before_the_print_cap():
    cheap_low = {**ITEM, "card_detail_id": 1, "name": "Cheap", "max_level_pp": 1000.0, "pp_per_cc": 2.5, "usd_per_pp": 0.0001,
                 "low_price_usd": 0.01, "low_price_per_cc_usd": 0.001, "splinters": []}
    dear_high = {**ITEM, "card_detail_id": 2, "name": "Big", "max_level_pp": 6000.0, "pp_per_cc": 15.0, "usd_per_pp": 0.01,
                 "low_price_usd": 5.0, "low_price_per_cc_usd": 0.1, "splinters": []}
    assert [p["name"] for p in card_market.pick_prints([cheap_low, dear_high], {}, "cost_per_pp", limit=1)] == ["Cheap"]
    assert [p["name"] for p in card_market.pick_prints([cheap_low, dear_high], {"min_pp": "3,000"}, "cost_per_pp", limit=1)] == ["Big"]
    assert [p["name"] for p in card_market.pick_prints([cheap_low, dear_high], {"max_pp": "10"}, "cost_per_pp", limit=2)] == ["Cheap"]
    assert card_market.pp_in_range(3000.0, {"min_pp": "3000"}) and not card_market.pp_in_range(None, {"min_pp": "1"})


def test_min_pp_read_returns_only_listings_in_range():
    from tests.ceres.test_card_market import _read
    body = _read({"min_pp": "3000"})
    assert body["listings"] and all(r["land_base_pp"] >= 3000 for r in body["listings"])
    assert all(r["land_base_pp"] <= 500 for r in _read({"max_pp": "500"})["listings"])


def test_route_takes_the_pp_range(client, monkeypatch):
    from tests.ceres.test_card_market import _getter as grouped_getter
    monkeypatch.setattr(card_market, "_default_get", grouped_getter())
    assert client.get("/api/market?player=player0005&min_pp=abc").status_code == 400
    body = client.get("/api/market?player=player0005&min_pp=3,000").json()
    assert body["filters"]["min_pp"] == "3000"


# placeholders in whole rows, filled in place, extras trimmed

def test_skeleton_count_is_whole_rows_filling_the_viewport():
    out = run_in_module_context(r"""
console.log(JSON.stringify([
 cmSkeletonCount(6, 864, 190, 455, 12), // 6 columns; (864 - 190) / 467 -> 2 rows
 cmSkeletonCount(1, 844, 560, 600, 12), // phone: one column, one row
 cmSkeletonCount(4, 900, -50, 400, 10), // scrolled: the grid's top above the viewport counts as 0 -> 3 rows
 cmSkeletonCount(5, 900, 100, 0, 0), // unknown tile size: one full row
 cmSkeletonCount(0, 900, 100, 400, 0), // no column count: one column
]));
""", entry=STATIC / "card-market.js").stdout
    assert json.loads(out.strip().splitlines()[-1]) == [12, 1, 12, 5, 2]


def test_unused_slots_fade_out_then_go():
    out = run_in_module_context(r"""
const removed = [];
const el = (i) => ({ i, classes: [], attrs: {}, classList: { add(c) { this.maintainer.classes.push(c); } }, setAttribute(k, v) { this.attrs[k] = v; }, remove() { removed.push(this.i); } });
const extra = [el(5), el(6)];
extra.forEach((e) => { e.classList.maintainer = e; });
cmTrimSlots(extra);
const now = extra.map((e) => [e.classes.join(' '), e.attrs['aria-hidden']]);
setTimeout(() => console.log(JSON.stringify({ now, removed })), 260);
""", entry=STATIC / "card-market.js").stdout
    got = json.loads(out.strip().splitlines()[-1])
    assert got["now"] == [["cm-fade-out", "true"], ["cm-fade-out", "true"]] and got["removed"] == [5, 6]
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    assert ".card-browser .cb-grid > .cm-fade-out { opacity: 0; transition: opacity 200ms ease; pointer-events: none; }" in css
    js = (STATIC / "card-market.js").read_text(encoding="utf-8")
    assert "export const CM_STAGGER_MS = 30;" in js and "grid.replaceChild(el, old[index])" in js and "CM_SKELETONS" not in js
    assert "gridTemplateColumns" in js and "window.addEventListener?.('resize'" in js


def test_price_badge_full_width_and_green_cart_tile_css():
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    assert ".card-browser .cb-art > .cm-price-badges { position: absolute; inset: auto 0 0; z-index: 4; display: grid; grid-template-columns: 1fr 1fr; gap: 3px;" in css  # two boxes; / edge to edge, flush #
    assert re.search(r"\.card-browser \.cb-grid > \.cm-tile\.is-in-cart, \.card-browser \.cb-grid > \.cm-tile\.is-in-cart:hover \{ border-color: var\(--ok\); background: linear-gradient\(160deg, color-mix\(in srgb, var\(--ok\)", css)
    assert "cm-market-link" not in css


def test_sequential_maxed_offers_second_dearer():
    item = {**ITEM, "max_level": 10}
    rows = [_row(1, 400, 1.0), _row(2, 300, 0.5), _row(3, 100, 0.4), _row(4, 250, 0.9), _row(5, 150, 0.6), _row(6, 400, 2.2)]
    out = card_market.maxed_offers(item, rows, 0.0005, None)
    # 1st: 300 + 100 = $0.90; 2nd from what remains: 400 alone ($1.00) vs 250 + 150 ($1.50) -> $1.00; 3rd: 250 + 150 = $1.50
    assert [(o["maxed_seq"], o["combined"], round(o["price_usd"], 2)) for o in out] == [(1, 2, 0.9), (2, 1, 1.0), (3, 2, 1.5)]
    used = [set(o.get("parts_keys") or [o["lkey"]]) for o in out]
    assert not (used[0] & used[1]) and not (used[1] & used[2]) and not (used[0] & used[2])



# own listings shown with a badge, never bought

def test_own_listings_badge_never_in_bundles_maxed_or_cart():
    rows = _listings()
    own = [r for r in rows if r["own"]]
    assert len(own) == 1 and own[0]["lkey"]
    assert all(own[0]["lkey"] not in (b.get("keys") or []) for b in level_bundles(ITEM, rows, 0.0005))
    offers = card_market.maxed_offers(ITEM, ROWS, 0.0005, "player0005")
    assert all(own[0]["lkey"] not in (o.get("parts_keys") or [o.get("lkey")]) for o in offers if o["maxed_seq"])
    out = run_in_module_context(r"""
const own = { lkey: 'aaaaaaaaaaaaaaa6', name: 'Gargoya Lion', card_detail_id: 349, edition: 3, foil: 0, level: 9, bcx: 300, price_usd: 0.1, price_dec: 200, own: true };
const added = cartAdd(cartState(), [own]);
const row = listingRow(own);
console.log(JSON.stringify({ added, row }));
""", entry=STATIC / "card-cart.js").stdout
    got = json.loads(out.strip().splitlines()[-1])
    assert got["added"] == 0
    assert 'data-state="own"' in got["row"] and ">Your listing<" in got["row"] and "You cannot buy your own listing" in got["row"]
    assert 'data-cm-action="add"' not in got["row"]
    tile = run_in_module_context(r"""
const row = { name: 'Gargoya Lion', card_detail_id: 349, edition: 3, card_set: 'chaos', rarity: 'common', foil: 0, level: 9, max_level: 10, bcx: 300,
 bcx_max: 400, land_base_pp: 750, max_level_pp: 1000, price_usd: 0.1, price_dec: 200, splinters: ['Neutral'], lkey: 'aaaaaaaaaaaaaaa6', own: true };
console.log(JSON.stringify(cmTileHtml(cmTiles([row])[0], tileHtml)));
""", entry=STATIC / "card-browser.js").stdout
    assert '<span class="cm-tag cm-own">Your listing</span>' in json.loads(tile.strip().splitlines()[-1])
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    assert ".card-browser .cm-tile .cb-art .cm-own { border-color: var(--warn); color: var(--warn); }" in css


# Load more

def test_load_more_offset_after_ranking():
    prints = [{**ITEM, "card_detail_id": i, "name": f"P{i}", "usd_per_pp": float(i), "low_price_usd": 1.0, "low_price_per_cc_usd": 0.01,
               "splinters": []} for i in range(1, 8)]
    first = card_market.pick_prints(prints, {}, "cost_per_pp", limit=3)
    second = card_market.pick_prints(prints, {}, "cost_per_pp", limit=3, offset=3)
    assert [p["name"] for p in first] == ["P1", "P2", "P3"] and [p["name"] for p in second] == ["P4", "P5", "P6"]
    from tests.ceres.test_card_market import _read
    page1 = _read()
    assert page1["page"] == 1 and page1["has_more"] is False
    page2 = read_market_page = card_market.read_market(DETAILS, {}, player="player0005", getter=__import__("tests.ceres.test_card_market", fromlist=["_getter"])._getter(),
                                                       cache=MarketCache(), page=2)
    assert page2["listings"] == [] and page2["page"] == 2 and page2["has_more"] is False


LOAD_MORE_JS = r"""
(async () => {
 const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
 const filters = { q: '', set: '', rarity: 'common', foil: '', splinter: '', min_pp: '', max_pp: '', maxed: false };
 let page = 0;
 globalThis.fetch = window.fetch = (url) => {
 __fetches.push(String(url));
 page = /page=2/.test(url) ? 2 : 1;
 const listing = (n, rate) => ({ name: 'Card ' + n, card_detail_id: n, edition: 3, foil: 0, rarity: 'common', card_set: 'chaos', level: 1, bcx: 1, bcx_max: 400,
 land_base_pp: rate, price_usd: 1, price_dec: 2000, splinters: [], lkey: ('a'.repeat(15) + n).slice(-16) });
 const body = page === 2 ? { listings: [listing(3, 9000)], prints_read: 200, prints_matching: 250, has_more: false, page: 2 }
 : { listings: [listing(1, 10), listing(2, 5)], prints_read: 100, prints_matching: 250, has_more: true, page: 1 };
 return Promise.resolve({ ok: true, status: 200, json: async () => ({ read_at: '2026-10-15T09:00:00Z', dec_usd: 0.0005, ...body }) });
 };
 initMarket({ tileHtml: (tile) => `<article class="cb-tile">${tile.listing ? tile.listing.name : ''}</article>`, filters: () => filters, player: () => 'player0005', renderCards() {} });
 await __els['cb-mode-market'].fire('click');
 await wait(30);
 if (__els['cm-more'].hidden) throw Error('no Load more while more prints are left');
 const before = __els['cm-grid'].innerHTML;
 await cmLoadMore();
 await wait(10);
 if (!/page=2/.test(__fetches[__fetches.length - 1])) throw Error('not the next page: ' + __fetches.join(', '));
 const after = __els['cm-grid'].innerHTML;
 // the new page is drawn after the tiles already there, even with a better rate (9000 PP per $): nothing on screen moves
 if (!after.startsWith(before) || after.indexOf('Card 3') < after.indexOf('Card 2')) throw Error('reflow: ' + after);
 if (!/best 200 of 250 prints/.test(__els['cm-summary'].innerHTML)) throw Error('summary: ' + __els['cm-summary'].innerHTML);
 if (!__els['cm-more'].hidden) throw Error('Load more stays when nothing is left');
 // a filter change starts again at page 1
 filters.rarity = 'rare'; await __els['cm-sort'].fire('change');
 await wait(450);
 const last = __fetches[__fetches.length - 1];
 if (/page=/.test(last) || !/rarity=rare/.test(last)) throw Error('reset: ' + last);
 if (/Card 3/.test(__els['cm-grid'].innerHTML)) throw Error('page 2 kept after a filter change');
})().catch((error) => { console.error(error); process.exit(1); });
"""


def test_load_more_appends_without_reflow_and_resets_on_filter_change():
    run_in_module_context(LOAD_MORE_JS, entry=STATIC / "card-market.js", extra_context_js=FAKE_DOM)
    page = (STATIC / "cards.html").read_text(encoding="utf-8")
    assert re.search(r'<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" id="cm-more" hidden>Load more</button>', page)
    contract = json.loads((Path(__file__).parent / "fixtures" / "ceres-button-contract.json").read_text(encoding="utf-8"))
    assert "#cm-more" in {c["selector"] for c in contract["controls"]}


# the summary wording

def test_summary_counts_maxed_cards_and_listings_behind():
    out = run_in_module_context(r"""
const rows = [{ name: 'A', combined: 4, land_base_pp: 1000, price_usd: 1 }, { name: 'B', combined: 1, land_base_pp: 1000, price_usd: 2 }, { name: 'C', land_base_pp: 1, price_usd: 1 }];
const s = cmSummary(rows);
console.log(JSON.stringify([cmSummaryCount(s, true), cmSummaryCount(s, false), cmSummaryCount(cmSummary([rows[1]]), true)]));
""", entry=STATIC / "card-market.js").stdout
    assert json.loads(out.strip().splitlines()[-1]) == ["<strong>3</strong> maxed cards (from 6 listings)", "<strong>3</strong> listings shown",
                                                         "<strong>1</strong> maxed card (from 1 listing)"]


# a print's maxed price never depends on the sort

def test_maxed_price_is_the_same_under_every_sort():
    from tests.ceres.test_market_maxed import _getter as maxed_getter
    assert card_market.MAXED_DEEP_PRINTS == card_market.MAX_PRINTS  # every full print in scope is read in depth
    prices = {}
    for sort in ("cost_per_pp", "price", "pp"):
        calls: list[str] = []
        body = card_market.read_market(DETAILS, {}, sort=sort, player="player0005", getter=maxed_getter(calls, full_ids={349}), cache=MarketCache(), maxed=True)
        prices[sort] = sorted((r["card_detail_id"], r["edition"], r["foil"], r.get("maxed_seq"), round(r["price_usd"], 6)) for r in body["listings"])
        assert body["deep_reads"] >= 1
    assert prices["cost_per_pp"] == prices["price"] == prices["pp"]


def test_menu_max_choice_has_one_price_and_remains_removable():
    out = run_in_module_context(r"""
const L = (k, bcx, usd) => ({ lkey: k, name: 'Gargoya Lion', card_detail_id: 349, edition: 3, foil: 0, level: 1, bcx,
 land_base_pp: bcx * 2.5, price_usd: usd, price_dec: usd / 0.0005 });
const data = { print: { id: '349-3-0', name: 'Gargoya Lion', bcx_max: 400, max_level: 10, max_level_pp: 1000,
 thresholds: [1, 5, 14, 30, 60, 100, 150, 220, 300, 400] }, listings: [L('aaaaaaaaaaaaaaa1', 400, 1), L('aaaaaaaaaaaaaaa2', 400, 2)] };
const before = cartMenuHtml(data, '', null);
cartAdd(cartState(), [data.listings[0]], { group: 'max-one', bundle: 'Lv 10' });
const after = cartMenuHtml(data, '', null);
console.log(JSON.stringify({ before, after }));
""", entry=STATIC / "card-cart.js").stdout
    got = json.loads(out.strip().splitlines()[-1])
    assert got["before"].count('data-cm-action="add-max"') == 1
    assert got["before"].count('<span class="cm-bb-level">Max level</span>') == 1
    assert got["before"].count('<span class="cm-bb-usd">$1.00</span>') == 1
    assert 'data-cm-action="remove-max" data-cm-group="max-one"' in got["after"]
    assert 'aria-pressed="true"' in got["after"] and '1 card · Remove' in got["after"]
    assert 'aria-label="Remove maxed card from cart · 1 card · $1.00"' in got["after"]
    assert got["after"].count('data-cm-action="add-max"') == 1  # next available maxed copy
    assert '<span class="cm-bb-usd">$2.00</span>' in got["after"]


def test_bundle_button_toggles_in_cart():
    out = run_in_module_context(r"""
const L = (k, bcx, usd) => ({ lkey: k, name: 'Gargoya Lion', card_detail_id: 349, edition: 3, foil: 0, level: 1, bcx, land_base_pp: bcx * 2.5, price_usd: usd, price_dec: usd / 0.0005 });
const data = { print: { id: '349-3-0', bcx_max: 400, max_level: 10, max_level_pp: 1000, thresholds: [1, 5, 14, 30, 60, 100, 150, 220, 300, 400] },
 listings: [L('aaaaaaaaaaaaaaa1', 400, 1), L('aaaaaaaaaaaaaaa2', 400, 2)] };
cartAdd(cartState(), [data.listings[0]], { group: 'lv-1', bundle: 'Lv 10' });
console.log(JSON.stringify(cartBundleButtonsHtml(data)));
""", entry=STATIC / "card-cart.js").stdout
    html = json.loads(out.strip().splitlines()[-1])
    # the max-level bundle in the cart is a green, pressed button that removes exactly that bundle
    assert 'cm-bundle-btn is-in-cart" data-control-density="comfortable" aria-pressed="true"' in html
    assert 'data-cm-action="remove-group" data-cm-group="lv-1"' in html
    assert 'aria-label="Remove maxed card from cart · 1 card · $1.00"' in html
    assert "1 card · Remove" in html


# on the Cards page: multi-select filters (lists), the market sends comma lists

def test_cards_filters_are_lists_and_old_single_values_still_work():
    run_in_module_context(r"""
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw Error(what + ': ' + JSON.stringify(a) + ' != ' + JSON.stringify(b)); };
const base = { card_set: 'chaos', foil: 0, rarity: 'common', splinters: ['Fire'], maxed: true, land_base_pp: 10, status: 'idle', available: true };
const cards = [{ ...base, name: 'A' }, { ...base, name: 'B', card_set: 'rebellion', foil: 1 }, { ...base, name: 'C', card_set: 'alpha', maxed: false }];
const f = (over) => ({ ...cbDefaultFilters(), ...over });
eq(cbDefaultFilters().set, [], 'default is a list');
eq(cbView(cards, f({ set: ['chaos', 'rebellion'] })).tiles.map((t) => t.card.name).sort(), ['A', 'B'], 'two sets: any of them');
eq(cbView(cards, f({ set: 'alpha' })).tiles.map((t) => t.card.name), ['C'], 'an old stored single value');
eq(cbView(cards, f({ foil: ['1'] })).tiles.map((t) => t.card.name), ['B'], 'foil list');
eq(cbView(cards, f({ level: ['maxed', 'not_maxed'] })).tiles.length, 3, 'both levels = all');
eq(cbList(['a', '']), ['a'], 'list'); eq(cbList(''), [], 'empty');
""", entry=STATIC / "card-browser-model.js")
    run_in_module_context(r"""
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw Error(what + ': ' + JSON.stringify(a) + ' != ' + JSON.stringify(b)); };
eq(cmQuery('player0005', { set: ['rebellion', 'chaos'], foil: ['0', '1'], rarity: [] }, 'price'), '/api/market?player=player0005&set=chaos%2Crebellion&foil=0%2C1&sort=price', 'comma lists');
eq(cmMatches({ card_set: 'chaos', foil: 1, splinters: ['Fire'] }, { set: ['chaos', 'alpha'], foil: ['1'], splinter: ['fire', 'water'] }), true, 'any of');
eq(cmFiltersChanged({ set: ['a', 'b'] }, { set: ['b', 'a'] }), false, 'order does not matter');
""", entry=STATIC / "card-market-model.js")
    assert card_market.print_matches({**ITEM, "splinters": ["Fire"]}, {"set": "alpha,chaos", "foil": "0,1", "splinter": "water,fire"})
    assert not card_market.print_matches({**ITEM, "splinters": ["Fire"]}, {"set": "alpha,rebellion"})
    js = (STATIC / "card-browser.js").read_text(encoding="utf-8")
    assert "multiple: el.dataset.cbFilter !== 'sort'" in js and "setSelectValues(el, cbList(f[key]))" in js


def test_hover_dimming_has_grace_and_pauses_while_scrolling():
    # (maintainer: "give the fade-in of the unhovered tiles a small grace time so there is less jumping while scrolling the
    # cards and shop"): dimming waits 220 ms, and never happens while the page scrolls.
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    assert ".card-browser.cb-scrolling .cb-grid { pointer-events: none; }" in css
    # scrolling
    # freezes the hover state (data-cb-hold) instead of forcing every tile to full opacity, so nothing flashes at scroll start or end
    assert ".cb-scrolling .cb-grid > .cb-tile { opacity: 1" not in css
    assert '.card-browser .cb-grid[data-cb-hold="clear"]:has(> .cb-tile) > .cb-tile { opacity: 1; transition-delay: 0s; }' in css
    js = (STATIC / "card-browser.js").read_text(encoding="utf-8")
    assert "cbHoldGrids(); document.body?.classList.add('cb-scrolling');" in js
    assert "document.body?.classList.remove('cb-scrolling');" in js and "}, 250);" in js
    assert "cbReleaseAfterHover(cbScrollGen);" in js and "const events = ['mouseover', 'pointermove', 'mouseout'];" in js
    assert "setTimeout(release" not in js  # no timed release: it flashed when the hover update came late


def test_maxed_price_sort_ranks_by_maxed_cost():
    # under Maxed the Price
    # sort ranks prints by the estimated price of one maxed card, not by their cheapest single listing.
    base = {"usd_per_pp": 0.004, "max_level_pp": 4000, "foil": 1, "card_set": "chaos", "rarity": "common", "splinters": [], "pp_per_cc": 2}
    cheap_single = {**base, "name": "Cheap single", "low_price_usd": 0.01, "low_price_per_cc_usd": 0.01, "bcx_max": 2000}
    cheap_maxed = {**base, "name": "Cheap maxed", "low_price_usd": 0.5, "low_price_per_cc_usd": 0.5, "bcx_max": 27}
    order = [p["name"] for p in card_market.pick_prints([cheap_single, cheap_maxed], {}, "price", maxed=True)]
    assert order == ["Cheap maxed", "Cheap single"]
    plain = [p["name"] for p in card_market.pick_prints([cheap_single, cheap_maxed], {}, "price")]
    assert plain == ["Cheap single", "Cheap maxed"]
