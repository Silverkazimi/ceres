"""Static guards for // mobile plot composition."""

from __future__ import annotations

import re
from pathlib import Path

from .vm_context import run_in_module_context


ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
ROWS = (STATIC / "rows.js").read_text(encoding="utf-8")
TOOLTIP = (STATIC / "tooltip.js").read_text(encoding="utf-8")
CSS = (STATIC / "mobile-plot.css").read_text(encoding="utf-8")


def _split_top_level_commas(prelude: str):
    # A `:is(a, b, c)` (or `:where`/`:not`) clause has its own commas -- splitting the
    # prelude naively on every comma turns one compound selector into several bogus
    # top-level ones. Only split where paren depth is 0.
    depth = 0
    start = 0
    for index, char in enumerate(prelude):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            yield prelude[start:index]
            start = index + 1
    yield prelude[start:]


def _css_selectors(text: str):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    cursor = 0
    while cursor < len(text):
        opening = text.find("{", cursor)
        if opening < 0:
            return
        prelude = " ".join(text[cursor:opening].strip().split())
        depth = 1
        end = opening + 1
        while end < len(text) and depth:
            depth += (text[end] == "{") - (text[end] == "}")
            end += 1
        assert depth == 0, f"unclosed CSS block after {prelude!r}"
        body = text[opening + 1:end - 1]
        if prelude.startswith("@media"):
            yield from _css_selectors(body)
        elif prelude and not prelude.startswith("@"):
            yield from (selector.strip() for selector in _split_top_level_commas(prelude))
        cursor = end


def test_mobile_plot_stylesheet_is_linked_after_shared_styles_with_version_token() -> None:
    ceres = HTML.index('<link rel="stylesheet" href="/static/ceres.css?v={{CERES_VERSION}}">')
    mobile = HTML.index('<link rel="stylesheet" href="/static/mobile-plot.css?v={{CERES_VERSION}}">')
    assert mobile > ceres
    assert HTML.count("/static/mobile-plot.css?v={{CERES_VERSION}}") == 1


def test_every_mobile_plot_rule_is_doubly_scoped_and_uses_palette_tokens() -> None:
    # (maintainer-approved button migration, release , commit a38cb80d
    # 2026-09-16) added the Full-HD-or-narrower scope ahead of the mobile one, so every
    # rule is now double-scoped `html.ceres-lw-le-1919.ceres-lw-le-1279`.
    # mobile-plot.css grew well past the plot sheet across many 2026-09-16..09-18 mobile
    # commits (receipts panel/history, plot cards, board header, harvest/worksite/changes
    # overlays, the shared bottom action bar) -- it is now the general mobile stylesheet
    # not plot-sheet-exclusive, so the "every rule targets .mobile-plot-sheet" half of this
    # guard is stale. The double viewport scope it also checks is still a real, current
    # invariant (keeps this file inert above the Full-HD/mobile breakpoints) and stays.
    selectors = list(_css_selectors(CSS))
    assert selectors
    for selector in selectors:
        assert selector.startswith("html.ceres-lw-le-1919.ceres-lw-le-1279"), selector
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", CSS)
    assert not re.search(r"(?:^|[;{])\s*(?:color|background(?:-color)?|border(?:-color)?)\s*:\s*(?:white|black|red|blue|green|orange)\b", CSS, re.I)


def test_worker_row_has_five_workers_and_assets_are_separated() -> None:
    # (2026-09-18, commit eed5c4ed) folded the Runi tile into the same worker
    # `.slot-grid` (matching the 2026-09-16 maintainer amendment putting Runi/Core alongside
    # workers 4-5), so decorateMobileWorkerTiles() now derives groupId per cell instead
    # of hard-filtering on `[data-group-id="worker"]`, and only tags an EMPTY worker
    # slot with its label -- occupied tiles retain no such label (maintainer amendment).
    assert "grid-template-columns: repeat(5, minmax(0, 1fr));" in CSS
    assert "const tile = cell.querySelector?.('.slot-tile');" in ROWS
    assert "cell.querySelector('.slot-power') ? 'runi' : tile.dataset.groupId" in ROWS
    assert "if (groupId === 'worker' && tile.classList.contains('is-empty'))" in ROWS
    assert "Boostable PP" in ROWS
    assert "Worker slot ${Number(slotIndex) + 1}" in ROWS


def test_back_button_uses_shared_contract() -> None:
    assert 'id="mobile-plot-sheet-close"' in HTML
    assert "back?.classList.add('ceres-btn', 'ceres-btn--secondary', 'mobile-plot-back');" in ROWS
    assert "back?.setAttribute('data-control-density', 'touch');" in ROWS
    assert not re.search(r"\.mobile-plot-back\s*\{", CSS)


