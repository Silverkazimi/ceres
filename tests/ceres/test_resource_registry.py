""" (Land 2.0 readiness, Phase 0): the resource registry.

Every list that used to be spelled out by hand is now a view of `ceres/resources.py`. The old literal
values live HERE (never in the code) and each derived view must equal them, in the same order, so the
refactor changes no output. The browser module `ceres/static/resources.js` is generated from the same
table and must match it byte for byte; the browser lists that derive from it are checked by evaluating
the real modules.
"""
from __future__ import annotations

import json
import re

from ceres import resources
from .vm_context import STATIC, run_in_module_context

# The literals exactly as they stood in the code before the registry (tree 78fb0c82a).
OLD = {
    "harvest.TOKENS": ("GRAIN", "WOOD", "STONE", "IRON", "SPS", "RESEARCH", "AURA"),
    "harvest.MATERIAL_COST_SYMBOLS": ("WOOD", "STONE", "IRON"),
    "harvest.COST_SYMBOLS": ("GRAIN", "WOOD", "STONE", "IRON"),
    "harvest.RESOURCE_ID_SYMBOL": {1: "GRAIN", 2: "SPS", 3: "RESEARCH", 5: "WOOD", 6: "STONE", 7: "IRON", 38: "AURA"},
    "harvest.REGION_BALANCE_SYMBOLS": ("GRAIN", "WOOD", "STONE", "IRON", "RESEARCH", "AURA"),
    "routes_wallet.LAND_RESOURCES": ("GRAIN", "WOOD", "STONE", "IRON", "RESEARCH", "AURA"),
    "routes_wallet.SOULBOUND": frozenset({"AURA", "RESEARCH"}),
    "routes_wallet.SWAP_SYMBOLS": ("GRAIN", "WOOD", "STONE", "IRON", "VOUCHER", "SPS"),
    "routes_wallet.ACCOUNT_SWAP_SYMBOLS": ("VOUCHER", "SPS"),
    "harvest_routing.COST_TOKENS": ("GRAIN", "WOOD", "STONE", "IRON"),
    "harvest_routing_runs._CHAINABLE_SALE_SYMBOLS": {"GRAIN", "WOOD", "STONE", "IRON", "RESEARCH", "AURA"},
    "wallet_lp_operations.RESOURCE_SYMBOLS": frozenset({"GRAIN", "WOOD", "STONE", "IRON"}),
    "board._RESOURCE_ABILITY_CODES": frozenset({"GRAIN", "WOOD", "STONE", "IRON", "AURA", "RESEARCH", "SPS"}),
    "worksite_swap._NATURAL_TERRAIN_RESOURCE": {
        "plains": "grain", "river": "grain", "bog": "grain", "lake": "grain",
        "swamp": "wood", "forest": "wood", "tundra": "wood", "jungle": "wood",
        "desert": "stone", "canyon": "stone", "hills": "stone",
        "mountain": "iron", "badlands": "iron", "caldera": "iron",
    },
    "worksite_swap._RATE_PER_PP": {"grain": 0.02, "wood": 0.005, "stone": 0.002, "iron": 0.0005, "aura": 0.0005, "research": 0.0001},
    "worksite_swap.WORKSITE_NAME": {
        "grain": "Grain Farm", "wood": "Logging Camp", "stone": "Quarry", "iron": "Ore Mine",
        "research": "Research Hut", "sps": "Shard Mine", "aura": "Aura Lab",
    },
    "worksite_swap.ALL_TARGETS": frozenset({"grain", "wood", "stone", "iron", "research", "sps", "aura"}),
}

OLD_JS = {
    "WALLET_TABS": ["DEC", "SPS", "VOUCHER", "GRAIN", "WOOD", "STONE", "IRON", "RESEARCH", "AURA"],
    "RESOURCE_ORDER": ["GRAIN", "WOOD", "STONE", "IRON", "AURA", "SPS", "RESEARCH"],
    "HARVEST_COST_ORDER": ["GRAIN", "WOOD", "STONE", "IRON", "AURA", "SPS", "RESEARCH"],
    "TRANSFER_SYMBOLS": ["GRAIN", "WOOD", "STONE", "IRON"],
    # module-private constants (checked through the same graph evaluation)
    "COST_ORDER": ["GRAIN", "WOOD", "STONE", "IRON"],
    "SYMBOLS": ["GRAIN", "WOOD", "STONE", "IRON"],
    "HARVEST_ROUTE_RESOURCES": ["GRAIN", "WOOD", "STONE", "IRON"],
    "ROUTE_RESOURCE_ORDER": ["GRAIN", "WOOD", "STONE", "IRON", "AURA", "SPS", "RESEARCH"],
}
OLD_TOKEN_LABELS = {"dec": "DEC", "grain": "Grain", "wood": "Wood", "stone": "Stone", "iron": "Iron", "aura": "Aura",
                    "sps": "SPS", "research": "Research", "voucher": "Voucher"}
