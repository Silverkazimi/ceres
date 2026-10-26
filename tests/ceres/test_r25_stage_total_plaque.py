"""Live guards for R-25 -> R-167's staged Total Base PP badge.

The guards exercise the real browser and live board data. Their documented mutations
are verifier-only injections from the R-167 plan; this normal run applies none of them.
The staged badge row must remain within the document viewport at 1920 px; the row's
horizontal footprint is the bounded surface now covered by this retired-plaque guard.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page

from .conftest import settle, spawn_live_ceres
from . import board_basic_recording as recording


ORDINARY = recording.ORDINARY.uid
CASCADE = recording.OVERCAP.uid
RUNI = recording.RUNI_SLOT5.uid
PENDING = recording.PENDING.uid
THEMES = ("alchemical", "dark", "light", "wood")
VIEWPORTS = (1920, 1745, 1536)
PLAQUE_DEED = ORDINARY
PLAQUE_SLOT = 1
CHANGE_BADGES = ".editor-change-badges .editor-change-badge"
# The plot-facts line is gone (editor.js renders `facts = ''`); the change badges now sit
# at the end of the worker group's controls line, which is the row these guards bound.
BADGE_ROW = ".editor-band .slot-group-controls"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _page(live_browser: tuple[Browser, str], width: int = 1920) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": width, "height": 1080})
    page.goto(base_url)
    page.wait_for_function("document.querySelector('#plot-tbody > tr.plot-row') !== null", timeout=30_000)
    return page


def _open_deed(page: Page, deed_uid: str) -> None:
    row = page.locator(f'tr.plot-row[data-deed-uid="{deed_uid}"]')
    assert row.count() == 1, deed_uid
    row.click()
    page.wait_for_selector(
        f'tr.expand-row[data-deed-uid="{deed_uid}"] .editor-band:is([data-plot-state="ready"],[data-plot-state="degraded"]) .slot-group-controls',
        timeout=15_000,
    )


def _served_plaque_fixture(page: Page) -> tuple[str, int]:
    payload = page.evaluate(
        """async deedUid => (await (await fetch(`/api/plots/${encodeURIComponent(deedUid)}`)).json())""",
        PLAQUE_DEED,
    )
    workers = payload.get("workers", [])
    active_companions = {
        companion
        for worker in workers
        for entry in (worker.get("land_abilities") or [])
        if isinstance(entry, list) and entry and entry[0] == "BLOODLINE" and len(entry) > 3
        and isinstance(entry[3], dict) and entry[3].get("active") and len(entry[3].get("companions") or []) == 1
        for companion in (entry[3].get("companions") or [])
    }
    safe = [
        worker for worker in workers
        if worker.get("slot") is not None and worker.get("name") not in active_companions
        and not any(
            isinstance(entry, list) and entry and entry[0] == "BLOODLINE" and len(entry) > 3
            and isinstance(entry[3], dict) and entry[3].get("active")
            and len(entry[3].get("companions") or []) == 1
            for entry in (worker.get("land_abilities") or [])
        )
    ]
    assert safe, f"no non-paired worker on {PLAQUE_DEED}"
    chosen = (PLAQUE_DEED, int(safe[0]["slot"]))
    print(f"R-167 badge fixture: deed={chosen[0]} worker_slot={chosen[1]}")
    assert chosen == (PLAQUE_DEED, PLAQUE_SLOT)
    return chosen


def _stage_slot_removal(page: Page, slot: int) -> None:
    tile = page.locator(f'.slot-editor-body .slot-tile[data-group-id="worker"][data-slot-index="{slot - 1}"]:not(.is-empty)')
    assert tile.count() == 1
    tile.hover()
    tile.locator(".slot-remove").click()
    settle(page)
    _assert_three_badges(page)


def _assert_three_badges(page: Page) -> None:
    badges = page.locator(CHANGE_BADGES)
    texts = badges.all_inner_texts()
    # editor.js renderChangeBadges: the separate "Base" badge was folded into "Total Base"
    # (a plot has one Base PP); the third badge is the staged production rate.
    assert len(texts) == 3
    assert texts[0].startswith("Total Base ")
    assert texts[1].startswith("Total ") and not texts[1].startswith("Total Base ")
    assert "editor-change-production" in (badges.nth(2).get_attribute("class") or "")
    assert texts[2].endswith("/h")


def _stage_slot_5_removal(page: Page) -> None:
    _stage_slot_removal(page, 5)


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def _pending_chips(page: Page):
    """Badge chips render in each row's badge slide-out, built only when the row's
    rail handle is opened; open the pending plot's row and return its chips."""
    handle = page.locator(f'#badge-bar .badge-bar-row[data-deed-uid="{PENDING}"]')
    handle.wait_for(timeout=15_000)
    assert handle.get_attribute("data-pending") == "true"
    handle.click()
    chips = page.locator(f'.badge-overlay-row[data-deed-uid="{PENDING}"] .badge-pending_change')
    chips.first.wait_for(timeout=15_000)
    return chips


