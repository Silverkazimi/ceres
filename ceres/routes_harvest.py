""" harvest routes (plan §6.2). Read-only by default; signing routes refuse while
`harvest_signing_enabled` is off (plan §13 1C/1D -- awaiting the maintainer's first on-chain test).

No route here reloads the 25 MB board bundle: the snapshot is the already-cached
`capture.latest_snapshot()` object, fresh reads are narrow (one deed, one region, one
balances call) and paced at 0.3 s per dispatch across threads.
"""

from __future__ import annotations

from .transaction_diagnostics import record_verification
import time as diagnostic_time

import asyncio
import logging
import threading
import re
import time
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from fides_land.harvest import harvest_all_op, harvest_resources_op, plan_chunks, signature_count, tax_collection_op
from fides_land.ops import LandOpError
from praetoria.land_analysis import LandClient
from praetoria.singleflight import singleflight

from . import client_limits
from . import harvest as hv
from . import harvest_evidence as evidence
from .routes_board import _config, _read_snapshot_or_503, _require_same_origin
from .security import player_for_request
from .transaction_identity import app_identity

_log = logging.getLogger("ceres.routing")
router = APIRouter()

DISPLAY_CACHE_SECONDS = 10.0
MIN_DISPATCH_SECONDS = 0.300
MAX_SELECTED_DEEDS = 20
MAX_SELECTED_TAX_DEEDS = 200  # the Taxes tab lists every Keep/Castle
MAX_WORKERS = 4
MARGIN_HOURS = 2.0 / 60.0  # plan §8.1: reserve two minutes of accrual for the Keychain wait

# short reads -- 15 s x 3 attempts, twice per plot, left the harvest box blank for over a
# minute on a slow network. A failed read falls back to the snapshot projection with an issue.
_client = LandClient(timeout=5.0, retries=1)
# region-level reads (inventory, region quotes) are larger and must survive a slow link
# the short per-plot client made "Harvest all" fail with "region inventory unavailable (TransportError)".
_region_client = LandClient(timeout=15.0, retries=2)
# a tax read on a kept-alive connection the game had already closed failed in 3 ms with no second try
# ("Tax read unavailable" on a Castle holding taxes). Tax reads get one retry, on a fresh connection.
_tax_client = LandClient(timeout=5.0, retries=2)
_pace_lock = threading.Lock()
_last_dispatch = [0.0]
_cache: dict[tuple, tuple[float, Any]] = {}
_cache_lock = threading.Lock()
_state_cache: dict[tuple, dict[str, Any]] = {}


def _paced(fn, *args):
    with _pace_lock:
        wait = _last_dispatch[0] + MIN_DISPATCH_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_dispatch[0] = time.monotonic()
    return fn(*args).data


def _inflight_kw(flag):
    return {"inflight": True} if flag else {}


def _cached(key: tuple, fn, *args, fresh: bool = False, inflight: bool = False):
    now = time.monotonic()
    if not fresh:
        with _cache_lock:
            hit = _cache.get(key)
        if hit and now - hit[0] < DISPLAY_CACHE_SECONDS:
            return hit[1]
    endpoint = {
        "active": "/land/projects/deed/{deed_uid}/active",
        "owned": "/land/deeds/owned/{player}",
        "bal": "/land/liquidity/region/{player}",
        "quote": "/land/resources/production/region/harvestable",
    }.get(key[0], key[0])
    request_key = ("GET", endpoint, *key[1:])
    fetch = lambda: _paced(fn, *args)
    value = singleflight(request_key, fetch) if inflight else fetch()
    if fresh:
        return value
    with _cache_lock:
        if len(_cache) > 512:
            _cache.clear()
        _cache[key] = (time.monotonic(), value)
    return value


def _balances(player: str, fresh: bool = False, client: LandClient = _region_client, inflight: bool = False) -> tuple[dict[str, Any] | None, str | None]:
    # Harvest all/preview read balances on the region client; the plot box keeps the short one ().
    try:
        return hv.parse_region_balances(_cached(("bal", player), client.regional_balances, player, fresh=fresh, **_inflight_kw(inflight))), None
    except Exception as exc:  # unknown balances block signing, never read as zero
        return None, f"region balances unavailable ({type(exc).__name__})"


def _snapshot(request: Request, player: str):
    from . import capture as capture_mod
    return _read_snapshot_or_503(capture_mod, _config(request), player)


