"""The board's idle-card pool for browser tests of the board's Combine doors.

The board's Combine doors (the tile chip, the worker-table button, the picker's Combine) read the idle-card
pool, which the page fetches only when `GET /api/board` reports `idle_cards_loaded: true`
(`prefetchIdleCardsCache`, static/combine.js). The server reports that only when every plot's assets were
read in the same snapshot as the card collection (`build_idle_cards`, ceres/board.py). Since plots load
their workers on demand, a page load's quick capture leaves most plots unread and drops earlier per-plot
reads, so on the recorded fixture account the flag is never true at page load. A real account reaches it
only once every plot has been read.

These helpers stand in for that state on the page alone: the board keeps every other field the server
returned, and the pool is either the test's own copies or the account's real pool, read from the server
after every recorded plot was read.
"""

from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlsplit

from .conftest import prime_all_fixture_plots


def _is_board(url: str) -> bool:
    return urlsplit(url).path == "/api/board"


def report_idle_pool_loaded(page) -> None:
    """Serve the real `GET /api/board`, with `idle_cards_loaded: true`."""

    def handle(route):
        response = route.fetch()
        if response.status != 200:
            route.fulfill(response=response)
            return
        board = response.json()
        board["idle_cards_loaded"] = True
        route.fulfill(response=response, body=json.dumps(board), content_type="application/json")

    page.route(_is_board, handle)


def serve_idle_cards(page, cards: list[dict]) -> None:
    """Serve `cards` as the idle-card pool, and a board that says the pool is loaded."""
    report_idle_pool_loaded(page)
    body = json.dumps({"loaded": True, "cards": cards})
    page.route("**/api/idle-cards*", lambda route: route.fulfill(status=200, content_type="application/json", body=body))


def recorded_idle_cards(base_url: str) -> list[dict]:
    """The recorded account's real idle-card pool (with the `browser-combine` partner copies), read after
    every recorded plot was read."""
    prime_all_fixture_plots(base_url)
    with urllib.request.urlopen(f"{base_url}/api/idle-cards", timeout=30) as response:
        pool = json.load(response)
    assert pool["loaded"] is True, "the idle-card pool did not load after every recorded plot was read"
    return pool["cards"]


def serve_recorded_idle_pool(page, base_url: str) -> list[dict]:
    """Serve the recorded account's real idle-card pool to `page` (see `recorded_idle_cards`)."""
    cards = recorded_idle_cards(base_url)
    serve_idle_cards(page, cards)
    return cards
