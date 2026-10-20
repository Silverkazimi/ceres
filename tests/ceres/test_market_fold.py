""" (; chosen rule "≥25% CC or a good deal").

Per print, a listing keeps its own tile when its CC is at least 25 % of the print's max CC or its PP/$ is within 20 % of the print's
best PP/$; the other listings of the print fold into one container tile at the place of the best of them. Under a name search
only; the server then keeps up to NAME_LISTINGS_PER_PRINT listings per print."""

from __future__ import annotations

from pathlib import Path

from ceres import card_market
from ceres.card_market import MarketCache, read_market
from tests.ceres.test_filter_audit import DETAILS, _getter
from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"

ROWS_JS = r"""
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw Error(what + ': ' + JSON.stringify(a) + ' != ' + JSON.stringify(b)); };
// one print, max CC 400; PP = CC (1 PP per CC); price sets the PP/$
const row = (name, bcx, usd, extra = {}) => ({ name: name + ' card', card_detail_id: 7, edition: 7, foil: 0, card_set: 'chaos', rarity: 'common', splinters: [],
  level: 1, bcx, bcx_max: 400, land_base_pp: bcx, max_level_pp: 400, price_usd: usd, price_dec: usd * 2000, combined: 1, lkey: name, ...extra });
"""


def test_fold_rule_boundaries_25_percent_cc_and_20_percent_deal():
    run_in_module_context(ROWS_JS + r"""
// CC share: 100 of 400 is exactly 25 % -> own tile; 99 is below (and a poor deal here) -> folds
eq(cmFoldKeeps(row('a', 100, 1000), 1), true, '25 % CC keeps');
eq(cmFoldKeeps(row('b', 99, 1000), 1), false, 'under 25 % CC and a poor deal folds');
// deal: best 1 PP/$; a copy at 1/1.2 PP/$ is exactly 20 % dearer per PP -> own tile; a hair more -> folds
eq(cmFoldKeeps(row('c', 10, 12), 1), true, 'exactly 20 % keeps');
eq(cmFoldKeeps(row('d', 10, 12.001), 1), false, 'over 20 % folds');
eq(cmFoldKeeps(row('e', 1, 1, { bcx_max: 0 }), 100), true, 'black foil / max CC 0 never folds');
eq(cmFoldKeeps(row('f', 1, 1, { bcx_max: null }), 100), true, 'unknown max CC never folds');
""", entry=STATIC / "card-market-model.js")


def test_container_count_price_and_place_in_the_order():
    run_in_module_context(ROWS_JS + r"""
// Price sort: the small copies are the cheapest and fold; the big copy and the good deal keep their tiles
// best PP/$ of the print: 2 (deal, big); the small copies give 1, 0.8 and 0.75 PP/$ and hold under 25 % of 400 CC
const rows = [row('small1', 1, 1), row('small2', 2, 2.5), row('big', 200, 100), row('deal', 20, 10), row('small3', 3, 4)];
const other = { ...row('other', 1, 1.5), card_detail_id: 8, lkey: 'o' };
const view = cmView([...rows, other], { q: 'card' }, 'price');
eq(view.tiles.map((t) => t.fold ? `fold:${t.fold.count}` : t.listing.name.replace(' card', '')), ['fold:3', 'other', 'deal', 'big'], 'order: container at its best folded copy');
const f = view.tiles[0];
eq([f.listing.name.replace(' card', ''), f.fold.fromUsd, f.fold.fromDec, f.fold.rows.map((r) => r.name.replace(' card', ''))], ['small1', 1, 2000, ['small1', 'small2', 'small3']], 'container content');
eq(view.summary.listings, 6, 'the summary still counts every listing');
// one copy to fold stays a tile of its own
eq(cmView([row('big', 200, 100), row('small', 1, 5)], { q: 'card' }, 'price').tiles.map((t) => t.fold ? 'fold' : t.listing.name.replace(' card', '')), ['small', 'big'], 'single fold kept');
// no name search, or Maxed: no folding
eq(cmView(rows, {}, 'price').tiles.some((t) => t.fold), false, 'plain grid unchanged');
eq(cmView(rows.map((r) => ({ ...r, maxed: true })), { q: 'card', maxed: true }, 'price').tiles.some((t) => t.fold), false, 'Maxed unchanged');
// PP sort: the container sorts by its best folded copy's PP
eq(cmView(rows, { q: 'card' }, 'pp').tiles.map((t) => t.fold ? `fold:${t.fold.count}` : t.listing.name.replace(' card', '')), ['big', 'deal', 'fold:3'], 'PP order');
""", entry=STATIC / "card-market-model.js")


