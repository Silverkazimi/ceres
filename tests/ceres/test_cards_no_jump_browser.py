""" in a real Chromium, hermetic: cards.html and ceres/static through page.route, /api/cards and /api/market
answered from fixtures after a delay (so the placeholders are on screen first), every image a 1x1 png, nothing else served.

From the first paint to the loaded page, at 1536x864 and 390x844, in My cards and in Market:
  * no layout shift at all (PerformanceObserver 'layout-shift', total 0);
  * the filter row (its box and every field in it) and the summary bar keep their size and place;
  * the first tile lands where its placeholder was (Market: the first placeholder tile; My cards: the loading line), and the
    tiles keep their height once drawn (no empty 26rem content-visibility box that shrinks a frame later).
Run under the shared browser lock:
flock -w 900 /tmp/er-browser.lock nice -n 15 python3 -m pytest -q tests/ceres/test_cards_no_jump_browser.py"""

from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright
from .conftest import settle

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
ORIGIN = "http://ceres.test"
DELAY_S = 1.2
PIXEL = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4b4"
                      "0000000049454e44ae426082")

NAMES = ["Goblin Shaman", "Highland Archer", "Goblin Chef", "Serpent of the Flame", "Pirate Captain", "Gargoya Lion",
         "Mycelic Slipspawn", "Spirit Hoarder", "Sabre Shark", "Wood Nymph", "Kobold Miner", "Crustacean King"]
SETS = [("beta", 2), ("chaos", 7), ("rebellion", 12), ("untamed", 4)]
RARITIES = ["common", "rare", "epic", "legendary"]
SPLINTERS = ["Fire", "Water", "Earth", "Life", "Death", "Dragon", "Neutral"]


def _card(i: int) -> dict:
    card_set, edition = SETS[i % len(SETS)]
    maxed = i % 3 == 0
    return {"card_detail_id": 100 + i % 12, "name": NAMES[i % len(NAMES)], "edition": edition, "card_set": card_set,
            "rarity": RARITIES[i % 4], "foil": 1 if i % 7 == 0 else 0, "gold": i % 7 == 0, "level": 10 if maxed else 1 + i % 9,
            "max_level": 10, "bcx": 505.0 if maxed else float(5 + i), "bcx_max": 505, "land_base_pp": 5000.0 if maxed else 100.0 + 37 * i,
            "max_level_pp": 5000.0, "max_pp_source": "formula", "maxed": maxed, "no_pp": False, "splinters": [SPLINTERS[i % 7]],
            "available": True, "unavailable_reason": None, "soulbound": False, "rented_in": False, "rental_expires_at": None,
            "status": "idle", "plot": None, "plot_territory": None, "ready_at": None, "land_abilities": [], "skin": None}


def _listing(i: int) -> dict:
    card = _card(i + 5)
    price = 0.5 + i * 0.37
    return {**{k: card[k] for k in ("card_detail_id", "name", "edition", "card_set", "rarity", "foil", "gold", "splinters", "level",
                                     "max_level", "bcx", "bcx_max", "maxed", "land_base_pp", "max_level_pp", "land_abilities")},
            "pp_source": "listing", "price_usd": price, "price_dec": price / 0.0005, "price_currency": "USD", "price_per_cc_usd": 0.003,
            "pp_per_dec": card["land_base_pp"] / (price / 0.0005), "pp_per_usd": card["land_base_pp"] / price, "dec_per_pp": 2.5,
            "max_cost_usd": price * 1.1, "max_cost_dec": price * 1.1 / 0.0005, "missing_cc": 0.0, "pp_per_dec_max": 0.4,
            "dec_per_pp_max": 2.5, "own": False, "lkey": f"k{i:04d}", "market_url": "https://splinterlands.com/card-detail/1/regular/7"}


CARDS = {"player": "sample01", "generated_at": "2026-10-15T12:00:00Z", "count": 240, "cards": [_card(i) for i in range(240)]}
MARKET = {"player": "sample01", "read_at": "2026-10-15T12:00:00Z", "filters": {}, "dec_usd": 0.0005, "prints_on_sale": 1734,
          "prints_matching": 1734, "prints_read": 100, "page": 1, "start": 0, "next_start": 100, "has_more": True, "count": 120,
          "capped": True, "sort": "cost_per_pp", "maxed": False, "listings": [_listing(i) for i in range(120)]}


def _page_html() -> str:
    html = (STATIC / "cards.html").read_text(encoding="utf-8")
    return html.replace("{{CERES_VERSION}}", "t").replace("{{CERES_RELEASED_VERSION}}", "1.0.0").replace("{{CERES_LOCAL_MARKER}}", "")


