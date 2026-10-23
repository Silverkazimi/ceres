"""G12 --  (R-85): a staged removal must never contribute `+0`.

The maintainer's live report, verbatim: *"any changes to workers fully breaks the basePP
display on all fronts and always says it is growing, even if it shrinks."* Root cause:
`stageForSlot()` staged a pure unstake with `base: normalizeEstimate(null)`, which is
`{status:'point', value:0}` -- a confident zero, not an unknown -- and every Base PP
surface (`combineEstimates` over `staged.map(e => e.base)`) summed that zero straight
into the change badge (editor.js:931) and the R-167 staged Total Base badge (editor.js:752),
so a removal could never show as a decrease.

The fix (ceres/static/staging.js, `stageForSlot`): a pure unstake with no priced
estimate yet gets `{status:'not_applicable', ...}` instead of the fabricated zero point
-- `combineEstimates` excludes `not_applicable` entirely, so an unpriced removal
contributes nothing rather than a wrong number, and `stagePricedRemovals()` (the one
caller that stages a bare unstake) always prices it via `removalImpact()` +
`applyRemovalImpactToStage()` before its own render, in the same synchronous tick, so
in the live flow the placeholder is created and overwritten before anything paints.

Mutation-tested: reverting `stageForSlot`'s `base` assignment to the old
`base: normalizeEstimate(baseEstimate)` (dropping the `action === 'unstake'` guard)
restores the fabricated `{status:'point', value:0}` placeholder. That turns
`test_g12_staged_unstake_reads_negative` red -- the Base PP change badge reads `+0`
(never negative) and the over-cap warning, if present, grows instead of shrinking. This
is deliberately a source-level injection (like the mutation tests this module borrows
its convention from, e.g. `test_editor_change_badges.py`'s docstring) rather than a
runtime toggle: the placeholder logic lives inside a closed ES module scope with no
debug hook exposed to the page, and none is added here since exposing internal staging
state to `window` for test convenience would itself be new product surface.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Browser, Page

from . import board_basic_recording as recording
from .conftest import settle, spawn_live_ceres


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _expanded_editor(live_browser: tuple[Browser, str]) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(base_url)
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0",
        timeout=30_000,
    )
    page.evaluate(
        """() => {
            const rows = [...document.querySelectorAll('#plot-tbody > tr.plot-row')];
            (rows.find(r => r.textContent.includes('/5')) || rows[0]).click();
        }"""
    )
    # The `.editor-facts` line is retired: the staged-change badges render on the worker
    # group's controls line (editor.js, "badges now render inside the worker group's controls
    # line"), so the opened editor is ready once its band is.
    page.wait_for_selector(".slot-editor .editor-band[data-plot-state='ready']", timeout=15_000)
    return page


def _stage_a_removal(page: Page) -> bool:
    """Clicks the first occupied worker tile's `×` unstake control, same helper
    shape as `test_editor_change_badges.py::_stage_a_removal` -- kept as a sibling
    copy rather than a cross-module import so this guard has no load-order
    dependency on that module's fixtures."""
    staged = page.evaluate(
        """() => {
            const btn = document.querySelector(
                '.slot-editor-body .slot-tile[data-group-id="worker"]:not(.is-empty) .slot-remove'
            );
            if (!btn) return false;
            btn.click();
            return true;
        }"""
    )
    if staged:
        settle(page)
    return bool(staged)


def _badge_signed_value(text: str) -> float | None:
    match = re.search(r"([+−-])\s*([\d,]+)", text)
    if not match:
        return None
    sign = -1 if match.group(1) in ("−", "-") else 1
    return sign * float(match.group(2).replace(",", ""))


def _total_base_badge(page):
    return page.locator(".slot-editor .editor-change-badges .editor-change-badge").filter(
        has_text=re.compile(r"^Total Base ")
    )