def test_at_most_two_good_deal_tiles_per_print():
    # the two best good deals keep tiles, further good deals fold
    run_in_module_context(ROWS_JS + r"""
const name = (t) => t.fold ? `fold:${t.fold.rows.map((r) => r.name.replace(' card', '')).join('+')}` : t.listing.name.replace(' card', '');
// best PP/$ 2 (big, 200 CC); good deals (within 20 %): d1 1.9, d2 1.95, d3 1.7, d4 1.75 PP/$; poor: p1 1.0
const rows = [row('big', 200, 100), row('d1', 19, 10), row('d2', 39, 20), row('d3', 17, 10), row('d4', 35, 20), row('p1', 10, 10)];
eq(CM_FOLD_DEAL_TILES, 2, 'the cap');
const tiles = cmView(rows, { q: 'card' }, 'price').tiles.map(name);
eq(tiles.filter((t) => !t.startsWith('fold')).sort(), ['big', 'd1', 'd2'], 'the two best deals (d2 1.95, d1 1.9) keep tiles');
eq(tiles.filter((t) => t.startsWith('fold')), ['fold:d3+p1+d4'], 'further deals fold with the poor copy, in sort order');
// equal PP/$: the earlier in the sort keeps its tile
const tie = [row('big', 200, 100), row('t1', 19, 10), row('t2', 19, 10), row('t3', 19, 10), row('p', 1, 5)];
const tt = cmView(tie, { q: 'card' }, 'price').tiles.map(name);
eq(tt.filter((t) => t.startsWith('fold'))[0], 'fold:p+t3', 'tie: t1 and t2 keep tiles, t3 folds');
// a deal of 25 % CC or more is a size tile and does not use the cap
const sized = [row('big', 200, 100), row('s1', 100, 52), row('s2', 100, 52.5), row('s3', 100, 53), row('d', 19, 10), row('e', 18, 9.6), row('p', 1, 5)];
eq(cmView(sized, { q: 'card' }, 'price').tiles.filter((t) => !t.fold).map(name).filter((n) => ['d', 'e'].includes(n)).sort(), ['d', 'e'], 'sized copies do not use the cap: both deals keep tiles');
""", entry=STATIC / "card-market-model.js")


def test_container_tile_markup_same_slots_and_opens_the_menu():
    out = run_in_module_context(ROWS_JS + r"""
const tileHtml = (t) => JSON.stringify(t.extra);
const rows = [row('small1', 1, 1), row('small2', 2, 2.5), row('big', 200, 100)];
const view = cmView(rows, { q: 'card' }, 'price');
const fold = view.tiles[0];
const html = JSON.parse(cmTileHtml(fold, tileHtml));
const plain = JSON.parse(cmTileHtml(view.tiles[1], tileHtml));
if (!/\bcm-tile cm-fold-tile\b/.test(html.className)) throw Error('class: ' + html.className);
if (!/class=\\?"cm-tag cm-fold-count\\?"/.test(JSON.stringify(html.art)) || !/\+2 more copies/.test(html.art)) throw Error('count: ' + html.art);
if (!/cm-fold-from/.test(html.bottom) || !/from \$1\.00/.test(html.bottom)) throw Error('from: ' + html.bottom);
if (!/data-cm-index="0"/.test(html.attrs) || !/aria-haspopup="dialog"/.test(html.attrs)) throw Error('attrs: ' + html.attrs);
if (!/every copy in the card menu/.test(html.bottom)) throw Error('tooltip: ' + html.bottom);
// the same slots as a listing tile (art, price badge, PP and PP/$, the one-click button): one tile size in the grid
for (const k of ['metrics', 'after']) if (!html[k] || !plain[k] || html[k].split('candidate-grid-metric').length !== plain[k].split('candidate-grid-metric').length) throw Error('slot ' + k);
if (!/cm-tile-actions/.test(html.after)) throw Error('actions: ' + html.after);
// the click: card-cart.js maps data-cm-index to the tile's listing (its best folded copy) and opens that print's menu
const picked = rowForTile({ dataset: { cmIndex: '0' } }, () => view.tiles.map((t) => t.listing));
if (!picked || picked.name.replace(' card', '') !== 'small1') throw Error('menu row: ' + (picked && picked.name));
console.log('ok');
""", entry=STATIC / "card-market.js").stdout
    assert out.strip().splitlines()[-1] == "ok"
    cart = (STATIC / "card-cart.js").read_text(encoding="utf-8")
    assert "event.target.closest?.('.cm-tile')" in cart and "cartOpenMenu(row, tile.querySelector" in cart


def test_server_keeps_more_listings_per_print_under_a_name_search():
    assert card_market.per_print({}) == card_market.LISTINGS_PER_PRINT
    assert card_market.per_print({"q": "  "}) == card_market.LISTINGS_PER_PRINT
    assert card_market.per_print({"q": "Card 50"}) == card_market.NAME_LISTINGS_PER_PRINT
    calls: list[str] = []
    body = read_market(DETAILS, {"q": "Card 500"}, sort="price", getter=_getter(calls), cache=MarketCache())
    by_print: dict[tuple, int] = {}
    for o in body["listings"]:
        key = (o["card_detail_id"], o["foil"])
        by_print[key] = by_print.get(key, 0) + 1
    assert max(by_print.values()) > card_market.LISTINGS_PER_PRINT
    # a named print is read whole (no level slice on its deep read)
    deep = [u for u in calls if "/market/market_query_by_card" in u]
    assert deep and not any("min_level" in u or "max_level" in u for u in deep)
