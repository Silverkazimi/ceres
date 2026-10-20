"""Live Chromium coverage for the door-2 tile combine chip's approved-mockup shape
(2026-08-29 maintainer correction to the v0.7.65 in-flow block button, and again to the
"Combine (N)" flat-label fix that superseded it; 2026-08-30 the `⧉` glyph was
dropped entirely -- it rendered as tofu in the maintainer's font stack): a compact
`.tile-combine-chip` positioned above the tile, `Combine N`, never the occupant's
own name.

`test_tile_combine_chip_click_opens_the_combine_dialog` guards the click itself. Root
cause: `.slot-grid` sets `pointer-events: none` on itself and re-enables it only
on `.slot-tile` -- the chip is a `.slot-tile-cell` SIBLING of the tile, not the
tile, so without its own explicit `auto` it inherited `none` and every click fell
through to whatever sat beneath it. Injection: force `pointer-events: none` back
onto the chip via inline style and confirm the dialog no longer opens.

Uses `spawn_live_ceres` (isolated server) and mocks `GET /api/idle-cards` so the
chip is deterministically reachable against a REAL occupied tile on the real
board snapshot, rather than hunting for one of the rare plots where a live idle
duplicate already exists.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import Page

from .conftest import settle, spawn_live_ceres, wait_ready

SCREEN_DIR = Path(__file__).parents[2] / "work" / "combine-reachability"


def _get(base_url: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=30) as response:
        return json.loads(response.read().decode())


def _wait_for_board(page: Page) -> None:
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)


def _two_distinct_workers(base_url: str, board: dict) -> tuple[dict, dict, dict]:
    """A worker to make combinable (`row`, `worker`) plus a SECOND, DIFFERENT worker
    from elsewhere on the board (`other`), kept for callers that need two distinct
    staked identities."""
    found: list[tuple[dict, dict]] = []
    for row in board["plots"]:
        if not row.get("assets_loaded"):
            continue
        detail = _get(base_url, f"/api/plots/{urllib.parse.quote(row['deed_uid'], safe='')}")
        for worker in detail.get("workers") or []:
            if worker.get("asset_ref") and isinstance(worker.get("slot"), int) and worker["slot"] >= 1 \
                    and worker.get("card_detail_id") is not None and worker.get("edition") is not None:
                found.append((row, worker))
                if len(found) >= 2:
                    return found[0][0], found[0][1], found[1][1]
    raise AssertionError("fewer than 2 staked workers with card_detail_id/edition were found -- cannot build the collision fixture")


def _install_idle_cards_mock(page: Page, worker: dict) -> None:
    """Forces `worker`'s printing to read as combinable: two idle copies of its own
    (card_detail_id, edition, foil), well under a synthetic bcx_max so neither reads
    maxed (isCardMaxed/rowCanCombine, app.js)."""
    copies = [
        {
            "uid": f"MOCK-IDLE-{i}", "card_detail_id": worker["card_detail_id"], "edition": worker["edition"],
            "foil": worker.get("foil", False), "bcx": 1, "bcx_max": 10, "available": True, "name": worker.get("name", "Mock Card"),
        }
        for i in range(2)
    ]
    page.route(
        "**/api/idle-cards*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"cards": copies})),
    )


def test_tile_combine_chip_is_compact_above_the_tile_not_an_inflow_block() -> None:
    """The chip: `.tile-combine-chip` class, positioned OUTSIDE `.slot-tile` (a
    `.slot-tile-cell` sibling, per ceres.css's own overflow:hidden reasoning), label
    text exactly `Combine 2` (2 idle copies, no parentheses, no glyph), and does not repeat
    the occupant's own name."""
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    with spawn_live_ceres() as (browser, base_url):
        board = _get(base_url, "/api/board")
        row, worker, _other = _two_distinct_workers(base_url, board)

        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, worker)
            page.goto(base_url)
            _wait_for_board(page)
            settle(page)  # prefetchIdleCardsCache() has answered

            page_row = page.locator(f"tr.plot-row[data-deed-uid='{row['deed_uid']}']")
            page_row.wait_for(timeout=15_000)
            page_row.click()
            expanded = page.locator(f"tr.expand-row[data-deed-uid='{row['deed_uid']}']")
            expanded.locator(".slot-editor").wait_for(timeout=15_000)

            chip = expanded.locator(
                f".slot-group[data-group-id='worker'] .slot-tile-cell:has(.slot-tile[data-slot-index='{worker['slot'] - 1}']) .tile-combine-chip"
            )
            chip.first.wait_for(timeout=15_000)
            label = chip.first.inner_text()
            assert label == "Combine 2", label
            assert worker.get("name", "") not in label

            # It must NOT be nested inside the tile it belongs to (the old
            # `.worker-combine-action` in-flow shape) -- it is a `.slot-tile-cell`
            # sibling of the tile, same wrapper `.slot-plan-flag` uses.
            is_inside_tile = chip.first.evaluate(
                "el => !!el.closest('.slot-tile') "
            )
            assert not is_inside_tile, "chip must not be nested inside .slot-tile (the rejected in-flow shape)"

            page.screenshot(path=str(SCREEN_DIR / "tile-chip-mockup-shape.png"))
        finally:
            page.close()


