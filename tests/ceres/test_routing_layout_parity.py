"""the browser's bundling (ceres/static/routing-layout.js) must equal the server compiler's grouping
(routing_plan.compile_manifest) for any rule set, so the setup screen never changes when Sign is pressed."""
import json
import random
from decimal import Decimal
from pathlib import Path

from ceres import harvest_routing as routing, routing_plan
from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"
SYMBOLS = ("GRAIN", "WOOD", "STONE", "IRON")
POOLS = {s: routing.Pool(i, s, Decimal("100000000"), Decimal("100000000"), Decimal("1000000"), False)
         for i, s in enumerate(SYMBOLS, 1)}


def _case(rng):
    regions = [f"PR-BRL-{n}" for n in rng.sample(range(1, 200), rng.randint(1, 7))]
    rules, seen = [], set()
    for _ in range(rng.randint(1, 26)):
        region, symbol, kind = rng.choice(regions), rng.choice(SYMBOLS), rng.choice(("sell", "wallet", "half"))
        if (region, symbol, kind) in seen:
            continue
        seen.add((region, symbol, kind))
        rule = {"id": f"id{len(rules)}-{rng.randint(0, 99)}", "source_region": region, "symbol": symbol,
                "quantity_mode": "percent", "quantity": str(rng.choice((10, 25))), "slippage": rng.choice(("2.5", "2.5", "1"))}
        if kind == "sell":
            rule.update(kind="sell", destination_symbol="DEC")
        else:
            rule.update(kind="liquidity", dec_source="wallet" if kind == "wallet" else "swap_half")
        rules.append(rule)
    return regions, rules


def _expected(regions, rules):
    balances = {(r, s): Decimal("4000") for r in regions for s in SYMBOLS}
    balances[(routing.INVENTORY, "DEC")] = Decimal("100000000")
    ledger = routing.Ledger(balances)
    ledger.harvested = {(r, s): Decimal("4000") for r in regions for s in SYMBOLS}
    steps = routing.plan_after_harvest(ledger, rules, POOLS, "2.5")
    manifest = routing_plan.compile_manifest(rules=rules, steps=steps, pools=POOLS, account="player0005", regions=len(regions),
                                             enabled_kinds={"sale_dec", "lp_wallet", "lp_sell_half"})
    return [[{"id": op["id"], "routes": sorted({m["route_id"] for m in op["members"]})} for op in bundle["operations"]]
            for bundle in manifest["bundles"]]


def test_browser_bundling_equals_the_compiler_on_random_rule_sets():
    rng = random.Random(8520)
    for _batch in range(8):  # small batches: the script travels on node's command line
        _run_batch(rng)


def _run_batch(rng):
    cases = []
    for _ in range(10):
        regions, rules = _case(rng)
        if rules:
            cases.append({"rules": rules, "expected": _expected(regions, rules)})
    run_in_module_context("const cases = " + json.dumps(cases) + r"""for (const [index, item] of cases.entries()) {
 const { bundles } = plannedBundles(item.rules);
 const got = bundles.map(bundle => bundle.operations.map(op => ({ id: op.id, routes: [...new Set(op.routeIds)].sort() })));
 if (JSON.stringify(got) !== JSON.stringify(item.expected)) {
 throw new Error('case ' + index + ' differs\nbrowser ' + JSON.stringify(got) + '\nserver ' + JSON.stringify(item.expected));
 }
}
""", entry=STATIC / "routing-layout.js")
