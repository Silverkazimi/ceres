""": the read-only card browser -- /api/cards rows, the max-level Land PP table and the
client's filter/sort/grouping functions (card-browser-model.js, run in the vm harness)."""

from __future__ import annotations

import json
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ceres import capture as capture_mod
from ceres import routes_cards as routes_cards_mod
from ceres.app import create_app
from ceres.card_browser import build_card_rows, max_level_land_pp, plot_label
from ceres.config import CeresConfig
from ceres.routes_cards import get_cards
from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


# max-level PP: Chaos baseline x set multiplier, gold per the measured values

MEASURED = [
    # (card_set, rarity, foil, expected) -- the maxed copies of the 2026-10-14 player0005 snapshot
    ("alpha", "common", 0, 10000), ("alpha", "rare", 0, 11000), ("alpha", "epic", 0, 12500), ("alpha", "legendary", 0, 15000),
    ("alpha", "rare", 1, 40000),
    ("beta", "common", 0, 5000), ("beta", "rare", 0, 5500), ("beta", "epic", 0, 6250), ("beta", "legendary", 0, 7500),
    ("beta", "common", 1, 10000), ("beta", "rare", 1, 20000), ("beta", "epic", 1, 30000),
    ("untamed", "common", 0, 2000), ("untamed", "rare", 0, 2200), ("untamed", "epic", 0, 2500), ("untamed", "legendary", 0, 3000),
    ("untamed", "common", 1, 4000), ("untamed", "rare", 1, 8000), ("untamed", "epic", 1, 12000),
    ("chaos", "common", 0, 1000), ("chaos", "rare", 0, 1100), ("chaos", "epic", 0, 1250), ("chaos", "legendary", 0, 1500),
    ("chaos", "common", 1, 2000), ("chaos", "rare", 1, 4000), ("chaos", "epic", 1, 6000), ("chaos", "legendary", 1, 10000),
    ("rebellion", "common", 0, 500), ("rebellion", "rare", 0, 550), ("rebellion", "epic", 0, 625), ("rebellion", "legendary", 0, 750),
    ("conclave", "common", 0, 500), ("conclave", "rare", 0, 550), ("conclave", "epic", 0, 625), ("conclave", "legendary", 0, 750),
    ("conclave", "common", 1, 1000), ("conclave", "rare", 1, 2000), ("conclave", "epic", 1, 3000),
    ("land", "common", 0, 2000), ("land", "rare", 0, 2200), ("land", "epic", 0, 2500), ("land", "legendary", 0, 3000),
]


@pytest.mark.parametrize(("card_set", "rarity", "foil", "expected"), MEASURED)
def test_max_level_pp_matches_measured_maxed_copies(card_set, rarity, foil, expected):
    assert max_level_land_pp(card_set, rarity, foil) == expected


@pytest.mark.parametrize(("card_set", "rarity", "foil"), [
    ("conclave", "common", 3),   # black foil: no formula
    ("conclave", "common", 4),   # black foil arcane
    ("chaos", "rare", 2),        # gold arcane
    ("chaos", "rare", None),     # unknown foil
    ("other", "common", 0),      # unmapped print class (Soulkeep)
    ("gladius", "rare", 0),
    ("chaos", "mythic", 0),
    (None, "common", 0),
])
def test_max_level_pp_unknown_print_is_none(card_set, rarity, foil):
    assert max_level_land_pp(card_set, rarity, foil) is None


def test_max_level_pp_no_pp_card_is_zero():
    assert max_level_land_pp("gladius", "rare", 0, no_pp=True) == 0.0


def test_plot_label_matches_board_format():
    assert plot_label("65-7-89") == "065-07-089"
    assert plot_label("I-294-abc") is None
    assert plot_label(None) is None


# rows from a snapshot

DETAILS = {
    100: {"name": "Goblin Chef", "rarity": 1, "color": "Red", "card_set": "chaos", "stats": {}},
    200: {"name": "Arcane Thing", "rarity": 1, "color": "Blue", "secondary_color": "Green"},
    300: {"name": "Tireless Farmhand", "rarity": 1, "color": "Green",
          "stats": {"land_abilities": [[["GRAIN", 0.01]], [["GRAIN", 0.03]]]}},
    400: {"name": "Gladiator", "rarity": 2, "color": "Gray", "no_pp": True},
}


def _card(detail_id, *, bucket="unstaked_apparent", **facts):
    base = {"card_detail_id": detail_id, "edition": 7, "card_set": "chaos", "foil": 0, "gold": False,
            "level": 1, "bcx": 1, "land_base_pp": "10.000", "player": "player0005", "uid": f"C7-{detail_id}-SECRETUID"}
    base.update(facts)
    return {"asset_ref": f"ref-{detail_id}", "uid": base["uid"], "classification": {"bucket": bucket}, "facts": base}


