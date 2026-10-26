""": the Cards and Market header shows the player's avatar medallion.

All page assets, avatar settings, avatar layers, and card reads are served locally.
"""
from __future__ import annotations

import json
import mimetypes
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.browser
ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(os.environ.get("IR9440_STATIC_ROOT", ROOT / "ceres" / "static"))
ORIGIN = "http://ceres.test"
PLAYER = "sample01"
PIXEL = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4b40000000049454e44ae426082")
AVATAR = {"race": "human", "gender": 1, "body": 1, "skin_color": 1, "background": 1,
          "face": 1, "hair": 1, "hair_color": 1, "eyebrows": 1, "eyes": 1, "eyes_color": 1,
          "nose": 1, "mouth": 1, "complexion": 0, "scars": 0, "tattoo": 0, "earrings": 0,
          "earrings_color": 1, "accessory": 0, "accessory_color": 1, "cloth": 1, "cloth_color": 1,
          "hat": 0, "hat_color": 1}


def _handler(html: str, card_requests: list[str], *, delay: float = 0):
    def handle(route):
        url = route.request.url
        path = urlparse(url).path
        if path == "/cards":
            return route.fulfill(status=200, content_type="text/html", body=html)
        if path.startswith("/static/"):
            file = STATIC / path.removeprefix("/static/")
            if file.is_file():
                kind = "text/javascript" if file.suffix == ".js" else (mimetypes.guess_type(file.name)[0] or "application/octet-stream")
                return route.fulfill(status=200, content_type=kind, body=file.read_bytes())
            return route.fulfill(status=404, body="")
        if "/players/player_avatar/" in url:
            return route.fulfill(status=200, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"}, body=json.dumps(AVATAR))
        if url.startswith("https://d36mxiodymuqjm.cloudfront.net/website/avatars/"):
            return route.fulfill(status=200, content_type="image/webp", body=PIXEL)
        if path == "/api/cards":
            card_requests.append(url)
            if delay:
                time.sleep(delay)
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"player": PLAYER, "cards": [], "count": 0}))
        if path == "/api/market":
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"player": PLAYER, "listings": [], "count": 0, "filters": {}}))
        if path.endswith((".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif")):
            return route.fulfill(status=200, content_type="image/png", body=PIXEL)
        return route.fulfill(status=404, body="")
    return handle


def _run(url: str) -> dict:
    html = (STATIC / "cards.html").read_text(encoding="utf-8")
    html = html.replace("{{CERES_VERSION}}", "t").replace("{{CERES_RELEASED_VERSION}}", "1.0.0").replace("{{CERES_LOCAL_MARKER}}", "")
    requests: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.add_init_script("""(() => { window.__avatarStates = []; new MutationObserver(() => { const e = document.querySelector('#player-avatar'); if (e) window.__avatarStates.push(e.dataset.refreshState || ''); }).observe(document, {subtree: true, attributes: true, attributeFilter: ['data-refresh-state']}); })()""")
            page.route("**/*", _handler(html, requests, delay=0.45))
            page.goto(f"{ORIGIN}{url}")
            assert page.locator("#player-avatar").count() == 1, "the cards page includes its avatar medallion"
            page.wait_for_function("!document.querySelector('#player-avatar')?.hidden && document.querySelectorAll('#player-avatar img').length > 0", timeout=15000) if "player=" in url else page.wait_for_function("document.querySelector('#player-avatar')?.hidden === true")
            if "player=" not in url:
                return {"hidden": page.locator("#player-avatar").evaluate("e => e.hidden"), "requests": requests}
            initial_cards = len(requests)
            page.locator("#player-avatar").click()
            page.wait_for_function("document.querySelector('#player-avatar').dataset.refreshState === 'success'")
            states = page.evaluate("window.__avatarStates")
            return {"hidden": page.locator("#player-avatar").evaluate("e => e.hidden"),
                    "images": page.locator("#player-avatar img").count(), "initial_cards": initial_cards, "requests": requests, "states": states}
        finally:
            browser.close()


@pytest.mark.parametrize("mode", ["cards", "market"])
def test_player_avatar_is_drawn_and_refreshes_cards_in_both_modes(mode):
    got = _run(f"/cards?player={PLAYER}&mode={mode}")
    assert got["hidden"] is False
    assert got["images"] >= 1
    assert "loading" in got["states"] and "success" in got["states"], got["states"]
    assert len(got["requests"]) > got["initial_cards"], got["requests"]
    assert any(urlparse(url).path == "/api/cards" for url in got["requests"][got["initial_cards"]:])


def test_avatar_is_hidden_without_player():
    got = _run("/cards")
    assert got["hidden"] is True
    assert got["requests"] == []