def test_touch_tooltips_require_a_real_long_press_and_never_use_pointer_coordinates() -> None:
    assert "const TOUCH_LONG_PRESS_MS = 500;" in TOOLTIP
    assert "!Number.isFinite(event.clientX) || !Number.isFinite(event.clientY)" in TOOLTIP
    assert "showTooltip(trigger, { immediate: true });" in TOOLTIP
    assert "positionTooltip(target);" in TOOLTIP
    # (2026-09-16, commit dda4d0b7): clamping now offsets from the visual
    # viewport's own left/top (not just a flat margin), so a tooltip stays within visible
    # viewport edges instead of the page's.
    assert "const minLeft = (viewport.left || 0) + margin;" in TOOLTIP
    assert "const left = Math.max(minLeft, Math.min(unclampedLeft, maxLeft));" in TOOLTIP
    assert "top = Math.max(minTop, Math.min(top, maxTop));" in TOOLTIP
    touch_click = TOOLTIP.split("document.addEventListener('click', (event) => {", 1)[1].split("}, true);", 1)[0]
    assert "hideTooltip();" in touch_click
    assert "showTooltip" not in touch_click
    assert "event.stopImmediatePropagation();" in touch_click
    # Release (2026-09-16, commit 83896c1d "remove mobile instructional hints")
    # deliberately removed the long-press hint affordance; it must stay gone.
    assert "data-mobile-long-press-hint" not in TOOLTIP


def test_focused_picker_moves_under_header_and_collapses_other_tiles() -> None:
    assert "worksite.after(region);" in ROWS
    assert "body.scrollTop = 0;" in ROWS
    assert "sheet.classList.toggle('is-picker-focused', focused)" in ROWS
    assert ".mobile-plot-sheet.is-picker-focused .slot-editor" in CSS
    assert "transition: max-block-size 180ms ease-in" in CSS


def test_phone_picker_grid_is_two_cards_per_row() -> None:
    # (2026-09-16, commit 97b8f76d): a fixed 2-column grid squeezed the
    # restored Base/Boostable PP box padding on narrow phones, so the grid now uses
    # auto-fit with a minimum card width instead, letting it drop to fewer columns.
    selector = ".mobile-plot-sheet.is-picker-focused .mobile-picker-focus-region .candidate-list.is-card-grid"
    assert selector in CSS
    rule = CSS.split(selector, 1)[1].split("}", 1)[0]
    assert "grid-template-columns: repeat(auto-fit, minmax(min(100%, 10rem), 1fr));" in rule


def _mobile_resource_flow_body() -> str:
    return ROWS.split("function mobileResourceFlowHtml(row) {", 1)[1].split("\nfunction ", 1)[0]


def test_resource_flow_block_reuses_desktop_material_formatter_and_matching_icon_size() -> None:
    # (table redesign version C, plan §Version C). Review fix, landing
    # no produced-resource line (it repeated the Harvest/hr band above verbatim), so
    # every line here is a consumed input and reuses the desktop `materialCostCell()`
    # formatter/icon (badges.js) directly. Food/hr must use the SAME `.material-cost
    # icon` class/size as Wood/Stone/Iron -- not `foodPerHourCell()`'s smaller desktop
    # `.dec-stake-icon`, the exact mismatch already rejected once for .
    body = _mobile_resource_flow_body()
    assert "materialCostCell(row, symbol)" in body
    assert "class=\"material-cost-icon\"" in body
    assert "dec-stake-icon" not in body
    assert "foodPerHourCell" not in body
    assert "resourceIconHtml" not in body
    # rendered after the existing Harvest/hr band and Total PP stat, not before them.
    assert "${mobileTotalPpHtml(row)}${mobileResourceFlowHtml(row)}" in ROWS


def test_resource_flow_missing_data_reads_unavailable_never_zero() -> None:
    # materialCostCell() returns the desktop "—" for a missing recipe or missing facts
    # never a numeric 0 (badges.js); grain_cost_per_hour is null/undefined the same way.
    # This block must translate that into the word "unavailable" -- the same
    # missing-data semantics, phone wording.
    body = _mobile_resource_flow_body()
    assert "grainCost === null || grainCost === undefined ? 'unavailable'" in body
    assert "cell.html !== '—'" in body


