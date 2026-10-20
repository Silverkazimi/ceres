"""the card menu of one print
names each listing's seller (escaped; "You" for the maintainer's own listing, which keeps "Your listing"); the Market grid / tile
listings never carry it. No network."""
from __future__ import annotations

import json
from pathlib import Path

from ceres import card_market
from ceres.card_market import MarketCache, read_print
from tests.ceres.test_shop_cart import DETAILS, ROWS, _getter, _listings
from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_seller_in_the_menu_read_only():
    body = read_print(DETAILS, 349, 3, 0, player="player0005", getter=_getter([]), cache=MarketCache())
    sellers = {r["seller"] for r in body["listings"]}
    assert sellers == {"x", "player0005"} and all("seller" in r for r in body["listings"])
    # the grid / tile listings (normalise_listing, maxed offers) never carry it
    assert all("seller" not in r for r in _listings())
    assert "seller" not in json.dumps(card_market.maxed_offers(card_market.read_print.__globals__["_print_item"](
        DETAILS, json.loads((Path(__file__).parent / "fixtures" / "ir9182-market" / "market-2026-10-14.json").read_text())["for_sale_grouped"],
        349, 3, 0), ROWS, 0.0005, "player0005"))


def test_menu_row_shows_the_seller_escaped_and_you_for_the_owner():
    out = run_in_module_context(r"""
const base = { name: 'Gargoya Lion', card_detail_id: 349, edition: 3, foil: 0, level: 1, bcx: 1, price_usd: 0.01, price_dec: 20, lkey: 'aaaaaaaaaaaaaaa1' };
console.log(JSON.stringify({
 other: listingRow({ ...base, seller: 'player_<b>0001' }),
 own: listingRow({ ...base, lkey: 'aaaaaaaaaaaaaaa2', seller: 'player0005', own: true }),
 none: listingRow({ ...base, lkey: 'aaaaaaaaaaaaaaa3' }),
 head: CM_COPY_HEAD,
}));
""", entry=STATIC / "card-cart.js").stdout
    got = json.loads(out.strip().splitlines()[-1])
    assert '<span class="cm-cell cm-c-seller" role="cell" title="player_&lt;b&gt;0001">player_&lt;b&gt;0001</span>' in got["other"]
    assert '<span class="cm-cell cm-c-seller" role="cell">You</span>' in got["own"] and ">Your listing<" in got["own"]
    assert '<span class="cm-cell cm-c-seller" role="cell" title="—">—</span>' in got["none"]
    assert '<span class="cm-cell cm-c-seller" role="columnheader">Seller</span>' in got["head"]
    # one column per cell, in the shared subgrid (): the head and every row carry the same cells in the same order
    order = lambda html: [c.split('"')[0] for c in html.split('class="cm-cell cm-c')[1:]]
    assert order(got["head"]) == order(got["other"]) == order(got["own"])
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    assert "[rate] max-content [seller] minmax(0, max-content) [tags]" in css
    assert ".card-browser .cm-dialog-panel.is-narrow .cm-copy .cm-c-seller { grid-column: 1 / 4; grid-row: 2;" in css


def test_the_market_tile_has_no_seller():
    tile = run_in_module_context(r"""
const row = { name: 'Gargoya Lion', card_detail_id: 349, edition: 3, card_set: 'chaos', rarity: 'common', foil: 0, level: 9, max_level: 10, bcx: 300,
 bcx_max: 400, land_base_pp: 750, max_level_pp: 1000, price_usd: 0.1, price_dec: 200, splinters: ['Neutral'], lkey: 'aaaaaaaaaaaaaaa6', seller: 'player_0001' };
console.log(JSON.stringify(cmTileHtml(cmTiles([row])[0], tileHtml)));
""", entry=STATIC / "card-browser.js").stdout
    assert "player_0001" not in json.loads(tile.strip().splitlines()[-1])
