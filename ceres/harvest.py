""" harvest -- pure projection, accrual and affordability (no network, no signing).

Formulas:
- hours counted h = min(hours since last harvest, 168); production past 168 h is lost.
- `projected_amount_received` is NET of the 10% tax; `rewards_per_hour` is the GROSS slope.
  net(t) = net_at_anchor + gross_per_hour * (1 - tax) * (min(h(t),168) - min(h0,168)).
- grain/hour = base PP after cap * 0.01 * (1 + discounts); recipe inputs/hour = qty * base PP
  after cap (no discount). Accrued costs come from the API anchor and advance at those slopes
  only while h < 168; past the cap the API's own figure is shown unchanged (plan §4: never
  silently assume infinite costs or free excess hours).
- totem fragment chance = 0.000007 * (base_pp/1000) * h; Labor's Luck = 0.000003593 * (base_pp/1000) * h.

The anchor instant is `last_action_time + hours_since_last_op` -- the moment the API computed
that row -- so a stale snapshot row still advances from the right place.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

CAP_HOURS = 168.0
DEFAULT_TAX_RATE = 0.10
TOKENS = ("GRAIN", "WOOD", "STONE", "IRON", "SPS", "RESEARCH", "AURA")
# the three material inputs a worksite's `resource_recipe` can name today.
# GRAIN is deliberately excluded -- it is priced off `grain_req_per_hour`, not the recipe.
MATERIAL_COST_SYMBOLS = ("WOOD", "STONE", "IRON")
COST_SYMBOLS = ("GRAIN", "WOOD", "STONE", "IRON")
FRAGMENT_RATE = 0.000007
LABORS_LUCK_RATE = 0.000003593
TAX_COLLECTION_WORKSITES = frozenset({"keep", "castle"})
# Chain resource ids seen in /land/resources/history and worksite rows.
RESOURCE_ID_SYMBOL = {1: "GRAIN", 2: "SPS", 3: "RESEARCH", 5: "WOOD", 6: "STONE", 7: "IRON", 38: "AURA"}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):  # S10 / L3-05: an integer too large for a float is malformed, not an error
        return None
    return out if math.isfinite(out) else None


def _worksite_token(worksite: dict[str, Any], facts: dict[str, Any]) -> str | None:
    for raw in (worksite.get("token_symbol"), worksite.get("resource")):
        token = str(raw or "").upper()
        if token in TOKENS:
            return token
    project = str(worksite.get("project_type") or "").lower()
    if project.startswith("worksite_"):
        token = project[len("worksite_"):]
        if token.endswith("_construction"):
            token = token[:-len("_construction")]
        if token.upper() in TOKENS:
            return token.upper()
    fallback = str(facts.get("resource") or "").upper()
    return fallback or None


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    try:
        return stamp.astimezone(timezone.utc)
    except (OverflowError, ValueError):  # S10 / L3-05: 9999-12-31T23:59:59-01:00 overflows on normalisation
        return None


def iso(stamp: datetime) -> str:
    return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def region_number(region_uid: Any) -> str:
    text = str(region_uid or "")
    tail = text.rsplit("-", 1)[-1]
    return tail.zfill(3) if tail.isdigit() else text


def _tax_rate(facts: dict[str, Any]) -> tuple[float, bool]:
    rate = _num(facts.get("tax_rate"))
    if rate is None or rate < 0 or rate >= 1:
        return DEFAULT_TAX_RATE, False
    return rate, True


def is_tax_collection_plot(facts: dict[str, Any], worksite: dict[str, Any] | None) -> bool:
    kind = str(facts.get("worksite_type") or (worksite or {}).get("worksite_type") or "").strip().lower()
    if kind in TAX_COLLECTION_WORKSITES:
        return True
    project = str((worksite or {}).get("project_type") or "").lower()
    return any(word in project for word in TAX_COLLECTION_WORKSITES)


def construction_facts(worksite: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    """: what is being built and when it is ready, for the harvest box's countdown.
    Read off the fresh active-project row first, then the capture's worksite_facts."""
    from .worksite_swap import WORKSITE_NAME
    pick = lambda k: worksite.get(k) if worksite.get(k) is not None else fallback.get(k)
    project_type = str(pick("project_type") or "")
    to = project_type[len("worksite_"):-len("_construction")] if project_type.endswith("_construction") else None
    to = to or None
    return {
        "to": to,
        "to_label": WORKSITE_NAME.get(to) if to else None,
        "project_id": pick("project_id") if pick("project_id") is not None else pick("id"),
        "started_at": pick("start_date"),
        "ready_at": pick("projected_end"),
        "pp_required": _num(pick("pp_required")),
        "pp_spent": _num(pick("pp_spent")),
        "hours_to_completion": _num(pick("hours_to_completion")),
        "projected_end": pick("projected_end"),
    }