def _report():
    return {
        "generated_at": "2026-10-14T11:00:00Z",
        "plots": [{"deed_uid": "I-290-deadbeef", "facts": {"plot": "65-7-89", "territory": "Central Fire"}}],
        "cards": [
            _card(100, bucket="stake_active_unconfirmed", stake_ref_uid="I-290-deadbeef", level=10, bcx=400, land_base_pp="1000.000"),
            _card(100, bucket="stake_cooling_down", stake_ref_uid="I-290-deadbeef", stake_end_date="2026-10-16T12:00:00Z"),
            _card(100, market_id="m-1"),
            _card(200, edition=18, card_set="conclave", foil=3, level=1, bcx=1, land_base_pp="5000.000"),
            _card(300, edition=19, card_set="land", level=2, bcx=5, land_base_pp="25.000"),
            _card(400, edition=6, card_set="gladius", land_base_pp="0"),
        ],
    }


def test_rows_carry_land_facts_and_never_a_uid():
    rows = build_card_rows(_report(), DETAILS, player="player0005", now=NOW)
    assert len(rows) == 6
    staked, cooling, listed, black, land, gladius = rows
    assert staked["status"] == "staked" and staked["plot"] == "065-07-089" and staked["plot_territory"] == "Central Fire"
    assert staked["rarity"] == "common" and staked["name"] == "Goblin Chef"
    assert staked["maxed"] is True and staked["max_level_pp"] == 1000 and staked["max_pp_source"] == "formula"
    assert cooling["status"] == "cooling" and cooling["ready_at"] == "2026-10-16T12:00:00Z" and cooling["plot"] == "065-07-089"
    assert cooling["maxed"] is False and cooling["max_level"] == 10
    assert listed["available"] is False and listed["unavailable_reason"] == "listed on market"
    # A black foil is always max level: its own Land PP is the max-level value.
    assert black["maxed"] is True and black["max_level_pp"] == 5000 and black["max_pp_source"] == "copy"
    assert black["soulbound"] is False and black["splinters"] == ["Water", "Earth"]  # an edition-18 foil is not bound
    assert land["land_abilities"] == [["GRAIN", 0.03]] and land["max_level_pp"] == 2000
    assert gladius["no_pp"] is True and gladius["max_level_pp"] == 0
    text = json.dumps(rows)
    assert "SECRETUID" not in text and "I-290-deadbeef" not in text and "ref-" not in text


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=SimpleNamespace(player="player0005"))))


def test_endpoint_shape(monkeypatch):
    routes_cards_mod._ROWS_CACHE.clear()
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (_report(), SimpleNamespace(token="t1", details_mtime_ns=1, generated_at="2026-10-14T11:00:00Z")))
    monkeypatch.setattr(capture_mod, "load_card_pool", lambda report: report)
    monkeypatch.setattr(routes_cards_mod, "load_details_by_id", lambda: DETAILS)
    body = json.loads(get_cards(_request(), player="player0005").body)
    assert body["player"] == "player0005" and body["count"] == 6 and len(body["cards"]) == 6
    assert body["generated_at"] == "2026-10-14T11:00:00Z"
    required = {
        "card_detail_id", "name", "edition", "card_set", "rarity", "foil", "gold", "level", "max_level", "bcx", "bcx_max",
        "land_base_pp", "max_level_pp", "max_pp_source", "maxed", "no_pp", "splinters", "available", "unavailable_reason",
        "soulbound", "rented_in", "status", "plot", "plot_territory", "ready_at", "land_abilities",
    }
    for row in body["cards"]:
        assert required <= row.keys()


def test_endpoint_without_snapshot_is_503(monkeypatch):
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (None, None))
    with pytest.raises(HTTPException) as raised:
        get_cards(_request(), player="player0005")
    assert raised.value.status_code == 503


def test_cards_page_is_served_with_version_and_module(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    client = TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player="player0005")))
    response = client.get("/cards?player=player0005")
    assert response.status_code == 200
    assert "{{CERES_VERSION}}" not in response.text
    assert "/static/card-browser.js?v=" in response.text and 'class="card-browser"' in response.text
    assert response.headers["cache-control"] == "no-cache, must-revalidate"


def test_board_header_links_to_the_card_browser():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    # the Cards button is the Plots | Cards switcher's Cards half, the last header control
    assert re.search(r'<a id="page-switch-cards" class="ceres-btn ceres-btn--choice"[^>]*href="/cards"', index)
    assert 'id="cards-btn"' not in index
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "openCardBrowser" in app and "getElementById('page-switch-cards')?.addEventListener('click', openCardBrowser)" in app


def test_card_browser_css_is_page_scoped():
    assert ".card-browser" not in (STATIC / "ceres.css").read_text(encoding="utf-8")  # no card-browser rule in the board stylesheet
    page = (STATIC / "cards.html").read_text(encoding="utf-8")
    assert page.index("/static/buttons.css") < page.index("/static/card-browser.css")
    assert "card-browser.css" not in (STATIC / "index.html").read_text(encoding="utf-8")
    block = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    selectors = [s.strip() for s in re.findall(r"([^{}]+)\{", block)]
    assert selectors
    for selector in selectors:
        for part in selector.split(","):
            assert ".card-browser" in part, part


