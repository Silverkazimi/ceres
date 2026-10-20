"""the Maxed filter (both modes), the combined max-level market offer, and the market price badge. No live network: the upstream is a synthetic getter."""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

import pytest

from ceres import card_market
from ceres.card_market import MarketCache, cheapest_max_combo, maxed_offers, read_market
from tests.ceres.vm_context import run_in_module_context

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ir9182-market" / "market-2026-10-14.json").read_text(encoding="utf-8"))
DETAILS = {int(k): v for k, v in FIXTURE["card_details"].items()}


# the min-cost combination

def test_combination_is_the_cheapest_total_that_reaches_max_cc():
    # 400 CC needed. Greedy by price per CC would take 300 (0.9) + 100 (0.5) = 1.4; 250 + 150 = 0.7 + 0.45 = 1.15 is cheaper.
    offers = [(300, 0.9), (100, 0.5), (250, 0.7), (150, 0.45), (60, 0.2)]
    chosen = cheapest_max_combo(offers, 400)
    assert sorted(chosen) == [2, 3]
    assert sum(offers[i][0] for i in chosen) >= 400
    # overshooting is allowed when it is cheaper (CC past max adds no PP but costs nothing extra here)
    assert cheapest_max_combo([(399, 1.0), (500, 0.8)], 400) == [1]


def test_combination_prefers_fewer_listings_on_a_price_tie():
    offers = [(200, 0.5), (200, 0.5), (400, 1.0)]
    assert cheapest_max_combo(offers, 400) == [2]
    assert sorted(cheapest_max_combo([(100, 0.25)] * 4 + [(200, 0.5), (200, 0.5)], 400)) == [4, 5]


def test_combination_uses_each_listing_once_and_none_when_impossible():
    assert cheapest_max_combo([(100, 0.1), (100, 0.1)], 400) is None
    assert cheapest_max_combo([], 400) is None
    assert cheapest_max_combo([(500, 1.0)], 0) is None and cheapest_max_combo([(500, 1.0)], None) is None
    assert cheapest_max_combo([(1, 0.01)] * 3 + [(1, 0.02)], 4) == [0, 1, 2, 3]


ITEM = {"card_detail_id": 349, "name": "Gargoya Lion", "edition": 3, "card_set": "chaos", "rarity": "common", "foil": 0,
        "gold": False, "splinters": ["Neutral"], "max_level": 10, "bcx_max": 400, "max_level_pp": 1000.0,
        "low_price_per_cc_usd": 0.003}


def _row(bcx, price, seller="x", **over):
    return {"bcx": bcx, "buy_price": price, "price_bcx": price / bcx, "level": 1, "currency": "USD", "seller": seller,
            "land_base_pp": None, "type": "SELL", "card_detail_id": 349, "edition": 3, "foil": 0, **over}


def test_maxed_offers_singles_and_one_combined_tile():
    rows = [_row(338, 1.0), _row(60, 0.3), _row(1, 0.01), _row(1, 0.01), _row(400, 2.0), _row(420, 1.9), _row(300, 0.1, seller="player0005")]
    out = maxed_offers(ITEM, rows, dec_usd=0.0005, player="player0005")
    # sequential -- 1st the cheapest way to one maxed card (338 + 60 + 1 + 1 CC, $1.32), then, with those listings
    # taken out, the 420 CC listing ($1.90), then the 400 CC one ($2.00); the maintainer's own listing is never in the sequence
    assert [(o["maxed_seq"], o["combined"], o["price_usd"]) for o in out] == [(1, 4, pytest.approx(1.32)), (2, 1, 1.9), (3, 1, 2.0)]
    c = out[0]
    # the maintainer's own 300 CC listing is never part of a combination
    assert c["combined"] == 4 and c["bcx"] == 400 and c["price_usd"] == pytest.approx(1.32) and c["parts_cc"] == [338, 60, 1, 1]
    assert c["maxed"] and c["level"] == 10 and c["land_base_pp"] == 1000.0 and c["pp_per_usd"] == pytest.approx(1000 / 1.32)
    assert c["price_dec"] == pytest.approx(1.32 / 0.0005) and not c["own"]
    assert c["market_url"] == "https://splinterlands.com/card-detail/349/regular/3?tab=for-sale"
    assert not {"uid", "seller", "market_id", "card_id"} & set(c)


def test_no_combined_tile_when_a_single_listing_is_cheapest_or_max_cc_cannot_be_reached():
    single_best = maxed_offers(ITEM, [_row(400, 1.0), _row(300, 0.9), _row(200, 0.5)], dec_usd=0.0005, player=None)
    # 1st the single 400 CC listing ($1.00), 2nd what remains combined (300 + 200 CC, $1.40)
    assert [(o["combined"], o["price_usd"]) for o in single_best] == [(1, 1.0), (2, pytest.approx(1.4))]
    short = maxed_offers(ITEM, [_row(100, 0.2), _row(200, 0.3)], dec_usd=0.0005, player=None)
    assert short == []
    black = maxed_offers({**ITEM, "foil": 3, "bcx_max": 0, "max_level_pp": 5000.0}, [_row(1, 3.0, foil=3), _row(1, 2.0, foil=3)], 0.0005, None)
    assert [(o["combined"], o["price_usd"]) for o in black] == [(1, 2.0), (1, 3.0)]