def _serve():
    page_html = _page_html()

    def handle(route):
        path = urlparse(route.request.url).path
        if path == "/cards":
            return route.fulfill(status=200, content_type="text/html", body=page_html)
        if path.startswith("/static/"):
            file = STATIC / path[len("/static/"):]
            if file.is_file():
                kind = "text/javascript" if file.suffix == ".js" else (mimetypes.guess_type(file.name)[0] or "application/octet-stream")
                return route.fulfill(status=200, content_type=kind, body=file.read_bytes())
            return route.fulfill(status=404, body="")
        if path in ("/api/cards", "/api/market"):
            time.sleep(DELAY_S)  # the placeholders are on screen meanwhile
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(CARDS if path == "/api/cards" else MARKET))
        if path.startswith("/api/"):
            return route.fulfill(status=404, content_type="application/json", body=json.dumps({"detail": "not in this fixture"}))
        if path.endswith((".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif")):
            return route.fulfill(status=200, content_type="image/png", body=PIXEL)
        return route.fulfill(status=404, body="")
    return handle


# Every animation frame from the first one: the boxes that must not move. Rects are [left, top, width, height] in CSS px.
SAMPLER = r"""
(() => {
  window.__shifts = []; window.__frames = [];
  try { new PerformanceObserver((list) => { for (const e of list.getEntries()) window.__shifts.push({ value: e.value, input: e.hadRecentInput,
    nodes: (e.sources || []).map((s) => (s.node && s.node.nodeType === 1 ? s.node.tagName + '#' + s.node.id + '.' + s.node.className : String(s.node))) }); })
    .observe({ type: 'layout-shift', buffered: true }); } catch (_) {}
  const R = (el) => { if (!el) return null; const b = el.getBoundingClientRect(); return b.width || b.height ? [b.left, b.top, b.width, b.height].map((v) => Math.round(v * 10) / 10) : null; };
  const tick = () => {
    if (document.body) {
      const market = (document.body.dataset.cbMode || document.documentElement.dataset.cbStart) === 'market';
      const grid = document.getElementById(market ? 'cm-grid' : 'cb-grid');
      const tiles = grid ? grid.querySelectorAll(':scope > .cb-tile') : [];
      const cols = grid ? getComputedStyle(grid).gridTemplateColumns.split(' ').length : 1;
      window.__frames.push({ t: performance.now(), mode: document.body.dataset.cbMode || null,
        controls: R(document.getElementById('cb-controls')), fields: [...document.querySelectorAll('#cb-controls > *')].map(R),
        purchaseBar: R(document.querySelector('.cp-bar')), purchaseButton: R(document.querySelector('[data-cp-action="load"]')),
        summary: R(document.getElementById(market ? 'cm-summary' : 'cb-summary')),
        loading: market ? null : R(document.getElementById('cb-loading')),
        tile: R(tiles[0]), row2: R(tiles[cols]), skeleton: !!(grid && grid.querySelector(':scope > .cm-skeleton')), tiles: tiles.length });
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
})();
"""


def _run(width: int, height: int, mode: str) -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--disable-gpu'])
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            errors: list[str] = []
            requests: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("request", lambda r: requests.append(urlparse(r.url).path))
            page.add_init_script(SAMPLER)
            page.route("**/*", _serve())
            page.goto(f"{ORIGIN}/cards?player=sample01&mode={mode}")
            if mode == "purchases":
                page.wait_for_selector('body[data-cb-mode="purchases"] #cp-root[data-cp-ready="true"]', timeout=30000)
            elif mode == "market":
                page.wait_for_function("document.body.dataset.marketState === 'loaded' && !!document.querySelector('#cm-grid > .cb-tile:not(.cm-skeleton)')", timeout=30000)
            else:
                page.wait_for_selector('body[data-view-ready="true"] #cb-grid > .cb-tile', timeout=30000)
            settle(page)  # the fade-ins and any late frame
            got = page.evaluate("({ shifts: window.__shifts, frames: window.__frames })")
            got["errors"] = errors
            got["requests"] = requests
            return got
        finally:
            browser.close()


def _header_only(shift: dict) -> bool:
    # design 4: the desktop header row spans the width, so the controls at its right end (Harvest, the player field)
    # are what the late --hdr-pad-right moves, not the row itself; they are the same known header slide (xfail below).
    header = ("DIV#.board-header-row", "SPAN#.player-switch", "BUTTON#cb-harvest-btn.", "BUTTON#cm-cart-btn.")
    return bool(shift["nodes"]) and all(n.startswith(header) for n in shift["nodes"])


@pytest.mark.xfail(strict=True, reason=" header row: card-browser.js placeThemeDock() sets --hdr-pad-right from the measured "
                   "theme picker after the first paint (ceres.css fallback 4.25rem = 68 px, measured 44 px at 1536), so the header "
                   "controls slide 24 px right; remove this xfail when the header reserves its final padding before the first paint")