def hourly_resource_costs(worksite_facts: dict[str, Any] | None,
                           staking_facts: dict[str, Any] | None) -> dict[str, float] | None:
    """Projected Wood/Stone/Iron cost per hour for one plot's current worksite ( plan §3).

    Same math `project_harvest_plot` already runs for its recipe costs: each
    `resource_recipe` entry's `qty` times `staking_facts.total_base_pp_after_cap`, SUMMED per
    symbol when a recipe carries more than one entry of it (). `project_harvest_plot`'s
    own `costs` list keeps one row per recipe entry instead (its own per-entry qty * base_pp),
    so the two surfaces read the same formula but at different granularity -- this function's
    per-symbol total always equals the sum of that symbol's individual `project_harvest_plot`
    rows.

    Returns None -- never a partial or zero-filled dict -- when the underlying facts cannot be
    trusted: missing/non-dict `worksite_facts` or `staking_facts`, a `resource_recipe` that
    isn't a list, an empty recipe (a base-resource worksite genuinely has none), a missing
    `total_base_pp_after_cap`, or a recipe with no usable WOOD/STONE/IRON entry at all. A
    malformed individual entry (not a dict, an unsupported symbol, a non-numeric qty) is
    dropped, matching `project_harvest_plot`'s tolerant per-entry handling. A recipe entry
    with `qty` exactly 0 IS reported, as 0.0 -- `project_harvest_plot`'s own recipe loop keeps
    a qty-0 entry (only `qty is None` drops one there), so dropping it here instead would let
    the two surfaces diverge on that exact plot ( parity finding). A genuinely zero
    measured cost (zero effective base PP against a real recipe) is likewise reported as 0.0 --
    both are known values, never invented for missing data.
    """
    if not isinstance(worksite_facts, dict) or not isinstance(staking_facts, dict):
        return None
    recipe = worksite_facts.get("resource_recipe")
    if not isinstance(recipe, list) or not recipe:
        return None
    base_pp = _num(staking_facts.get("total_base_pp_after_cap"))
    if base_pp is None:
        return None
    costs: dict[str, float] = {}
    for entry in recipe:
        if not isinstance(entry, dict):
            continue
        symbol = str(entry.get("symbol") or "").upper()
        qty = _num(entry.get("qty"))
        if symbol not in MATERIAL_COST_SYMBOLS or qty is None:
            continue
        # a recipe with two entries of one symbol must SUM their costs here -- this
        # used to overwrite with the last entry's value (dict assignment, not accumulation)
        # under-reporting the board's own per-symbol total. project_harvest_plot's `costs` list
        # (see that function) still keeps one row PER ENTRY, in that entry's own qty * base_pp.
        costs[symbol] = costs.get(symbol, 0.0) + qty * base_pp
    return costs or None


