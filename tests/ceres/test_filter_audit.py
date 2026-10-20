""".

The Market reads a bounded number of prints per page, so the rank of a print must be a bound of its best offer that passes
every filter, and each page must show exactly the offers between two such bounds. These tests run read_market against a
synthetic upstream (a few hundred prints with full listings; grouped answers capped at 10 rows per card id, deep reads at 60,
both honouring min_level / max_level like the real API) and compare every page with the brute-force truth: each print's best
offers computed from all its listings. No live network."""

from __future__ import annotations

import random
import urllib.parse

import pytest

from ceres import card_market
from ceres.card_market import (MarketCache, maxed_offers, normalise_print, offer_key, pp_in_range, print_matches, print_offers,
                               rank_key, rank_prints, read_market)
from ceres.level_cap import level_for_bcx
from tests.ceres.vm_context import run_in_module_context
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"
RARITY = {1: "common", 2: "rare", 3: "epic", 4: "legendary"}


def _market(seed: int = 9238, n_prints: int = 260):
    """Details, for_sale_grouped rows and every listing of each print (Chaos, edition 7; regular, gold and a few black foils)."""
    rng = random.Random(seed)
    details: dict[int, dict] = {}
    grouped: list[dict] = []
    listings: dict[tuple, list[dict]] = {}
    market_id = 0
    for n in range(n_prints):
        card_id = 5001 + n
        rarity = 1 + n % 4
        foil = 3 if n % 37 == 0 else (1 if n % 5 == 0 else 0)
        details[card_id] = {"id": card_id, "name": f"Card {card_id}", "rarity": rarity, "tier": 7, "editions": "7", "color": "Red",
                            "secondary_color": None, "no_pp": False, "type": "Monster"}
        item = normalise_print({"card_detail_id": card_id, "edition": 7, "foil": foil, "qty": 1, "low_price": 1, "low_price_bcx": 1}, details)
        thresholds = card_market.resolve_level_thresholds(card_id, 7, RARITY[rarity], foil in (1, 2), "chaos")
        bcx_max = item["bcx_max"]
        rows = []
        base_rate = {1: 0.004, 2: 0.08, 3: 0.6, 4: 4.0}[rarity] * (1 + rng.random()) * (4 if foil == 1 else 1)
        for _ in range(rng.randint(1, 40)):
            market_id += 1
            if foil == 3:
                cc, level = thresholds[-1] if thresholds else 1, len(thresholds or [1])
                pp = card_market.rank_max_pp(item)
                price = round(rng.uniform(20, 400) * rarity, 3)
            else:
                cc = rng.choice([1, 1, 2, 3, 5] + [rng.randint(1, bcx_max) for _ in range(4)] + [bcx_max, bcx_max + rng.randint(0, 20)])
                level = level_for_bcx(thresholds, cc)
                pp = card_market.pp_at(item["max_level_pp"], cc, bcx_max)
                # small copies cost more per CC (as on the real market), big ones less
                price = round(cc * base_rate * (1.0 + 2.5 / (1 + cc ** 0.5)) * rng.uniform(0.9, 1.6), 3)
            price = max(price, 0.01)
            rows.append({"market_id": f"{market_id:040x}-0", "card_id": f"C-TESTUID{market_id:05d}", "card_detail_id": card_id, "edition": 7,
                         "foil": foil, "bcx": cc, "level": level, "buy_price": price, "price_bcx": price / cc, "currency": "USD",
                         "land_base_pp": f"{pp:.3f}" if pp is not None else None, "seller": f"seller{market_id % 7}", "type": "SELL"})
        rows.sort(key=lambda r: r["price_bcx"])
        listings[(card_id, foil, 7)] = rows
        grouped.append({"card_detail_id": card_id, "gold": foil in (1, 2), "foil": foil, "edition": 7, "qty": len(rows), "level": 1,
                        "low_price": min(r["buy_price"] for r in rows), "low_price_bcx": min(r["price_bcx"] for r in rows)})
    return details, grouped, listings


DETAILS, GROUPED, LISTINGS = _market()


