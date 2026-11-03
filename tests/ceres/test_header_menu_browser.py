""" design 4 (maintainer go ) in a real Chromium -- BROWSER TEST, run in public CI (never on the laptop).

Board: the fixture server (, offline). Cards: hermetic, the page's files served from ceres/static and every /api read
a fixture (as test_combine_in_place_browser.py). Nothing is signed or staged for real.

Checks: Menu opens and closes (Escape, outside click, the X), focus returns to Menu; each entry presses today's control;
the page name follows the page and the Cards view; Changes shows only while staged; the player field with its avatar
ring is the same element; the wallet chip and Iris pill keep their boxes; the phone header keeps its own controls.
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests.ceres.fixture_live import install_routes, spawn_fixture_ceres
from tests.ceres.fixture_upstream_mini import SAMPLE, board_entries, write_set

playwright = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
ORIGIN = "http://ceres.test"
ENTRY_NAMES = ["Plots", "My cards", "Market", "My purchases", "Harvest", "Changes", "Find combinables", "Refresh", "Account"]

BOXES_JS = """() => {
  const box = (sel) => { const e = document.querySelector(sel); if (!e) return null; const r = e.getBoundingClientRect();
    return [r.x, r.y, r.width, r.height].map((v) => Math.round(v * 100) / 100); };
  return { header: box('header.board-header'), iris: box('#header .theme-picker'), irisToggle: box('#header .theme-toggle'),
           wallet: box('#wallet-picker'), walletChip: box('#wallet-edge-tab'), menu: box('#hdr-menu-btn'), place: box('#hdr-place'), harvest: box('#harvest-btn, #cb-harvest-btn'),
           input: box('#player-input'), go: box('.player-switch .player-go-btn'), changes: box('#checkout-btn') };
}"""
VISIBLE_JS = "(sel) => { const e = document.querySelector(sel); return !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden'; }"


def _visible(page, selector: str) -> bool:
    return page.evaluate(VISIBLE_JS, selector)


def _open_menu(page):
    page.click("#hdr-menu-btn")
    page.wait_for_selector("#hdr-menu:not([hidden])")


def _entry(page, name: str):
    return page.locator("#hdr-menu [data-menu-entry]", has_text=name).first


@pytest.fixture(scope="module")
def board_set(tmp_path_factory):
    return write_set(tmp_path_factory.mktemp("ir9436") / "set", board_entries())


def _board(p, base, root, width=1536, height=864):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": width, "height": height})
    install_routes(page, root)
    page.goto(f"{base}/?player={SAMPLE}", timeout=15_000)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row, #plot-card-list > *').length > 0", timeout=30_000)
    if width > 1279:
        page.wait_for_selector("#edge-tabs.edge-tabs-revealed", timeout=60_000)
    page.wait_for_timeout(400)  # the theme dock's second centring pass (app.js, 300 ms)
    return browser, page


def test_board_desktop_header_menu(board_set):
    with spawn_fixture_ceres(board_set) as base, playwright.sync_playwright() as p:
        browser, page = _board(p, base, board_set)
        try:
            closed = page.evaluate(BOXES_JS)
            assert round(closed["header"][3]) == 81, closed["header"]
            assert page.inner_text("#hdr-place") == "Plots"
            assert closed["menu"][0] < closed["place"][0] < closed["harvest"][0] < closed["input"][0] < closed["go"][0]
            assert "Harvest" in page.inner_text("#harvest-btn")
            for gone in (".ceres-page-switch", "#account-menu-btn"):
                assert not _visible(page, gone), gone
                assert page.locator(gone).count() == 1, f"{gone} stays in the DOM for the phone header"
            # the player field: the same element, avatar medallion first, then field and go button, at the row's right end
            same = page.evaluate("""() => { const s = document.querySelector('.board-header-row > .player-switch');
              return !!s && s.contains(document.getElementById('player-avatar')) && s.contains(document.getElementById('player-input'))
                && s.contains(document.getElementById('player-go-btn')) && document.querySelectorAll('.player-switch').length === 1; }""")
            assert same
            ring = page.evaluate("""() => { const a = document.getElementById('player-avatar');
              a.hidden = false; a.dataset.refreshState = 'loading';
              const r = a.getBoundingClientRect(), i = document.getElementById('player-input').getBoundingClientRect();
              const cs = getComputedStyle(a); const out = { w: r.width, h: r.height, overlap: r.right - i.left, radius: cs.borderRadius,
                anim: cs.animationName, input: i.width };
              a.hidden = true; delete a.dataset.refreshState; return out; }""")
            assert ring["w"] == 60 and ring["h"] == 60 and ring["overlap"] > 0 and ring["radius"] == "50%"
            assert ring["anim"] == "avatar-refresh-border" and ring["input"] == 176
            # Changes: hidden while nothing is staged, shown (left of Harvest) with today's state classes once something is
            assert not _visible(page, "#checkout-btn")
            staged = page.evaluate("""() => { const b = document.getElementById('checkout-btn'); b.hidden = false;
              b.classList.add('is-icon-only'); const iconOnly = b.getClientRects().length > 0;
              b.classList.remove('is-icon-only'); b.classList.add('is-changes-ready');
              const c = document.getElementById('checkout-count'); c.hidden = false; c.textContent = '3';
              const r = b.getBoundingClientRect(), h = document.getElementById('harvest-btn').getBoundingClientRect();
              const out = { iconOnly, shown: b.getClientRects().length > 0, leftOfHarvest: r.right <= h.left, height: r.height };
              b.classList.remove('is-changes-ready'); b.classList.add('is-icon-only'); b.hidden = true; return out; }""")
            assert staged == {"iconOnly": False, "shown": True, "leftOfHarvest": True, "height": 34}

            # open: dialog under Menu, focus on the current place, nine entries in three columns
            _open_menu(page)
            opened = page.evaluate(BOXES_JS)
            info = page.evaluate("""() => { const p = document.getElementById('hdr-menu'), b = document.getElementById('hdr-menu-btn');
              const r = p.getBoundingClientRect(), h = document.querySelector('header.board-header').getBoundingClientRect();
              return { role: p.getAttribute('role'), label: p.getAttribute('aria-labelledby'), expanded: b.getAttribute('aria-expanded'),
                left: r.left - b.getBoundingClientRect().left, top: r.top - h.bottom, width: r.width,
                focus: document.activeElement?.dataset?.menuEntry || null,
                current: [...p.querySelectorAll('[aria-current="page"]')].map((e) => e.dataset.menuEntry),
                names: [...p.querySelectorAll('.hdr-menu-name')].map((e) => e.textContent),
                cols: p.querySelectorAll('.hdr-menu-col').length }; }""")
            assert info == {"role": "dialog", "label": "hdr-menu-title", "expanded": "true", "left": 0, "top": 8, "width": 720,
                            "focus": "plots", "current": ["plots"], "names": ENTRY_NAMES, "cols": 3}
            for key in ("header", "iris", "irisToggle", "wallet", "walletChip", "input", "go"):
                assert opened[key] == closed[key], key
            page.keyboard.press("ArrowDown")
            assert page.evaluate("document.activeElement.dataset.menuEntry") == "myCards"
            page.keyboard.press("Escape")
            assert page.locator("#hdr-menu").is_hidden()
            assert page.evaluate("document.activeElement.id") == "hdr-menu-btn"
            assert page.get_attribute("#hdr-menu-btn", "aria-expanded") == "false"
            _open_menu(page)
            page.mouse.click(900, 700)  # outside
            assert page.locator("#hdr-menu").is_hidden()
            _open_menu(page)
            page.click("#hdr-menu [data-menu-close]")
            assert page.locator("#hdr-menu").is_hidden() and page.evaluate("document.activeElement.id") == "hdr-menu-btn"
            after = page.evaluate(BOXES_JS)
            for key in ("header", "iris", "irisToggle", "wallet", "walletChip"):
                assert after[key] == closed[key], key

            # each entry presses today's control
            page.evaluate("""() => { window.__presses = []; for (const id of ['harvest-btn', 'checkout-btn', 'account-menu-btn'])
              document.getElementById(id).addEventListener('click', () => window.__presses.push(id));
              window.__refresh = []; new MutationObserver(() => window.__refresh.push(document.getElementById('player-avatar').dataset.refreshState || ''))
                .observe(document.getElementById('player-avatar'), { attributes: true, attributeFilter: ['data-refresh-state'] }); }""")
            _open_menu(page); _entry(page, "Harvest").click()
            page.wait_for_function("window.__presses.includes('harvest-btn')")
            page.keyboard.press("Escape")
            _open_menu(page); _entry(page, "Changes").click()
            page.wait_for_function("window.__presses.includes('checkout-btn')")
            page.keyboard.press("Escape")
            _open_menu(page); _entry(page, "Account").click()
            page.wait_for_function("window.__presses.includes('account-menu-btn')")
            page.wait_for_selector("#account-menu:not([hidden])")
            acct = page.evaluate("document.getElementById('account-menu').getBoundingClientRect().left")
            assert acct >= 8
            page.keyboard.press("Escape")
            assert page.evaluate("document.activeElement.id") == "hdr-menu-btn"
            _open_menu(page); _entry(page, "Refresh").click()
            page.wait_for_function("window.__refresh.includes('loading')", timeout=30_000)
            _open_menu(page); _entry(page, "Plots").click()
            assert page.locator("#hdr-menu").is_hidden() and "/cards" not in page.url
            for name, mode in (("My cards", "cards"), ("Market", "market"), ("My purchases", "purchases"), ("Find combinables", "combine")):
                _open_menu(page)
                with page.expect_navigation():
                    _entry(page, name).click()
                assert page.url.endswith(f"/cards?player={SAMPLE}&mode={mode}"), page.url
                page.go_back()
                page.wait_for_selector("#hdr-menu-btn")
        finally:
            browser.close()


def test_board_phone_header_unchanged(board_set):
    with spawn_fixture_ceres(board_set) as base, playwright.sync_playwright() as p:
        browser, page = _board(p, base, board_set, 390, 844)
        try:
            for hidden in ("#hdr-menu-btn", "#hdr-place", "#harvest-btn"):
                assert not _visible(page, hidden), hidden
            for shown in ("#page-switch-plots", "#page-switch-cards", "#account-menu-btn", "#player-input", "#player-go-btn"):
                assert _visible(page, shown), shown
            order = page.evaluate("""() => { const b = (id) => document.getElementById(id).getBoundingClientRect();
              return { switchLast: b('page-switch-cards').right >= b('account-menu-btn').right,
                       twoLines: b('page-switch-cards').top > b('player-input').bottom - 1 }; }""")
            assert order == {"switchLast": True, "twoLines": True}
        finally:
            browser.close()


# Cards, hermetic
CARDS = [{"uid": f"C7-1-AAAAAAAAA{i}", "card_detail_id": 1, "edition": 7, "foil": 0, "name": "Goblin Shaman", "rarity": "rare",
          "card_set": "chaos", "splinters": ["Fire"], "bcx": 1, "level": 1, "max_level": 8, "bcx_max": 22, "land_base_pp": 91.0,
          "max_level_pp": 1000, "status": "idle", "maxed": False} for i in (1, 2)]


def _serve(record):
    html = (STATIC / "cards.html").read_text(encoding="utf-8")
    html = html.replace("{{CERES_VERSION}}", "t").replace("{{CERES_RELEASED_VERSION}}", "1.0.0").replace("{{CERES_LOCAL_MARKER}}", "")

    def handle(route):
        url = urlparse(route.request.url)
        record.append(url.path + (f"?{url.query}" if url.query else ""))
        if url.path == "/cards":
            return route.fulfill(status=200, content_type="text/html", body=html)
        if url.path == "/":
            return route.fulfill(status=200, content_type="text/html", body="<!doctype html><title>board</title>")
        if url.path.startswith("/static/"):
            file = STATIC / url.path[len("/static/"):]
            if file.is_file():
                kind = "text/javascript" if file.suffix == ".js" else (mimetypes.guess_type(file.name)[0] or "application/octet-stream")
                return route.fulfill(status=200, content_type=kind, body=file.read_bytes())
            return route.fulfill(status=404, body="")
        if url.path == "/api/cards":
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"cards": CARDS}))
        if url.path == "/api/cards/combinables":
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"groups": [], "summary": {}}))
        if url.path.startswith("/api/"):
            return route.fulfill(status=404, content_type="application/json", body=json.dumps({"detail": "not in this fixture"}))
        return route.fulfill(status=404, body="")
    return handle


def _cards(p, width, height, record):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": width, "height": height})
    page.route("**/*", _serve(record))
    page.goto(f"{ORIGIN}/cards?player=sample01&mode=cards")
    page.wait_for_selector('body[data-view-ready="true"] #cb-grid .cb-tile', timeout=30_000)
    return browser, page


def test_cards_desktop_menu_follows_the_view():
    record: list = []
    with playwright.sync_playwright() as p:
        browser, page = _cards(p, 1536, 864, record)
        try:
            assert page.inner_text("#hdr-place") == "My cards"
            assert _visible(page, "#cb-harvest-btn") and "Harvest" in page.inner_text("#cb-harvest-btn")
            for gone in (".cb-mode", ".ceres-page-switch", "label:has(#cb-mode-combine)"):
                assert not _visible(page, gone), gone
            for kept in ("#cb-q", "#cb-sort + .csel-wrap, label:has(#cb-sort) .csel-wrap", "#player-input", "#cb-player-go"):
                assert _visible(page, kept), kept
            boxes = page.evaluate(BOXES_JS)
            assert round(boxes["header"][3]) == 81
            assert boxes["menu"][0] < boxes["place"][0] < boxes["harvest"][0] < boxes["input"][0] < boxes["go"][0]
            for name, mode, place in (("Market", "market", "Market"), ("My purchases", "purchases", "My purchases"), ("My cards", "cards", "My cards")):
                _open_menu(page)
                _entry(page, name).click()
                page.wait_for_function(f"document.body.dataset.cbMode === '{mode}'")
                assert page.inner_text("#hdr-place") == place
                assert page.locator("#hdr-menu").is_hidden()
                _open_menu(page)
                current = page.evaluate("[...document.querySelectorAll('#hdr-menu [aria-current=\"page\"]')].map((e) => e.dataset.menuEntry)")
                assert current == [{"market": "market", "purchases": "purchases", "cards": "myCards"}[mode]]
                page.keyboard.press("Escape")
            _open_menu(page)
            _entry(page, "Find combinables").click()
            page.wait_for_function("document.body.dataset.cbMode === 'combine' && document.getElementById('cb-mode-combine').checked")
            assert page.inner_text("#hdr-place") == "My cards"
            with page.expect_navigation():
                page.click("#cb-harvest-btn")
            assert page.url == f"{ORIGIN}/?player=sample01&open=harvest"
        finally:
            browser.close()


def test_cards_phone_header_unchanged():
    record: list = []
    with playwright.sync_playwright() as p:
        browser, page = _cards(p, 390, 844, record)
        try:
            for hidden in ("#hdr-menu-btn", "#hdr-place", "#cb-harvest-btn"):
                assert not _visible(page, hidden), hidden
            for shown in ("#cb-mode-cards", "#cb-mode-market", "#cb-mode-purchases", "#page-switch-plots", "label:has(#cb-mode-combine)"):
                assert _visible(page, shown), shown
        finally:
            browser.close()