def project_harvest_plot(plot: dict[str, Any], worksite_override: dict[str, Any] | None = None,
                         *, observed_at: str | None = None) -> dict[str, Any]:
    """Compact, display-ready harvest state for one plot (plan §6.2 HarvestPlotState).

    `plot` is a report plot (`facts`, `staking_facts`, `worksite_facts`); `worksite_override`
    is a fresh `/land/projects/deed/{deed}/active` row that replaces only the worksite part.
    Unknown stays null with a reason; it is never defaulted to zero.
    """
    facts = plot.get("facts") or {}
    staking = plot.get("staking_facts") or {}
    worksite = worksite_override if worksite_override is not None else (plot.get("worksite_facts") or {})
    deed_uid = plot.get("deed_uid") or worksite.get("deed_uid")
    tax_rate, tax_known = _tax_rate(facts)
    out: dict[str, Any] = {
        "deed_uid": deed_uid,
        "plot": facts.get("plot"),
        "region_uid": facts.get("region") or worksite.get("region_uid"),
        "worksite_type": facts.get("worksite_type") or worksite.get("worksite_type") or None,
        "token": None,
        "state": "unavailable",
        "issues": [],
        "cap_hours": CAP_HOURS,
        "tax_rate": tax_rate,
        "tax_rate_known": tax_known,
        "source": "fresh" if worksite_override is not None else "board",
        "observed_at": observed_at,
    }
    if is_tax_collection_plot(facts, worksite):
        out["state"] = "tax_collection"
        return out
    if facts.get("is_construction") or worksite.get("is_construction"):
        # the game sets is_construction on every uncleared plot too (no worksite, nothing
        # started). Only a build with a start date or a named target is "Building"; the rest fall
        # through to no_project.
        construction = construction_facts(worksite, plot.get("worksite_facts") or {})
        if construction.get("started_at") or construction.get("to"):
            out["state"] = "construction"
            out["construction"] = construction
            # a finished build is completed by feeding workers Grain, never by reloading the
            # board -- `worksite` is `worksite_override` (the fresh /active read) when one was passed
            # so this is the same live-facts check `ceres/board.py` runs at page load, re-run here for
            # a per-plot refresh so a countdown expiring while the page is open also picks it up.
            from .worksite_swap import ready_worksite_change
            out["worksite_completion"] = ready_worksite_change(worksite, staking.get("total_work_per_hour"))
            return out
    if not worksite or not worksite.get("is_active", True):
        out["state"] = "no_project"
        return out
    token = _worksite_token(worksite, facts)
    out["token"] = token
    if token not in TOKENS:
        out["issues"].append("unsupported_token")
        return out
    last_action = parse_utc(worksite.get("last_action_time"))
    h0 = _num(worksite.get("hours_since_last_op"))
    gross_per_hour = _num(worksite.get("rewards_per_hour"))
    net0 = _num(worksite.get("projected_amount_received"))
    if last_action is None or h0 is None or h0 < 0 or h0 > 24 * 365 * 100 or gross_per_hour is None or net0 is None:  # S10 / L3-07: absurd hours are no anchor
        out["issues"].append("missing_anchor")
        return out
    try:
        anchor = last_action + timedelta(hours=h0)
    except (OverflowError, ValueError):  # S10 round 2 / L3-05: a parseable but absurd timestamp is no anchor
        out["issues"].append("missing_anchor")
        return out
    base_pp = _num(staking.get("total_base_pp_after_cap"))
    grain_per_hour = _num(worksite.get("grain_req_per_hour"))
    costs: list[dict[str, Any]] = []
    if grain_per_hour is not None:
        costs.append({"symbol": "GRAIN", "accrued": _num(worksite.get("grain_required")), "per_hour": grain_per_hour})
    recipe = worksite.get("resource_recipe") if isinstance(worksite.get("resource_recipe"), list) else []
    # this loop keeps one row PER RECIPE ENTRY, in the entry's own qty
    # never `hourly_resource_costs()`'s per-SYMBOL sum. `ceres/board.py::_plot_row` calls that
    # shared helper directly for its own flat `wood_cost_per_hour`/etc. sort keys (a genuine
    # total-per-symbol reading), so the two surfaces still share one formula (qty * base_pp) and
    # cannot drift on a normal one-entry-per-symbol recipe. But a recipe with TWO entries of the
    # same symbol used to have every one of that symbol's rows here read the helper's SUMMED
    # value (both entries reporting the combined total instead of their own share) -- fixed by
    # computing each entry's own `qty * base_pp` directly, matching this function's own
    # `costs` contract (one entry per recipe line, not one per symbol).
    for entry in recipe:
        if not isinstance(entry, dict):  # S10 / L3-07: a malformed recipe entry is reported, never raised
            out["issues"].append("unsupported_recipe")
            continue
        symbol = str((entry or {}).get("symbol") or "").upper()
        qty = _num((entry or {}).get("qty"))
        if symbol not in COST_SYMBOLS or qty is None or qty < 0:
            out["issues"].append("unsupported_recipe")
            continue
        per_hour = qty * base_pp if base_pp is not None else None
        if per_hour is not None and not math.isfinite(per_hour):  # S10 / L3-05: an infinite slope is malformed data
            out["issues"].append("unsupported_recipe")
            continue
        costs.append({"symbol": symbol, "accrued": _num(worksite.get(f"{symbol.lower()}_required")), "per_hour": per_hour})
    discount = (_num(staking.get("grain_food_discount")) or 0.0) + (_num(staking.get("lite_food_discount")) or 0.0)
    kind = str(out["worksite_type"] or "").lower()
    frag_elig = "eligible" if "research" in kind else "ineligible"
    ll_flag = staking.get("has_labors_luck")
    # S5 / L3-01: a cost the game did not report is UNKNOWN, never "no cost"; callers must not read missing as covered.
    # S10 / L3-01 (completeness): every cost the worksite has must be present and numeric -- the accrued grain, each recipe entry's
    # quantity and accrued amount, and the PP the per-hour slope needs. An empty recipe and an explicit zero are real answers; absent or
    # malformed data is not.
    costs_known = (grain_per_hour is not None and grain_per_hour >= 0 and isinstance(worksite.get("resource_recipe"), list)
                   and all(isinstance(entry, dict) for entry in worksite.get("resource_recipe") or []))
    if costs_known and grain_per_hour > 0 and _num(worksite.get("grain_required")) is None:
        costs_known = False
    raw_grain = worksite.get("grain_required")
    if costs_known and raw_grain is not None and _num(raw_grain) is None:  # S10 / L3-01: supplied but malformed accrued grain is unknown, even at a zero rate
        costs_known = False
    if costs_known and any(isinstance(e, dict) and str(e.get("symbol") or "").upper() == "GRAIN" for e in worksite.get("resource_recipe") or []) and _num(raw_grain) is None:  # a GRAIN recipe entry needs its accrued amount too
        costs_known = False
    if costs_known and (_num(worksite.get("grain_required")) or 0) < 0:  # S10 round 2 / L3-01: a negative accrued amount is malformed, not a credit
        costs_known = False
    if costs_known and (worksite.get("resource_recipe") or []):
        if base_pp is None or base_pp < 0 or any(not isinstance(item.get("accrued"), (int, float)) or isinstance(item.get("accrued"), bool) or item["accrued"] < 0
                                                  or (item.get("per_hour") is not None and item["per_hour"] < 0) for item in costs if item["symbol"] != "GRAIN"):
            costs_known = False
        if "unsupported_recipe" in out["issues"]:
            costs_known = False
    if not costs_known:
        out["issues"].append("missing_cost")
    out.update({
        "costs_known": costs_known,
        "state": "harvesting" if worksite.get("is_harvesting") else "idle",
        "is_sps": bool(worksite.get("is_sps_work")) or token == "SPS",
        "anchor_at": iso(anchor),
        "hours_at_anchor": h0,
        "last_action_time": iso(last_action),
        "next_op_allowed_date": worksite.get("next_op_allowed_date"),
        "gross_per_hour": gross_per_hour,
        "net_at_anchor": net0,
        "costs": costs,
        "food_discount": discount,
        "base_pp_after_cap": base_pp,
        "is_powered": staking.get("is_powered"),
        "finds": {
            "fragment_eligibility": frag_elig,
            "fragment_rate_per_hour": FRAGMENT_RATE * (base_pp / 1000.0) if base_pp is not None and frag_elig == "eligible" else None,
            "labors_luck_eligibility": "eligible" if ll_flag is True else ("ineligible" if ll_flag is False else "unknown"),
            "labors_luck_rate_per_hour": LABORS_LUCK_RATE * (base_pp / 1000.0) if base_pp is not None and ll_flag is True else None,
        },
    })
    return out


