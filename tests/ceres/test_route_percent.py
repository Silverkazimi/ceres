from decimal import Decimal
import json
from pathlib import Path

import pytest

from ceres import harvest_routing as routing, routing_plan
from tests.ceres.vm_context import run_in_module_context


STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_percent_allocation_is_an_integer_and_floors_to_resource_precision():
    rule = {"quantity_mode": "percent", "quantity": "25"}
    assert routing.allocation_quantity(rule, Decimal("1234.567")) == Decimal("308.641")
    for value in ("0", "101", "25.0", "25.5", "-1"):
        with pytest.raises(routing.RoutingError, match="whole number from 1% to 100%"):
            routing.allocation_quantity({**rule, "quantity": value}, Decimal("100"))


def test_manifest_member_retains_the_percent_used_to_compute_its_source_amount():
    member = routing_plan._member(
        {"id": "step-1", "kind": "sell", "quantity": "308.641",
         "quote": {"minimum": "0.1", "output": "1"}},
        {"id": "route-1", "quantity_mode": "percent", "quantity": "25"},
        "sale_dec",
    )
    assert member["source_amount"] == "308.641"
    assert member["source_percent"] == "25"


def test_amount_routes_are_flagged_and_remain_previewable_at_the_saved_amount():
    run_in_module_context(r"""
const __seed1 = { rules: [{
  id: 'old', kind: 'sell', source_region: 'land-PNW-041', symbol: 'WOOD',
  destination_symbol: 'DEC', quantity_mode: 'amount', quantity: '25',
}] };
const setup = createRoutingSetup({ initialRules: __seed1.rules, player: 'fixtureowner', scope: { kind: 'all' },
  regions: [{ region_uid: 'land-PNW-041' }], receipts: ['confirmed-attempt'],
  enabledKinds: ['sale_dec'], signingEnabled: true });
let html = setup.html();
if (!html.includes('25 Wood — fixed amount (old style). Change to a percent') || !html.includes('value=""'))
  throw Error('saved amount route was not flagged with an empty percent field');
if (html.includes('25%')) throw Error('saved amount was silently shown as a percent');
setup.action('start').then(() => {
  html = setup.html();
  if (html.includes('Saved amount routes must be set to a percent before review.'))
    throw Error('saved amount route was blocked from v2 review');
  if (!html.includes('vm_context fetch stub'))
    throw Error('legacy route did not reach the preview request');
}).catch(error => { console.error(error); process.exitCode = 1; });
""", entry=STATIC / "routing-setup.js")
    assert routing.validate_percent_allocation({"quantity_mode": "amount", "quantity": "25"}) == Decimal("25")


def test_setup_pager_posting_copy_default_and_distinct_region_count():
    run_in_module_context(r"""
const rules = Array.from({ length: 6 }, (_, index) => ({
  id: `route-${index}`, kind: 'sell', source_region: index < 4 ? 'land-PNW-041' : 'land-BRL-065',
  symbol: 'WOOD', destination_symbol: 'DEC', quantity_mode: 'percent', quantity: '25',
}));
const __seed2 = { rules };
const setup = createRoutingSetup({ initialRules: __seed2.rules, player: 'fixtureowner', scope: { kind: 'all' },
  regions: [{ region_uid: 'land-PNW-041' }, { region_uid: 'land-BRL-065' }],
  enabledKinds: ['sale_dec'], signingEnabled: true });
let html = setup.html();
if (html.includes('r3-pagebar')) throw Error('single-page pager is visible');
if (!html.includes('<strong>6 routes</strong><span>2 source regions</span>')) throw Error('setup summary or distinct source-region count is missing');
if (!html.includes('Signs with your Posting key') || !html.includes('Money transfers need the Active key.'))
  throw Error('Posting summary or tooltip copy missing');
if (html.includes('<span class="r3-wallet">')) throw Error('Posting summary has button styling');

const empty = createRoutingSetup({ player: 'fresh', scope: { kind: 'all' },
  regions: [{ region_uid: 'land-PNW-041' }], enabledKinds: ['sale_dec'], signingEnabled: true });
empty.action('add');
html = empty.html();
if (!/<span class="r3-percent-field"><input[^>]*value="25"[^>]*><span aria-hidden="true">%<\/span><\/span>/.test(html))
  throw Error('new route does not default to a visibly marked 25%');

""", entry=STATIC / "routing-setup.js")


def test_route_chips_and_flow_rows_include_percent_and_estimate():
    member = {"route_id": "route-1", "kind": "sale_dec", "source_amount": "308.641",
              "source_percent": "25", "protected_minimum": "0"}
    operation = {"id": "op-1", "source_region": "land-PNW-041", "source_symbol": "WOOD",
                 "destination_symbol": "DEC", "pool": "WOOD", "protected_minimum": "0",
                 "members": [member]}
    bundle = {"id": "bundle-1", "wave": 0, "approval": 1, "operations": [operation]}
    record = {"state": "ready", "manifest": {"bundles": [bundle], "call_budget": {"run_ceiling": 120}},
              "bundles": {"bundle-1": {"state": "planned"}}, "operations": {},
              "call_budget": {"total": 0, "ceiling": 120, "actions": {"manual_retry": 0}}}
    run_in_module_context(
        f"const html = routingWindowHtml({json.dumps(record)}, {{ player: 'fixtureowner' }});\n"
        "if (!html.includes('25%') || !html.includes('≈ 308.64 Wood')) throw Error('percent or amount estimate missing');\n"
        "if (!html.includes('1 route · 1 planned bundle')) throw Error('run route and bundle summary missing');",
        entry=STATIC / "routing-window.js",
    )
