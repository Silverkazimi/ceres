"""//: the route list scrolls without a visible bar; the cue for that costs the list NO space. History: /0.11.8
put pill rows above and below the list; the maintainer rejected them (they cut space and left a gap on the side without an active pill, so
the list no longer lined up with the box beside it). Now: a counter with two page buttons in the SUMMARY row ("23-36 of 36"), and a soft
fade on the list edge that has more routes. The list has no wrapper and no extra rows. Also: the route chips show the DEC icon ()."""
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"

WINDOW_JS = r"""
const operation = { id: 'op', kind: 'sell', source_region: 'PR-BRL-41', source_symbol: 'WOOD', destination_symbol: 'DEC', protected_minimum: '1',
  members: [{ nonce: 'n1', kind: 'sale_dec', route_id: 'r1', source_amount: '10', source_percent: '25', protected_minimum: '1' }] };
const bundle = { id: 'b1', wave: 0, approval: 1, operations: [operation] };
const record = { run_id: 'x', state: 'ready', manifest: { bundles: [bundle], approval_counts: { routing: 1 } },
  bundles: { b1: { state: 'planned' } }, operations: {}, call_budget: { total: 0, ceiling: 120 } };
const html = routingWindowHtml(record, { player: 'alice' });
"""


def test_the_list_has_no_pill_rows_and_no_wrapper():
    run_in_module_context(WINDOW_JS + r"""
if (/r3-route-col|r3-more--above|r3-more--below/.test(html)) throw Error('pill rows or their wrapper are back');
if (!/<div class="r3-flow-layout"><div class="r3-route-index">/.test(html)) throw Error('the list is not a direct child of the flow layout');
""", entry=STATIC / "routing-window.js")


def test_the_cue_is_in_the_summary_row_and_starts_hidden():
    run_in_module_context(WINDOW_JS + r"""
const summary = html.match(/<div class="r3-summary">.*?<\/div>/s)[0];
if (!/<span class="r3-scroll-cue" hidden>/.test(summary) || !summary.includes('data-r3-more="up"') || !summary.includes('data-r3-more="down"')) throw Error('cue missing from the summary row');
""", entry=STATIC / "routing-window.js")


def test_route_chip_shows_the_dec_icon_for_a_sale():
    run_in_module_context(WINDOW_JS + r"""
const chip = html.match(/<div class="r3-route-chip[^"]*".*?<\/div>/s)[0];
if (!/class="r3-to"><img class="r3-art"[^>]*>DEC<\/span>/.test(chip)) throw Error('sale chip has no DEC icon: ' + chip);
""", entry=STATIC / "routing-window.js")


def test_cue_wiring_and_the_fade_cost_no_space():
    js = (STATIC / "routing-window.js").read_text(encoding="utf-8")
    assert "export function updateRouteScrollCue(root) {" in js and "export function scrollRouteIndexByPage(button) {" in js
    assert "list.classList.toggle('has-more-above'" in js and "list.classList.toggle('has-more-below'" in js
    assert "list.setAttribute('aria-label', 'Routes, scrollable')" in js
    modal = (STATIC / "harvest-modal.js").read_text(encoding="utf-8")
    assert modal.count("updateRouteScrollCue(") == 4 and "[data-r3-more]" in modal
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    assert "r3-route-col" not in css and "r3-more--" not in css                       # nothing of the pill rows is left
    block = css[css.index("/* "):]
    assert ".r3-route-index.has-more-above, .r3-route-index.has-more-below {" in block and "mask-image: linear-gradient" in block
    for forbidden in ("position: absolute", "position: sticky", "padding-block", "margin-block"):
        assert forbidden not in block.split(".r3-route-index.has-more-above")[1], forbidden  # the fade moves and pads nothing


def test_single_share_has_no_member_row_and_its_percentage_is_in_the_flow_box():
    run_in_module_context(WINDOW_JS + r"""
if (/class="r3-members"|class="r3-member[ "]/.test(html)) throw Error('a single-share route still has a member row');
const source = html.match(/<div class="r3-flow-source">.*?<\/div>/s)[0];
if (!/<span>10\.00 · 25%<\/span>/.test(source)) throw Error('percentage missing from the flow box header: ' + source);
const editable = routingWindowHtml(record, { player: 'alice', excludedRouteIds: new Set() });
""", entry=STATIC / "routing-window.js")