def advance(projection: dict[str, Any], at: datetime | None = None) -> dict[str, Any]:
    """Amounts of one projection at `at` (default now): the same math the browser ticks."""
    at = at or now_utc()
    anchor = parse_utc(projection.get("anchor_at"))
    if anchor is None or projection.get("net_at_anchor") is None:
        return {"known": False}
    h0 = float(projection["hours_at_anchor"])
    h = h0 + max(0.0, (at - anchor).total_seconds()) / 3600.0
    hc, h0c = min(h, CAP_HOURS), min(h0, CAP_HOURS)
    dh = max(0.0, hc - h0c)
    raw_tax = projection.get("tax_rate")  # S5 / L3-09: an explicit 0 is a real rate; only a missing or invalid one takes the default
    tax = float(raw_tax) if isinstance(raw_tax, (int, float)) and not isinstance(raw_tax, bool) and 0 <= raw_tax < 1 else DEFAULT_TAX_RATE
    gph = float(projection.get("gross_per_hour") or 0.0)
    net = float(projection["net_at_anchor"]) + gph * (1 - tax) * dh
    gross = net / (1 - tax) if tax < 1 else net
    costs = {}
    cost_rates = {}
    for cost in projection.get("costs") or []:
        symbol = cost["symbol"]
        if symbol not in costs:
            costs[symbol] = cost.get("accrued")
        cost_rates[symbol] = cost_rates.get(symbol, 0.0) + (cost.get("per_hour") or 0.0)
    for symbol, accrued in costs.items():
        costs[symbol] = accrued + cost_rates[symbol] * dh if accrued is not None and h0 < CAP_HOURS else accrued
    return {"known": True, "hours": h, "capped_hours": hc, "net": net, "gross": gross, "tax": gross - net,
            "costs": costs, "full": h >= CAP_HOURS}