# client filter / sort / grouping (vm harness)

MODEL = STATIC / "card-browser-model.js"


def _vm(body: str) -> None:
    run_in_module_context(body, entry=MODEL)


CARDS_JS = r"""
const NOW = Date.parse('2026-10-14T12:00:00Z');
const base = { card_set: 'chaos', edition: 7, foil: 0, gold: false, level: 10, bcx: 400, max_level: 10, no_pp: false,
  splinters: ['Fire'], available: true, unavailable_reason: null, soulbound: false, status: 'idle', plot: null,
  ready_at: null, land_abilities: [], maxed: true, max_level_pp: 1000, land_base_pp: 1000, rarity: 'common' };
const cards = [
  { ...base, card_detail_id: 1, name: 'Goblin Chef' },
  { ...base, card_detail_id: 1, name: 'Goblin Chef', status: 'staked', plot: '065-07-089', plot_territory: 'Central Fire' },
  { ...base, card_detail_id: 1, name: 'Goblin Chef', status: 'staked', plot: '065-07-089' },
  { ...base, card_detail_id: 1, name: 'Goblin Chef', level: 5, bcx: 50, maxed: false, land_base_pp: 125 },
  { ...base, card_detail_id: 2, name: 'Zebra', rarity: 'rare', foil: 1, gold: true, max_level_pp: 4000, land_base_pp: 4000, bcx: 22, splinters: ['Water'] },
  { ...base, card_detail_id: 3, name: 'Arcane', card_set: 'conclave', foil: 3, max_level_pp: null, land_base_pp: 5000, bcx: 1, soulbound: true },
  { ...base, card_detail_id: 4, name: 'Cooler', status: 'cooling', ready_at: '2026-10-16T00:00:00Z', land_abilities: [['GRAIN', 0.01]] },
  { ...base, card_detail_id: 5, name: 'Expired', status: 'cooling', ready_at: '2026-10-13T00:00:00Z' },
  { ...base, card_detail_id: 6, name: 'Listed', available: false, unavailable_reason: 'listed on market' },
  { ...base, card_detail_id: 7, name: 'Gladiator', card_set: 'gladius', no_pp: true, land_base_pp: 0, max_level_pp: 0, bcx: 1 },
];
const f = (over) => ({ ...cbDefaultFilters(), ...over });
const names = (view) => view.tiles.map((t) => t.card.name);
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw Error(what + ': ' + JSON.stringify(a) + ' != ' + JSON.stringify(b)); };
"""


def test_every_copy_is_its_own_tile():
    # (maintainer: "Show copies of all cards dont bundle them by name. We need to see each copy as an individual to prepare
    # them for land"): one tile per copy, its own status and plot; copies of one card sit together.
    _vm(CARDS_JS + r"""
const view = cbView(cards, f({ q: 'goblin' }), NOW);
eq(names(view), ['Goblin Chef', 'Goblin Chef', 'Goblin Chef', 'Goblin Chef'], 'one tile per copy');
eq(view.tiles.map((t) => [t.status, t.plot && t.plot.label, t.ready]), [['idle', null, true], ['staked', '065-07-089', false], ['staked', '065-07-089', false], ['idle', null, true]], 'own status');
eq(view.tiles[1].plot.territory, 'Central Fire', 'own plot territory');
if (view.tiles.some((t) => 'count' in t || 'copies' in t)) throw Error('a tile still bundles copies');
eq(view.summary, { copies: 4, pp: 3125, maxPp: 4000, maxUnknown: 0 }, 'summary counts copies');
const cooler = cbView(cards, f({ status: 'cooling' }), NOW).tiles[0];
eq([cooler.status, cooler.readyAt], ['cooling', '2026-10-16T00:00:00Z'], 'own cooldown end');
eq(cbView(cards, f({ q: 'listed' }), NOW).tiles[0].unavailable, 'Listed for sale', 'own reason');
// ties: same PP and name -> highest CC first, the sort is stable
const twins = [{ ...base, card_detail_id: 9, name: 'Twin', bcx: 17 }, { ...base, card_detail_id: 9, name: 'Twin', bcx: 18 }, { ...base, card_detail_id: 9, name: 'Twin', bcx: 17, plot: 'x' }];
eq(cbView(twins, f({}), NOW).tiles.map((t) => t.card.bcx + (t.card.plot || '')), ['18', '17', '17x'], 'tie-break CC desc, stable');
""")