OLD_RESOURCE_ICON = {
    "dec": "land/token-dec.png", "wood": "resources/wood_200.webp", "stone": "resources/stone_200.webp",
    "iron": "resources/iron_200.webp", "aura": "resources/aura_200.webp", "grain": "resources/grain.png",
    "research": "resources/research.png", "sps": "resources/sps.png", "voucher": "resources/voucher.svg",
}


def _get(dotted: str):
    import importlib
    module, name = dotted.split(".", 1)
    return getattr(importlib.import_module(f"ceres.{module}"), name)


def test_every_derived_server_view_equals_its_old_literal_in_value_type_and_order():
    for dotted, old in OLD.items():
        new = _get(dotted)
        assert type(new) is type(old), dotted
        assert new == old, dotted
        if isinstance(old, tuple):
            assert list(new) == list(old), f"{dotted}: order changed"


def test_no_server_module_spells_the_resource_list_out_any_more():
    root = STATIC.parent
    for name in ("harvest.py", "routes_wallet.py", "harvest_routing.py", "harvest_routing_runs.py",
                 "wallet_lp_operations.py", "board.py", "worksite_swap.py"):
        code = "\n".join(line.split("#", 1)[0] for line in (root / name).read_text(encoding="utf-8").splitlines())
        assert not re.search(r"[\"']GRAIN[\"'],\s*[\"']WOOD[\"']", code), name


def test_registry_invariants():
    symbols = [r.symbol for r in resources.RESOURCES]
    assert len(symbols) == len(set(symbols)) and all(s == s.upper() for s in symbols)
    for name, order in resources.ORDERS.items():
        assert len(order) == len(set(order)) and set(order) <= set(symbols), name
    ids = [r.chain_id for r in resources.RESOURCES if r.chain_id is not None]
    assert len(ids) == len(set(ids))
    assert all(r.kind in {"natural", "special", "account", "ore", "magical", "refined"} for r in resources.RESOURCES)
    # soulbound resources never get a DEC pool or a transfer
    assert not any(r.soulbound and (r.poolable or r.transferable) for r in resources.RESOURCES)
    assert set(resources.ORDERS["wallet_tabs"]) == set(symbols)


def test_praetoria_work_divisors_are_the_inverse_of_the_registry_rates():
    # praetoria stays independent of ceres (ceres imports praetoria, never the reverse); this pins the two together.
    from praetoria.land_recommend import WORK_PER_HOUR_DIVISOR_BY_RESOURCE
    assert {key.upper(): 1 / rate for key, rate in resources.rates_per_pp().items()} == WORK_PER_HOUR_DIVISOR_BY_RESOURCE


def test_label_never_raises_and_keeps_unknown_codes_raw():
    assert resources.label("grain") == "Grain" and resources.label("SPS") == "SPS"
    assert resources.label("TIN") == "TIN" and resources.label(None) == ""
    assert resources.symbol_for_chain_id(99) is None and resources.symbol_for_chain_id(38) == "AURA"


def test_generated_browser_module_is_byte_identical_to_the_python_table():
    assert (STATIC / "resources.js").read_text(encoding="utf-8") == resources.render_js()


def test_browser_registry_carries_identical_values_and_order():
    body = """
const out = { resources: CERES_RESOURCES, orders: { wallet_tabs: resourceOrder('wallet_tabs'), harvest: resourceOrder('harvest') } };
process.stdout.write(JSON.stringify(out));
"""
    result = run_in_module_context(body, entry=STATIC / "resources.js")
    data = json.loads(result.stdout)
    assert [r["symbol"] for r in data["resources"]] == [r.symbol for r in resources.RESOURCES]
    for js, py in zip(data["resources"], resources.RESOURCES):
        for field in ("kind", "label", "icon", "chain_id", "worksite_key", "worksite_name", "rate_per_pp",
                      "harvestable", "regional", "poolable", "transferable", "soulbound", "cost_input", "material_input"):
            assert js[field] == getattr(py, field), (py.symbol, field)
    assert data["orders"] == {name: list(order) for name, order in resources.ORDERS.items()}