def _total_base_badge(page: Page):
    return page.locator(CHANGE_BADGES).filter(has_text="Total Base")


def test_t8_plaque_is_staged_only_and_bounded(live_browser: tuple[Browser, str]) -> None:
    """Injection: unconditional badge rendering must make the initial absence assertion fail."""
    page = _page(live_browser)
    deed_uid, slot = _served_plaque_fixture(page)
    _open_deed(page, deed_uid)
    assert page.locator(CHANGE_BADGES).count() == 0
    before_facts_height = page.evaluate("s => Math.round(document.querySelector(s).getBoundingClientRect().height)", BADGE_ROW)
    _stage_slot_removal(page, slot)
    _assert_three_badges(page)
    after = page.evaluate(
        """row => ({
            factsHeight: document.querySelector(row).getBoundingClientRect().height,
            documentScroll: document.documentElement.scrollWidth,
            viewport: document.documentElement.clientWidth,
        })""",
        BADGE_ROW,
    )
    assert abs(after["factsHeight"] - before_facts_height) <= 1
    assert after["documentScroll"] <= after["viewport"]
    page.close()


def test_t8d_cap_loss_and_effective_frame(live_browser: tuple[Browser, str]) -> None:
    """Injection: signing the frame from raw estimate instead of effective change must fail the neutral capped state."""
    page = _page(live_browser)
    _open_deed(page, CASCADE)
    _stage_slot_5_removal(page)
    badge = _total_base_badge(page)
    assert badge.locator(":scope > strong").inner_text() == "100,000"
    assert badge.locator(".change-badge-cap strong").inner_text() == "−5,441"  # the sign sits inside the figure
    # editor.js renderChangeBadges frames a gain is-positive and a loss is-negative; a move the
    # cap fully absorbs changes nothing and is is-neutral, neither frame.
    classes = (badge.get_attribute("class") or "").split()
    assert "is-neutral" in classes
    assert "is-positive" not in classes
    assert "is-negative" not in classes
    assert "cap −5,441" in badge.inner_text()  # one inline suffix, never a second line
    page.close()


def test_t8b_runi_and_terrain_use_served_base_delta(live_browser: tuple[Browser, str]) -> None:
    """Injection: reading own_construction_pp instead of own_base_pp must change the pinned result."""
    page = _page(live_browser)
    _open_deed(page, RUNI)
    _stage_slot_5_removal(page)
    assert _total_base_badge(page).locator(":scope > strong").inner_text() == "94,000"
    page.close()


def test_t8c_formula_and_synthetic_runi_branch(live_browser: tuple[Browser, str]) -> None:
    """Injection: removing Runi from the cap formula must fail the synthetic over-cap row."""
    page = _page(live_browser)
    result = page.evaluate(
        """async () => {
            const payload = await (await fetch('/api/board')).json();
            const rows = payload?.plots || [];
            const payloadRows = rows.map(row => ({
                base_pp: Number(row?.base_pp),
                base_pp_cap: Number(row?.base_pp_cap),
                base_pp_after_cap: Number(row?.base_pp_after_cap),
            })).filter(row => Number.isFinite(row.base_pp) && Number.isFinite(row.base_pp_cap));
            const rowsFromAttributes = payloadRows.length ? payloadRows : [];
            const effective = (raw, runi, cap) => Math.min(raw - runi, cap) + runi;
            const overCap = rowsFromAttributes.filter(row => row.base_pp > row.base_pp_cap);
            const derived = overCap.map(row => ({effectiveNow: effective(row.base_pp, 0, row.base_pp_cap), served: row.base_pp_after_cap}));
            const source = overCap[0] || {base_pp: 107641.176, base_pp_cap: 100000};
            const synthetic = {
                effectiveAfter: effective(source.base_pp + 1500, 1500, source.base_pp_cap),
                loss: Math.max(0, source.base_pp - source.base_pp_cap),
                runiAfter: 1500,
            };
            return {derived, floor: overCap.length > 0, synthetic};
        }"""
    )
    assert result["floor"]
    assert all(abs(item["effectiveNow"] - item["served"]) <= 0.01 for item in result["derived"])
    assert result["synthetic"]["runiAfter"] == 1500
    assert result["synthetic"]["effectiveAfter"] == 101500
    assert abs(result["synthetic"]["loss"] - 7641.176) <= 0.01
    page.close()