def summarize_regions(report: dict[str, Any], at: datetime | None = None) -> list[dict[str, Any]]:
    """Per-region snapshot estimates for the header modal's first paint (no network)."""
    at = at or now_utc()
    regions: dict[str, dict[str, Any]] = {}
    for plot in report.get("plots") or []:
        facts = plot.get("facts") or {}
        region = facts.get("region")
        if not region:
            continue
        entry = regions.setdefault(region, {
            "region_uid": region, "region_number": region_number(region), "plots": 0, "harvesting": 0,
            "net_by_token": {}, "gross_by_token": {}, "cost_now": {}, "cost_per_hour": {},
            "tax_collection": [], "unknown": 0,
        })
        entry["plots"] += 1
        projection = project_harvest_plot(plot)
        if projection["state"] == "tax_collection":
            entry["tax_collection"].append({"deed_uid": plot.get("deed_uid"), "plot": facts.get("plot"), "worksite_type": facts.get("worksite_type")})
            continue
        if projection["state"] not in ("harvesting", "idle"):
            if projection["state"] == "unavailable":
                entry["unknown"] += 1
            continue
        amounts = advance(projection, at)
        if not amounts.get("known"):
            entry["unknown"] += 1
            continue
        if amounts["net"] > 0:
            entry["harvesting"] += 1
            token = projection["token"]
            entry["net_by_token"][token] = entry["net_by_token"].get(token, 0.0) + amounts["net"]
            entry["gross_by_token"][token] = entry["gross_by_token"].get(token, 0.0) + amounts["gross"]
            for symbol, value in amounts["costs"].items():  # S5 / L3-10: accrued cost is already summed per symbol: add it once
                if value is not None:
                    entry["cost_now"][symbol] = entry["cost_now"].get(symbol, 0.0) + value
            if not projection.get("costs_known", True):
                entry["cost_unknown"] = entry.get("cost_unknown", 0) + 1
            for cost in projection.get("costs") or []:
                symbol = cost["symbol"]
                if cost.get("per_hour") is not None and not amounts["full"]:
                    entry["cost_per_hour"][symbol] = entry["cost_per_hour"].get(symbol, 0.0) + cost["per_hour"]
    return sorted(regions.values(), key=lambda row: row["region_number"])


def parse_region_quote(payload: Any, tax_rate: float = DEFAULT_TAX_RATE) -> dict[str, Any]:
    """`/land/resources/production/region/harvestable` -> net per token + summed costs.

    `amount_claimable` is NET (dossier §2, checked against the per-plot projected sum)."""
    if not isinstance(payload, dict) or payload.get("status") not in (None, "success") or not isinstance(payload.get("data"), list):
        raise ValueError("region harvestable response is not a success list")
    net: dict[str, float] = {}
    costs = {symbol: 0.0 for symbol in COST_SYMBOLS}
    for row in payload["data"]:
        if not isinstance(row, dict):
            raise ValueError("region harvestable row is not an object")
        token = str(row.get("token_symbol") or "").upper()
        amount = _num(row.get("amount_claimable"))
        fields = {"GRAIN": _num(row.get("grain_required_for_food")), "WOOD": _num(row.get("wood_required")),
                  "STONE": _num(row.get("stone_required")), "IRON": _num(row.get("iron_required"))}
        if token not in TOKENS or amount is None or amount < 0 or any(value is None or value < 0 for value in fields.values()):
            raise ValueError(f"region harvestable row incomplete or unsupported token {token!r}")
        net[token] = net.get(token, 0.0) + amount
        for symbol, value in fields.items():
            costs[symbol] += value
    gross = {token: value / (1 - tax_rate) for token, value in net.items()}
    return {"net_by_token": net, "gross_by_token": gross, "cost_now": {k: v for k, v in costs.items() if v}}


