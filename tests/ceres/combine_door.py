"""Opening the board's combine dialog in browser tests.

The worker tiles carry no Combine control. The board opens the dialog for one printing from the
address, `/?combine=<card_detail_id>-<edition>-<foil>` (`openCombineFromAddress`, static/combine-dialog.js),
which is where Find combinables on the Cards page hands off: once the board is revealed the page reads
that printing's idle copies and opens the same dialog -- same default selection, preview, build, signing
and verification -- as every other opener. In the page itself, the worker picker's candidate rows carry
the Combine (door 3, `candidateCombineActionHtml`, static/candidates.js).

The page reads the idle-card pool only when `GET /api/board` reports `idle_cards_loaded: true`
(`prefetchIdleCardsCache`, static/combine.js). The server reports that only once every plot's assets were
read in the same snapshot as the collection, which the recorded fixture account never reaches at page
load (plots load their workers on demand, and a page load's quick capture drops earlier per-plot reads).
`serve_idle_cards` stands in for that state on the page alone: the board keeps every other field the
server returned, and the pool is the test's own copies. `recorded_idle_cards` reads the recorded
account's real pool instead (with the `browser-combine` overlay's partner copies).
"""

from __future__ import annotations

import copy
import functools
import json
import urllib.parse
import urllib.request
from urllib.parse import urlsplit

from playwright.sync_api import Page

from .conftest import LIVE_PLAYER, prime_all_fixture_plots, settle, wait_for_board


def print_id(card: dict) -> str:
    """The address form of a card's printing: `<card_detail_id>-<edition>-<foil>`, foil as the game's
    0-4 value (a `true`/`gold` flag is the gold foil, 1)."""
    foil = card.get("foil", card.get("gold"))
    if foil in (True, "true", "gold"):
        foil = 1
    elif foil in (None, False, "false", "regular"):
        foil = 0
    return f"{int(card['card_detail_id'])}-{int(card['edition'])}-{int(foil)}"


def report_idle_pool_loaded(page: Page) -> None:
    """Serve the real `GET /api/board`, with `idle_cards_loaded: true`."""

    def handle(route):
        response = route.fetch()
        if response.status != 200:
            route.fulfill(response=response)
            return
        board = response.json()
        board["idle_cards_loaded"] = True
        route.fulfill(response=response, body=json.dumps(board), content_type="application/json")

    page.route(lambda url: urlsplit(url).path == "/api/board", handle)


def _serve_pool(page: Page, cards: list[dict]) -> None:
    report_idle_pool_loaded(page)
    body = json.dumps({"loaded": True, "cards": cards})
    page.route("**/api/idle-cards*", lambda route: route.fulfill(status=200, content_type="application/json", body=body))


def serve_idle_cards(page: Page, cards: list[dict]) -> None:
    """Serve the test's own `cards` as the idle-card pool, and a board that says the pool is loaded.
    The server has never seen these copies, so the printing's Find combinables answer is served
    empty: the dialog opens on these copies with its own default selection."""
    _serve_pool(page, cards)
    page.route(
        "**/api/cards/combinables*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"groups": []})),
    )


@functools.cache
def _recorded_pool(base_url: str) -> tuple[dict, ...]:
    prime_all_fixture_plots(base_url)
    with urllib.request.urlopen(f"{base_url}/api/idle-cards", timeout=30) as response:
        pool = json.load(response)
    assert pool.get("loaded") is True, "the idle-card pool did not load after every recorded plot was read"
    return tuple(pool["cards"])


def recorded_idle_cards(base_url: str) -> list[dict]:
    """The recorded account's real idle-card pool, read after every recorded plot was read (once per
    server: the recording does not change, and reading every plot takes about a second each)."""
    return copy.deepcopy(list(_recorded_pool(base_url)))


def serve_recorded_idle_pool(page: Page, base_url: str) -> list[dict]:
    """Serve the recorded account's real idle-card pool (read once, before the page loads: the page
    load's own quick capture drops the per-plot reads the pool needs). Find combinables stays real."""
    cards = recorded_idle_cards(base_url)
    _serve_pool(page, cards)
    return cards


def recorded_combine_print() -> dict:
    """The recorded COMBINE plot's staked gold worker, whose printing has the `browser-combine`
    overlay's idle partner copies (tests/ceres/board_basic_recording.py, COMBINE_PARTNERS)."""
    from . import board_basic_recording as recording

    worker = recording.COMBINE_WORKER
    return {"card_detail_id": worker.cdid, "edition": worker.edition, "foil": 1 if worker.gold else 0}


def open_combine_by_print(page: Page, base_url: str, card: dict, *, timeout: float = 30_000) -> None:
    """Load the board at `/?combine=<print>` for `card`'s printing and wait until the dialog is open."""
    page.goto(f"{base_url}/?player={LIVE_PLAYER}&combine={print_id(card)}")
    wait_for_board(page)
    page.wait_for_function(
        "document.querySelector('#combine-dialog-overlay')?.hidden === false", timeout=timeout,
    )


# The worker picker's Combine (door 3): a candidate row offers Combine when the idle-card pool holds
# a combinable copy of that candidate.

def open_slot_candidate_template(base_url: str) -> tuple[str, dict]:
    """A plot with an open worker slot, and one real candidate row payload for it -- the exact shape
    `renderCandidateRow` expects, for a test to adapt rather than hand-build."""
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=30) as response:
        board = json.load(response)
    params = urllib.parse.urlencode({"group_id": "worker", "limit": 5, "offset": 0, "sort": "construction_pp", "bucket": "all", "preset": "net_gain_default"})
    for plot in board["plots"]:
        if (plot.get("capacity") or {}).get("open_slots", 0) > 0:
            deed_uid = plot["deed_uid"]
            with urllib.request.urlopen(f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates?{params}", timeout=30) as response:
                payload = json.load(response)
            if payload.get("candidates"):
                return deed_uid, copy.deepcopy(payload["candidates"][0])
    raise AssertionError("no plot with an open worker slot returned a candidate to build a fixture from")


def serve_candidates(page: Page, deed_uid: str, candidates: list[dict]) -> None:
    """Serve `candidates` as the plot's worker candidate list."""
    body = json.dumps({"candidates": candidates, "total": len(candidates), "source": "test-fixture", "excluded_counts": {}, "notices": []})
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=body),
    )


def open_worker_picker(page: Page, base_url: str, deed_uid: str):
    """Load the board, wait until the page has asked for the idle-card pool, open the plot's first
    empty worker slot, and return the first candidate row once the picker has rendered it and every
    request in flight (the pool's among them) has answered."""
    with page.expect_request("**/api/idle-cards*", timeout=30_000):
        page.goto(f"{base_url}/?player={LIVE_PLAYER}")
        wait_for_board(page)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    # not the loading frame's ghost tiles (aria-hidden), which open nothing
    empty = page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}'] + tr.expand-row .slot-tile.is-empty:not([aria-hidden='true'])").first
    empty.wait_for(timeout=30_000)
    with page.expect_response(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*", timeout=30_000):
        empty.click()
    row = page.locator(".candidate-row[data-candidate-index='0']")
    row.wait_for(timeout=30_000)
    settle(page)
    return row