def test_tile_combine_chip_click_opens_the_combine_dialog() -> None:
    """Guards the CLICK, not the markup (2026-08-30 maintainer report: the chip rendered
    but clicking it did nothing). Spies on the real outcome -- `#combine-dialog-overlay`
    losing its `hidden` attribute -- rather than the delegated handler's source.
    Injection: force `pointer-events: none` back onto the live chip (the exact
    defect this guard forbids -- `.slot-grid` disables pointer events on itself and
    only `.slot-tile` opts back in, so the chip, a `.slot-tile-cell` sibling of the
    tile, needs its own `auto`) and confirm the dialog stays closed."""
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    with spawn_live_ceres() as (browser, base_url):
        board = _get(base_url, "/api/board")
        row, worker, _other = _two_distinct_workers(base_url, board)

        page = browser.new_page(viewport={"width": 1400, "height": 900})
        try:
            _install_idle_cards_mock(page, worker)
            page.goto(base_url)
            _wait_for_board(page)
            wait_ready(page)
            settle(page)

            page_row = page.locator(f"tr.plot-row[data-deed-uid='{row['deed_uid']}']")
            page_row.wait_for(timeout=15_000)
            page_row.click()
            expanded = page.locator(f"tr.expand-row[data-deed-uid='{row['deed_uid']}']")
            expanded.locator(".slot-editor").wait_for(timeout=15_000)

            chip = expanded.locator(
                f".slot-group[data-group-id='worker'] .slot-tile-cell:has(.slot-tile[data-slot-index='{worker['slot'] - 1}']) .tile-combine-chip"
            )
            chip.first.wait_for(timeout=15_000)

            overlay_hidden = "document.getElementById('combine-dialog-overlay')?.hidden ?? true"
            assert page.evaluate(overlay_hidden) is True

            chip.first.click()
            settle(page)
            assert page.evaluate(overlay_hidden) is False, "click did not open the combine dialog"
            page.evaluate("document.getElementById('combine-dialog-overlay').hidden = true; window.closeCombineDialog?.();")

            # Injection: reproduce the reported defect by forcing the inherited
            # pointer-events:none back onto the chip and confirm the click no
            # longer reaches the delegated handler.
            chip.first.evaluate("el => el.style.setProperty('pointer-events', 'none', 'important')")
            box = chip.first.evaluate("el => { const r = el.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; }")
            page.mouse.click(box["x"], box["y"])
            settle(page)
            assert page.evaluate(overlay_hidden) is True, "guard is void -- dialog opened even with pointer-events:none forced onto the chip"

            page.screenshot(path=str(SCREEN_DIR / "tile-chip-click-opens-dialog.png"))
        finally:
            page.close()