def _plot_index(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {plot["deed_uid"]: plot for plot in report.get("plots") or [] if plot.get("deed_uid")}


def _summaries(report, ref, player) -> list[dict[str, Any]]:
    key = (player, getattr(ref, "token", None) or id(report))
    cached = _state_cache.get(key)
    if cached and time.monotonic() - cached["at"] < 30:
        return cached["regions"]
    regions = hv.summarize_regions(report)
    if len(_state_cache) > 8:
        _state_cache.clear()
    _state_cache[key] = {"at": time.monotonic(), "regions": regions}
    return regions


def _public_region(summary: dict[str, Any]) -> dict[str, Any]:
    return {k: summary[k] for k in ("region_uid", "region_number", "plots", "harvesting", "net_by_token",
                                     "gross_by_token", "cost_now", "cost_per_hour", "tax_collection", "unknown")}


@router.get("/api/harvest/state")
def harvest_state(request: Request, player: str | None = None):
    player = player_for_request(request, player)
    report, ref = _snapshot(request, player)
    return {"player": player, "server_now": hv.iso(hv.now_utc()), "board_read_at": report.get("generated_at"),
            "signing_enabled": bool(_config(request).harvest_signing_enabled),
            "regions": [_public_region(r) for r in _summaries(report, ref, player)]}


def _fresh_projection(plot: dict[str, Any], fresh: bool, inflight: bool = False) -> tuple[dict[str, Any], str | None]:
    deed = plot["deed_uid"]
    try:
        payload = _cached(("active", deed), _client.active_project, deed, fresh=fresh, **_inflight_kw(inflight))
        row = payload.get("data") if isinstance(payload, dict) else None
        if row is None:
            return hv.project_harvest_plot(plot, {}, observed_at=hv.iso(hv.now_utc())), None
        return hv.project_harvest_plot(plot, row, observed_at=hv.iso(hv.now_utc())), None
    except Exception as exc:
        return hv.project_harvest_plot(plot), f"fresh plot read unavailable ({type(exc).__name__})"


@router.get("/api/plots/{deed_uid}/harvest")
def plot_harvest(deed_uid: str, request: Request, player: str | None = None, fresh: bool = False):
    player = player_for_request(request, player)
    client_limits.limit(request, "plot_read")  # C3: per-client limit before any upstream read
    report, _ref = _snapshot(request, player)
    plot = _plot_index(report).get(deed_uid)
    if plot is None:
        raise HTTPException(status_code=404, detail="unknown deed_uid")
    projection, issue = _fresh_projection(plot, fresh=fresh, inflight=True)
    balances, bal_issue = _balances(player, fresh=fresh, client=_client)  # S10 round 2 / L3-04: a fresh read refreshes the balances too
    region = projection.get("region_uid")
    region_bal = (balances or {}).get(region)
    amounts = hv.advance(projection)
    cost_now = {k: v for k, v in (amounts.get("costs") or {}).items() if v is not None}
    cost_per_hour: dict[str, float] = {}
    for cost in projection.get("costs") or []:
        if cost.get("per_hour") is not None:
            symbol = cost["symbol"]
            cost_per_hour[symbol] = cost_per_hour.get(symbol, 0.0) + float(cost["per_hour"])
    credits = {projection["token"]: amounts["net"]} if projection.get("token") in hv.COST_SYMBOLS and amounts.get("net", 0) > 0 else {}
    afford = hv.affordability(cost_now, region_bal["balances"] if region_bal else None,
                              cost_per_hour, MARGIN_HOURS, credits)
    if not projection.get("costs_known", True) and afford["status"] == "covered":
        afford = {**afford, "status": "unknown", "reason": "missing_cost"}
    return {"server_now": hv.iso(hv.now_utc()), "projection": projection, "region_name": (region_bal or {}).get("name"),
            "region_balances": region_bal["balances"] if region_bal else None, "affordability": afford,
            "issues": [x for x in (issue, bal_issue) if x], "signing_enabled": bool(_config(request).harvest_signing_enabled)}


@router.get("/api/plots/{deed_uid}/taxes")
def plot_taxes(deed_uid: str, request: Request, player: str | None = None):
    """: what a Keep/Castle has collected -- every token it holds, the total and the capacity."""
    player = player_for_request(request, player)
    client_limits.limit(request, "plot_read")  # C3: per-client limit before any upstream read
    report, _ref = _snapshot(request, player)
    plot = _plot_index(report).get(deed_uid)
    if plot is None:
        raise HTTPException(status_code=404, detail="unknown deed_uid")
    facts = plot.get("facts") or {}
    if str(facts.get("resource") or "").upper() != "TAX":
        raise HTTPException(status_code=422, detail="not a Keep or Castle plot")
    try:
        payload = _cached(("taxes", deed_uid), _tax_client.taxes, deed_uid)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"tax read unavailable ({type(exc).__name__})") from exc
    data = payload.get("data") or {}
    tokens = []
    for row in data.get("taxes") or []:
        try:
            tokens.append({"token": str(row.get("token") or "").upper(), "balance": float(row.get("balance") or 0.0)})
        except (TypeError, ValueError):
            continue
    tokens.sort(key=lambda item: -item["balance"])
    pool = next((str(row.get("player")) for row in data.get("taxes") or [] if row.get("player")), None)
    return {"deed_uid": deed_uid, "worksite_type": facts.get("worksite_type"), "tokens": tokens,
            "total": sum(item["balance"] for item in tokens), "capacity": data.get("capacity"), "pool": pool,
            "server_now": hv.iso(hv.now_utc())}


def _normalize_scope(body: dict[str, Any]) -> dict[str, Any]:
    scope = body.get("scope") if isinstance(body, dict) else None
    if not isinstance(scope, dict) or scope.get("kind") not in ("all", "regions", "deeds", "taxes"):
        raise HTTPException(status_code=422, detail="scope.kind must be all, regions, deeds or taxes")
    kind = scope["kind"]
    if kind == "all":
        return {"kind": "all"}
    key = "region_uids" if kind == "regions" else "deed_uids"  # "taxes" names Keep/Castle deeds ()
    values = scope.get(key)
    if not isinstance(values, list) or not values or not all(isinstance(v, str) and 3 <= len(v) <= 40 for v in values):
        raise HTTPException(status_code=422, detail=f"scope.{key} must be a non-empty list")
    values = sorted(set(values))
    if kind == "deeds" and len(values) > MAX_SELECTED_DEEDS:
        raise HTTPException(status_code=422, detail=f"at most {MAX_SELECTED_DEEDS} plots per explicit selection")
    if kind == "taxes" and len(values) > MAX_SELECTED_TAX_DEEDS:
        raise HTTPException(status_code=422, detail=f"at most {MAX_SELECTED_TAX_DEEDS} Keeps/Castles per selection")
    if kind == "regions" and len(values) > 200:
        raise HTTPException(status_code=422, detail="too many regions")
    return {"kind": kind, key: values}


