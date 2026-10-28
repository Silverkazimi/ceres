""" (Land 2.0 readiness, Phase 0): the one resource registry.

Every resource list Ceres used to spell out by hand (harvest tokens, cost symbols, pool symbols,
worksite names, rates, chain ids, wallet tabs, icons, labels) is a view derived from `RESOURCES`
below. Today the table holds exactly the nine symbols Ceres already knew, in the order the old
lists used, so every derived view equals its old literal (pinned in
`tests/ceres/test_resource_registry.py`). When Land 2.0 ships (Tin, Copper, Rhodium, Vanadium,
the magical resources, ...) a new symbol is one new row here plus its place in `ORDERS`.

The browser gets the same table from `ceres/static/resources.js`, which is GENERATED from this
module (`python3 -m ceres.resources --write`) and checked byte-for-byte by the test, so the two
can never drift. Ceres has no build step: the generated file is committed.

Kinds today: `natural` (Grain, Wood, Stone, Iron), `special` (Research, Aura: worksite products
that stay in their region), `account` (DEC, SPS, Voucher: account-wallet tokens). Land 2.0 adds
`ore`, `magical` and `refined`; none exists yet.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class Resource:
    symbol: str                      # upper-case, as the game API spells it
    kind: str                        # natural | special | account (later: ore | magical | refined)
    label: str                       # display name (tokenLabel in the browser)
    icon: str | None                 # asset path under ASSET_BASE, None = no icon
    chain_id: int | None = None      # resource_id in /land/resources/history and worksite rows
    worksite_key: str | None = None  # worksite target key ("grain" ... "aura", "sps")
    worksite_name: str | None = None
    rate_per_pp: float | None = None  # per PP per hour; None = no fixed rate (SPS is a pool share)
    terrains: tuple[str, ...] = ()   # natural terrains whose native worksite produces it
    harvestable: bool = False        # produced by a worksite: a harvest token
    regional: bool = False           # balance kept per land region
    poolable: bool = False           # has a DEC pool in /land/liquidity/pools
    transferable: bool = False       # player-to-player transfer proven ()
    soulbound: bool = False          # never leaves its region
    cost_input: bool = False         # a worksite running cost (grain_required/wood_required/...)
    material_input: bool = False     # a `resource_recipe` material input (Grain is priced separately)


# Canonical order: the four natural resources, then the pool tokens, then the soulbound worksite
# products, then DEC. Filtering this order by a flag reproduces every old server tuple.
RESOURCES: tuple[Resource, ...] = (
    Resource("GRAIN", "natural", "Grain", "resources/grain.png", chain_id=1, worksite_key="grain",
             worksite_name="Grain Farm", rate_per_pp=0.02, terrains=("plains", "river", "bog", "lake"),
             harvestable=True, regional=True, poolable=True, transferable=True, cost_input=True),
    Resource("WOOD", "natural", "Wood", "resources/wood_200.webp", chain_id=5, worksite_key="wood",
             worksite_name="Logging Camp", rate_per_pp=0.005, terrains=("swamp", "forest", "tundra", "jungle"),
             harvestable=True, regional=True, poolable=True, transferable=True, cost_input=True, material_input=True),
    Resource("STONE", "natural", "Stone", "resources/stone_200.webp", chain_id=6, worksite_key="stone",
             worksite_name="Quarry", rate_per_pp=0.002, terrains=("desert", "canyon", "hills"),
             harvestable=True, regional=True, poolable=True, transferable=True, cost_input=True, material_input=True),
    Resource("IRON", "natural", "Iron", "resources/iron_200.webp", chain_id=7, worksite_key="iron",
             worksite_name="Ore Mine", rate_per_pp=0.0005, terrains=("mountain", "badlands", "caldera"),
             harvestable=True, regional=True, poolable=True, transferable=True, cost_input=True, material_input=True),
    Resource("VOUCHER", "account", "Voucher", "resources/voucher.svg", poolable=True),
    Resource("SPS", "account", "SPS", "resources/sps.png", chain_id=2, worksite_key="sps",
             worksite_name="Shard Mine", harvestable=True, poolable=True),
    Resource("RESEARCH", "special", "Research", "resources/research.png", chain_id=3, worksite_key="research",
             worksite_name="Research Hut", rate_per_pp=0.0001, harvestable=True, regional=True, soulbound=True),
    Resource("AURA", "special", "Aura", "resources/aura_200.webp", chain_id=38, worksite_key="aura",
             worksite_name="Aura Lab", rate_per_pp=0.0005, harvestable=True, regional=True, soulbound=True),
    Resource("DEC", "account", "DEC", "land/token-dec.png"),
)

BY_SYMBOL: dict[str, Resource] = {r.symbol: r for r in RESOURCES}

# Display orders that are not a flag filter of the canonical order (the browser's own today).
ORDERS: dict[str, tuple[str, ...]] = {
    # wallet-resources.js WALLET_TABS: account tokens first, then the region resources.
    "wallet_tabs": ("DEC", "SPS", "VOUCHER", "GRAIN", "WOOD", "STONE", "IRON", "RESEARCH", "AURA"),
    # harvest-resources.js RESOURCE_ORDER, harvest-net.js HARVEST_COST_ORDER, routing-setup.js ROUTE_RESOURCE_ORDER.
    "harvest": ("GRAIN", "WOOD", "STONE", "IRON", "AURA", "SPS", "RESEARCH"),
}


def symbols(where: str | None = None, *, kind: str | None = None) -> tuple[str, ...]:
    """Symbols in canonical order, optionally only those whose boolean flag `where` is set."""
    return tuple(r.symbol for r in RESOURCES
                 if (where is None or getattr(r, where)) and (kind is None or r.kind == kind))


def symbols_all(*flags: str) -> tuple[str, ...]:
    """Symbols whose every named flag is set, canonical order."""
    return tuple(r.symbol for r in RESOURCES if all(getattr(r, f) for f in flags))


def label(symbol: Any) -> str:
    """Display name; an unknown symbol keeps its raw code (never raises)."""
    raw = str(symbol or "")
    entry = BY_SYMBOL.get(raw.upper())
    return entry.label if entry else raw


def resource_id_symbol() -> dict[int, str]:
    return {r.chain_id: r.symbol for r in RESOURCES if r.chain_id is not None}


def symbol_for_chain_id(resource_id: Any) -> str | None:
    """Symbol for a chain resource id; None for an unknown id (callers keep their own fallback)."""
    return resource_id_symbol().get(resource_id)


def worksite_names() -> dict[str, str]:
    return {r.worksite_key: r.worksite_name for r in RESOURCES if r.worksite_key and r.worksite_name}


def rates_per_pp() -> dict[str, float]:
    return {r.worksite_key: r.rate_per_pp for r in RESOURCES if r.worksite_key and r.rate_per_pp is not None}


def natural_terrain_resource() -> dict[str, str]:
    return {terrain: r.worksite_key for r in RESOURCES if r.worksite_key for terrain in r.terrains}


def icon_paths() -> dict[str, str]:
    """{lower-case symbol: asset path}, as icons.js RESOURCE_ICON keys them."""
    return {r.symbol.lower(): r.icon for r in RESOURCES if r.icon}


# Column names on /land/liquidity/region/{player} rows that are not resources.
REGION_ROW_META = frozenset({"player", "name", "uid", "plots_owned", "region_number", "region_name", "region_uid"})


def unknown_resource_columns(row: dict[str, Any], known: Iterable[str]) -> dict[str, float]:
    """: numeric columns on a region-balance row that are neither a known resource nor row metadata.
    Kept (upper-cased) so a new Land 2.0 balance column is carried instead of silently dropped."""
    known_lower = {s.lower() for s in known}
    out: dict[str, float] = {}
    for key, value in row.items():
        if not isinstance(key, str) or key in REGION_ROW_META or key.lower() in known_lower:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(number):
            out[key.upper()] = number
    return out


# browser module

JS_PATH = Path(__file__).resolve().parent / "static" / "resources.js"
_JS_FIELDS = ("symbol", "kind", "label", "icon", "chain_id", "worksite_key", "worksite_name", "rate_per_pp",
              "harvestable", "regional", "poolable", "transferable", "soulbound", "cost_input", "material_input")


def _js_value(value: Any) -> str:
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return json.dumps(value)


def render_js() -> str:
    """The exact text of ceres/static/resources.js."""
    lines = [
        # No ticket ids in this header: the public export rewrites ids in comments, and this text must stay
        # byte-identical in both repositories for the parity test.
        "// GENERATED from ceres/resources.py by `python3 -m ceres.resources --write` -- do not hand-edit.",
        "// The one resource registry for the browser; tests/ceres/test_resource_registry.py checks this file",
        "// byte-for-byte against the Python table, so server and browser lists can never drift.",
        "export const CERES_RESOURCES = Object.freeze([",
    ]
    for r in RESOURCES:
        data = asdict(r)
        body = ", ".join(f"{name}: {_js_value(data[name])}" for name in _JS_FIELDS)
        lines.append(f"  Object.freeze({{ {body} }}),")
    lines.append("]);")
    lines.append("const CERES_RESOURCE_ORDERS = Object.freeze({")
    for name, order in ORDERS.items():
        lines.append(f"  {name}: Object.freeze([{', '.join(_js_value(s) for s in order)}]),")
    lines.append("});")
    lines += [
        "// A fresh array each call, so a caller may treat its list as its own (as the old literals were).",
        "export function resourceOrder(name) { return [...(CERES_RESOURCE_ORDERS[name] || [])]; }",
        "// Symbols in canonical order whose every named flag is true.",
        "export function resourceSymbolsWhere(...flags) { return CERES_RESOURCES.filter(r => flags.every(f => r[f])).map(r => r.symbol); }",
        "// {lower-case symbol: value of `fieldName`} for the entries where it is set.",
        "export function resourceFieldMap(fieldName) { return Object.fromEntries(CERES_RESOURCES.filter(r => r[fieldName] != null).map(r => [r.symbol.toLowerCase(), r[fieldName]])); }",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    text = render_js()
    if "--write" in argv:
        JS_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {JS_PATH}")
        return 0
    if "--check" in argv:
        ok = JS_PATH.exists() and JS_PATH.read_text(encoding="utf-8") == text
        print("resources.js up to date" if ok else "resources.js is stale: run python3 -m ceres.resources --write")
        return 0 if ok else 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