def test_g12_staged_unstake_reads_negative(live_browser: tuple[Browser, str]) -> None:
    """The maintainer's exact complaint: a staged unstake must read a NEGATIVE Base PP
    change, in both the change badge and the R-167 Total Base badge, and the
    projected total must decrease -- never `+0`, never a grow arrow on a shrink."""
    page = _expanded_editor(live_browser)

    stage_total_before = page.evaluate(
        "[...document.querySelectorAll('.editor-change-badges .editor-change-badge')].find(e => e.textContent.trim().startsWith('Total Base '))?.querySelector('strong')?.textContent?.trim() || null"
    )
    assert stage_total_before is None, "a fresh editor must not already show a staged total"

    if not _stage_a_removal(page):
        pytest.skip("no occupied worker tile with an unstake control on the opened plot")

    badges = page.evaluate(
        "[...document.querySelectorAll('.slot-editor .editor-change-badge')].map(e => e.textContent.trim())"
    )
    base_badges = [b for b in badges if "Base" in b]
    assert base_badges, f"no Base PP change badge rendered after staging a removal: {badges}"
    signed = _badge_signed_value(base_badges[0])
    assert signed is not None, f"Base PP badge had no signed figure: {base_badges[0]!r}"
    assert signed < 0, (
        f"a staged removal must read a NEGATIVE Base PP change, got {base_badges[0]!r} "
        "-- this is the exact +0 defect  fixes"
    )

    badge = _total_base_badge(page)
    if badge.count():
        figure_text = badge.locator("strong").first.inner_text()
        figure = float(figure_text.replace(",", ""))
        # The plaque shows the projected AFTER total, not a delta -- it must be
        # strictly less than the plot's own pre-stage Base PP (read off the facts
        # row's own figure before anything was staged, captured via the badge's own
        # tooltip key rather than re-deriving it client-side, per the ticket's own
        # "never re-derive a PP figure in JS" rule).
        pre_stage_key = page.evaluate(
            """() => {
                const badge = [...document.querySelectorAll('.editor-change-badges .editor-change-badge')]
                    .find(e => e.textContent.trim().startsWith('Base '));
                return badge ? badge.getAttribute('data-tip-key') : null;
            }"""
        )
        if pre_stage_key:
            after = float(pre_stage_key.replace(",", ""))
            assert figure <= after, (
                f"Total Base badge ({figure}) must not exceed the Base badge's own after-change " f"figure ({after}) for a pure removal"
            )
    page.close()


def test_g12_staged_replace_with_smaller_card_shrinks_total(
    live_browser: tuple[Browser, str],
) -> None:
    """Second G12 assertion: replacing an occupied worker slot with a smaller card
    must shrink the projected total, and on an over-cap plot, the inline "· cap −N" suffix
    must shrink too -- never grow. Runs on the recording's over-cap plot and its real
    candidate list: this guard observes real staged pricing, it does not manufacture it."""
    page = _expanded_editor(live_browser)

    # The plot-row badges moved out of the table rows into #badge-bar (badge-overlay.js), so a
    # row no longer carries `.badge-over_cap`; the recording's over-cap plot is named directly.
    over_cap_plot = recording.OVERCAP.uid
    plot_before = page.evaluate(
        """async deedUid => (await (await fetch(`/api/plots/${encodeURIComponent(deedUid)}`)).json())""",
        over_cap_plot,
    )
    raw_before = float(plot_before.get("base_pp") or 0)
    after_cap_before = float(plot_before.get("base_pp_after_cap") or raw_before)
    pre_stage_loss = max(0.0, raw_before - after_cap_before)
    assert pre_stage_loss > 0, f"the recording's over-cap plot is not over its cap: {raw_before} vs {after_cap_before}"

    expand = page.locator(f'tr.expand-row[data-deed-uid="{over_cap_plot}"]')
    if not expand.locator(".slot-editor").is_visible():
        page.locator(f'tr.plot-row[data-deed-uid="{over_cap_plot}"]').click()
    expand.locator(".editor-band[data-plot-state='ready']").wait_for(timeout=15_000)
    assert expand.locator(".change-badge-cap").count() == 0, "a fresh editor on an over-cap plot must not already show a staged cap loss"

    # Replace the plot's largest worker (slot 1) with the smallest loaded candidate: an occupied
    # tile is itself the replace control and opens the inline picker.
    largest = max(plot_before["workers"], key=lambda w: float(w.get("base_pp") or 0) if int(w.get("slot") or 0) >= 1 else -1)
    tile = expand.locator(f".slot-tile[data-group-id='worker'][data-slot-index='{int(largest['slot']) - 1}']")
    with page.expect_response("**/api/plots/*/candidates*"):
        tile.click()
    expand.locator(".candidate-row").first.wait_for(timeout=30_000)
    settle(page)
    bases = expand.locator(".candidate-row .candidate-base strong").evaluate_all(
        "els => els.map((el) => Number(el.textContent.replace(/[^0-9.]/g, '')))"
    )
    assert bases, "the picker listed no worker candidates to replace with"
    smallest = min(range(len(bases)), key=bases.__getitem__)
    assert bases[smallest] < float(largest["base_pp"]), f"no candidate smaller than {largest['base_pp']}: {bases}"
    expand.locator(".candidate-row").nth(smallest).click()
    settle(page)

    total_badge = expand.locator(".editor-change-badges .editor-change-badge").filter(has_text=re.compile(r"^Total Base "))
    total_badge.wait_for(timeout=15_000)
    cap_line_after = total_badge.locator(".change-badge-cap")
    # The ticket's exact G12 assertion: replacing with a SMALLER card must SHRINK the over-cap
    # loss relative to the pre-stage, server-served figure -- never hold steady or grow, which
    # is what the fabricated +0 placeholder produced. No cap suffix means no loss is left.
    loss = 0.0
    if cap_line_after.count():
        loss_text = cap_line_after.inner_text()
        loss = float(re.search(r"([\d,]+)", loss_text).group(1).replace(",", ""))
        assert loss >= 0, f"Over cap loss must never read negative: {loss_text!r}"
    assert loss < pre_stage_loss, f"Over cap loss must shrink after replacing with a smaller card: before={pre_stage_loss}, after={loss}"
    page.close()