def _nothing_reason(report: dict[str, Any], region_uid: str) -> dict[str, Any]:
    """/: say WHY the game quotes nothing for a region, and what is waiting.

    Cooldown: the resources exist, they just cannot be claimed yet -- `waiting_net_by_token` carries
    this load's accrued amounts so the modal never shows them as 0. A region with no production and
    nothing accrued is `idle` (listed last, faded)."""
    now = hv.now_utc()
    cooling, other, producing = [], set(), 0
    waiting: dict[str, float] = {}
    for plot in report.get("plots") or []:
        if (plot.get("facts") or {}).get("region") != region_uid:
            continue
        projection = hv.project_harvest_plot(plot)
        if projection["state"] in ("construction", "no_project", "tax_collection"):
            other.add({"construction": "under construction", "no_project": "no worksite", "tax_collection": "tax collection"}[projection["state"]])
            continue
        if projection["state"] == "harvesting" and (projection.get("gross_per_hour") or 0) > 0:  # 0 PP plots produce nothing
            producing += 1
        amounts = hv.advance(projection) if projection["state"] in ("harvesting", "idle") else {"known": False}
        if amounts.get("known") and amounts["net"] > 0 and projection.get("token"):
            waiting[projection["token"]] = waiting.get(projection["token"], 0.0) + amounts["net"]
        next_op = hv.parse_utc(projection.get("next_op_allowed_date"))
        if next_op and next_op > now:
            cooling.append(next_op)
    out: dict[str, Any] = {"waiting_net_by_token": waiting, "idle": not cooling and not producing and not waiting}
    if cooling:
        out.update(next_op_at=hv.iso(min(cooling)), cooling_plots=len(cooling),
                   reason=f"Harvest cooldown on {len(cooling)} plot{'s' if len(cooling) != 1 else ''}")
    elif out["idle"]:
        out["reason"] = "0 PP on every plot — nothing accrues" + (f"; plots are {', '.join(sorted(other))}" if other else "")
    else:
        out["reason"] = "The game reports nothing claimable in this region yet"
    return out