def _getter(calls: list[str]):
    def levels_ok(row, query):
        low = int(query["min_level"][0]) if "min_level" in query else None
        high = int(query["max_level"][0]) if "max_level" in query else None
        return (low is None or row["level"] >= low) and (high is None or row["level"] <= high)

    def get(url: str):
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parsed.query)
        calls.append(url)
        if parsed.path == "/market/for_sale_grouped":
            return GROUPED
        if parsed.path == "/settings":
            return {"dec_price": 0.0005}
        if parsed.path == "/market/market_query_grouped":
            foil, limit = int(query["foil"][0]), min(int(query["limit"][0]), card_market.GROUPED_ROWS_CAP)
            out = []
            for card_id in (int(i) for i in query["card_ids"][0].split(",")):
                rows = [r for r in LISTINGS.get((card_id, foil, 7), []) if levels_ok(r, query)]
                out.append({"card_detail_id": card_id, "foil": foil, "result": rows[:limit]})
            return out
        if parsed.path == "/market/market_query_by_card":
            key = (int(query["card_detail_id"][0]), int(query["foil"][0]), int(query["edition"][0]))
            return [r for r in LISTINGS.get(key, []) if levels_ok(r, query)][:min(int(query["limit"][0]), 60)]
        raise AssertionError(parsed.path)
    return get


def _truth(filters: dict, sort: str, maxed: bool) -> list[tuple]:
    """Every offer the Market should show, in order: each print's best offers from all its listings."""
    f = {**filters, "maxed": maxed}
    items = [p for p in (normalise_print(r, DETAILS) for r in GROUPED) if p and print_matches(p, f)]
    out = []
    for p in items:
        rows = LISTINGS[(p["card_detail_id"], p["foil"], p["edition"])]
        if maxed:
            mine = [o for o in maxed_offers(p, rows, 0.0005, "player0005") if pp_in_range(o["land_base_pp"], f)]
        else:
            mine = print_offers(p, rows, sort, f, 0.0005, "player0005")
        out.extend((offer_key(o, sort, seq), _id(o)) for seq, o in enumerate(mine) if offer_key(o, sort, seq) is not None)
    return sorted(out)


def _id(o: dict) -> tuple:
    return (o["card_detail_id"], o["edition"], o["foil"], tuple(o.get("parts_keys") or [o["lkey"]]), round(o["price_usd"], 6))


def _pages(filters: dict, sort: str, maxed: bool, cache: MarketCache, limit: int = 40):
    pages, start = [], None
    for page in range(1, limit):
        body = read_market(DETAILS, filters, sort=sort, player="player0005", getter=_getter([]), cache=cache, maxed=maxed, page=page, start=start)
        pages.append(body)
        if not body["has_more"]:
            return pages
        start = body["next_start"]
    raise AssertionError("no last page")


CASES = [({}, False), ({"min_pp": "3000"}, False), ({"min_pp": "3000", "rarity": "legendary"}, False), ({"max_pp": "200"}, False),
         ({"min_pp": "500", "max_pp": "2500"}, False), ({"rarity": "common,epic", "foil": "0"}, False), ({}, True),
         ({"min_pp": "3000"}, True), ({"max_pp": "2000"}, True)]


@pytest.mark.parametrize("sort", card_market.SORTS)
@pytest.mark.parametrize("filters,maxed", CASES)
def test_every_page_is_complete_and_in_order(filters, maxed, sort, monkeypatch):
    monkeypatch.setattr(card_market, "PAGE_DEEP_BUDGET", 10 ** 6)  # exactness; the budget has its own test below
    truth = _truth(filters, sort, maxed)
    pages = _pages(filters, sort, maxed, MarketCache())
    seen: list[tuple] = []
    for body in pages:
        keys = [offer_key(o, sort, 0)[:3] for o in body["listings"]]
        values = [k[0] for k in keys]
        assert values == sorted(values), (sort, filters, "a page is out of order")
        seen.extend(_id(o) for o in body["listings"])
    # every page together: exactly the truth, in its order, nothing twice and nothing better left out
    assert seen == [i for _, i in truth], (sort, filters, maxed, len(seen), len(truth))
    assert sum(b["uncertain"] for b in pages) == 0


