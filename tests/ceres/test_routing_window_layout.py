""": the routing window never scrolls; the status lives in the summary row; only the route list scrolls, alone, with its bar
on the left; it jumps to the active approval; the approval box never scrolls. (Measured in a real browser at 12/36/84 routes and
1885x1049, 1536x864, 1366x768 for the release; these guards pin the structure.)"""
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_status_is_part_of_the_summary_row_and_the_old_bar_is_gone():
    run_in_module_context(r"""
const operation = { id: 'op', kind: 'sell', source_region: 'PR-BRL-41', source_symbol: 'WOOD', destination_symbol: 'DEC', protected_minimum: '1',
  members: [{ nonce: 'n1', kind: 'sale_dec', route_id: 'r1', source_amount: '10', source_percent: '25', protected_minimum: '1' }] };
const bundle = { id: 'b1', wave: 0, approval: 1, operations: [operation] };
const record = { run_id: 'x', state: 'ready', manifest: { bundles: [bundle], approval_counts: { routing: 1 } },
  bundles: { b1: { state: 'planned' } }, operations: {}, call_budget: { total: 2, ceiling: 120 } };
const html = routingWindowHtml(record, { player: 'alice' });
if (html.includes('r3-status-bar')) throw Error('the standalone status bar is back');
const summary = html.match(/<div class="r3-summary">.*?<\/div>/s);
if (!summary || !summary[0].includes('r3-summary-status') || !summary[0].includes('Ready to sign')) throw Error('status is not in the summary row');
// the metered-checks counter appears once the approval is on chain and being checked; it sits in the same summary row.
const checking = { ...record, bundles: { b1: { state: 'checking', tx_id: '0'.repeat(39) + '1' } } };
const checkingHtml = routingWindowHtml(checking, { player: 'alice' });
if (checkingHtml.includes('r3-status-bar')) throw Error('the standalone status bar is back');
const checkingSummary = checkingHtml.match(/<div class="r3-summary">.*?<\/div>/s);
if (!checkingSummary || !checkingSummary[0].includes('r3-summary-status') || !checkingSummary[0].includes('Metered routing checks 2/120')) throw Error('checking status is not in the summary row');
""", entry=STATIC / "routing-window.js")


def test_layout_css_never_scrolls_the_window_and_keeps_the_bar_off_the_pointers():
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    block = css[css.index("/* "):]
    assert ".hm-body:has(> .r3-window) { overflow: hidden; }" in block                 # the window never scrolls
    assert ".r3-window .r3-flow-stage { min-block-size: 0; overflow: hidden; }" in block  # the approval box never scrolls
    assert "overflow-y: auto" in block and "direction: rtl" in block                     # the list scrolls alone, bar on the LEFT
    assert ".r3-window .r3-route-index > * { direction: ltr; }" in block


def test_the_list_jumps_to_the_active_approval_only_when_it_changes():
    js = (STATIC / "routing-window.js").read_text(encoding="utf-8")
    assert "export function scrollRouteIndexToActive(root) {" in js
    assert "if (list.dataset.r3Scrolled === bundle) continue;" in js                     # never fights a manual scroll
    modal = (STATIC / "harvest-modal.js").read_text(encoding="utf-8")
    assert modal.count("scrollRouteIndexToActive(panel)") == 2                           # patched and fully rendered windows


def test_compact_widths_keep_the_routing_window_height_limited():
    """: at 761-1279 px the  page-flow rules had lifted the height limit, so the window grew with its list (2003 px at 36 routes).
    Real-browser proof (staged, 1279x720 / 1100x700 / 800x900): panel height == viewport, body does not scroll, only the list does."""
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    block = css[css.index("/* "):]
    assert "@media (min-width: 761px)" in block and ".harvest-panel.is-r3:has(.r3-window)" in block
    assert "overflow: hidden" in block and "max-block-size: calc(var(--ceres-vh, 1dvh) * 100)" in block


def test_status_segment_is_as_tall_as_the_wallet_chip_and_gives_the_spinner_room():
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    block = css[css.index("/* "):]
    assert "align-self: stretch" in block and "min-block-size: 48px" in block and ".r3-status-spinner { flex: none" in block