def test_filters_status_pp_range_and_flags():
    _vm(CARDS_JS + r"""
eq(names(cbView(cards, f({ status: 'staked' }), NOW)), ['Goblin Chef', 'Goblin Chef'], 'staked');
eq(names(cbView(cards, f({ status: 'cooling' }), NOW)), ['Cooler'], 'cooling (an ended cooldown is ready again)');
eq(cbView(cards, f({ status: 'unavailable' }), NOW).tiles.map((g) => g.card.name).sort(), ['Arcane', 'Listed'], 'unavailable');
const ready = cbView(cards, f({ status: 'ready' }), NOW).tiles.map((g) => g.card.name);
if (ready.includes('Arcane') || ready.includes('Listed') || ready.includes('Gladiator') || ready.includes('Cooler')) throw Error('ready: ' + ready);
if (!ready.includes('Expired') || !ready.includes('Zebra')) throw Error('ready missing: ' + ready);
eq(names(cbView(cards, f({ min_pp: '2,000' }), NOW)), ['Arcane', 'Zebra'], 'min pp current');
eq(names(cbView(cards, f({ min_pp: '2000', use_max: true }), NOW)), ['Arcane', 'Zebra'], ': a stale use_max is ignored, min pp is the current PP');
if ('use_max' in cbDefaultFilters()) throw Error('use_max default still present');
eq(names(cbView(cards, f({ max_pp: '200' }), NOW)), ['Goblin Chef', 'Gladiator'], 'max pp');
eq(names(cbView(cards, f({ level: 'not_maxed' }), NOW)), ['Goblin Chef'], 'not maxed');
eq(names(cbView(cards, f({ ability: true }), NOW)), ['Cooler'], 'land ability');
eq(names(cbView(cards, f({ foil: '1' }), NOW)), ['Zebra'], 'gold');
eq(names(cbView(cards, f({ foil: '0', set: 'conclave' }), NOW)), [], 'set + foil');
eq(names(cbView(cards, f({ splinter: 'water' }), NOW)), ['Zebra'], 'splinter');
eq(names(cbView(cards, f({ rarity: 'rare' }), NOW)), ['Zebra'], 'rarity');
""")


def test_sorts():
    _vm(CARDS_JS + r"""
const order = (sort) => cbView(cards, f({ sort }), NOW).tiles.map((g) => g.card.name + (g.card.level === 5 ? '5' : ''));
eq(order('pp').slice(0, 3), ['Arcane', 'Zebra', 'Cooler'], 'pp desc, then name');
eq(order('max_pp')[0], 'Zebra', 'max pp');
eq(order('max_pp').at(-1), 'Arcane', 'unknown max last');
eq(order('pp_per_cc').slice(0, 2), ['Arcane', 'Zebra'], 'pp per cc');
eq(order('name').slice(0, 3), ['Arcane', 'Cooler', 'Expired'], 'name');
if (cbUnavailableLabel(cards[5]) !== 'Soulbound' || cbUnavailableLabel(cards[8]) !== 'Listed for sale') throw Error('labels');
""")


def test_staked_line_links_to_the_plot_as_in_the_picker_and_the_board_opens_it():
    # "Staked on" + region badge link (no blue pill); the board opens /?plot=<label>; the Board button reads "Plots"
    static = Path(__file__).parents[2] / "ceres" / "static"
    js = (static / "card-browser.js").read_text()
    assert 'class="slot-wait-badge cb-onland"><span class="slot-wait-label">Staked on</span>' in js and "plot=${encodeURIComponent(p.label)}" in js
    assert "candidate-status-badge is-info cb-badge\"><span>${group.staked === group.count ? 'Staked'" not in js
    nav = (static / "stage-navigation.js").read_text()
    assert "export function openPlotFromAddress()" in nav and "revealStagedChange({ deedUid: row.deed_uid })" in nav and "searchParams.delete('plot')" in nav
    assert "openPlotFromAddress();" in (static / "app.js").read_text()
    html = (static / "cards.html").read_text()
    # the Plots button is the switcher's Plots half, icon only, "Plots" in its name and tooltip
    assert 'id="page-switch-plots"' in html and 'aria-label="Plots" data-tip="Plots"' in html and ">Board<" not in html


# glass tiles, every badge on the card art, name and land abilities on the art

def _tile_js() -> str:
    return (STATIC / "card-browser.js").read_text(encoding="utf-8")


