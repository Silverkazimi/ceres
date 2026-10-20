"""the routing SETUP and the routing
WINDOW show the same route list. Their shared parts are built once in routing-route-list.js; this test renders both screens and fails
when their list markup drifts apart, so a change cannot reach only one of them.

If this fails: a class (or element) now exists in the list of only ONE screen. Either build it in routing-route-list.js so both get
it, or -- if it is truly specific to one screen -- add it to ONLY_SETUP / ONLY_WINDOW below with a reason."""
import json
import re
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"

# classes the list of BOTH screens must carry (the shared structure)
COMMON = {"r3-route-index", "r3-route-chip", "ch-plot-badge", "r3-route-name", "r3-route-name--icon", "r3-from", "r3-route-arrow", "r3-to", "r3-state", "r3-art"}
# reviewed differences: patterns of classes that may exist on one side only
ONLY_SETUP = [r"r3-bundle-head", r"is-warn"]                                            # approval headings; a percent that needs fixing
ONLY_WINDOW = [r"is-confirmed", r"is-excluded", r"r3-state--.*"]                        # run states of a route in a live run
TERRITORY = r"territory-.*"                                                              # region plaque colour: data, not structure

WINDOW = r"""
const operation = { id: 'op', kind: 'sell', source_region: 'PR-BRL-41', source_symbol: 'WOOD', destination_symbol: 'DEC', protected_minimum: '1',
 members: [{ nonce: 'n1', kind: 'sale_dec', route_id: 'r1', source_amount: '10', source_percent: '25', protected_minimum: '1' }] };
const lp = { id: 'op2', kind: 'liquidity', source_region: 'PR-BRL-41', source_symbol: 'STONE', pool: 'STONE', destination_symbol: 'DEC',
 members: [{ nonce: 'n2', kind: 'lp_wallet', route_id: 'r2', source_amount: '10', source_percent: '25', protected_minimum: '1', required_wallet_dec: '1' }] };
const bundle = { id: 'b1', wave: 0, approval: 1, operations: [operation, lp] };
const record = { run_id: 'x', state: 'ready', manifest: { bundles: [bundle], approval_counts: { routing: 1 } },
 bundles: { b1: { state: 'planned' } }, operations: {}, call_budget: { total: 0, ceiling: 120 } };
console.log('__LIST__' + JSON.stringify(routingWindowHtml(record, { player: 'alice' })));
"""
SETUP = r"""
const __seed1 = { rules: [
 { id: 'r1', kind: 'sell', destination_symbol: 'DEC', source_region: 'PR-BRL-41', symbol: 'WOOD', quantity_mode: 'percent', quantity: '25', slippage: '2.5' },
 { id: 'r2', kind: 'liquidity', dec_source: 'wallet', source_region: 'PR-BRL-41', symbol: 'STONE', quantity_mode: 'percent', quantity: '25', slippage: '2.5' }] };
navigator.locks = { request: async (_name, fn) => fn() };
const setup = createRoutingSetup({ initialRules: __seed1.rules, player: 'alice', scope: { kind: 'all' }, regions: [{ region_uid: 'PR-BRL-41' }], receipts: [],
 enabledKinds: ['sale_dec', 'lp_wallet'], signingEnabled: true, onRender: () => {}, isCurrent: () => true });
console.log('__LIST__' + JSON.stringify(setup.html()));
"""


def _list_markup(script, entry):
    result = run_in_module_context(script, entry=STATIC / entry)
    html = json.loads(result.stdout.split("__LIST__", 1)[1].splitlines()[0])
    start = html.index('<div class="r3-route-index">')
    return html[start:html.index('<div class="r3-flow-stage"', start)]


def _classes(markup):
    return {token for value in re.findall(r'class="([^"]*)"', markup) for token in value.split()}


def _allowed(token, patterns):
    return any(re.fullmatch(pattern, token) for pattern in patterns + [TERRITORY])


def test_both_screens_carry_the_shared_list_structure():
    window, setup = _classes(_list_markup(WINDOW, "routing-window.js")), _classes(_list_markup(SETUP, "routing-setup.js"))
    assert COMMON <= window, f"routing window list lacks: {sorted(COMMON - window)}"
    assert COMMON <= setup, f"routing setup list lacks: {sorted(COMMON - setup)}"


def test_no_list_class_exists_on_only_one_screen():
    window, setup = _classes(_list_markup(WINDOW, "routing-window.js")), _classes(_list_markup(SETUP, "routing-setup.js"))
    only_window = sorted(t for t in window - setup if not _allowed(t, ONLY_WINDOW))
    only_setup = sorted(t for t in setup - window if not _allowed(t, ONLY_SETUP))
    assert not only_window, f"classes only in the routing WINDOW list: {only_window}. Share them via routing-route-list.js (see this file)."
    assert not only_setup, f"classes only in the routing SETUP list: {only_setup}. Share them via routing-route-list.js (see this file)."


def test_the_shared_markup_is_built_in_one_place_only():
    shared = (STATIC / "routing-route-list.js").read_text(encoding="utf-8")
    for token in ('r3-route-index', 'r3-scroll-cue', 'class="r3-from"', 'class="r3-route-arrow"', 'class="r3-to"'):
        assert token in shared, token
    for name in ("routing-window.js", "routing-setup.js"):
        source = (STATIC / name).read_text(encoding="utf-8")
        assert "from './routing-route-list.js'" in source and "routeListColumnHtml(" in source and "routeNameHtml(" in source and "routeScrollCueHtml(" in source, name
        for literal in ('class="r3-route-index"', 'class="r3-scroll-cue"', 'class="r3-from"', 'class="r3-route-arrow"', 'class="r3-to"', 'class="ceres-btn ceres-btn--secondary r3-more"'):
            assert literal not in source, f"{name} builds {literal} itself instead of using routing-route-list.js"


def test_the_flow_boxes_are_shared_too():
    """The Pool box and the two-row outcome box of a route's flow (approval window AND setup preview) are built once."""
    shared = (STATIC / "routing-route-list.js").read_text(encoding="utf-8")
    assert 'class="r3-pool"' in shared and 'class="r3-destination r3-destination--two"' in shared
    for name in ("routing-window.js", "routing-setup.js"):
        source = (STATIC / name).read_text(encoding="utf-8")
        assert "poolBoxHtml(" in source and "outcomeBoxHtml(" in source, name
        for literal in ('class="r3-pool"', 'class="r3-destination'):
            assert literal not in source, f"{name} builds {literal} itself instead of using routing-route-list.js"


def test_both_summaries_carry_the_scroll_cue():
    """the cue lives in the summary row of BOTH screens (built once in routing-route-list.js)."""
    def summary(script, entry):
        result = run_in_module_context(script, entry=STATIC / entry)
        html = json.loads(result.stdout.split("__LIST__", 1)[1].splitlines()[0])
        return _classes(html[html.index('<div class="r3-summary">'):html.index('<div class="r3-flow-layout">')])
    for classes in (summary(WINDOW, "routing-window.js"), summary(SETUP, "routing-setup.js")):
        assert {"r3-scroll-cue", "r3-scroll-cue-text", "r3-more", "ceres-btn"} <= classes
