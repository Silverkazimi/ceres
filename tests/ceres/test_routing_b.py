"""Concept B presentation guards for the gated  routing view."""

from pathlib import Path
import json

from ceres import harvest_routing as routing, routing_plan

from tests.ceres.vm_context import run_in_module_context


STATIC = Path(__file__).parents[2] / "ceres" / "static"
FIXTURES = Path(__file__).parent / "fixtures" / "routing-runs"


def _compiled_record():
    source = json.loads((FIXTURES / "b.json").read_text())
    wood = next(route for route in source["model"]["routes"] if route["id"] == "sale_wood")
    pools = {symbol: routing.Pool(index, symbol, routing.amount("1000000"),
                                  routing.amount("1000000"), routing.amount("1000000"), False)
             for index, symbol in enumerate(("WOOD", "GRAIN"), 1)}
    rules, steps = [], []
    for index, amount in enumerate(("900", "900", "989.777")):
        identifier = f"{wood['id']}-{index}"
        region = "PR-PNW-41"
        quote = routing.swap_quote("WOOD", "DEC", amount, pools, wood["slippage"])
        rules.append({"id": identifier, "kind": "sell", "source_region": region,
                      "symbol": "WOOD", "destination_symbol": "DEC", "quantity": amount,
                      "slippage": wood["slippage"]})
        steps.append({"id": identifier, "kind": "sell", "status": "ready", "source_region": region,
                      "destination_region": routing.INVENTORY, "symbol": "WOOD",
                      "destination_symbol": "DEC", "quantity": amount,
                      "slippage": wood["slippage"], "quote": quote.public()})
    grain_amount = "1000"
    grain_quote = routing.swap_quote("GRAIN", "DEC", grain_amount, pools, "2.5")
    rules.append({"id": "sale_grain", "kind": "sell", "source_region": "PR-BRL-65",
                  "symbol": "GRAIN", "destination_symbol": "DEC", "quantity": grain_amount,
                  "slippage": "2.5"})
    steps.append({"id": "sale_grain", "kind": "sell", "status": "ready",
                  "source_region": "PR-BRL-65", "destination_region": routing.INVENTORY,
                  "symbol": "GRAIN", "destination_symbol": "DEC", "quantity": grain_amount,
                  "slippage": "2.5", "quote": grain_quote.public()})
    manifest = routing_plan.compile_manifest(rules=rules, steps=steps, pools=pools,
                                             account="fixtureowner", regions=2,
                                             enabled_kinds={"sale_dec"})
    assert len(manifest["bundles"]) == 1
    assert sorted(len(operation["members"]) for operation in manifest["bundles"][0]["operations"]) == [1, 3]
    record = {"state": "ready", "manifest": manifest,
              "bundles": {bundle["id"]: {"state": "checking", "tx_id": "fixture-tx"}
                          for bundle in manifest["bundles"]},
              "operations": {operation["id"]: {"state": "checking"}
                             for bundle in manifest["bundles"] for operation in bundle["operations"]},
              "call_budget": {"total": 38, "ceiling": 120, "actions": {"manual_retry": 2}}}
    return record