def test_status_badges_live_inside_the_art_container():
    js = _tile_js()
    tile = js[js.index("function tileHtml(tile)"):js.index("// ── rendering")]
    art_open = tile.index('<span class="candidate-art cb-art"')
    art_close = tile.index('</span><span class="candidate-grid-icon-level cb-level">')
    art = tile[art_open:art_close]
    for part in ("${topBar}", "${coolingBadge(tile)}", "${foot}", "${caption}"):
        assert part in art, part
    # the foot holds the full-width staked badge directly over the name (and, on Market tiles, the ability row above it)
    # on My cards tiles the ability row sits in the caption, below the name
    assert 'class="slot-foot cb-foot">${above}${badges}</span>' in tile
    assert '<strong class="slot-name">${esc(card.name)}</strong>${below}</span>' in tile
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    assert ".card-browser .cb-foot .cb-onland { position: static; align-self: stretch;" in css
    # the staked badge is glass and lower; the plot link a solid compact chip (its territory colours kept, not glass)
    assert ".card-browser .cb-foot .slot-wait-badge.cb-onland { padding-block: 0.08rem; color: var(--heading);" in css
    # the cooling and staked badges share one glass
    assert ".card-browser .cb-art .slot-wait-badge.cb-wait, .card-browser .cb-art .slot-wait-badge.cb-onland { border-color: var(--cb-glass-edge); background: var(--cb-glass);" in css
    # the plot link in the exact centre, the words to its left
    assert "grid-template-columns: 1fr auto 1fr;" in css and ".cb-onland:has(.ch-plot-badge) .slot-wait-label { justify-self: end; }" in css
    # a maxed copy's PP box reads "PP"
    assert "<small>${esc(card.maxed && !card.no_pp ? 'PP' : maxLine)}</small>" in (STATIC / "card-browser.js").read_text(encoding="utf-8")
    chip = css[css.index(".card-browser .cb-foot .cb-onland .ch-plot-badge {"):]
    chip = chip[:chip.index("}")]
    assert "background" not in chip and "glass" not in chip
    # the art spans the PP boxes' row at the plot tile's proportions
    assert "inline-size: 100%; block-size: auto; aspect-ratio: 6.7 / 9.4; }" in css
    # art and PP boxes share one width, capped so a one-column phone grid does not blow the card up
    assert ".card-browser .cb-tile .candidate-grid-face { inline-size: 100%; max-inline-size: 12.5rem; margin-inline: auto; }" in css
    # one top bar, the tags then the market price
    assert '`<span class="cb-art-top">${top}${artExtra}</span>`' in tile
    assert art.index("${foot}") < art.index("${caption}")
    # below the art: the level / CC line and the PP boxes only
    below = tile[art_close:]
    assert "statusBadges" not in below and "Maxed" not in below and "foil" not in below
    assert below.index("cb-level") < below.index("candidate-grid-metrics")
    assert "Staked on" in js and "cb-onland" in js  # link kept
    assert "plot=${encodeURIComponent(p.label)}" in js
    assert "cb-count" not in js and "×${" not in js  # no copy count, one tile per copy


def test_cooling_uses_the_board_wait_badge_with_a_live_timer():
    js = _tile_js()
    cooling = js[js.index("function coolingBadge(tile)"):js.index("function abilityRow(card)")]
    assert 'class="slot-wait-badge cb-wait"' in cooling and 'class="slot-wait-time" data-countdown-until="' in cooling
    assert "import { bindCountdowns, countdownText } from './countdown.js';" in js and "bindCountdowns();" in js
    selector = (STATIC / "countdown.js").read_text(encoding="utf-8")
    assert ".slot-wait-badge [data-countdown-until]" in selector  # the shared tick covers this page's badge
    assert "ready in" not in js  # the old static "ready in" text is gone


def test_ability_row_sits_directly_above_the_name():
    js = _tile_js()
    row = js[js.index("function abilityRow(card)"):js.index("function tileHtml(tile)")]
    assert 'class="slot-ability-row is-centred cb-abilities"' in row and 'class="slot-ability-item"' in row
    tile = js[js.index("function tileHtml(tile)"):js.index("// ── rendering")]
    assert "} cb-caption\"><strong class=\"slot-name\">" in tile and "'slot-caption slot-caption--long' : 'slot-caption'" in tile
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    # the plot tile's name geometry (ceres.css .editor-band, reference width 107.2) scaled to this art's width
    assert ".card-browser .cb-grid .cb-tile .cb-art { container-type: inline-size; }" in css
    assert ".card-browser .cb-art .cb-foot { bottom: calc(48.5cqi + 3px)" in css  # just above the name strip
    assert ".card-browser .cb-art .cb-caption { block-size: 48.5cqi; padding-block-start: 2.425cqi; z-index: 2; background: none;" in css
    board = (STATIC / "ceres.css").read_text(encoding="utf-8")
    for rule in ("block-size: calc(100cqi * 29.4 / 107.2)", "font-size: calc(100cqi * 12.8 / 107.2); line-height: calc(100cqi * 14.7 / 107.2)",
                 "font-size: calc(100cqi * 11.5 / 107.2)", "text-wrap: balance"):
        assert rule in board and rule in css, rule
    assert ".editor-band .slot-caption { height: 48.5cqi; padding-block-start: 2.425cqi; }" in board
    # the tags share one row and wrap only when the width is used up, every tag the same height
    assert ".card-browser .cb-art-tags { display: flex; flex: 1 1 0; flex-flow: row wrap;" in css
    assert "block-size: 1.05rem;" in css
    # one type size for every tag and the labels centred on their capitals
    assert "font-size: 0.6rem; line-height: 1; vertical-align: top; text-box: trim-both cap alphabetic; }" in css
    assert ".card-browser .cb-art-tags .foil-badge { box-shadow: 0 1px 4px var(--shadow); }" in css