def _js_values(entry: str, names: list[str]) -> dict:
    body = "process.stdout.write(JSON.stringify({" + ", ".join(f"{n}: [...{n}]" for n in names) + "}));"
    return json.loads(run_in_module_context(body, entry=STATIC / entry).stdout)


def test_browser_lists_derived_from_the_registry_equal_their_old_literals():
    assert _js_values("wallet-resources.js", ["WALLET_TABS"]) == {"WALLET_TABS": OLD_JS["WALLET_TABS"]}
    assert _js_values("harvest-resources.js", ["RESOURCE_ORDER"]) == {"RESOURCE_ORDER": OLD_JS["RESOURCE_ORDER"]}
    assert _js_values("harvest-net.js", ["HARVEST_COST_ORDER"]) == {"HARVEST_COST_ORDER": OLD_JS["HARVEST_COST_ORDER"]}
    assert _js_values("wallet-lp.js", ["TRANSFER_SYMBOLS"]) == {"TRANSFER_SYMBOLS": OLD_JS["TRANSFER_SYMBOLS"]}
    assert _js_values("harvest-modal.js", ["COST_ORDER"]) == {"COST_ORDER": OLD_JS["COST_ORDER"]}
    assert _js_values("routing-setup.js", ["HARVEST_ROUTE_RESOURCES", "ROUTE_RESOURCE_ORDER"]) == {
        "HARVEST_ROUTE_RESOURCES": OLD_JS["HARVEST_ROUTE_RESOURCES"], "ROUTE_RESOURCE_ORDER": OLD_JS["ROUTE_RESOURCE_ORDER"]}
    chainable = run_in_module_context("process.stdout.write(JSON.stringify([...CHAINABLE_SALE_SYMBOLS]));",
                                      entry=STATIC / "harvest-routing.js")
    assert json.loads(chainable.stdout) == ["GRAIN", "WOOD", "STONE", "IRON", "RESEARCH", "AURA"]


def test_routing_setup_symbols_constant_is_unchanged():
    # `SYMBOLS` is a common name, so it is read from routing-setup.js's own scope by a source check instead.
    source = (STATIC / "routing-setup.js").read_text(encoding="utf-8")
    assert "const SYMBOLS = resourceSymbolsWhere('regional', 'poolable');" in source
    assert list(resources.symbols_all("regional", "poolable")) == OLD_JS["SYMBOLS"]


def test_token_labels_and_icons_are_unchanged_and_unknown_codes_stay_raw():
    body = """
const keys = %s;
const labels = Object.fromEntries(keys.map(k => [k, tokenLabel(k)]));
const upper = Object.fromEntries(keys.map(k => [k, tokenLabel(k.toUpperCase())]));
const icons = Object.fromEntries(keys.map(k => [k, tooltipIconPath('resource', k)]));
process.stdout.write(JSON.stringify({ labels, upper, icons, tin: tokenLabel('TIN'), tinIcon: tokenIcon('TIN'), spsp: tokenLabel('SPSP') }));
""" % json.dumps(sorted(OLD_TOKEN_LABELS))
    data = json.loads(run_in_module_context(body, entry=STATIC / "harvest-state.js").stdout)
    assert data["labels"] == OLD_TOKEN_LABELS and data["upper"] == OLD_TOKEN_LABELS
    assert data["icons"] == OLD_RESOURCE_ICON
    assert data["tin"] == "TIN" and data["tinIcon"] == "" and data["spsp"] == "SPSP"
    assert resources.icon_paths() == OLD_RESOURCE_ICON


def test_every_registry_label_is_decorated_in_text():
    # icons.js RESOURCE_MENTION_RE stays a hand-written regex (plurals, acronyms, worksite names); this guard
    # fails the day a resource is added to the registry without its word being added to the regex.
    source = (STATIC / "icons.js").read_text(encoding="utf-8")
    words = re.search(r"const RESOURCE_MENTION_RE = /\\b\(([^)]*)\)", source).group(1).split("|")
    stems = {w.rstrip("?").rstrip("s") for w in words}
    assert {r.label for r in resources.RESOURCES} <= stems | set(words)