def _region_rows(player, scope, report, ref, fresh: bool, inflight: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    issues: list[str] = []
    summaries = {r["region_uid"]: r for r in _summaries(report, ref, player)}
    if scope["kind"] == "all":
        wanted = set(summaries)
        try:
            owned = _cached(("owned", player), _region_client.owned_deed_counts, player, fresh=fresh, **_inflight_kw(inflight))
            for row in owned.get("data") or []:
                wanted.add(row["uid"])
        except Exception as exc:
            issues.append(f"region inventory unavailable ({type(exc).__name__})")
            if fresh:
                # S10 round 2 / L3-03: "all" built from the stored region list is for display; a signable preview needs the live inventory.
                issues.append("live region inventory required")
                return [], issues
    else:
        owned_regions = set(summaries)
        wanted = {r for r in scope["region_uids"] if r in owned_regions}
        if len(wanted) != len(scope["region_uids"]):
            raise HTTPException(status_code=422, detail="scope names a region not in this account's board")
    balances, bal_issue = _balances(player, fresh=fresh, **_inflight_kw(inflight))
    if bal_issue:
        issues.append(bal_issue)

    def quote(region_uid: str):
        try:
            return region_uid, hv.parse_region_quote(_cached(("quote", player, region_uid), _region_client.region_harvestable, player, region_uid, fresh=fresh, **_inflight_kw(inflight))), None
        except Exception as exc:
            return region_uid, None, f"{type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        quotes = {uid: (q, err) for uid, q, err in pool.map(quote, sorted(wanted))}
    rows = []
    for uid in sorted(wanted, key=hv.region_number):
        summary = summaries.get(uid, {})
        q, err = quotes[uid]
        bal = (balances or {}).get(uid)
        row = {"region_uid": uid, "region_number": hv.region_number(uid), "name": (bal or {}).get("name"),
               "plots": summary.get("plots"), "harvesting": summary.get("harvesting", 0), "tax_collection": summary.get("tax_collection") or [],
               "cost_per_hour": summary.get("cost_per_hour") or {}, "balances": bal["balances"] if bal else None}
        if q is None:
            row.update(status="unavailable", reason=f"fresh region quote unavailable ({err})")
        else:
            row.update(net_by_token={k: v for k, v in q["net_by_token"].items() if v > 0},
                       gross_by_token={k: v for k, v in q["gross_by_token"].items() if v > 0}, cost_now=q["cost_now"])
            if not row["net_by_token"]:
                row.update(status="nothing", affordability={"status": "covered", "rows": [], "short": {}, "margin_hours": MARGIN_HOURS},
                           **_nothing_reason(report, uid))
            else:
                afford = hv.affordability(q["cost_now"], row["balances"], row["cost_per_hour"], MARGIN_HOURS,
                                          q["net_by_token"])
                row["affordability"] = afford
                row["status"] = {"covered": "included", "short": "short", "unknown": "unknown"}[afford["status"]]
        rows.append(row)
    return rows, issues


def _deed_rows(player, scope, report, fresh: bool, inflight: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    index = _plot_index(report)
    taxes_only = scope["kind"] == "taxes"
    issues: list[str] = []
    missing = [d for d in scope["deed_uids"] if d not in index]
    if missing:
        raise HTTPException(status_code=422, detail="scope names a plot not in this account's board")
    balances, bal_issue = _balances(player, fresh=fresh, **_inflight_kw(inflight))
    if bal_issue:
        issues.append(bal_issue)
    by_region: dict[str, dict[str, Any]] = {}
    for deed in scope["deed_uids"]:
        plot = index[deed]
        owner = (plot.get("facts") or {}).get("owner")
        if owner is not None and owner != player:
            raise HTTPException(status_code=422, detail="scope names a plot this account does not own")
        if taxes_only:
            # a Keep/Castle is known from the board; only its taxes are read live (no project read).
            projection, issue = hv.project_harvest_plot(plot), None
            if projection["state"] != "tax_collection":
                raise HTTPException(status_code=422, detail="scope names a plot that is not a Keep or Castle")
        else:
            projection, issue = _fresh_projection(plot, fresh=fresh, **_inflight_kw(inflight))
        if issue:
            issues.append(issue)
        region = projection.get("region_uid")
        entry = by_region.setdefault(region, {"region_uid": region, "region_number": hv.region_number(region),
                                              "name": ((balances or {}).get(region) or {}).get("name"),
                                              "plots": 0, "deeds": [], "net_by_token": {}, "gross_by_token": {},
                                              "cost_now": {}, "cost_per_hour": {}, "tax_collection": [],
                                              "balances": ((balances or {}).get(region) or {}).get("balances")})
        entry["plots"] += 1
        amounts = hv.advance(projection)
        member = {"deed_uid": deed, "plot": projection.get("plot"), "projection": projection,
                  "eligible": False, "reason": None, "live_failed": bool(issue and fresh) or bool(fresh and projection["state"] in ("harvesting", "idle") and not amounts.get("known"))}  # B4-04: a failed live read is tracked apart from the display state
        if projection["state"] == "tax_collection":
            # (maintainer chose "Show and claim"): a Keep/Castle is claimable when it holds taxes; the claim is the
            # game's own tax_collection op, no costs.
            try:
                tax_payload = _cached(("taxes", deed), _tax_client.taxes, deed, fresh=fresh)
                held = {str(r.get("token") or "").upper(): float(r.get("balance") or 0.0) for r in ((tax_payload.get("data") or {}).get("taxes") or [])}
            except Exception as exc:
                held = None
                member["live_failed"] = True  # S10 / B5-04: a failed tax read makes the whole preview unsignable
                issues.append(f"tax read unavailable ({type(exc).__name__})")
            held = {k: v for k, v in (held or {}).items() if v > 0} if held is not None else None
            if not held:
                member["reason"] = "Nothing collected yet" if held is not None else "Tax read unavailable"
            else:
                member["eligible"] = True
                member["op_kind"] = "tax_collection"
                member["held_by_token"] = held
                for token, value in held.items():
                    entry["net_by_token"][token] = entry["net_by_token"].get(token, 0.0) + value
                    entry["gross_by_token"][token] = entry["gross_by_token"].get(token, 0.0) + value
        elif projection["state"] not in ("harvesting", "idle") or not amounts.get("known"):
            member["reason"] = {"construction": "Under construction", "no_project": "No worksite"}.get(projection["state"], "Harvest data unavailable")
        elif issue and fresh:
            # S10 / L3-03: a signable preview needs the LIVE plot read. A failed read falls back to the stored board for display only; it
            # never makes the plot eligible.
            member["reason"] = "Live plot data unavailable"
        else:
            next_op = hv.parse_utc(projection.get("next_op_allowed_date"))
            if next_op and next_op > hv.now_utc():
                member["reason"] = f"Next harvest allowed at {hv.iso(next_op)}"
            elif amounts["net"] <= 0:
                member["reason"] = "Nothing to harvest"
            else:
                member["eligible"] = True
                token = projection["token"]
                entry["net_by_token"][token] = entry["net_by_token"].get(token, 0.0) + amounts["net"]
                entry["gross_by_token"][token] = entry["gross_by_token"].get(token, 0.0) + amounts["gross"]
                for symbol, value in amounts["costs"].items():  # S5 / L3-10: once per symbol, not once per recipe entry
                    if value:
                        entry["cost_now"][symbol] = entry["cost_now"].get(symbol, 0.0) + value
                if not projection.get("costs_known", True):
                    entry["cost_unknown"] = entry.get("cost_unknown", 0) + 1
                for cost in projection.get("costs") or []:
                    if cost.get("per_hour") and not amounts["full"]:
                        entry["cost_per_hour"][cost["symbol"]] = entry["cost_per_hour"].get(cost["symbol"], 0.0) + cost["per_hour"]
        member["amounts"] = amounts
        entry["deeds"].append(member)
    rows = []
    for entry in by_region.values():
        if not any(m["eligible"] for m in entry["deeds"]):
            entry["status"] = "nothing"
            entry["reason"] = "; ".join(sorted({m["reason"] for m in entry["deeds"] if m["reason"]})) or None
            entry["affordability"] = {"status": "covered", "rows": [], "short": {}, "margin_hours": MARGIN_HOURS}
        else:
            # A tax claim costs nothing -- never blocked by an unknown region balance. S10 / L3-02: the shortcut needs no accrued cost
            # AND no per-hour slope; a positive slope with zero accrual still needs its two-minute margin.
            afford = ({"status": "covered", "rows": [], "short": {}, "margin_hours": MARGIN_HOURS} if not entry["cost_now"] and not entry["cost_per_hour"]
                      else hv.affordability(entry["cost_now"], entry["balances"], entry["cost_per_hour"], MARGIN_HOURS,
                                           entry["net_by_token"]))
            if entry.get("cost_unknown") and afford["status"] == "covered":  # L3-01: missing cost fields never read as "covered"
                afford = {**afford, "status": "unknown", "reason": "missing_cost"}
            entry["affordability"] = afford
            entry["status"] = {"covered": "included", "short": "short", "unknown": "unknown"}[afford["status"]]
        if any(m.get("live_failed") for m in entry["deeds"]):  # S10 / L3-04: one failed live read makes the whole preview unsignable
            entry["status"] = "unknown"
            entry["reason"] = "Live plot data unavailable"
        rows.append(entry)
    return sorted(rows, key=lambda r: r["region_number"]), issues


def _signing_allowed(request: Request, player: str, scope: dict[str, Any]) -> bool:
    """Global flag (off), or the  allowlist: exactly one allowlisted (account, deed)."""
    config = _config(request)
    if config.harvest_signing_enabled:
        return True
    deeds = scope.get("deed_uids") if scope.get("kind") == "deeds" else None
    return bool(deeds) and len(deeds) == 1 and (player, deeds[0]) in (config.harvest_signing_allowlist or frozenset())


def _build_preview(request: Request, player: str, scope: dict[str, Any], fresh: bool, inflight: bool = False) -> dict[str, Any]:
    report, ref = _snapshot(request, player)
    if scope["kind"] in ("deeds", "taxes"):
        rows, issues = _deed_rows(player, scope, report, fresh, **_inflight_kw(inflight))
    else:
        rows, issues = _region_rows(player, scope, report, ref, fresh, **_inflight_kw(inflight))
    harvestable = [r for r in rows if r["status"] not in ("nothing",)]
    blocked = [r for r in harvestable if r["status"] != "included"]
    by_deed = scope["kind"] in ("deeds", "taxes")
    op_count = sum(len([m for m in r.get("deeds", []) if m["eligible"]]) if by_deed else 1 for r in harvestable)
    preview: dict[str, Any] = {
        "scope": scope, "server_now": hv.iso(hv.now_utc()), "board_read_at": report.get("generated_at"),
        "regions": rows, "issues": issues, "blocked": bool(blocked) or not harvestable,
        "blocked_regions": [r["region_uid"] for r in blocked], "op_count": op_count,
        "signatures": signature_count(op_count), "signing_enabled": _signing_allowed(request, player, scope),
        "chunks": [],
    }
    if blocked or not harvestable:
        return preview  # plan §6.2: no signable operations in a blocked preview
    try:
        if by_deed:
            ops = [(tax_collection_op if m.get("op_kind") == "tax_collection" else harvest_resources_op)(player, r["region_uid"], m["deed_uid"], app=app_identity())
                   for r in harvestable for m in r["deeds"] if m["eligible"]]
        else:
            ops = [harvest_all_op(player, r["region_uid"], app=app_identity()) for r in harvestable]
        chunks = plan_chunks(ops, player)
    except LandOpError as exc:
        preview["blocked"] = True
        preview["issues"].append(f"operation refused: {exc}")
        return preview
    reward_eligibility = {}
    for region_row in rows:
        for member in region_row.get("deeds", []):
            projection = member["projection"]
            finds = projection.get("finds") or {}
            reward_eligibility[member["deed_uid"]] = {
                "fragment": "eligible" if projection.get("state") == "tax_collection" else finds.get("fragment_eligibility", "unknown"),
                "labors_luck": finds.get("labors_luck_eligibility", "unknown"),
            }
    preview["chunks"] = [{
        "operations": [op.as_keychain_tuple() for op in chunk],
        "members": [{"op": op.json_payload["op"], "region_uid": op.json_payload["region_uid"],
                     "deed_uids": op.json_payload["deed_uids"], "nonce": op.json_payload["n"],
                     "reward_eligibility": reward_eligibility.get(next(iter(op.json_payload["deed_uids"]), None))} for op in chunk],
    } for chunk in chunks]
    return preview


def _strip(preview: dict[str, Any]) -> dict[str, Any]:
    out = dict(preview)
    out["regions"] = [{**r, "deeds": [{k: v for k, v in m.items() if k != "projection"} | {"token": m["projection"].get("token")} for m in r.get("deeds", [])]} for r in preview["regions"]]
    return out


@router.post("/api/harvest/preview")
async def harvest_preview(request: Request, player: str | None = None):
    _require_same_origin(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "preview")  # C3: per-client limit before any upstream read
    scope = _normalize_scope(await request.json())
    preview = await run_in_threadpool(_build_preview, request, player, scope, fresh=True, inflight=True)
    if preview["chunks"]:
        preview = evidence.save_preview(player, preview)
    return _strip(preview)


def _signing_or_403(request: Request, player: str, scope: dict[str, Any]) -> None:
    if not _signing_allowed(request, player, scope):
        raise HTTPException(status_code=403, detail="Signing not yet enabled — awaiting first on-chain test")


@router.post("/api/harvest/preflight")
async def harvest_preflight(request: Request, player: str | None = None):
    _require_same_origin(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "preview")  # C3: per-client limit before any upstream read
    body = await request.json()
    stored = evidence.load_preview(player, str(body.get("preview_id") or ""), str(body.get("preview_digest") or ""))
    if stored is None:
        return {"outcome": "CHANGED", "reason": "review expired or unknown; review again"}
    _signing_or_403(request, player, stored["scope"])
    index = body.get("chunk_index")
    if not isinstance(index, int) or not 0 <= index < len(stored["chunks"]):
        raise HTTPException(status_code=422, detail="chunk_index out of range")
    members = stored["chunks"][index]["members"]
    if stored["scope"]["kind"] in ("deeds", "taxes"):
        check_scope = {"kind": stored["scope"]["kind"], "deed_uids": sorted({d for m in members for d in m["deed_uids"]})}
    else:
        check_scope = {"kind": "regions", "region_uids": sorted({m["region_uid"] for m in members})}
    fresh = await run_in_threadpool(_build_preview, request, player, check_scope, fresh=True)
    if fresh["issues"] and fresh["blocked"]:
        return {"outcome": "UNAVAILABLE", "reason": "; ".join(fresh["issues"]) or "fresh data unavailable"}
    checked_chunks = [stored["chunks"][index]]
    old_members = [(m["op"], m["region_uid"], tuple(m["deed_uids"])) for c in checked_chunks for m in c["members"]]
    new_members = [(m["op"], m["region_uid"], tuple(m["deed_uids"])) for c in fresh["chunks"] for m in c["members"]]
    if fresh["blocked"] or set(old_members) != set(new_members):
        return {"outcome": "CHANGED", "reason": "the harvest changed since review", "replacement": _strip(fresh)}
    return {"outcome": "PERMIT", "chunk": stored["chunks"][index], "expires_in_seconds": 5}


@router.post("/api/harvest/prepare")
async def harvest_prepare(request: Request, player: str | None = None):
    _require_same_origin(request)
    player = player_for_request(request, player)
    body = await request.json()
    stored = evidence.load_preview(player, str(body.get("preview_id") or ""), str(body.get("preview_digest") or ""))
    if stored is None:
        raise HTTPException(status_code=409, detail="review expired; review again")
    _signing_or_403(request, player, stored["scope"])
    attempts = body.get("attempts")
    if not isinstance(attempts, list) or not attempts or len(attempts) > len(stored["chunks"]):
        raise HTTPException(status_code=422, detail="invalid harvest batch")
    indices, identifiers = set(), set()
    for item in attempts:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="invalid harvest attempt")
        index, identifier = item.get("chunk_index"), item.get("attempt_id")
        if (type(index) is not int or not 0 <= index < len(stored["chunks"]) or index in indices
                or not isinstance(identifier, str) or not 8 <= len(identifier) <= 64
                or not identifier.replace("-", "").isalnum() or identifier in identifiers):
            raise HTTPException(status_code=422, detail="invalid harvest attempt")
        indices.add(index)
        identifiers.add(identifier)
    root = _config(request).root
    normalized = [{"chunk_index": item["chunk_index"], "attempt_id": item["attempt_id"]} for item in attempts]
    if stored.get("routing_run_id"):
        from .harvest_routing_runs import prepare_harvest_batch
        from .harvest_routing import RoutingError
        try:
            # S10 / L1-03: the routing run lock is a blocking file lock; take it on a worker thread, not on the event loop.
            return {"attempts": await asyncio.to_thread(prepare_harvest_batch, root, player, stored, normalized)}
        except RoutingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        return {"attempts": await asyncio.to_thread(lambda: [evidence.begin_attempt(root, player, stored, item["chunk_index"], item["attempt_id"]) for item in normalized])}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/harvest/attempt")
async def harvest_attempt(request: Request, player: str | None = None):
    _require_same_origin(request)
    player = player_for_request(request, player)
    body = await request.json()
    root = _config(request).root
    attempt_id = str(body.get("attempt_id") or "")
    if body.get("tx_id") is not None or body.get("state"):
        tx = body.get("tx_id")
        run_id = body.get("run_id")
        if run_id is not None and (not isinstance(run_id, str) or not run_id.startswith("run-") or len(run_id) > 128 or any(char.isspace() for char in run_id)):
            raise HTTPException(status_code=422, detail="invalid run_id")
        if tx is not None and (not isinstance(tx, str) or not tx.isalnum() or len(tx) > 64):
            raise HTTPException(status_code=422, detail="invalid tx_id")
        try:
            fields = {"tx_id": tx, "state": str(body.get("state") or "submitted")[:40]}
            if run_id is not None:
                fields["run_id"] = run_id
            record = await asyncio.to_thread(lambda: evidence.update_attempt(root, player, attempt_id, **fields))  # S10 / B4-01: lock + fsync off the event loop
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="unknown attempt")
        return record
    stored = evidence.load_preview(player, str(body.get("preview_id") or ""), str(body.get("preview_digest") or ""))
    if stored is None:
        raise HTTPException(status_code=409, detail="review expired; review again")
    _signing_or_403(request, player, stored["scope"])
    try:
        # S10 round 2 / L1-01: both branches wait on file locks and disk; they run on a worker thread, never on the event loop.
        if stored.get("routing_run_id"):
            from .harvest_routing_runs import begin_harvest
            return await asyncio.to_thread(begin_harvest, root, player, stored, int(body.get("chunk_index")), attempt_id)
        return await asyncio.to_thread(evidence.begin_attempt, root, player, stored, int(body.get("chunk_index")), attempt_id)
    except (ValueError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _reward_finds(record):
    nested = record.get("fragment_roll")
    roll = {**record, **(nested if isinstance(nested, dict) else {})}
    finds = []
    if roll.get("fragment_found") is True:
        tier = roll.get("fragment_type")
        tier = {"TOTEMFC": "common", "TOTEMFR": "rare", "TOTEMFE": "epic", "TOTEMFL": "legendary"}.get(tier, tier)
        finds.append({"kind": "fragment", "type": tier, "deed_uid": record.get("deed_uid")})
    if isinstance(roll.get("labors_luck_uid"), str) and roll["labors_luck_uid"]:
        finds.append({"kind": "labors_luck", "uid": roll["labors_luck_uid"], "deed_uid": record.get("deed_uid")})
    return finds


def _regional_reward_rows(op, member, member_tx):
    if not op or op.get("success") is not True or op.get("lookup_id") != member_tx:
        return None
    data = op.get("data") or {}
    if data.get("op") != "harvest_all" or data.get("region_uid") != member["region_uid"]:
        return None
    result = op.get("result")
    if not isinstance(result, dict) or result.get("success") is not True:
        return None
    inner = result.get("result")
    if not isinstance(inner, dict) or inner.get("success") is not True:
        return None
    payload = inner.get("data")
    if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(payload.get("results"), list):
        return None
    region = int(hv.region_number(member["region_uid"]))
    rows = payload["results"]
    if any(not isinstance(item, dict) or item.get("region_number") != region or not item.get("deed_uid") for item in rows):
        return None
    deeds = {item["deed_uid"]: item for item in payload.get("deeds", []) if isinstance(item, dict) and item.get("deed_uid")}
    return [(item, deeds.get(item["deed_uid"], {})) for item in rows if item.get("result_message") == "Successful"]


def _reward_card_art(members: list[dict[str, Any]]) -> None:
    from .board import load_details_by_id

    rewards = [find for member in members for find in member.get("finds", [])
               if find.get("kind") == "labors_luck"]
    if not rewards:
        return
    details = load_details_by_id()
    for find in rewards:
        match = re.fullmatch(r"([CG])[0-9]+-([0-9]+)-[A-Za-z0-9]+", str(find.get("uid") or ""))
        if not match:
            continue
        detail = details.get(int(match[2]), {})
        editions = str(detail.get("editions", "")).split(",")
        if detail.get("name") and len(editions) == 1 and editions[0].isdigit():
            find["card"] = {"name": detail["name"], "card_detail_id": int(match[2]),
                            "edition": int(editions[0]), "gold": match[1] == "G"}


@router.post("/api/harvest/verify")
async def harvest_verify(request: Request, player: str | None = None):
    """Member-level result for one recorded attempt (plan §9, ). Reads only; never
    re-broadcasts. The client polls this until every member is `success` or `failed`: right
    after inclusion the game's lookup and reward actions may not be indexed yet, which is
    `pending` -- never "not sent" once a transaction id exists."""
    _require_same_origin(request)
    player = player_for_request(request, player)
    body = await request.json()
    return await run_in_threadpool(_verify_harvest_attempt, request, player, body)


def _verify_harvest_attempt(request: Request, player: str, body: dict[str, Any]):
    """Verify one signed harvest with ≤25 metered, player-attributed outbound reads."""
    from . import outbound_meter
    with outbound_meter.action("harvest.verify.receipt", player, 25):
        return _verify_harvest_attempt_inner(request, player, body)


def _verify_harvest_attempt_inner(request: Request, player: str, body: dict[str, Any]):
    """One game lookup and ≤1 fragment read per member; legacy fallback is unchanged."""
    from fides.game_verdict import game_verdict
    from . import game_read
    from .game_read import ACTION_MAX, OutboundBudget
    from . import receipt_model
    record = evidence.load_attempt(_config(request).root, player, str(body.get("attempt_id") or ""))
    if record is None:
        raise HTTPException(status_code=404, detail="unknown attempt")
    tx_id = record.get("tx_id")
    members = record["members"]
    if not tx_id:
        return {"outcome": "pending", "tx_id": None, "members": [{**m, "outcome": "pending"} for m in members]}
    diagnostic_started = diagnostic_time.monotonic()
    budget = OutboundBudget(player, ACTION_MAX)

    def metered_lookup(url):
        """Spend once and read one account-verifiable game lookup through the P0 meter."""
        parsed = urlsplit(url)
        if parsed.hostname != "api.splinterlands.com" or parsed.path != "/transactions/lookup":
            raise ValueError("unexpected game verdict endpoint")
        budget.spend()
        return game_read._get(parsed.hostname, parsed.path + "?" + parsed.query, timeout=12)

    verdict = game_verdict(tx_id, op_count=len(members), expected_nonces=[m["nonce"] for m in members], account=player, include_result=True, transport=metered_lookup)
    by_nonce = {op.get("nonce"): op for op in (verdict.get("evidence") or {}).get("operations") or []}
    # in a multi-operation transaction the game files operation i>=1 under "<tx>-<i>" (the
    # ledger, reward actions and lookup all use that id); only operation 0 uses the bare id. Members are
    # stored in operation order, so member i is looked up under its own id.
    effects_by_id: dict[str, Any] = {}

    def _effects_for(member_tx: str):
        if member_tx not in effects_by_id:
            try:
                effects_by_id[member_tx] = hv.ledger_effects(_paced(_client.resource_history, member_tx))
            except Exception as exc:
                _log.warning("resource_history_failed tx=%s error=%s", member_tx, type(exc).__name__)
                effects_by_id[member_tx] = None
        return effects_by_id[member_tx]

    out = []
    for index, m in enumerate(members):
        member_tx = tx_id if index == 0 else f"{tx_id}-{index}"
        op = by_nonce.get(m["nonce"])
        game = "unknown" if op is None else ("accepted" if op.get("success") else "refused")
        if m.get("op") in ("harvest_all", "harvest_resources", "tax_collection"):
            if op and (op.get("lookup_id") != member_tx or op.get("player", player) != player or
                       not isinstance(op.get("data"), dict) or op["data"].get("op") != m.get("op") or
                       op["data"].get("region_uid") != m.get("region_uid")):
                op = None
                game = "unknown"
            header = {"tx_id": tx_id, "operation_index": index, "player": player,
                      "custom_json_id": "sm_land_operation", "payload": (op or {}).get("data") or {},
                      "timestamp": None}
            lookup = {"trx_info": {"id": member_tx, "player": player, "type": "land_operation",
                                   "data": header["payload"], "success": op.get("success"),
                                   "result": op.get("result")}} if op else None
            extras = {}
            if game == "accepted":
                try:
                    budget.spend()
                    extras["fragment_history"] = game_read._get(
                        "vapi.splinterlands.com", "/land/resources/fragment_history/" + member_tx, timeout=12)
                except Exception as exc:
                    _log.warning("fragment_history_failed tx=%s error=%s", member_tx, type(exc).__name__)
            normalized = receipt_model.receipt(header, lookup, extras)
            has_amounts = normalized["state"] == "confirmed" and "game_amounts_unavailable" not in normalized["gaps"]
            has_finds_evidence = normalized["state"] == "confirmed" and "finds_unavailable" not in normalized["gaps"]
            evidence_kinds = ["game_result"] + (["fragment_history"] if has_finds_evidence else [])
            if has_amounts and m.get("op") == "harvest_all":
                evidence_kinds.append("regional_amounts")
                if has_finds_evidence:
                    evidence_kinds.append("regional_rewards")
            row = {**m, "game_state": game, "error": (op or {}).get("error"), "tx_id": member_tx,
                   "received_by_token": normalized["received"], "costs_by_token": normalized["costs"],
                   "taxes_by_token": normalized["tax"], "gross_by_token": normalized["before_tax"],
                   "finds": normalized["finds"], "receipt": normalized,
                   "evidence": evidence_kinds,
                   "reward_eligibility": {"fragment": "unknown", "labors_luck": "unknown"},
                   "outcome": "failed" if game == "refused" else "success" if game == "accepted" and
                   has_amounts else "pending"}
            out.append(row)
            continue
        regional = _regional_reward_rows(op, m, member_tx) if m.get("op") == "harvest_all" else None
        direct = hv.regional_harvest_effects([item for item, _ in regional]) if regional is not None else None
        effects = None if direct is not None else _effects_for(member_tx)
        region_effect = (effects or {}).get(int(hv.region_number(m["region_uid"]))) if effects else None
        row = {**m, "game_state": game, "error": (op or {}).get("error"), "tx_id": member_tx,
               "received_by_token": (region_effect or {}).get("received", {}),
               "costs_by_token": (region_effect or {}).get("spent", {}), "taxes_by_token": None,
               "gross_by_token": None, "finds": [], "evidence": []}
        if region_effect:
            row["evidence"].append("ledger")
        if direct is not None:
            row.update(received_by_token=direct["received"], costs_by_token=direct["spent"],
                       taxes_by_token=direct["taxes"], gross_by_token=direct["gross"])
            row["evidence"].append("regional_amounts")
        # Exact per-plot amounts from the plot's reward action for THIS transaction. : a tax claim writes one
        # reward action PER TOKEN (claim_taxes_aura, claim_taxes_grain, ...), so amounts add up per token.
        reward_deeds = list(m.get("deed_uids") or [])
        regional_complete = regional is not None
        if regional is not None:
            row["reward_eligibility"] = {"fragment": "ineligible", "labors_luck": "ineligible"}
            if any("has_labors_luck" not in metadata for _, metadata in regional):
                row["reward_eligibility"]["labors_luck"] = "unknown"
            for item, metadata in regional:
                for find in _reward_finds(item):
                    if find not in row["finds"]:
                        row["finds"].append(find)
                if item.get("fragment_chance", 0) or item.get("fragment_found"):
                    row["reward_eligibility"]["fragment"] = "eligible"
                if metadata.get("has_labors_luck") or item.get("labors_luck_uid"):
                    row["reward_eligibility"]["labors_luck"] = "eligible"
                    if "labors_luck_uid" not in item:
                        reward_deeds.append(item["deed_uid"])
            if reward_deeds:
                regional_complete = False
        matched_deeds = set()
        ra_started = False
        for deed in reward_deeds:
            try:
                payload = _paced(_client.reward_actions, deed, 5)
            except Exception:
                continue
            for ra in (payload.get("data") if isinstance(payload, dict) else None) or []:
                if not isinstance(ra, dict) or ra.get("trx_id") not in (tx_id, member_tx) or ra.get("deed_uid") != deed:
                    continue
                if regional is not None and ra.get("trx_id") != member_tx:
                    continue
                matched_deeds.add(deed)
                if regional is not None:
                    row["finds"].extend(find for find in _reward_finds(ra) if find not in row["finds"])
                    continue
                token = str(ra.get("resource_symbol") or "").upper()
                if not ra_started:
                    row["received_by_token"], row["taxes_by_token"], row["gross_by_token"] = {}, {}, {}
                    ra_started = True
                for field, target in (("amount_received", "received_by_token"), ("amount_taxed", "taxes_by_token"), ("claim_amount", "gross_by_token")):
                    value = hv._num(ra.get(field))
                    if value is not None:
                        row[target][token] = row[target].get(token, 0.0) + value
                spent = {"GRAIN": hv._num(ra.get("grain_required"))}
                for sym in ("WOOD", "STONE", "IRON"):
                    value = hv._num(ra.get(f"{sym.lower()}_required"))
                    if value:
                        spent[sym] = value
                row["costs_by_token"] = {k: v for k, v in spent.items() if v}
                for find in _reward_finds(ra):
                    if find not in row["finds"]:
                        row["finds"].append(find)
                row["evidence"].append("reward_action")
        if regional is not None and (regional_complete or set(reward_deeds) <= matched_deeds):
            row["evidence"].append("regional_rewards")
        if game == "refused":
            row["outcome"] = "failed"
        elif game == "accepted" or row["evidence"]:
            row["outcome"] = "success" if any(kind in row["evidence"] for kind in ("ledger", "regional_amounts", "reward_action")) else "pending"
        else:
            row["outcome"] = "pending"
        if game == "accepted" and m.get("op") == "harvest_all" and "regional_rewards" not in row["evidence"]:
            row["outcome"] = "pending"
        out.append(row)
    _reward_card_art(out)
    outcomes = {r["outcome"] for r in out}
    # each member keeps its own game outcome; while any member's game record is still unknown the attempt
    # stays pending (never a terminal "failed" that releases the transaction), even when another member was refused.
    game_unknown = any(r.get("game_state") == "unknown" for r in out)
    overall = "pending" if game_unknown else "failed" if "failed" in outcomes else ("success" if outcomes == {"success"} else "pending")
    if overall != "pending":
        # store the verified amounts (net received, costs, tax, finds per member) with the final state.
        import time as _time
        stored = [{k: r.get(k) for k in ("op", "region_uid", "deed_uids", "nonce", "outcome", "game_state", "error",
                                         "received_by_token", "costs_by_token", "taxes_by_token", "gross_by_token", "finds", "reward_eligibility", "evidence")}
                  for r in out]
        evidence.update_attempt(_config(request).root, player, record["attempt_id"],
                                state="reconciled" if overall == "success" else "game_refused",
                                members=stored, updated=_time.time())
    record_verification(account=player, tx_id=tx_id, attempt=None,
                        elapsed_ms=(diagnostic_time.monotonic() - diagnostic_started) * 1000,
                        operation_count=len(members), family="harvest",
                        outcome={"state": overall, "game": {"status": verdict.get("status")}})
    return {"outcome": overall, "game_status": verdict.get("status"), "tx_id": tx_id, "members": out}
