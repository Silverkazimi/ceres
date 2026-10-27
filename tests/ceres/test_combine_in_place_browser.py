""" /  in a real Chromium, hermetic: the page's files are served from ceres/static through page.route and
every /api read is a fixture (no server, no network, nothing signed -- the test never presses the sign button).

Checks: Find combinables is the filter row's pill; Combine opens the combine dialog over /cards without navigation and
without the board (board.js and the dialog are fetched only on the press, wallet.js first; no /api/board or
/api/idle-cards read); the proposed set is preselected and is what the dialog's own preview reads; closing leaves Combine
available. Run under the shared browser lock:
flock -w 900 /tmp/er-browser.lock nice -n 15 python3 -m pytest -q tests/ceres/test_combine_in_place_browser.py"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
ORIGIN = "http://ceres.test"

PRINT = {"id": "1-7-0", "card_detail_id": 1, "name": "Goblin Shaman", "edition": 7, "card_set": "chaos", "rarity": "rare", "foil": 0,
         "gold": False, "splinters": ["Fire"], "max_level": 8, "bcx_max": 22, "max_level_pp": 1000.0, "thresholds": [1, 3, 5, 8, 12, 17, 22, 22]}
UIDS = ["C7-1-AAAAAAAAA1", "C7-1-AAAAAAAAA2", "C7-1-AAAAAAAAA3"]
COPIES = [{"uid": UIDS[0], "bcx": 3, "level": 2, "land_base_pp": 200.0, "status": "idle", "plot": None, "maxed": False, "combinable": True, "warnings": [], "reasons": []},
          {"uid": UIDS[1], "bcx": 1, "level": 1, "land_base_pp": 91.0, "status": "idle", "plot": None, "maxed": False, "combinable": True, "warnings": [], "reasons": []},
          {"uid": UIDS[2], "bcx": 1, "level": 1, "land_base_pp": 91.0, "status": "idle", "plot": None, "maxed": False, "combinable": True, "warnings": [], "reasons": []}]
SET = {"uids": UIDS[:2], "survivor_uid": UIDS[0], "count": 2, "destroyed_count": 1, "warnings": [],
       "before": {"bcx": 3, "level": 2, "land_base_pp": 200}, "after": {"bcx": 4, "level": 2, "land_base_pp": 291, "maxed": False}}
GROUP = {"print": PRINT, "copies": COPIES, "owned_count": 3, "combinable_count": 3, "sets": [SET],
         "base": {"source": "copy", "bcx": 3, "level": 2, "maxed": False}, "upgrade_hint": {"possible": False, "reason": "none"}}
CARDS = [{"uid": c["uid"], "card_detail_id": 1, "edition": 7, "foil": 0, "name": "Goblin Shaman", "rarity": "rare", "card_set": "chaos",
          "splinters": ["Fire"], "bcx": c["bcx"], "level": c["level"], "max_level": 8, "bcx_max": 22, "land_base_pp": c["land_base_pp"],
          "max_level_pp": 1000, "status": "idle", "maxed": False} for c in COPIES]


def _page_html() -> str:
    html = (STATIC / "cards.html").read_text(encoding="utf-8")
    return html.replace("{{CERES_VERSION}}", "t").replace("{{CERES_RELEASED_VERSION}}", "1.0.0").replace("{{CERES_LOCAL_MARKER}}", "")


def _serve(record):
    page_html = _page_html()

    def handle(route):
        url = urlparse(route.request.url)
        record.append((route.request.method, url.path + (f"?{url.query}" if url.query else ""), route.request.post_data))
        path = url.path
        if path == "/cards":
            return route.fulfill(status=200, content_type="text/html", body=page_html)
        if path.startswith("/static/"):
            file = STATIC / path[len("/static/"):]
            if file.is_file():
                kind = "text/javascript" if file.suffix == ".js" else (mimetypes.guess_type(file.name)[0] or "application/octet-stream")
                return route.fulfill(status=200, content_type=kind, body=file.read_bytes())
            return route.fulfill(status=404, body="")
        if path == "/api/cards":
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"cards": CARDS}))
        if path == "/api/cards/combinables":
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"groups": [GROUP], "summary": {}}))
        if path == "/api/combine/preview":
            uids = json.loads(route.request.post_data or "{}").get("uids", [])
            body = {"account": "sample01", "buildable": True, "uids": uids, "predicted_survivor_uid": UIDS[0], "resulting_bcx": 4,
                    "hazard": {"destroyed_uids": [u for u in uids if u != UIDS[0]]}, "observed_at": "2026-10-15T00:00:00Z"}
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        if path.startswith("/api/"):
            return route.fulfill(status=404, content_type="application/json", body=json.dumps({"detail": "not in this fixture"}))
        return route.fulfill(status=404, body="")
    return handle


def _run(width: int, height: int) -> dict:
    record: list = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("**/*", _serve(record))
            page.goto(f"{ORIGIN}/cards?player=sample01")
            page.wait_for_selector('body[data-view-ready="true"] #cb-grid .cb-tile', timeout=30000)
            header_modes = page.eval_on_selector_all(".cb-mode .ceres-btn", "(els) => els.map((b) => ({name:b.getAttribute('aria-label'), icon:!!b.querySelector('svg')}))")
            if width > 1279:  # design 4: on desktop Find combinables is a Menu entry; the phone keeps the filter-row pill
                assert not page.locator("label:has(#cb-mode-combine)").is_visible()
                page.click("#hdr-menu-btn")
                page.locator("#hdr-menu [data-menu-entry='combine']").click()
            else:
                page.click("label:has(#cb-mode-combine)")
            page.wait_for_selector("#cx-list .cx-entry", timeout=30000)
            mode = page.evaluate("() => ({ mode: document.body.dataset.cbMode, pill: document.getElementById('cb-mode-combine').checked,"
                                 " cards: document.getElementById('cb-mode-cards').getAttribute('aria-pressed') })")
            before = [r[1] for r in record]
            url_before = page.url
            mark = len(record)
            page.click('#cx-list [data-cx-action="combine"]')
            page.wait_for_selector("#combine-dialog-overlay:not([hidden])", timeout=30000)
            page.wait_for_function("!!document.querySelector('.combine-sign') && !document.querySelector('.combine-sign').disabled", timeout=30000)
            after = record[mark:]
            dialog = page.evaluate("""() => {
              const o = document.getElementById('combine-dialog-overlay');
              const box = o.querySelector('.combine-dialog-panel').getBoundingClientRect();
              return { title: document.getElementById('combine-dialog-title').textContent,
                sign: document.querySelector('.combine-sign').textContent,
                on: [...o.querySelectorAll('.combine-preset.is-on')].map((b) => b.textContent),
                text: o.innerText, z: getComputedStyle(o).zIndex,
                onTop: document.elementFromPoint(box.left + box.width / 2, box.top + 20)?.closest('#combine-dialog-overlay') === o,
                table: !!document.getElementById('table') };
            }""")
            dialog["layout"] = page.evaluate("""() => {
              const o = document.getElementById('combine-dialog-overlay'), vw = document.documentElement.clientWidth, vh = window.innerHeight;
              const box = (e) => e.getBoundingClientRect();
              const left = box(o.querySelector('.combine-editor-left')), info = box(o.querySelector('.combine-editor-info'));
              const sign = box(o.querySelector('.combine-sign')), cancel = box(o.querySelector('[data-combine-action="close-dialog"].picker-chip'));
              const panel = box(o.querySelector('.combine-dialog-panel'));
              return { vw, oneColumn: info.top >= left.bottom - 1 && Math.abs(info.left - left.left) < 2,
                overflow: [...o.querySelectorAll('*')].filter((e) => box(e).width && box(e).right > vw + 0.5).map((e) => e.className),
                small: [...o.querySelectorAll('button')].filter((e) => box(e).width && (box(e).height < 43.5 || box(e).width < 43.5)).map((e) => e.className),
                footerVisible: sign.bottom <= vh && sign.top >= 0 && cancel.bottom <= vh && cancel.top >= 0,
                panelInside: panel.left >= 0 && panel.right <= vw && panel.bottom <= vh && panel.top >= 0 };
            }""")
            page.keyboard.press("Escape")
            page.wait_for_function("document.getElementById('combine-dialog-overlay').hidden === true")
            after_close = page.evaluate("document.querySelector('#cx-list [data-cx-action=\"combine\"]')?.textContent || null")
            if width > 1279:  # design 4: on desktop the pill is hidden; the way back is the Menu's My cards
                page.click("#hdr-menu-btn")
                page.locator("#hdr-menu [data-menu-entry='myCards']").click()
                page.wait_for_function("document.body.dataset.cbMode === 'cards'", timeout=30000)
            else:
                page.uncheck("#cb-mode-combine", force=True)
            back = page.evaluate("() => ({ mode: document.body.dataset.cbMode, cards: document.getElementById('cb-mode-cards').getAttribute('aria-pressed') })")
            return {"header": header_modes, "mode": mode, "before": before, "after": after, "url_same": page.url == url_before,
                    "dialog": dialog, "after_close": after_close, "back": back, "errors": errors}
        finally:
            browser.close()


@pytest.mark.parametrize("width,height", [(1536, 864), (390, 844)])
def test_combine_opens_in_place_on_cards(width, height):
    got = _run(width, height)
    assert got["errors"] == []
    # moved Purchases into the header; made all three buttons icon-only.
    assert got["header"] == [{"name": "My cards", "icon": True}, {"name": "Market", "icon": True},
                             {"name": "My purchases", "icon": True}]
    assert got["mode"] == {"mode": "combine", "pill": True, "cards": "true"}               # the pill, a view of My cards
    paths = [p.split("?")[0] for p in got["before"]]
    for module in ("/static/board.js", "/static/combine-dialog.js", "/static/combine.js", "/static/wallet.js", "/static/app.js"):
        assert module not in paths, module                                                 # first paint: no board graph
    loaded = [p.split("?")[0] for _, p, _ in got["after"]]
    assert "/static/wallet.js" in loaded and "/static/combine-dialog.js" in loaded and "/static/board.js" in loaded
    assert loaded.index("/static/wallet.js") < loaded.index("/static/combine-dialog.js")   # wallet.js first
    assert "/static/app.js" not in loaded                                                  # the board's entry never runs
    api = [(m, p.split("?")[0], body) for m, p, body in got["after"] if p.startswith("/api/")]
    assert [a[1] for a in api] == ["/api/combine/preview"]                                 # no /api/board, no /api/idle-cards
    assert json.loads(api[0][2])["uids"] == UIDS[:2]                                       # the proposed set
    assert got["url_same"] is True                                                         # no navigation
    d = got["dialog"]
    assert d["title"] == "Combine — Goblin Shaman" and d["on"] == ["Proposed set"] and d["table"] is False
    assert d["sign"] == "Combine — destroys 1" and "Unknown safety notice" not in d["text"]  # the dialog's texts off the board
    assert d["z"] == "1200" and d["onTop"] is True
    assert "C7-1-" not in d["text"]                                                        # no Card ID in the rows
    assert got["after_close"] == "Combine"
    assert got["back"] == {"mode": "cards", "cards": "true"}
    lay = d["layout"]
    assert lay["overflow"] == [] and lay["footerVisible"] is True and lay["panelInside"] is True   # nothing clipped
    if width <= 600:
        assert lay["oneColumn"] is True and lay["small"] == []                             # phones: one column, 44px targets
    else:
        assert lay["oneColumn"] is True                                                    # pilot: result follows compact identity