def test_tiles_and_summary_are_glass_without_backdrop_filter():
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    assert "--cb-glass:" in css and "var(--glass-row, 60%)" in css
    assert ".card-browser .cb-grid > .cb-tile { border-color: var(--cb-glass-edge); background: var(--cb-glass);" in css
    assert "background: var(--cb-glass); box-shadow: inset 0 1px 0 var(--cb-glass-edge)" in css  # summary bar
    assert "backdrop-filter" not in re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_use_max_level_toggle_is_removed():
    # (Use max-level PP\""): no toggle, no filter state, no contract selector; the sort stays.
    html = (STATIC / "cards.html").read_text(encoding="utf-8")
    assert "cb-use-max" not in html and "Use max-level PP" not in html
    js = _tile_js()
    assert "cb-use-max" not in js and "f.use_max" not in js
    assert "const { use_max: _dropped, ...kept } = saved;" in js  # a remembered old value is dropped, never applied
    model = (STATIC / "card-browser-model.js").read_text(encoding="utf-8")
    assert "use_max" not in model and "max_pp: 'Max-level PP'" in model
    contract = (Path(__file__).parent / "fixtures" / "ceres-button-contract.json").read_text(encoding="utf-8")
    assert "cb-use-max" not in contract and '"selector": "#cb-ability"' in contract


def test_pp_per_cc_ignores_surplus_cc_past_max():
    # surplus CC adds no PP.
    _vm(r"""
const atMax = { name: 'Alric', bcx: 17, bcx_max: 17, land_base_pp: 40000 };
const over = { name: 'Neb', bcx: 18, bcx_max: 17, land_base_pp: 40000 };
const black = { name: 'Black', bcx: 1, bcx_max: 0, land_base_pp: 5000 };
const unknown = { name: 'Odd', bcx: 4, bcx_max: null, land_base_pp: 100 };
if (cbPpPerCcOf(atMax) !== cbPpPerCcOf(over)) throw Error('surplus CC changed PP per CC: ' + cbPpPerCcOf(over));
if (Math.abs(cbPpPerCcOf(over) - 40000 / 17) > 1e-9) throw Error('over: ' + cbPpPerCcOf(over));
if (cbPpPerCcOf(black) !== 5000 || cbPpPerCcOf(unknown) !== 25) throw Error('black / unknown max');
if (cbPpPerCc({ card: over }) !== cbPpPerCcOf(atMax)) throw Error('sort value');
""")
    js = (STATIC / "card-browser.js").read_text(encoding="utf-8")
    assert "card.land_base_pp / card.bcx" not in js and "cbPpPerCcOf(card)" in js


def test_no_overcombined_tag_on_the_art():
    # 's surplus count stays for PP per CC
    _vm(r"""
const c = (bcx, bcx_max) => ({ bcx, bcx_max, land_base_pp: 40000 });
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw Error(what + ': ' + JSON.stringify(a)); };
eq([cbSurplusCc(c(18, 17)), cbSurplusCc(c(17, 17)), cbSurplusCc(c(3, 0)), cbSurplusCc(c(3, null))], [1, 0, 0, 0], 'surplus');
""")
    js = (STATIC / "card-browser.js").read_text(encoding="utf-8")
    assert "overcombinedTag" not in js and "cb-overcombined" not in js and ">Overcombined" not in js
    assert "${foil}${maxed}${levelTag}</span>" in js  # the Market level tag sits where Maxed sits
    assert "cbOvercombinedTip" not in (STATIC / "card-browser-tips.js").read_text(encoding="utf-8")
    assert "cb-overcombined" not in (STATIC / "card-browser.css").read_text(encoding="utf-8")


def test_cc_line_shows_current_over_max():
    js = (STATIC / "card-browser.js").read_text(encoding="utf-8")
    line = js[js.index("function ccLine(card)"):js.index("function tileHtml(tile)")]
    assert "CC <b>${cc}</b> / ${max}" in line
    assert "card.bcx_max === 0 ? 'max'" in line and "card.bcx_max > 0 ? num(card.bcx_max) : '—'" in line
    tile = js[js.index("function tileHtml(tile)"):js.index("// ── rendering")]
    assert "${ccLine(card)}" in tile and "} CC` : '— CC'" not in tile


# the board's tooltips on every icon of the card viewer

CARD_BROWSER = STATIC / "card-browser.js"
TIPS = STATIC / "card-browser-tips.js"
_IMPORT = re.compile(r"^import \{[^}]*\} from '\./([A-Za-z0-9_-]+)\.js';$", re.M)


def _graph(entry: Path) -> set[str]:
    seen: set[str] = set()
    todo = [entry]
    while todo:
        path = todo.pop()
        if path.name in seen:
            continue
        seen.add(path.name)
        todo.extend(path.parent / f"{dep}.js" for dep in _IMPORT.findall(path.read_text(encoding="utf-8")))
    return seen