def parse_region_balances(payload: Any) -> dict[str, dict[str, Any]]:
    """`/land/liquidity/region/{player}` -> {region_uid: {GRAIN..AURA balances, name}}.

    Schema-checked (plan §3.7): rows must carry uid and numeric grain/wood/stone/iron; LP
    share-only rows are rejected rather than read as spendable balances."""
    if not isinstance(payload, dict) or payload.get("status") not in (None, "success") or not isinstance(payload.get("data"), list):
        raise ValueError("regional balance response is not a success list")
    out: dict[str, dict[str, Any]] = {}
    for row in payload["data"]:
        if not isinstance(row, dict) or not isinstance(row.get("uid"), str):
            raise ValueError("regional balance row without uid")
        balances = {symbol: _num(row.get(symbol.lower())) for symbol in ("GRAIN", "WOOD", "STONE", "IRON", "RESEARCH", "AURA")}
        if any(balances[symbol] is None for symbol in COST_SYMBOLS):
            raise ValueError("regional balance row lacks grain/wood/stone/iron")
        name = str(row.get("name") or "")
        if " - " in name:
            name = name.split(" - ", 1)[1]
        out[row["uid"]] = {"balances": balances, "name": name, "plots_owned": row.get("plots_owned")}
    return out


def affordability(cost_now: dict[str, float], balances: dict[str, float] | None,
                  cost_per_hour: dict[str, float] | None = None, margin_hours: float = 0.0,
                  credits: dict[str, float] | None = None) -> dict[str, Any]:
    """Check each cost against stored balance plus the same-harvest credit it can fund."""
    if balances is None:
        return {"status": "unknown", "rows": [], "short": {}, "margin_hours": margin_hours}
    rows, short = [], {}
    credits = credits or {}
    for symbol in COST_SYMBOLS:
        need = float(cost_now.get(symbol) or 0.0) + float((cost_per_hour or {}).get(symbol) or 0.0) * margin_hours
        if need <= 0:
            continue
        have = balances.get(symbol)
        if have is None:
            return {"status": "unknown", "rows": rows, "short": short, "margin_hours": margin_hours}
        credit = max(0.0, float(credits.get(symbol) or 0.0))
        available = have + credit
        gap = max(0.0, need - available)
        if gap > 0:
            short[symbol] = gap
        rows.append({"symbol": symbol, "need": need, "balance": have, "credit": credit,
                     "available": available, "after": available - need, "short": gap})
    return {"status": "short" if short else "covered", "rows": rows, "short": short, "margin_hours": margin_hours}


def ledger_effects(payload: Any) -> dict[int, dict[str, Any]]:
    """`/land/resources/history/{trx}` -> per region number: minted (net received) and consumed."""
    if not isinstance(payload, dict) or payload.get("status") not in (None, "success") or not isinstance(payload.get("data"), list):
        raise ValueError("resource history response is not a success list")
    out: dict[int, dict[str, Any]] = {}
    for row in payload["data"]:
        if not isinstance(row, dict):
            continue
        region = row.get("region_number")
        amount = _num(row.get("amount"))
        symbol = RESOURCE_ID_SYMBOL.get(row.get("resource_id"))
        counterparty = str(row.get("counterparty") or "")
        if not isinstance(region, int) or amount is None or symbol is None:
            continue
        entry = out.setdefault(region, {"received": {}, "spent": {}})
        if counterparty.endswith("_MINT") and amount > 0:
            entry["received"][symbol] = entry["received"].get(symbol, 0.0) + amount
        elif amount < 0:
            entry["spent"][symbol] = entry["spent"].get(symbol, 0.0) - amount
    return out


def regional_harvest_effects(rows):
    """Wrap the pure receipt model for legacy callers; zero outbound calls."""
    from .receipt_model import amounts
    try:
        result = amounts(rows)
    except (ValueError, TypeError, AttributeError):
        return None
    return {"received": result["received"], "spent": result["costs"],
            "taxes": result["tax"], "gross": result["before_tax"]}