# the server read under Maxed: bounded deep reads

def _getter(calls, full_ids=(), maxed=True):
    def get(url):
        parsed = urllib.parse.urlsplit(url)
        assert parsed.scheme == "https" and parsed.hostname == "api.splinterlands.com", url
        calls.append(url)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/market/for_sale_grouped":
            return FIXTURE["for_sale_grouped"]
        if parsed.path == "/settings":
            return FIXTURE["settings"]
        if parsed.path == "/market/market_query_grouped":
            assert query["limit"] == [str(card_market.GROUPED_ROWS_CAP)]  # the grouped read always asks the full 10 rows
            ids = {int(i) for i in query["card_ids"][0].split(",")}
            groups = [g for g in FIXTURE["market_query_grouped"].get(query["foil"][0], []) if g["card_detail_id"] in ids]
            # pretend a full group (10 rows) for the asked ids, so the server reads them deeper
            return [{**g, "result": (g["result"] * 10)[:10]} if g["card_detail_id"] in full_ids else g for g in groups]
        if parsed.path == "/market/market_query_by_card":
            assert maxed, "a deep read without the Maxed filter"
            detail, foil, edition = int(query["card_detail_id"][0]), int(query["foil"][0]), int(query["edition"][0])
            assert query["limit"] == [str(card_market.MAXED_DEEP_LISTINGS)]
            if (detail, foil, edition) != (349, 0, 3):
                return []
            return [_row(338, 1.0), _row(60, 0.3), _row(1, 0.01), _row(1, 0.01), _row(400, 2.5)]
        raise AssertionError(parsed.path)
    return get


def test_maxed_read_returns_only_maxed_offers_and_bounds_deep_reads():
    calls: list[str] = []
    body = read_market(DETAILS, {}, player="player0005", getter=_getter(calls, full_ids={349}), cache=MarketCache(), maxed=True)
    assert body["maxed"] and body["listings"] and all(r["maxed"] for r in body["listings"])
    paths = [urllib.parse.urlsplit(u).path for u in calls]
    deep = paths.count("/market/market_query_by_card")
    assert 1 <= deep <= card_market.MAXED_DEEP_PRINTS and body["deep_reads"] == deep
    assert paths.count("/market/for_sale_grouped") == 1 and paths.count("/settings") == 1
    assert len(calls) <= 2 + 5 + card_market.MAXED_DEEP_PRINTS  # grouped + settings, one grouped call per foil class, deep reads
    gargoya = [r for r in body["listings"] if r["card_detail_id"] == 349 and r["edition"] == 3 and r["foil"] == 0]
    assert any(r["combined"] == 4 and r["price_usd"] == pytest.approx(1.32) for r in gargoya)
    # without Maxed a full group is read in depth only when its kept offers cannot be proven best (); here they can
    plain: list[str] = []
    other = read_market(DETAILS, {}, player="player0005", getter=_getter(plain, full_ids={349}, maxed=False), cache=MarketCache())
    assert other["deep_reads"] == 0 and not other["maxed"] and "/market/market_query_by_card" not in " ".join(plain)


def test_no_deep_read_when_no_group_is_full():
    calls: list[str] = []
    body = read_market(DETAILS, {}, player=None, getter=_getter(calls), cache=MarketCache(), maxed=True)
    assert body["deep_reads"] == 0 and "/market/market_query_by_card" not in " ".join(calls)
    assert all(r["maxed"] for r in body["listings"])


def test_route_passes_maxed(monkeypatch, tmp_path):
    import socket

    from fastapi.testclient import TestClient

    from ceres import routes_market
    from ceres.app import create_app
    from ceres.config import CeresConfig
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    seen = {}
    real = card_market.read_market

    def spy(details, filters, **kw):
        seen.update(kw)
        return real(details, filters, getter=_getter([]), cache=MarketCache(), **{k: v for k, v in kw.items() if k not in ("getter", "cache")})
    monkeypatch.setattr(card_market, "read_market", spy)
    monkeypatch.setattr(routes_market, "load_details_by_id", lambda: DETAILS)
    client = TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player="player0005")))
    response = client.get("/api/market?player=player0005&maxed=1")
    assert response.status_code == 200 and seen["maxed"] is True and response.json()["maxed"] is True
    assert all(r["maxed"] for r in response.json()["listings"])
    client.get("/api/market?player=player0005")
    assert seen["maxed"] is False


# My cards: Level replaces the Maxed pill (); Market keeps the Max pill