def test_tooltip_engine_runs_on_cards_without_the_board_graph():
    # tooltip.js reads esc from escape.js (no imports); format.js re-exports the same function for the board.
    assert _graph(STATIC / "tooltip.js") == {"tooltip.js", "escape.js", "state.js"}
    page = _graph(CARD_BROWSER)
    assert "tooltip.js" in page and "card-browser-tips.js" in page
    for board_module in ("board.js", "badges.js", "icons.js", "format.js", "editor.js", "app.js"):
        assert board_module not in page, board_module
    js = CARD_BROWSER.read_text(encoding="utf-8")
    assert "initTooltips();" in js
    fmt = (STATIC / "format.js").read_text(encoding="utf-8")
    assert "import { esc } from './escape.js';" in fmt and "export { esc };" in fmt and "export function esc" not in fmt
    # the board's tooltip markup is unchanged: same esc, same tipAttrs output
    run_in_module_context(r"""
if (esc(`a<b>"c'&`) !== 'a&lt;b&gt;&quot;c&#39;&amp;') throw Error('esc changed');
const a = tipAttrs({ title: 'Rarity: Rare', icon: 'rarity/gem-rare.svg', peer: 'icons_identity' });
if (a !== ' data-tip="Rarity: Rare" data-tip-kind="explain" aria-label="Rarity: Rare" data-tip-peer="icons_identity" data-tip-depth="brief" data-tip-icon="rarity/gem-rare.svg"') throw Error(a);
""")


TILE_JS = r"""
const base = { name: 'Goblin Chef', card_set: 'chaos', edition: 7, foil: 0, level: 10, max_level: 10, bcx: 379, bcx_max: 379,
  maxed: true, no_pp: false, splinters: ['Fire'], rarity: 'legendary', land_base_pp: 1000, max_level_pp: 1000, land_abilities: [] };
const tiles = [
  { card: { ...base, foil: 1, bcx: 380, land_abilities: [['GRAIN', 0.01], ['BLOODLINE', 0.3, 'Undead'], 'ENERGIZED', ['DD', 0.5], ['STATIC_CHARGE', 0.1], ['RESEARCH', 0.02]] },
    status: 'staked', plot: { label: '065-09-083', territory: 'Central Fire' }, readyAt: null, unavailable: '' },
  { card: { ...base, foil: 3, bcx: 1, bcx_max: 0, splinters: ['Water', 'Death'], card_set: 'conclave', edition: 14 }, status: 'cooling', plot: null, readyAt: '2026-10-16T02:00:00Z', unavailable: 'Soulbound' },
  { card: { ...base, maxed: false, level: 4, bcx: 20, card_set: 'land', edition: 21, rarity: 'rare', foil: 2 }, status: 'staked', plot: null, readyAt: null, unavailable: 'Listed for sale' },
  { card: { ...base, card_set: 'gladius', edition: 6, no_pp: true, land_base_pp: 0, foil: 4 }, status: 'idle', plot: null, readyAt: null, unavailable: 'Dead in Survival' },
];
console.log(JSON.stringify(tiles.map(tileHtml)));
"""

ICON_CLASSES = ("foil-badge", "cb-maxed", "slot-wait-badge", "ch-plot-badge", "cb-onland", "cb-badge",
                "ability-icon", "splinter-icon", "rarity-gem", "set-icon", "cb-metric-pp", "cb-metric-cc", "cb-cc")
UIDISH = re.compile(r"\b(?:[A-Z]{1,2}\d?-\d+-[a-z0-9]{6,}|[0-9a-f]{8,}|I-\d+-)", re.I)


def _rendered_tiles() -> list[str]:
    out = run_in_module_context(TILE_JS, entry=CARD_BROWSER).stdout
    return json.loads(out.strip().splitlines()[-1])


def _elements(html: str, cls: str) -> list[str]:
    return [m.group(0) for m in re.finditer(r"<(?:img|span|a)\b[^>]*\bclass=\"[^\"]*\b" + re.escape(cls) + r"\b[^\"]*\"[^>]*>", html)]


def test_every_icon_on_a_tile_has_a_board_tooltip_and_no_uid():
    tiles = _rendered_tiles()
    seen = set()
    for html in tiles:
        for img in re.findall(r"<img\b[^>]*>", html):
            if "data-cb-art" in img:
                continue  # the card art itself, not an icon
            assert re.search(r'data-tip="[^"]+"', img), img
        for cls in ICON_CLASSES:
            for el in _elements(html, cls):
                if cls in ("cb-onland", "slot-wait-badge") and "cb-onland" in el and "data-tip" not in el:
                    continue  # "Staked on" + plot badge: the badge (<a>) carries the tooltip
                seen.add(cls)
                tip = re.search(r'data-tip="([^"]+)"', el)
                assert tip and tip.group(1).strip(), (cls, el)
                text = " ".join(re.findall(r'(?:data-tip|data-tip-body|aria-label)="([^"]*)"', el))
                assert not UIDISH.search(text.replace("065-09-083", "")), (cls, text)
        assert ' title="' not in html  # one tooltip system: no native titles left on a tile
    assert seen >= set(ICON_CLASSES), set(ICON_CLASSES) - seen
    joined = "".join(tiles)
    for text in ('data-tip="Splinter: Fire"', 'data-tip="Rarity: Legendary"', 'data-tip="Set: Chaos"', 'data-tip-body="Edition: Chaos Legion"',
                 'data-tip="Gold foil"', 'data-tip="Black foil"', 'data-tip="Max level"',
                 'data-tip="Cooling down"', 'data-tip="Plot 065-09-083 · Central Fire"', 'data-tip="Soulbound"', 'data-tip="Listed for sale"',
                 'data-tip="Current Land base PP"', 'data-tip="PP per CC"', 'data-tip="Bountiful Grain"', 'data-tip="Toil and Kin"',
                 'data-tip="Set: Land / Verico"', 'data-tip-body="Edition: Visions of Verico"', 'data-tip="Staked on land"'):
        assert text in joined, text
    assert 'data-tip-peer="tile_ability_icons"' in joined and 'data-ability-tip="1"' in joined