def test_compiled_r0_sale_members_render_one_flow_per_operation():
    record = _compiled_record()
    run_in_module_context(rf"""
const record = {json.dumps(record)};
const html = routingWindowHtml(record, {{ player: 'fixtureowner' }});
const operations = record.manifest.bundles[0].operations;
if ((html.match(/class="r3-flowline"/g) || []).length !== operations.length)
  throw Error('flow rows do not match compiler operations');
if ((html.match(/class="r3-flow-source"/g) || []).length !== operations.length)
  throw Error('connected flow does not have one aggregate source per operation');
const flowSources = [...html.matchAll(/<div class="r3-flow-sources">(.*?)<\/div><span class="r3-arrow"/gs)];
if (flowSources.length !== operations.length) throw Error('operation source groups missing');
for (const [index, operation] of operations.entries()) {{
  const source = flowSources[index][1];
  const total = operation.members.reduce((sum, member) => sum + Number(member.source_amount), 0)
    .toLocaleString('en-US', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
  if ((source.match(/class="r3-flow-source"/g) || []).length !== 1
      || !source.includes(`<span>${{total}}`))  // a single share adds ` · 25%` after the amount
    throw Error('flow source is not the aggregate amount: ' + operation.id);
}}
// only an operation that merges several shares lists them in a member row; a single share is in its flow box.
const merged = operations.filter(operation => operation.members.length > 1).flatMap(operation => operation.members);
if ((html.match(/class="r3-member(?:\s|")/g) || []).length !== merged.length)
  throw Error('member cells missing');
for (const member of merged) {{
  const operation = operations.find(item => item.members.includes(member));
  // member cells use the page's number format and resource name ("3,606.64 Wood"), like the rows above.
  const label = `${{Number(member.source_amount).toLocaleString('en-US', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }})}} ${{operation.source_symbol[0]}}${{operation.source_symbol.slice(1).toLowerCase()}}`;
  const cell = html.match(new RegExp(`<div class="r3-member[^>]*data-r3-member="${{member.route_id}}"[^>]*>(.*?)</div>`))?.[1] || '';
  if (!cell.includes('ch-plot-badge') || !cell.includes('class="r3-art"') || !cell.includes(label))
    throw Error('member plaque, resource icon, or amount missing: ' + label);
}}
if ((html.match(/class="r3-summary-status"/g) || []).length !== 1)
  throw Error('status segment count (: the status lives in the summary row)');
if (!html.includes('Metered routing checks 38/120') || />Region /.test(html) || !html.includes('ch-plot-badge'))
  throw Error('status or plaque text');
if (!/Wood<\/span><span class="r3-route-arrow">→<\/span><span class="r3-to">(<img[^>]*>)?DEC/.test(html) || !/Grain<\/span><span class="r3-route-arrow">→<\/span><span class="r3-to">(<img[^>]*>)?DEC/.test(html) || html.includes('…')) // resource names, not codes; : DEC carries its icon
  throw Error('full step names missing');
// every route of the shown approval is marked.
if ((html.match(/r3-route-chip is-selected/g) || []).length !== new Set(operations.flatMap(operation => operation.members.map(member => member.route_id))).size)
  throw Error('not every route of the shown approval is highlighted');
if (!html.includes('r3-status-spinner') || !html.includes('Metered routing checks 38/120'))
  throw Error('run status has no reserved spinner slot');
record.bundles[record.manifest.bundles[0].id].state = 'outcome_unknown';
const unknown = routingWindowHtml(record, {{ player: 'fixtureowner' }});
if ((unknown.match(/class="r3-terminal"/g) || []).length !== 1
    || (unknown.match(/class="r3-summary-status"/g) || []).length !== 1
    || !unknown.includes('Reservation retained · dependents blocked'))
  throw Error('unknown state is outside terminal and status strips');
""", entry=STATIC / "routing-window.js")
    css = (STATIC / "ceres.css").read_text()
    assert ".r3-route-name { flex:none; white-space:nowrap; }" in css
    assert "block-size:80px; min-block-size:80px" in css
    assert ".r3-window .r3-stage-status { box-sizing:border-box; min-block-size:44px; }" in css
    assert ".r3-window .r3-members { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); }" in css
    assert ".r3-route-index .r3-route-name { flex:1 1 auto; min-inline-size:0;" in css


def test_current_bundle_owns_the_only_member_grid_and_status_bar():
    run_in_module_context(r"""
const first = { id: 'bundle-1', wave: 0, approval: 1, operations: [{
  id: 'operation-1', source_region: 'land-PNW-041', source_symbol: 'WOOD',
  destination_symbol: 'DEC', pool: 'WOOD', protected_minimum: '11.7',
  members: [
    { route_id: 'route-one', source_amount: '6', protected_minimum: '5.4' },
    { route_id: 'route-two', source_amount: '4', protected_minimum: '3.6' },
  ],
}] };
const later = { id: 'bundle-2', wave: 0, approval: 2, operations: [{
  id: 'operation-2', source_region: 'land-BRL-065', source_symbol: 'STONE',
  destination_symbol: 'DEC', pool: 'STONE',
  members: [{ route_id: 'route-later', source_amount: '8', protected_minimum: '4' }],
}] };
const record = { state: 'ready', manifest: { bundles: [first, later], approval_counts: { routing: 4 }, call_budget: { run_ceiling: 120 } },
  bundles: { 'bundle-1': { state: 'checking', tx_id: 'tx' }, 'bundle-2': { state: 'planned' } },
  operations: { 'operation-1': { state: 'checking' } },
  call_budget: { total: 38, ceiling: 120, actions: { manual_retry: 2 } } };
const view = routingBundleView(record);
if (view.members.length !== 2 || view.members.some(item => item.member.route_id === 'route-later'))
  throw Error('member view includes another bundle');
const html = routingWindowHtml(record, { player: 'fixtureowner' });
if ((html.match(/class="r3-summary-status"/g) || []).length !== 1) throw Error('status segment count (: the status lives in the summary row)');
const memberGrid = html.match(/<div class="r3-members"[^>]*>(.*?)<\/div><div class="r3-stage-status">/s)?.[1];
if (!memberGrid || !memberGrid.includes('route-one') || !memberGrid.includes('route-two')
    || memberGrid.includes('route-later')) throw Error('wrong bundle member grid');
if (/>Region /.test(html) || !html.includes('ch-plot-badge') || !html.includes('Metered routing checks 38/120'))
  throw Error('plaque or budget text');
if (!html.includes('Approval 1 of 4')) throw Error('approval denominator did not use manifest routing count');
// the footer holds Stop and Sign; "Review current bundle" is gone.
if (!html.includes('Stop remaining routes') || html.includes('Review current bundle'))
  throw Error('footer actions missing');
if (html.includes('data-r3="sign"')) throw Error('checking bundle offers signing');
""", entry=STATIC / "routing-window.js")