@pytest.mark.parametrize("mode", ["cards", "market"])
def test_header_does_not_move_from_first_paint_to_loaded(mode):
    got = _run(1536, 864, mode)
    header = [s for s in got["shifts"] if not s["input"] and _header_only(s)]
    assert sum(s["value"] for s in header) == 0, header


@pytest.mark.parametrize("mode", ["cards", "market"])
@pytest.mark.parametrize("width,height", [(1536, 864), (390, 844)])
def test_nothing_moves_from_first_paint_to_loaded(width, height, mode):
    got = _run(width, height, mode)
    assert got["errors"] == []
    assert "/api/cards/purchases" not in got["requests"]
    shifts = [s for s in got["shifts"] if not s["input"] and not _header_only(s)]
    assert sum(s["value"] for s in shifts) == 0, shifts
    frames = [f for f in got["frames"] if f["controls"]]
    assert frames, "the filter row is drawn"
    first, last = frames[0], frames[-1]
    assert last["tiles"] > 0 and not last["skeleton"]
    # the filter row: one box and one place per field, from the first paint on
    assert {json.dumps(f["controls"]) for f in frames} == {json.dumps(first["controls"])}
    assert {json.dumps(f["fields"]) for f in frames} == {json.dumps(first["fields"])}, (first["fields"], last["fields"])
    # the summary bar: drawn (with its placeholder) from the first paint, never resized
    assert {json.dumps(f["summary"]) for f in frames if f["summary"]} and first["summary"]
    assert {f["summary"][1] for f in frames if f["summary"]} == {first["summary"][1]}
    assert {f["summary"][3] for f in frames if f["summary"]} == {first["summary"][3]}, [f["summary"] for f in frames[::10]]
    drawn = [f for f in frames if f["tile"]]
    if mode == "market":
        placeholder = [f for f in drawn if f["skeleton"]]
        assert placeholder, "the placeholder tiles are on screen before the read"
        assert {f["tile"][1] for f in drawn} == {placeholder[0]["tile"][1]}            # the first tile lands on its placeholder
        assert {f["tile"][3] for f in drawn} == {placeholder[0]["tile"][3]}            # at its height
    else:
        waiting = [f for f in frames if f["loading"] and not f["tile"]]
        assert waiting, "the loading line is on screen before the read"
        assert {f["tile"][1] for f in drawn} == {waiting[0]["loading"][1]}             # the grid starts where the loading line was
        assert {f["tile"][3] for f in drawn} == {drawn[0]["tile"][3]}                  # no empty 26rem box that shrinks
    rows = [f["row2"][1] for f in drawn if f["row2"]]
    assert len(set(rows)) <= 1, sorted(set(rows))                                       # the second row never moves


def test_market_summary_placeholder_in_the_page_is_the_scripts_own():
    js = (STATIC / "card-market.js").read_text(encoding="utf-8")
    placeholder = js.split("export const CM_SUMMARY_PLACEHOLDER = '", 1)[1].split("';", 1)[0]
    assert f'<div class="cb-summary" id="cm-summary" role="status" aria-live="polite">{placeholder}</div>' in _page_html()


@pytest.mark.parametrize("width,height", [(1536, 864), (390, 844)])
def test_purchases_bar_reserved_from_first_paint_without_history_read(width, height):
    got = _run(width, height, "purchases")
    assert got["errors"] == []
    assert "/api/cards/purchases" not in got["requests"]
    frames = [f for f in got["frames"] if f["purchaseBar"]]
    assert frames and frames[0]["mode"] is None
    assert {json.dumps(f["purchaseBar"]) for f in frames} == {json.dumps(frames[0]["purchaseBar"])}
    assert {json.dumps(f["purchaseButton"]) for f in frames} == {json.dumps(frames[0]["purchaseButton"])}
    assert not [s for s in got["shifts"] if not s["input"] and not _header_only(s)]


@pytest.mark.xfail(strict=True, reason=": with the 8.25rem name box and \"Ability\" line 1 has 98.4 px left at 1536x864 and "
                   "Sort needs 118 px (8.8 px gap + 109.2 px): 19.6 px short; remove this xfail when the row fits")
def test_my_cards_filter_row_is_one_line_at_1536():
    """ (maintainer chose "Narrow name search + \"Ability\""): with the 8.25rem name box and the "Ability" pill the whole
    My cards filter row, Sort included, is one line at 1536x864."""
    got = _run(1536, 864, "cards")
    last = [f for f in got["frames"] if f["controls"]][-1]
    tops = {round(r[1]) for r in last["fields"] if r}
    assert len(tops) == 1, sorted(tops)
