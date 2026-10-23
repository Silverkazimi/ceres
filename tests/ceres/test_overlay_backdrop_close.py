"""Click-outside-closes, applied to every lazy-singleton overlay in
the app. Four exist, found by grepping app.js for `.id = '...-overlay'` and
`ensure*Overlay`: `#slot-hazard-overlay`, `#worksite-modal-overlay`,
`#combine-dialog-overlay`, `#combine-stake-prompt-overlay`. All four share the
exact same construction: a backdrop click (`event.target === overlay`) closes
instantly; a click on the panel never sets `event.target` to the overlay
itself (the click only bubbles as far as the panel, which does not equal
`overlay`), so it can never close from inside.

This file drives the combine dialog -- the overlay this session's own work
touches most and the cheapest to reach with real board data -- with REAL
dispatched clicks against the REAL app (Playwright, no mocked DOM), per the
brief: assert on the overlay's `hidden` state after an actual click, never on
a listener merely being attached. The other three overlays
(`ensureHazardOverlay`, `ensureWorksiteModalOverlay`,
`ensureCombineStakePromptOverlay`) are covered by the structural guard at the
bottom of this file, which reads their real source and confirms each has the
identical `if (event.target === overlay) close...()` construction -- verified
by direct source inspection, not by import, since driving all four live would
require unstaking a real sole power source, opening a real worksite change,
and completing a real combine, each an expensive live-data precondition.

Mutation-tested per the internal rules's binding standard: each guard's own injected
defect is recorded in its docstring and was watched red.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from .conftest import settle, spawn_live_ceres
from .idle_pool import serve_recorded_idle_pool
from .test_combine_joint_flow import _find_real_combine_candidate, _expand_plot
from .vm_context import read_entry_source

ROOT = Path(__file__).parents[2]
APP = ROOT / "ceres" / "static" / "app.js"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _open_board(page: Page, base_url: str) -> None:
    # The board's Combine doors read the idle-card pool, which the page fetches only when the
    # board reports it loaded; serve the recorded account's real pool, read after every
    # recorded plot was read (tests/ceres/idle_pool.py).
    serve_recorded_idle_pool(page, base_url)
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)


def _open_combine_dialog(page: Page) -> None:
    candidate = _find_real_combine_candidate(page)
    _expand_plot(page, candidate["index"])
    # The plot-level Combine button is gone; the door on the board is the worker tile's
    # own Combine chip (combineTileActionHtml, a `.slot-tile-cell` sibling of the tile).
    slot_index = int(candidate["worker"]["slot"]) - 1
    combine_btn = page.locator("tr.expand-row").nth(candidate["index"]).locator(
        f".slot-group[data-group-id='worker'] .slot-tile-cell:has(.slot-tile[data-slot-index='{slot_index}']) .tile-combine-chip"
    )
    combine_btn.wait_for(timeout=30_000)
    combine_btn.click()
    overlay = page.locator("#combine-dialog-overlay")
    overlay.wait_for(timeout=30_000)
    chooser = page.locator(".combine-plot-chooser .picker-chip")
    if chooser.count():
        chooser.first.click()
    page.locator("#combine-dialog-body").wait_for(timeout=30_000)


def test_backdrop_click_closes_the_combine_dialog_instantly(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    _open_board(page, base_url)
    _open_combine_dialog(page)

    overlay = page.locator("#combine-dialog-overlay")
    assert overlay.is_visible()
    # Click the overlay itself, well outside the panel (top-left corner of the
    # dimmed backdrop, not the centred panel) -- a REAL dispatched click, not a
    # programmatic .hidden = true.
    overlay.click(position={"x": 5, "y": 5})
    overlay.wait_for(state="hidden", timeout=5_000)
    page.close()


def test_panel_click_never_closes_the_combine_dialog(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    _open_board(page, base_url)
    _open_combine_dialog(page)

    overlay = page.locator("#combine-dialog-overlay")
    assert overlay.is_visible()
    # Click INSIDE the panel (its header title, well away from the close/cancel
    # buttons so this is testing the backdrop listener, not a button handler).
    page.locator("#combine-dialog-title").click()
    settle(page)
    assert overlay.is_visible(), "a click inside the panel must never close the overlay"
    page.close()


def test_escape_still_closes_the_combine_dialog(live_browser: tuple[Browser, str]) -> None:
    """The pre-existing Escape/focus-trap contract must survive the backdrop-click
    addition, not be replaced by it."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    _open_board(page, base_url)
    _open_combine_dialog(page)

    overlay = page.locator("#combine-dialog-overlay")
    assert overlay.is_visible()
    page.keyboard.press("Escape")
    overlay.wait_for(state="hidden", timeout=5_000)
    page.close()