def test_ability_tooltips_match_the_plot_tiles_text():
    entries = [["GRAIN", 0.01], ["WOOD", 0.02], ["STONE", 0.05], ["IRON", 1], ["AURA", 0.1], ["RESEARCH", 0.02], ["SPS", 0.01],
               ["BLOODLINE", 0.3, "Undead"], ["DD", 0.5], ["RATIONING", 0.1], ["RATIONING_LITE", 0.05], "ENERGIZED", "LL",
               "ALTERATION", "GLINT_RECOVERY", "FORTUNE_SEEKER", ["STATIC_CHARGE", 0.1], ["NEW_THING", 0.2]]
    probe = f"const E = {json.dumps(entries)};\n"
    board = run_in_module_context(probe + "console.log(JSON.stringify(E.map(abilityTipModel)));").stdout.strip().splitlines()[-1]
    cards = run_in_module_context(probe + "console.log(JSON.stringify(E.map(cbAbilityTipModel)));", entry=TIPS).stdout.strip().splitlines()[-1]
    assert json.loads(cards) == json.loads(board)


def test_filter_row_set_rarity_and_splinter_options_carry_tooltips():
    js = CARD_BROWSER.read_text(encoding="utf-8")
    assert "set: (v) => (v ? { icon: setIcon(v) } : null)" in js and "splinter: (v) => (v ? { icon: splinterIcon(v) } : null)" in js
    run_in_module_context(r"""
for (const html of [setIcon('chaos'), setIcon('other'), splinterIcon('dragon'), rarityGem('epic')]) {
  if (!/ data-tip="[^"]+"/.test(html)) throw Error('no tooltip: ' + html);
}
if (!setIcon('rebellion').includes('data-tip="Set: Rebellion"')) throw Error(setIcon('rebellion'));
""", entry=CARD_BROWSER)


def test_icons_have_their_own_row_and_level_and_cc_share_one():
    html = _rendered_tiles()[0]
    level = html[html.index('class="candidate-grid-icon-level cb-level"'):html.index('class="candidate-grid-metrics"')]
    icons = level[level.index('class="candidate-icon-row"'):level.index('class="cb-level-cc"')]
    assert "Lv " not in icons and "CC" not in icons.replace("data-tip", "")
    row = level[level.index('class="cb-level-cc"'):]
    assert 'class="cb-lv">Lv 10/10</span>' in row and 'class="cb-cc"' in row and "CC <b>380</b> / 379" in row
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    assert ".card-browser .cb-level { display: flex; flex-direction: column;" in css
    assert ".card-browser .cb-level .cb-level-cc { display: inline-flex; flex-wrap: nowrap;" in css and "white-space: nowrap; font-size: 0.72rem; }" in css


def test_pp_per_cc_has_thousands_commas_and_no_needless_decimals():
    _vm(r"""
const cases = [[2500, '2,500'], [12500.00, '12,500'], [40000 / 17, '2,352.94'], [26.39, '26.39'], [5.00, '5'], [10000 / 11, '909.09'], [null, '—']];
for (const [value, want] of cases) { if (cbFormatPpPerCc(value) !== want) throw Error(value + ' -> ' + cbFormatPpPerCc(value)); }
""")
    js = CARD_BROWSER.read_text(encoding="utf-8")
    assert "<strong>${cbFormatPpPerCc(perCc)}</strong>" in js and "perCc.toFixed(2)" not in js
    assert 'data-tip="Set: Alpha" data-tip-kind="explain" aria-label="Set: Alpha"' in run_in_module_context(
        "console.log(setIcon('alpha', 0));", entry=CARD_BROWSER).stdout  # no "Edition: Alpha" repeat


def test_black_foils_draw_the_blk_art():
    # the official site names black foil art "<name>_blk.jpg" for black and black arcane alike; gold and gold arcane use "_gold".
    js = (STATIC / "card-browser.js").read_text(encoding="utf-8")
    art = js[js.index("function cardArtCandidates(card)"):js.index("function onArtError(event)")]
    assert "[1, 2].includes(card.foil) || (card.foil == null && card.gold) ? '_gold' : [3, 4].includes(card.foil) ? '_blk' : ''" in art
    editor = (STATIC / "editor.js").read_text(encoding="utf-8")
    assert "isGoldFoil(asset) ? '_gold' : isBlackFoil(asset) ? '_blk' : ''" in editor
    fmt = (STATIC / "format.js").read_text(encoding="utf-8")
    assert "export function isBlackFoil(w) {\n  return ['3', '4'].includes(cardFoilValue(w));" in fmt