def test_my_cards_level_dropdown_filters_max_and_not_max():
    run_in_module_context(r"""
const cards = [{ name: 'A', maxed: true, land_base_pp: 1 }, { name: 'B', maxed: false, land_base_pp: 1 }, { name: 'C', land_base_pp: 1 }];
const f = { ...cbDefaultFilters() };
if (cbView(cards, f).tiles.length !== 3) throw Error('off');
const max = cbView(cards, { ...f, level: ['maxed'] }).tiles.map((t) => t.card.name);
const notMax = cbView(cards, { ...f, level: ['not_maxed'] }).tiles.map((t) => t.card.name);
if (JSON.stringify(max) !== '["A"]') throw Error('max: ' + max);
if (JSON.stringify(notMax) !== '["B"]') throw Error('not max: ' + notMax); // unknown max state is not "not max"
""", entry=STATIC / "card-browser-model.js")


def test_maxed_attribute_follows_the_filter_and_the_market_tile():
    out = run_in_module_context(r"""
document.body.dataset = {};
state.filters.maxed = true; syncMaxed();
const on = document.body.dataset.cbMaxed;
state.filters.maxed = false; syncMaxed();
const off = 'cbMaxed' in document.body.dataset;
const row = { name: 'Gargoya Lion', card_detail_id: 349, edition: 3, card_set: 'chaos', rarity: 'common', foil: 0, level: 10, max_level: 10,
 bcx: 400, bcx_max: 400, maxed: true, land_base_pp: 1000, max_level_pp: 1000, price_usd: 1.364, price_dec: 2785.4, combined: 4,
 parts_cc: [338, 60, 1, 1], splinters: ['Neutral'], market_url: 'https://splinterlands.com/card-detail/349/regular/3?tab=for-sale', own: true };
console.log(JSON.stringify({ on, off, html: cmTileHtml(cmTiles([row])[0], tileHtml) }));
""", entry=STATIC / "card-browser.js").stdout
    got = json.loads(out.strip().splitlines()[-1])
    assert got["on"] == "1" and got["off"] is False
    html = got["html"]
    assert "at max level" not in html and "cm-metric-max" not in html  # the box is gone
    assert "4 listings combined" in html and 'class="cm-tag cm-own">Your listing<' in html
    # after the name, at the art's bottom edge, not in the foot
    assert "cm-price-badge" not in (re.search(r'<span class="slot-foot cb-foot">(.*?)<span class="slot-caption', html) or re.search(r'()', html)).group(1)
    bottom = re.search(r'<strong class="slot-name">[^<]*</strong></span>(<span class="cm-price-badges".*?)</span></span></span><span class="candidate-grid-icon-level', html).group(1)
    # two boxes, $ left and DEC right
    assert '<span class="slot-wait-badge cm-price-badge cm-price-box-usd"><span class="cm-price-usd">$1.36</span></span>' in bottom
    assert '<span class="slot-wait-badge cm-price-badge cm-price-box-dec"><span class="cm-price-dec"><img class="cm-dec-icon" src="/static/assets/land/token-dec.png" alt="DEC" width="16" height="16">2,785</span>' in bottom
    assert 'class="cm-price"' not in html
    for uid in ("C-", "market_id", "seller"):
        assert uid not in html


def test_market_css_is_scoped_and_the_old_price_box_is_gone():
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    assert ".cm-price " not in css and ".cm-price {" not in css and "#cm-load" not in css and "cm-filters-changed" not in css
    assert ".card-browser .cb-art > .cm-price-badges { position: absolute; inset: auto 0 0; z-index: 4; display: grid; grid-template-columns: 1fr 1fr; gap: 3px;" in css  # two boxes; / edge to edge, flush # , full width, flush
    assert ".cb-foot .slot-wait-badge.cm-price-badge" not in css
    assert ".card-browser[data-cb-mode=\"market\"][data-cb-maxed] .cb-level .cb-level-cc { display: none; }" in css
    for rule in re.findall(r"^([^@/\s][^{]*)\{", css, re.M):
        if "cm-" in rule:
            for selector in rule.split(","):
                assert selector.strip().startswith((".card-browser", "html")) and ".card-browser" in selector, selector


def test_maxed_price_is_the_same_under_every_sort():
    # every print in view
    # whose group came back full gets the same deep read, whatever the sort, so a card's maxed offers never change with it.
    def offers(sort: str) -> dict:
        calls: list[str] = []
        body = read_market(DETAILS, {}, sort=sort, player="player0005", getter=_getter(calls, full_ids={349}), cache=MarketCache(), maxed=True)
        return {(r["card_detail_id"], r["edition"], r["foil"], r.get("combined")): r["price_usd"] for r in body["listings"]}
    by_rate, by_price, by_pp = offers("cost_per_pp"), offers("price"), offers("pp")
    shared = set(by_rate) & set(by_price) & set(by_pp)
    assert any(k[0] == 349 for k in shared)
    for key in shared:
        assert by_rate[key] == by_price[key] == by_pp[key], key
    assert card_market.MAXED_DEEP_PRINTS == card_market.MAX_PRINTS
