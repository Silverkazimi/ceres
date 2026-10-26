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
   worker picker's Combine door renders while both deleted sections are confirmed
   absent from the live DOM. Injection: a failed idle-cards read leaves
   `IDLE_CARDS_CACHE` null and the same candidate row shows no Combine, proving the
   door's markup genuinely depends on the cache (not some now-dead reference
   to the removed section) and that a gutted cache is exactly the failure
   mode this pairing of tests would catch.
"""

from __future__ import annotations

from pathlib import Path

from .combine_door import open_slot_candidate_template, open_worker_picker, report_idle_pool_loaded, serve_candidates, serve_idle_cards
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


def test_combine_door_depends_on_the_cache_not_the_deleted_section() -> None:
    # The board's in-page Combine door is the worker picker's Combine on a candidate row
    # (worker tiles carry none): served one candidate, it renders exactly when the idle-card
    # cache holds a combinable copy of that candidate.
    with spawn_live_ceres() as (browser, base_url):
        deed_uid, template = open_slot_candidate_template(base_url)
        copies = [
            {
                "uid": f"MOCK-IDLE-{i}", "card_detail_id": template["card_detail_id"], "edition": template["edition"],
                "foil": template.get("foil", 0), "bcx": 1, "bcx_max": 10, "available": True, "name": template.get("name", "Mock Card"),
            }
            for i in range(2)
        ]
        candidate = dict(template, uid=copies[0]["uid"], bcx=1, bcx_max=10)

        def _open_and_get_door_count(mock_idle_cards: bool):
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            serve_candidates(page, deed_uid, [candidate])
            if mock_idle_cards:
                serve_idle_cards(page, copies)
            else:
                # Injection: gut the cache exactly as the maintainer warned against
                # by forcing GET /api/idle-cards to fail before it ever
                # populates -- the real fetch path already treats a failed
                # request as "stay null" (see prefetchIdleCardsCache's catch)
                # which is the same end state a botched removal of the
                # rendering surface could leave the cache in.
                report_idle_pool_loaded(page)
                page.route("**/api/idle-cards*", lambda route: route.abort())
            row = open_worker_picker(page, base_url, deed_uid)

            sections = page.evaluate(
                "() => ({idle: !!document.getElementById('idle-cards-section'), unassigned: !!document.getElementById('unassigned-bonus-section')})"
            )
            assert sections == {"idle": False, "unassigned": False}
            count = row.locator(".picker-combine[data-picker-action='combine']").count()
            page.close()
            return count

        assert _open_and_get_door_count(mock_idle_cards=True) > 0, \
            "combine door did not render even though the cache holds a combinable duplicate"
        assert _open_and_get_door_count(mock_idle_cards=False) == 0, \
            "guard is void -- the door still rendered with a failed/empty cache"