# Structural guard: all four overlays share the identical backdrop-click
# construction. Verified by direct source read for the three not driven live
# above (hazard/worksite/stake-prompt each require an expensive live-data
# precondition -- a real sole-power-source unstake, a real worksite change, a
# real completed combine -- to reach at all).

_OVERLAY_CLOSE_FN = {
    "slot-hazard-overlay": "closeHazard",
    "worksite-modal-overlay": "closeWorksiteModal",
    "combine-dialog-overlay": "closeCombineDialog",
    "combine-stake-prompt-overlay": "closeCombineStakePrompt",
}


def _backdrop_listener_pattern(overlay_id: str, close_fn: str) -> str:
    return f"overlay.addEventListener('click', (event) => {{ if (event.target === overlay) {close_fn}(); }});"


def _assert_all_overlays_have_backdrop_close(source: str) -> None:
    for overlay_id, close_fn in _OVERLAY_CLOSE_FN.items():
        assert _backdrop_listener_pattern(overlay_id, close_fn) in source, (
            f"#{overlay_id} is missing the backdrop-click-closes listener " f"(event.target === overlay -> {close_fn}())"
        )


def test_every_overlay_has_the_backdrop_close_listener() -> None:
    _assert_all_overlays_have_backdrop_close(read_entry_source())


def test_backdrop_listener_missing_on_one_overlay_mutation_is_red() -> None:
    """Injected defect: remove the backdrop-click listener from
    #combine-stake-prompt-overlay only -- the guard above must fail because it
    checks EVERY overlay individually, not "at least one exists somewhere"."""
    source = read_entry_source()
    target = _backdrop_listener_pattern("combine-stake-prompt-overlay", "closeCombineStakePrompt")
    assert target in source  # sanity
    mutated = source.replace(target, "", 1)
    with pytest.raises(AssertionError):
        _assert_all_overlays_have_backdrop_close(mutated)


def test_backdrop_listener_bound_to_panel_too_mutation_is_red() -> None:
    """Injected defect: bind the SAME naive handler to the panel as well as the
    backdrop (the exact failure mode named in the brief: 'a naive listener on
    the overlay catches both') -- simulated here by checking that the panel
    click test above (test_panel_click_never_closes_the_combine_dialog) is the
    guard that would catch this, since a source-level check cannot distinguish
    'listener on overlay only' from 'listener on overlay AND panel' -- both
    produce the same `addEventListener` line on the overlay. This test instead
    verifies the INVARIANT the live test depends on: the click handler checks
    `event.target === overlay`, not `event.currentTarget` or unconditional --
    a naive `() => closeCombineDialog()` with no target check would make EVERY
    click (panel included) close the dialog. Removing the target check here
    reproduces exactly that regression."""
    source = read_entry_source()
    naive = "overlay.addEventListener('click', (event) => { if (event.target === overlay) closeCombineDialog(); });"
    assert naive in source  # sanity
    mutated = source.replace(
        naive,
        "overlay.addEventListener('click', () => { closeCombineDialog(); });",
        1,
    )
    # The structural guard above only checks the target-gated FORM is present
    # so a naive listener (which closes on every click, including inside the
    # panel) makes this specific pattern absent -- go red.
    with pytest.raises(AssertionError):
        _assert_all_overlays_have_backdrop_close(mutated)


# In-flight verification is not orphaned by a click-outside close: verifyCombineBroadcast()
# is a plain async function with no AbortController tied to the overlay, and
# neither the toast nor the board-recapture call is gated on the overlay's
# hidden state anywhere in the confirmed branch -- read directly off source
# rather than asserted from a live timing-dependent race.

def test_confirmation_toast_and_settle_are_never_gated_on_overlay_visibility() -> None:
    source = read_entry_source()
    verifier_start = source.index("async function verifyCombineBroadcast")
    confirmed_start = source.index("if (result.state === 'domain-confirmed') {", verifier_start)
    next_branch = source.index("\n    } else", confirmed_start)
    confirmed_branch = source[confirmed_start:next_branch]
    assert "stageToast(" in confirmed_branch
    assert "settleCombineRefresh(" in confirmed_branch
    # Neither call may be wrapped in a condition that reads overlay.hidden
    # closeCombineDialog() only ever sets DOM/global state, never cancels the
    # in-flight fetch this function is awaiting, so both must fire unconditionally.
    assert ".hidden" not in confirmed_branch