def test_resource_flow_block_never_truncates_and_every_line_is_signed_consumption() -> None:
    # Review fix, landing : dropping the cap (there are at most four possible
    # lines -- Food/Wood/Stone/Iron -- so none is ever needed) means a plot consuming
    # all four materials, e.g. an SPS/Research/Aura worksite, keeps Iron. Every line is
    # consumption, so every line carries the same leading minus sign; Net/hr stays gone
    # (maintainer decision 5).
    body = _mobile_resource_flow_body()
    assert "lines.slice(0, 4)" not in body
    assert ".slice(" not in body
    assert "Net/hr" not in body
    assert "`−${num(grainCost, 2)}${grainIcon}`" in body
    assert "`−${cell.html}`" in body
    assert "'IRON'" in body and "Iron/hr" in body


def test_resource_flow_block_never_touches_the_card_face_or_breakpoint() -> None:
    # Plan §Version C: card faces and the mobile breakpoint stay exactly as they were
    # the new block belongs to the detail sheet only.
    card_stats = ROWS.split("export function plotStatsHtml(row) {", 1)[1].split("\n}\n", 1)[0]
    assert "materialCostCell" not in card_stats
    assert "mobileResourceFlowHtml" not in card_stats
    card_html = ROWS.split("export function plotCardHtml(row, rowIndex) {", 1)[1].split("\n}\n", 1)[0]
    assert "mobileResourceFlowHtml" not in card_html
    assert "ceres-lw-le-1279" not in _mobile_resource_flow_body()


def test_resource_flow_css_is_scoped_label_left_value_right() -> None:
    assert "html.ceres-lw-le-1919.ceres-lw-le-1279 .mobile-plot-sheet .mobile-plot-resource-flow {" in CSS
    rows_selector = "html.ceres-lw-le-1919.ceres-lw-le-1279 .mobile-plot-sheet .mobile-plot-resource-flow-rows > div {"
    assert rows_selector in CSS
    rule = CSS.split(rows_selector, 1)[1].split("}", 1)[0]
    assert "justify-content: space-between;" in rule


# review fix, landing : the static-source guards above cannot catch a
# dropped Iron line by themselves, so these two run the real `mobileResourceFlowHtml()`
# against fixture rows through the shared whole-module VM sandbox (vm_context.py).

_ALL_FOUR_INPUTS_BODY = r"""
const row = {
 resource: 'sps',
 work_per_hour: 12.5,
 worksite_type: 'Rift Watchtower',
 terrain: 'grasslands',
 grain_cost_per_hour: 12.34,
 wood_cost_per_hour: 5.6,
 stone_cost_per_hour: 2.1,
 iron_cost_per_hour: 0.75,
};
const html = mobileResourceFlowHtml(row);
for (const label of ['Food/hr', 'Wood/hr', 'Stone/hr', 'Iron/hr']) {
 if (!html.includes(label)) throw new Error('missing ' + label + ': ' + html);
}
const icons = html.match(/class="material-cost-icon"/g) || [];
if (icons.length !== 4) throw new Error('expected 4 material-cost-icon uses, got ' + icons.length + ': ' + html);
const minuses = html.match(/−/g) || [];
if (minuses.length !== 4) throw new Error('expected 4 leading minus signs, got ' + minuses.length + ': ' + html);
if (html.includes('dec-stake-icon')) throw new Error('Food/hr used the wrong (smaller) icon class: ' + html);
if (html.includes('unavailable')) throw new Error('a fully-consuming plot must not show unavailable: ' + html);
"""

_EMPTY_PLOT_BODY = r"""
const row = {
 resource: null,
 work_per_hour: null,
 worksite_type: null,
 terrain: null,
 grain_cost_per_hour: null,
 wood_cost_per_hour: null,
 stone_cost_per_hour: null,
 iron_cost_per_hour: null,
};
const html = mobileResourceFlowHtml(row);
if (!html.includes('Food/hr')) throw new Error('Food/hr must always be shown: ' + html);
if (!html.includes('unavailable')) throw new Error('missing recipe must read unavailable, not 0: ' + html);
if (html.includes('0.00') || />0</.test(html)) throw new Error('missing data must never render as a zero: ' + html);
for (const label of ['Wood/hr', 'Stone/hr', 'Iron/hr']) {
 if (html.includes(label)) throw new Error('an empty plot must not show an unconsumed material line: ' + label);
}
"""


def test_resource_flow_shows_all_four_consumed_inputs_with_matching_icons_and_signs() -> None:
    run_in_module_context(_ALL_FOUR_INPUTS_BODY)


def test_resource_flow_empty_plot_reads_unavailable() -> None:
    run_in_module_context(_EMPTY_PLOT_BODY)


