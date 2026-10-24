""": stored Cards paint first, then one capture updates stake state in place."""

from __future__ import annotations

import json
import time
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright

from tests.ceres.test_cards_no_jump_browser import CARDS, ORIGIN, PIXEL, STATIC, _page_html
from .conftest import settle

pytestmark = pytest.mark.browser


def test_cards_refresh_once_and_preserves_filter_and_scroll():
    fresh = json.loads(json.dumps(CARDS))
    fresh["cards"][0].update(status="staked", plot={"label": "065-09-059", "territory": "Pristine Northwest"})
    requests = []
    card_reads = 0

    def serve(route):
        nonlocal card_reads
        path = urlparse(route.request.url).path
        requests.append(path)
        if path == "/cards":
            return route.fulfill(status=200, content_type="text/html", body=_page_html())
        if path.startswith("/static/"):
            file = STATIC / path[len("/static/"):]
            if file.is_file():
                return route.fulfill(status=200, content_type="text/javascript" if file.suffix == ".js" else "text/css", body=file.read_bytes())
            return route.fulfill(status=404)
        if path == "/api/cards":
            card_reads += 1
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(CARDS if card_reads == 1 else fresh))
        if path == "/api/capture/refresh":
            return route.fulfill(status=200, content_type="application/json", body='{"state":"running"}')
        if path == "/api/capture/status":
            time.sleep(0.5)
            return route.fulfill(status=200, content_type="application/json", body='{"state":"complete"}')
        if path.endswith((".png", ".jpg", ".jpeg", ".svg", ".webp")):
            return route.fulfill(status=200, content_type="image/png", body=PIXEL)
        return route.fulfill(status=404)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1536, "height": 864})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/*", serve)
            page.goto(f"{ORIGIN}/cards?player=sample01")
            page.wait_for_selector('body[data-view-ready="true"] #cb-grid > .cb-tile')
            page.locator("#cb-q").fill("Goblin")
            # cards states it is drawn with `body[data-view-ready]` (card-browser.js); `window.__ceresReady`
            # is published by the board page only, so `wait_ready` never resolves here
            page.wait_for_selector('body[data-view-ready="true"] #cb-grid > .cb-tile')
            settle(page)
            page.evaluate("window.scrollTo(0, 350)")
            before = page.evaluate("window.scrollY")
            page.wait_for_function("document.querySelector('#cb-grid .cb-onland') !== null")
            assert page.evaluate("window.scrollY") == before
            assert page.locator("#cb-q").input_value() == "Goblin"
            assert requests.count("/api/capture/refresh") == 1
            assert card_reads == 2
            assert not errors
        finally:
            browser.close()
