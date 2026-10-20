"""Guards the 2026-08-30 removal of the "Idle cards" and "Unassigned bonus
assets" board sections.

The CRITICAL hazard the maintainer flagged in the same breath was gutting
`IDLE_CARDS_CACHE` along with the rendering surface -- all three combine
doors (the tile chip, the worker-table Combine button, the picker column's
Combine button) read that cache directly and would go dark if the fetch that
populates it were removed too. Only the RENDERING surface was removed; the
cache and its background fetch (`prefetchIdleCardsCache`, renamed from
`prefetchIdleCardsBadge`) are kept.

Two guards, matching the internal rules's mutation standard:

1. `test_neither_section_exists_anywhere` -- static check that index.html
 carries no trace of either section's DOM ids. Injection: re-inserted the
 literal removed `<details id="idle-cards-section">` markup into a copy of
 index.html's text and re-ran the same assertion helper -- it failed,
 confirming the check actually distinguishes "section present" from
 "section absent" rather than trivially passing on any HTML.

2. `test_combine_door_depends_on_the_cache_not_the_deleted_section` -- live
 Chromium: with idle-cards mocked to guarantee a combinable duplicate, the
 tile-chip door renders while both deleted sections are confirmed absent
 from the live DOM. Injection: forced `IDLE_CARDS_CACHE = null` in the live
 page and re-rendered the same row -- the chip disappeared, proving the
 door's markup genuinely depends on the cache (not some now-dead reference
 to the removed section) and that a gutted cache is exactly the failure
 mode this pairing of tests would catch.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Page

from .conftest import spawn_live_ceres
from .vm_context import read_entry_source

ROOT = Path(__file__).parents[2]
INDEX_HTML = ROOT / "ceres" / "static" / "index.html"
APP_JS = ROOT / "ceres" / "static" / "app.js"

DELETED_SECTION_IDS = ["idle-cards-section", "idle-cards-body", "unassigned-bonus-section", "unassigned-bonus-body"]
DELETED_JS_SYMBOLS = [
    "renderIdleCardsBody", "mountIdleCardsTable", "mountIdleCardRangeInto", "scheduleIdleCardsFill",
    "idleCardsPlaceholderHtml", "cancelIdleCardsFill", "loadIdleCardsIfNeeded", "idleCardRowEl",
    "idleCardRowCellsHtml", "unassignedBonusHtml", "unassignedItemFamilyHtml", "IDLE_UPGRADEABLE_ONLY",
]

REMOVED_IDLE_SECTION_HTML = (
    '<details class="idle-section" id="idle-cards-section">'
    '<summary>Idle cards (<span id="idle-cards-count">0</span>)'
    '<span id="idle-cards-combinable-badge" class="idle-cards-combinable-badge"></span></summary>'
    '<div id="idle-cards-body"></div>'
    '</details>'
)


def _no_deleted_ids(html: str) -> bool:
    return not any(f'id="{section_id}"' in html for section_id in DELETED_SECTION_IDS)


def test_neither_section_exists_anywhere() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert _no_deleted_ids(html), "a deleted section's DOM id is still present in index.html"

    js = read_entry_source()
    for symbol in DELETED_JS_SYMBOLS:
        assert symbol not in js, f"{symbol} should have been removed along with the rendering surface"

    # Injection: the literal removed markup, reinserted -- confirms the id
    # check above actually distinguishes present from absent.
    mutated = html.replace("</main>", REMOVED_IDLE_SECTION_HTML + "</main>")
    assert not _no_deleted_ids(mutated), "guard is void -- it passed with the deleted section reinserted"


def _install_idle_cards_mock(page: Page, worker: dict) -> None:
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


def test_combine_door_depends_on_the_cache_not_the_deleted_section() -> None:
    import urllib.parse
    import urllib.request

    with spawn_live_ceres() as (browser, base_url):
        with urllib.request.urlopen(f"{base_url}/api/board", timeout=30) as response:
            board = json.loads(response.read().decode())

        # Same fixture shape as test_tile_combine_chip.py's _two_distinct_workers
        # slot >= 1 (never the Runi/reserved slot 0) and card_detail_id/edition
        # both present -- combineTileActionHtml's own precondition.
        row = worker = None
        for candidate_row in board["plots"]:
            if not candidate_row.get("assets_loaded"):
                continue
            with urllib.request.urlopen(f"{base_url}/api/plots/{urllib.parse.quote(candidate_row['deed_uid'], safe='')}", timeout=30) as response:
                detail = json.loads(response.read().decode())
            for candidate_worker in detail.get("workers") or []:
                if candidate_worker.get("asset_ref") and isinstance(candidate_worker.get("slot"), int) and candidate_worker["slot"] >= 1 \
                        and candidate_worker.get("card_detail_id") is not None and candidate_worker.get("edition") is not None:
                    row, worker = candidate_row, candidate_worker
                    break
            if worker:
                break
        assert row and worker, "no staked worker with card_detail_id/edition found -- cannot build the fixture"

        def _open_and_get_chip_count(mock_idle_cards: bool):
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            if mock_idle_cards:
                _install_idle_cards_mock(page, worker)
            else:
                # Injection: gut the cache exactly as the maintainer warned against
                # by forcing GET /api/idle-cards to fail before it ever
                # populates -- the real fetch path already treats a failed
                # request as "stay null" (see prefetchIdleCardsCache's catch)
                # which is the same end state a botched removal of the
                # rendering surface could leave the cache in.
                page.route("**/api/idle-cards*", lambda route: route.abort())
            page.goto(base_url)
            page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
            page.wait_for_timeout(1200)  # let prefetchIdleCardsCache() settle either way

            sections = page.evaluate(
                "() => ({idle: !!document.getElementById('idle-cards-section'), unassigned: !!document.getElementById('unassigned-bonus-section')})"
            )
            assert sections == {"idle": False, "unassigned": False}

            page_row = page.locator(f"tr.plot-row[data-deed-uid='{row['deed_uid']}']")
            page_row.wait_for(timeout=15_000)
            page_row.click()
            expanded = page.locator(f"tr.expand-row[data-deed-uid='{row['deed_uid']}']")
            expanded.locator(".slot-editor").wait_for(timeout=15_000)
            page.wait_for_timeout(500)
            count = expanded.locator(".tile-combine-chip").count()
            page.close()
            return count

        assert _open_and_get_chip_count(mock_idle_cards=True) > 0, \
            "combine door did not render even though the cache holds a combinable duplicate"
        assert _open_and_get_chip_count(mock_idle_cards=False) == 0, \
            "guard is void -- the door still rendered with a failed/empty cache"