@pytest.mark.parametrize("width", VIEWPORTS)
@pytest.mark.parametrize("theme", THEMES)
def test_t8_geometry_all_themes_and_viewports(
    live_browser: tuple[Browser, str], width: int, theme: str,
) -> None:
    """The staged Total Base badge remains inside its controls row and document viewport."""
    page = _page(live_browser, width)
    page.evaluate("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
    deed_uid, slot = _served_plaque_fixture(page)
    _open_deed(page, deed_uid)
    _stage_slot_removal(page, slot)
    rects = page.evaluate(
        """row => {
            const q = s => document.querySelector(s).getBoundingClientRect();
            const badge = document.querySelector('.editor-change-badges .editor-change-badge:nth-child(2)');
            const facts = document.querySelector(row);
            return {badge: badge.getBoundingClientRect(), facts: facts.getBoundingClientRect(), band: q('.editor-band'), client: document.documentElement.clientWidth, documentScroll: document.documentElement.scrollWidth};
        }""",
        BADGE_ROW,
    )
    assert rects["badge"]["left"] >= rects["facts"]["left"]
    assert rects["badge"]["right"] <= rects["facts"]["right"] + 1
    assert rects["facts"]["left"] >= rects["band"]["left"]
    assert rects["facts"]["right"] <= rects["band"]["right"] + 1
    assert rects["badge"]["right"] <= rects["client"]
    assert rects["documentScroll"] <= rects["client"]
    page.close()


@pytest.mark.xfail(strict=False, reason="R-167: no unknown-estimate badge is specified; pre-existing gap")
def test_t8e_unknown_stake_candidate(live_browser: tuple[Browser, str]) -> None:
    """Injection: returning no unknown-estimate badge must fail the unknown-state assertions."""
    page = _page(live_browser)
    deed_uid, slot = _served_plaque_fixture(page)
    _open_deed(page, deed_uid)
    page.evaluate(
        """() => {
            window.__r25_unknown_candidate = true;
            const original = window.fetch;
            window.fetch = async (...args) => {
                const response = await original(...args);
                if (String(args[0]).includes('/candidates')) {
                    const payload = await response.json();
                    if (payload?.candidates?.[0]) payload.candidates[0].base_pp_delta = {status: 'unknown', value: null, low: null, high: null, reason: 'target_roster_unknown'};
                    return new Response(JSON.stringify(payload), {status: response.status, headers: {'content-type': 'application/json'}});
                }
                return response;
            };
        }"""
    )
    _stage_slot_removal(page, slot)
    settle(page)
    if page.locator('.slot-editor-body .slot-tile.is-empty').count() == 0:
        pytest.skip("pinned fixture has no empty worker slot for candidate injection")
    page.locator('.slot-editor-body .slot-tile.is-empty').first.click()
    settle(page)
    badge = page.locator(CHANGE_BADGES).filter(has_text="Total Base")
    assert badge.count() == 1
    assert "is-unknown" in (badge.get_attribute("class") or "")
    assert "not calculated" in badge.inner_text().lower()
    assert badge.locator(".change-badge-cap").count() == 0
    page.close()


def test_t8g_badge_tooltip_matches_plaque(live_browser: tuple[Browser, str]) -> None:
    """Injection: using rawAfter in the badge title must disagree with the plaque and fail this guard."""
    page = _page(live_browser)
    _open_deed(page, CASCADE)
    _stage_slot_5_removal(page)
    badge = _total_base_badge(page)
    tip = badge.get_attribute("data-tip") or ""
    body = badge.get_attribute("data-tip-body") or ""
    assert tip == "Total Base PP after the change"
    # The tooltip key is the plaque's own effective figure, never the raw estimate.
    assert badge.get_attribute("data-tip-key") == "100,000"
    assert badge.locator(":scope > strong").inner_text() == badge.get_attribute("data-tip-key")
    # The body states the same effective change the face shows in brackets, and the cap.
    face_change = badge.inner_text().split("(", 1)[1].split(")", 1)[0]
    assert body.startswith(f"Projected · {face_change} from ")
    assert "5,441" in body
    assert _total_base_badge(page).locator(":scope > strong").inner_text() in body
    page.close()


def test_h1_pending_chip_face_and_tooltip(live_browser: tuple[Browser, str]) -> None:
    """Injection: restoring the Changing prefix and ready suffix must fail the measured face guard."""
    page = _page(live_browser, 1745)
    chip = _pending_chips(page).first
    assert chip.count() == 1
    face = chip.inner_text().strip()
    # The chip face is the plain "Building" label (badges.js pendingChangeChip); the
    # target and ready time live in the tooltip only.
    assert face == "Building"
    assert "Changing →" not in face
    assert "· ready" in face or "· in " in face or "stalled" in face or "·" not in face
    assert chip.get_attribute("data-tip") == "Worksite change under way"
    tip = chip.get_attribute("data-tip-body") or ""
    assert "building" in tip.lower()
    assert "ready" in tip.lower()
    page.close()


def test_h3_pending_chip_preserves_absolute_and_relative_time(live_browser: tuple[Browser, str]) -> None:
    """Injection: dropping when.absolute from the tooltip must fail this ready-state guard."""
    page = _page(live_browser)
    chips = _pending_chips(page)
    assert chips.count() >= 1
    tip = chips.first.get_attribute("data-tip-body") or ""
    assert "ready" in tip.lower()
    assert "(" in tip and ")" in tip
    page.close()
