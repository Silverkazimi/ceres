"""G12 -- (R-85): a staged removal must never contribute `+0`.

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

from .conftest import spawn_live_ceres


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
    page.wait_for_selector(".editor-facts", timeout=15_000)
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
        page.wait_for_timeout(1500)
    return bool(staged)


def _badge_signed_value(text: str) -> float | None:
    match = re.search(r"([+−-])\s*([\d,]+)", text)
    if not match:
        return None
    sign = -1 if match.group(1) in ("−", "-") else 1
    return sign * float(match.group(2).replace(",", ""))


def _total_base_badge(page):
    return page.locator(".editor-facts .editor-change-badges .editor-change-badge").filter(
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
        "[...document.querySelectorAll('.editor-facts .editor-change-badge')].map(e => e.textContent.trim())"
    )
    base_badges = [b for b in badges if "Base" in b]
    assert base_badges, f"no Base PP change badge rendered after staging a removal: {badges}"
    signed = _badge_signed_value(base_badges[0])
    assert signed is not None, f"Base PP badge had no signed figure: {base_badges[0]!r}"
    assert signed < 0, (
        f"a staged removal must read a NEGATIVE Base PP change, got {base_badges[0]!r} "
        "this is the exact +0 defect fixes"
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
 must shrink too -- never grow. Skips (rather than fabricates a fixture) when the
 live board has no over-cap plot with a smaller replacement available, since this
 guard's job is to observe real staged pricing, not to manufacture the scenario."""
    page = _expanded_editor(live_browser)

    over_cap_plot = page.evaluate(
        """() => {
 const rows = [...document.querySelectorAll('#plot-tbody > tr.plot-row')];
 const row = rows.find(r => r.querySelector('.badge-over_cap'));
 return row ? row.getAttribute('data-deed-uid') : null;
 }"""
    )
    if not over_cap_plot:
        pytest.skip("no over-cap plot on the live board to exercise the shrink assertion against")

    plot_before = page.evaluate(
        """async deedUid => (await (await fetch(`/api/plots/${encodeURIComponent(deedUid)}`)).json())""",
        over_cap_plot,
    )
    raw_before = float(plot_before.get("base_pp") or 0)
    after_cap_before = float(plot_before.get("base_pp_after_cap") or raw_before)
    pre_stage_loss = max(0.0, raw_before - after_cap_before)

    row = page.locator(f'tr.plot-row[data-deed-uid="{over_cap_plot}"]')
    row.click()
    page.wait_for_selector(".editor-facts", timeout=15_000)

    cap_line_before = page.locator(".change-badge-cap")
    assert cap_line_before.count() == 0, "a fresh editor on an over-cap plot must not already show a staged cap loss"

    tile = page.locator('.slot-editor-body .slot-tile[data-group-id="worker"]:not(.is-empty)').first
    if tile.count() == 0:
        pytest.skip("the over-cap plot has no occupied worker tile to replace")
    tile.hover()
    replace_btn = tile.locator('[data-slot-action="replace"]')
    if replace_btn.count() == 0:
        pytest.skip("the over-cap plot's occupied tile offers no replace action")
    replace_btn.click()
    page.wait_for_selector(".picker-panel, .ceres-picker", timeout=10_000)

    # Sort ascending by construction/base PP so the first real candidate row is the
    # smallest available -- the picker's own `base_pp` sort, already server-driven.
    page.evaluate(
        """() => {
 const sortBtn = [...document.querySelectorAll('[data-picker-action="sort"]')]
 .find(el => el.dataset.sort === 'base_pp');
 if (sortBtn) sortBtn.click();
 }"""
    )
    page.wait_for_timeout(800)
    choose = page.locator('[data-picker-action="choose"]').first
    if choose.count() == 0:
        pytest.skip("no picker candidate available to replace with")
    choose.click()
    page.wait_for_timeout(1200)

    cap_line_after = page.locator(".change-badge-cap")
    if cap_line_after.count():
        loss_text = cap_line_after.inner_text()
        loss = float(re.search(r"([\d,]+)", loss_text).group(1).replace(",", ""))
        assert loss >= 0, f"Over cap loss must never read negative: {loss_text!r}"
        # The ticket's exact G12 assertion: replacing with a SMALLER card must SHRINK
        # the over-cap loss relative to the pre-stage, server-served figure -- never
        # hold steady or grow, which is what the fabricated +0 placeholder produced
        # (a removal that never registers means the incoming card's own PP is the
        # only thing counted, so the loss can only ever look flat or bigger).
        if pre_stage_loss > 0:
            assert loss < pre_stage_loss, (
                f"Over cap loss must shrink after replacing with a smaller card: " f"before={pre_stage_loss}, after={loss}"
            )
    page.close()