def test_setup_uses_saved_rules_and_never_invents_a_minimum():
    run_in_module_context(r"""
const __seed1 = { rules: [{
  id: 'saved-1', kind: 'sell', source_region: 'land-PNW-041', symbol: 'WOOD',
  destination_symbol: 'DEC', quantity_mode: 'amount', quantity: '6',
}, {
  id: 'saved-2', kind: 'sell', source_region: 'land-PNW-041', symbol: 'WOOD',
  destination_symbol: 'DEC', quantity_mode: 'amount', quantity: '4',
}, {
  id: 'saved-3', kind: 'sell', source_region: 'land-PNW-041', symbol: 'WOOD',
  destination_symbol: 'DEC', quantity_mode: 'amount', quantity: '3',
}] };
const setup = createRoutingSetup({ initialRules: __seed1.rules, player: 'fixtureowner', scope: { kind: 'all' },
  regions: [{ region_uid: 'land-PNW-041', region_number: 41 }],
  enabledKinds: ['sale_dec'], signingEnabled: true });
const html = setup.html();
if (!html.includes('WOOD') || !html.includes('data-r3-field="source_region"')
    || !html.includes('data-r3-field="symbol"')) throw Error('saved route missing');
if (!/class="ch-plot-badge[^\"]*"[^>]*>041<\/span>/.test(html) || !html.includes('Protected wallet DEC'))
  throw Error('setup plaque or account-wide protected wallet summary missing');
if (!html.includes('value="0"') || html.includes('Protected minimum set by fresh review'))
  throw Error('protected wallet DEC is not an account-wide setting');
if ((html.match(/class="r3-flowline"/g) || []).length !== 1
    || (html.match(/class="r3-flow-source"/g) || []).length !== 1
    || (html.match(/class="r3-member"/g) || []).length !== 3)
  throw Error('setup does not preview grouped planned shares');
const plannedSource = html.match(/<div class="r3-flow-sources">(.*?)<\/div><span class="r3-arrow"/s)?.[1] || '';
if (!plannedSource.includes('<span>13.00</span>') || plannedSource.includes('6.00'))
  throw Error('planned flow does not show one aggregate amount');
for (const [id, amount] of [['saved-1', '6.00'], ['saved-2', '4.00'], ['saved-3', '3.00']]) {
  const cell = html.match(new RegExp(`<div class="r3-member" data-r3-member="${id}">(.*?)</div>`))?.[1] || '';
  if (!cell.includes('ch-plot-badge') || !cell.includes('class="r3-art"') || !cell.includes(`${amount} Wood`))
    throw Error('planned member plaque, resource, or amount missing: ' + id);
}
if (!html.includes('value=""'))
  throw Error('setup amounts do not use run formatting');
if (!html.includes('<small>Harvest first; approvals follow confirmed receipts.</small>')
    || /<div class="r3-actions">[^<]*<span>Harvest first/.test(html))
  throw Error('receipt hint escaped into footer actions');
setup.change('quantity', 0, '1,000.001');
if (!setup.html().includes('value="1,000.001"'))
  throw Error('formatted edit lost the third decimal place');
""", entry=STATIC / "routing-setup.js")
    css = (STATIC / "ceres.css").read_text()
    assert ".r3-edit .csel-value img { inline-size:20px; block-size:20px;" in css
    assert ".r3-edit .csel-value .ch-plot-badge" in css
    assert ".r3-edit { grid-template-columns:minmax(0,1.2fr)" in css


def test_off_switch_and_fetch_boundaries_remain_intact():
    modal = (STATIC / "harvest-modal.js").read_text()
    setup = (STATIC / "routing-setup.js").read_text()
    window = (STATIC / "routing-window.js").read_text()
    run = (STATIC / "routing-run.js").read_text()
    assert "if (M.v2Kinds.length) return" in modal
    assert "if (!HARVEST_ROUTING_ENABLED) return '';" in modal
    assert "panel.classList.toggle('is-r3', !!M.v2);" in modal
    assert "field === 'source_region'" in modal
    assert setup.count("fetch(") == 3
    assert "/api/harvest/routing/v2/kinds" in setup
    assert "/api/harvest/routing/preview" in setup
    assert "fetch(" not in window
    assert run.count("fetch(") == 1
    assert "const ROOT = '/api/harvest/routing/v2/';" in run