def test_deep_reads_of_one_market_read_are_bounded(monkeypatch):
    # PP + Max PP 200 measured 2026-10-15: 285 deep reads in one read before the budget, and the API answered 429
    monkeypatch.setattr(card_market, "PAGE_DEEP_BUDGET", 5)
    calls: list[str] = []
    body = read_market(DETAILS, {"max_pp": "200"}, sort="pp", getter=_getter(calls), cache=MarketCache())
    deep = [u for u in calls if "/market/market_query_by_card" in u]
    assert len(deep) <= 5 and body["deep_reads"] <= 5
    assert body["uncertain"] > 0  # the prints past the budget are counted, never hidden
    assert len(calls) <= card_market.PAGE_CALL_BUDGET + 2 * card_market.IDS_PER_CALL


def test_rank_key_bounds_every_qualifying_offer():
    for sort in card_market.SORTS:
        for filters, maxed in CASES:
            f = {**filters, "maxed": maxed}
            for p in rank_prints([normalise_print(r, DETAILS) for r in GROUPED], f, sort, maxed):
                rows = LISTINGS[(p["card_detail_id"], p["foil"], p["edition"])]
                offers = maxed_offers(p, rows, 0.0005, None) if maxed else [x for x in (card_market.normalise_listing(r, p, 0.0005, None) for r in rows) if x]
                offers = [o for o in offers if pp_in_range(o["land_base_pp"], f)]
                bound = rank_key(p, f, sort, maxed)
                for o in offers:
                    key = offer_key(o, sort, 0)
                    assert key is None or key >= bound, (sort, filters, p["name"], key, bound)


def test_owner_case_min_pp_3000_price_shows_the_cheapest_legendaries():
    cache = MarketCache()
    everything = read_market(DETAILS, {"min_pp": "3000"}, sort="price", getter=_getter([]), cache=cache)
    legendary = read_market(DETAILS, {"min_pp": "3000", "rarity": "legendary"}, sort="price", getter=_getter([]), cache=cache)
    shown = [_id(o) for o in everything["listings"]]
    assert any(o["rarity"] == "legendary" for o in everything["listings"])
    hi = everything["window"][1]
    for o in legendary["listings"]:
        if hi is None or o["price_usd"] < hi:
            assert _id(o) in shown, o["name"]  # the legendary-only page's best offers are on the all-rarity page
    # before the cheapest listing of any size ranked the prints: 100 commons with a $0.01 one-CC copy came first
    ranked = rank_prints([normalise_print(r, DETAILS) for r in GROUPED], {"min_pp": "3000"}, "price")
    assert ranked[0]["rarity"] != "common" or ranked[0]["low_price_usd"] > 0.01


def test_a_later_page_reuses_the_cached_reads_and_page_alone_still_works():
    cache = MarketCache()
    first = read_market(DETAILS, {}, sort="price", getter=_getter([]), cache=cache)
    calls: list[str] = []
    second = read_market(DETAILS, {}, sort="price", getter=_getter(calls), cache=cache, page=2, start=first["next_start"])
    assert second["start"] == first["next_start"] and second["window"][0] == first["window"][1]
    assert not any(urllib.parse.urlsplit(u).path == "/market/for_sale_grouped" for u in calls)
    old_style = read_market(DETAILS, {}, sort="price", getter=_getter([]), cache=cache, page=2)
    assert old_style["start"] == card_market.MAX_PRINTS


def test_market_client_ties_bounds_and_load_more_cursor():
    run_in_module_context(r"""
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw Error(what + ': ' + JSON.stringify(a) + ' != ' + JSON.stringify(b)); };
const r = (name, pp, usd) => ({ name, land_base_pp: pp, price_usd: usd, price_dec: usd * 2000 });
eq(cmSort([r('A', 100, 2), r('B', 100, 1)], 'pp').map((x) => x.name), ['B', 'A'], 'equal PP: cheaper first');
eq(cmSort([r('A', 100, 1), r('B', 300, 1)], 'price').map((x) => x.name), ['B', 'A'], 'equal price: more PP first');
eq(cmSort([r('A', 200, 2), r('B', 100, 1)], 'cost_per_pp').map((x) => x.name), ['B', 'A'], 'equal rate: cheaper first');
eq(cmPpBounds({ min_pp: '0', max_pp: '-5' }), { min: null, max: null }, 'Min PP 0 and a negative Max PP are no bound (the server rule)');
eq(cmView([r('No PP', null, 1)], { min_pp: '0' }).tiles.length, 1, 'Min PP 0 keeps a listing without PP');
eq(cmQuery('player0005', { min_pp: '0', max_pp: '-1' }, 'price'), '/api/market?player=player0005&sort=price', 'no bound sent');
eq(CM_SORTS.pp, 'PP', ': the Market sort says PP, as My cards does');
""", entry=STATIC / "card-market-model.js")
    js = (STATIC / "card-market.js").read_text(encoding="utf-8")
    assert "`${market.url}&page=${next}${from}`" in js and "&start=${Number(market.meta.next_start)}" in js


