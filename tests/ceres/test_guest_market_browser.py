""": guest Cards visits start in Market unless an explicit mode is requested.

The browser is hermetic: page/static assets are routed locally and market rows
come from the recorded  fixture, so no game network is contacted.
"""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(os.environ.get("IR9432_STATIC_ROOT", ROOT / "ceres" / "static"))
ORIGIN = "http://ceres.test"
PLAYER = "sample01"
MARKET_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ir9182-market" / "market-2026-10-14.json").read_text(encoding="utf-8"))


def _market_response() -> dict:
    """Build the page's API shape from the checked-in recorded upstream fixture."""
    from ceres.card_market import MarketCache, read_market

    details = {int(k): v for k, v in MARKET_FIXTURE["card_details"].items()}

    def get(url: str):
        parsed = urlparse(url)
        if parsed.path == "/market/for_sale_grouped":
            return MARKET_FIXTURE["for_sale_grouped"]
        if parsed.path == "/settings":
            return MARKET_FIXTURE["settings"]
        if parsed.path == "/market/market_query_grouped":
            from urllib.parse import parse_qs
            query = parse_qs(parsed.query)
            ids = {int(i) for i in query["card_ids"][0].split(",")}
            return [row for row in MARKET_FIXTURE["market_query_grouped"].get(query["foil"][0], [])
                    if row["card_detail_id"] in ids]
        raise AssertionError(f"unexpected fixture upstream path: {parsed.path}")

    return read_market(details, {}, sort="cost_per_pp", player=PLAYER, getter=get, cache=MarketCache())


MARKET = _market_response()
PIXEL = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4b4"
                      "0000000049454e44ae426082")


def _handler():
    html = (STATIC / "cards.html").read_text(encoding="utf-8")
    html = html.replace("{{CERES_VERSION}}", "t").replace("{{CERES_RELEASED_VERSION}}", "1.0.0").replace("{{CERES_LOCAL_MARKER}}", "")

    def handle(route):
        path = urlparse(route.request.url).path
        if path == "/cards":
            return route.fulfill(status=200, content_type="text/html", body=html)
        if path == "/api/market":
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(MARKET))
        if path == "/api/cards":
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"player": PLAYER, "cards": [], "count": 0}))
        if path.startswith("/static/"):
            file = STATIC / path.removeprefix("/static/")
            if file.is_file():
                kind = "text/javascript" if file.suffix == ".js" else (mimetypes.guess_type(file.name)[0] or "application/octet-stream")
                return route.fulfill(status=200, content_type=kind, body=file.read_bytes())
        if path.endswith((".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif")):
            return route.fulfill(status=200, content_type="image/png", body=PIXEL)
        return route.fulfill(status=404, body="")

    return handle


def _run(url: str, storage: dict[str, str] | None = None) -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            if storage:
                context.add_init_script("localStorage.clear(); " + " ".join(
                    f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)});" for k, v in storage.items()))
            page = context.new_page()
            page.route("**/*", _handler())
            page.goto(f"{ORIGIN}{url}")
            page.wait_for_function("document.body.dataset.cbMode === 'market' ? document.querySelectorAll('#cm-grid > .cb-tile:not(.cm-skeleton)').length > 0 : document.body.dataset.cbMode === 'cards'", timeout=30000)
            return page.evaluate("""() => ({ mode: document.body.dataset.cbMode,
              start: document.documentElement.getAttribute('data-cb-start'),
              tiles: document.querySelectorAll('#cm-grid > .cb-tile:not(.cm-skeleton)').length,
              body: document.body.innerText })""")
        finally:
            browser.close()


def test_guest_without_storage_starts_in_market_with_listings():
    got = _run("/cards")
    assert got["mode"] == got["start"] == "market"
    assert got["tiles"] > 0


def test_guest_ignores_remembered_cards_mode():
    got = _run("/cards", {"ceres:cards:mode": "cards"})
    assert got["mode"] == got["start"] == "market"
    assert got["tiles"] > 0


def test_player_with_remembered_cards_mode_starts_in_cards():
    got = _run(f"/cards?player={PLAYER}", {"ceres:lastPlayer": PLAYER, "ceres:cards:mode": "cards"})
    assert got["mode"] == got["start"] == "cards"


def test_explicit_cards_mode_without_player_shows_player_prompt():
    got = _run("/cards?mode=cards")
    assert got["mode"] == got["start"] == "cards"
    assert "Type a player name" in got["body"]
