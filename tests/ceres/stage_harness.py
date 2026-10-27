"""Browser-test helpers for the staged card-change tests (checkout, reload guard, verification, refusal)."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError


def _strip_constructions(value):
    if isinstance(value, dict):
        if isinstance(value.get("pending_change"), dict) and value["pending_change"].get("kind") == "worksite_change":
            value["pending_change"] = None
        if value.get("worksite_completion"):
            value["worksite_completion"] = None
        for item in value.values():
            _strip_constructions(item)
    elif isinstance(value, list):
        for item in value:
            _strip_constructions(item)
    return value


def hide_running_constructions(page) -> None:
    """Serve the board and plot reads without the recording's running worksite constructions (its finished
    feed and its building plot). Running constructions join the Changes model by design
    (static/checkout.js, constructionModel), so on the full recording the Changes button never hides and a
    signing run also signs the ready feed. Tests about the staged card changes alone install this before the
    page loads, so their stage counts, button visibility and banners are the card change's own."""

    def handle(route):
        try:
            response = route.fetch()
            content_type = response.headers.get("content-type", "")
            if response.status != 200 or "json" not in content_type:
                route.fulfill(response=response)
                return
            route.fulfill(response=response, body=json.dumps(_strip_constructions(response.json())), content_type="application/json")
        except PlaywrightError:
            if not route.request.frame.page.is_closed():
                raise  # a read the closing page no longer waits for is the only error to drop

    page.route(lambda url: urlsplit(url).path == "/api/board" or urlsplit(url).path.startswith("/api/plots/"), handle)