def test_my_cards_min_pp_zero_is_no_bound():
    run_in_module_context(r"""
const cards = [{ name: 'A', land_base_pp: null }, { name: 'B', land_base_pp: 50 }];
if (cbView(cards, { ...cbDefaultFilters(), min_pp: '0' }).tiles.length !== 2) throw Error('Min PP 0 hid a copy');
if (cbView(cards, { ...cbDefaultFilters(), max_pp: '-1' }).tiles.length !== 2) throw Error('negative Max PP hid copies');
if (cbView(cards, { ...cbDefaultFilters(), min_pp: '10' }).tiles.length !== 1) throw Error('Min PP 10');
if (CB_SORTS.pp !== 'PP') throw Error(': My cards sort label ' + CB_SORTS.pp);
""", entry=STATIC / "card-browser-model.js")


# (maintainer: "cost per pp filter color codes the PP per $ box in gold (top pick), green (above 20% pp/$ range to top
# pick), orange (20-40% above top pick) and red (40+% above top pick)")

def test_rate_tiers_boundaries_and_only_under_cost_per_pp():
    out = run_in_module_context(r"""
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw Error(what + ': ' + JSON.stringify(a) + ' != ' + JSON.stringify(b)); };
const tier = (rate) => cmRateTier(rate, 1000)?.cls ?? null;
// gap = top / rate - 1: 1000 -> 0, 1000/1.2 -> exactly 20 %, 1000/1.4 -> exactly 40 %
eq([tier(1000), tier(1200), tier(1000 / 1.0001), tier(1000 / 1.2), tier(1000 / 1.2001), tier(1000 / 1.4), tier(1000 / 1.4001), tier(100), tier(null)],
   ['is-top', 'is-top', 'is-good', 'is-good', 'is-fair', 'is-fair', 'is-dear', 'is-dear', null], 'tiers and their exact boundaries');
const row = (name, pp, usd) => ({ name, card_detail_id: 1, edition: 7, foil: 0, card_set: 'chaos', rarity: 'common', splinters: [], level: 1, bcx: 1,
  land_base_pp: pp, max_level_pp: pp, price_usd: usd, price_dec: usd * 2000, combined: 1 });
const pages = [[row('Top', 1000, 1), row('Tie', 2000, 2), row('Good', 1000, 1.2)], [row('Fair', 1000, 1.4), row('Dear', 1000, 1.41)]];
const view = cmPagedView(pages, {}, 'cost_per_pp');
eq(view.tiles.map((t) => [t.card.name, t.rateTier.cls]), [['Top', 'is-top'], ['Tie', 'is-top'], ['Good', 'is-good'], ['Fair', 'is-fair'], ['Dear', 'is-dear']], 'across loaded pages');
const html = (t) => cmMetrics(t.listing, t.rateTier || null);
if (!/cm-metric-rate is-top"/.test(html(view.tiles[0])) || !/Top pick\./.test(html(view.tiles[0]))) throw Error('top: ' + html(view.tiles[0]));
if (!/cm-metric-rate is-fair"/.test(html(view.tiles[3])) || !/40% more per PP than the top pick/.test(html(view.tiles[3]))) throw Error('fair: ' + html(view.tiles[3]));
for (const sort of ['price', 'pp']) {
  const other = cmPagedView(pages, {}, sort);
  if (other.tiles.some((t) => t.rateTier) || other.tiles.some((t) => /is-(top|good|fair|dear)/.test(html(t)))) throw Error('a tier under ' + sort);
}
console.log('ok');
""", entry=STATIC / "card-market.js").stdout
    assert out.strip().splitlines()[-1] == "ok"
