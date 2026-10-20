"""Read-only candidate ranking endpoint."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import capture as capture_mod
from .board import load_details_by_id
from .candidates import planned_card, plot_removal_delta, rank_candidates, stage_impact
from .metrics import ability_codes, land_ability_row
from .security import player_for_request

router = APIRouter()
logger = logging.getLogger("ceres.routes_candidates")


def _collection_too_large(report: dict) -> dict | None:
    failures = report.get("capture", {}).get("failures", []) if isinstance(report, dict) else []
    for failure in failures:
        if not isinstance(failure, dict) or failure.get("deed_uid") != "collection":
            continue
        outcome = failure.get("outcome")
        if isinstance(outcome, dict) and outcome.get("status") == "response_too_large":
            return outcome
    return None


def _slots_missing(report: dict, deed_uid: str) -> bool:
    """: a worker on this plot with no slot (the bulk read carries none -- `roster_predates_stake`).
    Priced like that, an over-cap plot answers `worker_slot_pending` and the picker's Boostable PP is blank."""
    return any(
        str(row.get("stake_ref_uid")) == str(deed_uid)
        and row.get("slot") is None
        and row.get("stake_type_uid") != "STK-LND-RUNI"
        for row in report.get("workers") or []
    )


def with_live_slots(config, player: str, deed_uid: str, report: dict) -> dict:
    """ / : when the board read has no worker slots for this plot, read the plot live from the
    game for this request (the four narrow reads, in memory only, nothing written) and price on that. An
    opened plot whose slots are already known costs nothing extra; a read the game does not answer leaves
    the report as it was."""
    if not _slots_missing(report, deed_uid):
        return report
    plot = next((p for p in report.get("plots") or [] if str(p.get("deed_uid")) == str(deed_uid)), None)
    plot_id = ((plot or {}).get("facts") or {}).get("plot_id")
    if isinstance(plot_id, bool) or not isinstance(plot_id, int) or plot_id <= 0:
        return report
    try:
        expanded = capture_mod.read_plot_live(player, str(deed_uid), plot_id)
    except Exception as exc:  # noqa: BLE001 -- the unrepaired report is today's behaviour, never an error
        logger.info("live slot read for %s/%s did not answer: %s", player, deed_uid, exc)
        return report
    live_plot = next((p for p in expanded.get("plots") or [] if str(p.get("deed_uid")) == str(deed_uid)), None)
    if not live_plot or (live_plot.get("asset_fetch") or {}).get("status") != "retrieved":
        return report
    # A new report for this request only: merge_reports builds new top-level lists and copies the rows it
    # changes, so the board read the other routes see is not modified.
    patch = capture_mod._asset_repair_patch(report, expanded, [str(deed_uid)])
    return capture_mod.merge_reports(report, patch, stale_after_seconds=config.stale_after_seconds)


def _candidate_staged_moves(raw: str | None, deed_uid: str) -> list[dict] | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise HTTPException(status_code=400, detail="staged_moves must be valid JSON")
    if len(raw) > 20_000:
        raise HTTPException(status_code=400, detail="staged_moves is too large")
    try:
        moves = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="staged_moves must be valid JSON") from exc
    if not isinstance(moves, list):
        raise HTTPException(status_code=400, detail="staged_moves must be a list of at most 50 objects")
    result = []
    for move in moves:
        if not isinstance(move, dict):
            raise HTTPException(status_code=400, detail="every staged move must be an object")
        copied = dict(move)
        # The browser keeps one account-wide plan and callers may send that plan
        # unchanged. The candidate projection is plot-local, so discard entries
        # for another deed here; stage-impact/rank validation handles this deed's
        # action and field contract in one place.
        if copied.get("deed_uid") is not None and str(copied["deed_uid"]) != str(deed_uid):
            continue
        copied.setdefault("deed_uid", deed_uid)
        result.append(copied)
    if len(result) > 50:
        raise HTTPException(status_code=400, detail="staged_moves must be a list of at most 50 objects")
    return result


@router.get("/api/plots/{deed_uid}/candidates")
def get_candidates(
    deed_uid: str,
    request: Request,
    group_id: str = Query(...),
    player: str | None = None,
    q: str | None = None,
    min_pp: float | None = Query(None, ge=0),
    max_pp: float | None = Query(None, ge=0),  # small cards to fill the Base PP cap precisely
    rarity: str | None = None,
    splinter: str | None = None,
    edition: int | None = Query(None, ge=0),
    bucket: str = "all",
    sort: str = "construction_pp",
    preset: str | None = None,
    include_cooling: bool = False,
    include_rented_in: bool = False,
    include_terrain_penalised: bool = False,
    land_only: bool = False,  # cards with land abilities (Land set + Visions of Verico)
    ability: list[str] = Query(default=[]),  # filter before pagination
    include_on_land: bool = False,  # cards already staked on another plot are hidden unless asked for
    include_rented: bool | None = None,
    slot: int | None = Query(None, ge=0),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    reserved_asset_ref: list[str] = Query(default=[]),
    released_asset_ref: list[str] = Query(default=[]),
    bloodline_sub_type: list[str] = Query(default=[]),
    staged_moves: Annotated[str | None, Query(max_length=20_000)] = None,
    planned_cards: Annotated[str | None, Query(max_length=100_000)] = None,
    staged_worksite: Annotated[str | None, Query(max_length=32)] = None,
    staged_worksite_type: Annotated[str | None, Query(max_length=32)] = None,
):
    config = request.app.state.ceres_config
    player = player_for_request(request, player)
    try:
        report, _ref = capture_mod.latest_snapshot(config, player)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="no stored board is available; load the board before continuing") from exc
    if report is None:
        raise HTTPException(status_code=503, detail="no stored board is available; load the board before continuing")
    if include_rented is not None:
        include_rented_in = include_rented
    details_by_id = load_details_by_id()
    collection_failure = _collection_too_large(report) if group_id in {"worker", "runi"} else None
    if not any(str(row.get("deed_uid")) == str(deed_uid) for row in report.get("plots", [])):
        raise HTTPException(status_code=404, detail=f"unknown deed_uid: {deed_uid}")
    report = with_live_slots(config, player, deed_uid, report)  # Boostable PP never priced without slots
    for name, value in (("staged_worksite", staged_worksite), ("staged_worksite_type", staged_worksite_type)):
        if value is not None and (not isinstance(value, str) or len(value) > 32):
            raise HTTPException(status_code=400, detail=f"{name} must be a string of at most 32 characters")
    if staged_worksite is not None and staged_worksite_type is not None and staged_worksite != staged_worksite_type:
        raise HTTPException(status_code=400, detail="staged_worksite and staged_worksite_type disagree")
    parsed_staged_moves = _candidate_staged_moves(staged_moves, deed_uid)

    # bounded client summaries are computation inputs, never a server store.
    summaries = []
    if isinstance(planned_cards, str):
        try:
            summaries = json.loads(planned_cards)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="planned_cards must be valid JSON") from exc
        if not isinstance(summaries, list) or len(summaries) > 200 or any(
            not isinstance(plan, dict) or not isinstance(plan.get("id"), str) or len(plan["id"]) > 128
            or not isinstance(plan.get("card"), dict) or not isinstance(plan.get("expected"), dict)
            for plan in summaries
        ):
            raise HTTPException(status_code=400, detail="planned_cards must contain at most 200 plan summaries")

    def rank(source_report: dict) -> dict:
        result = rank_candidates(
            source_report, deed_uid, group_id, player=player, now=datetime.now(timezone.utc), q=q, min_pp=min_pp, max_pp=max_pp,
            rarity=rarity, splinter=splinter, edition=edition, bucket=bucket, sort=sort, preset=preset,
            include_cooling=include_cooling, include_rented_in=include_rented_in,
            include_terrain_penalised=include_terrain_penalised, land_only=land_only, include_on_land=include_on_land,
            ability_filter=frozenset(str(code).upper() for code in (ability if isinstance(ability, (list, tuple, set)) else []) if str(code)),
            limit=limit, offset=offset,
            # `slot=None` (the query param genuinely omitted) is passed through as-is
            # rather than collapsed to `0` -- `0` is a legitimate, resolvable slot index
            # (the first worker slot) that a real click sends, and collapsing "not
            # provided" into the same value as "slot 1, explicitly" would make
            # `rank_candidates`'s fail-safe fallback unable to tell them apart. See
            # `candidates.py::_resolve_real_slot`.
            slot=slot,
            reserved_asset_refs=set(reserved_asset_ref) if isinstance(reserved_asset_ref, (list, tuple, set)) else set(),
            released_asset_refs=set(released_asset_ref) if isinstance(released_asset_ref, (list, tuple, set)) else set(),
            details_by_id=details_by_id,
            staged_moves=parsed_staged_moves,
            staged_worksite_type=staged_worksite_type or staged_worksite,
            bloodline_sub_types=({str(value) for value in bloodline_sub_type if str(value)} or None)
            if isinstance(bloodline_sub_type, (list, tuple, set)) else None,
        )

        if group_id == "worker" and offset == 0:
            planned = [planned_card(source_report, deed_uid, plan, player=player, slot=slot,
                                    details_by_id=details_by_id, staged_moves=parsed_staged_moves,
                                    staged_worksite_type=staged_worksite_type or staged_worksite)
                       for plan in summaries]
            result["candidates"] = planned + result["candidates"]
            result["returned"] += len(planned)
        return result

    try:
        if collection_failure is not None:
            endpoint = collection_failure.get("endpoint", "the collection endpoint")
            trace_id = collection_failure.get("trace_id", "unavailable")
            raise capture_mod.CardPoolUnavailable(
                f"{endpoint} exceeded its response limit (trace {trace_id})"
            )
        body = rank(report)
    except capture_mod.CardPoolUnavailable as exc:
        logger.warning("candidate card pool unavailable for %s/%s; refreshing live collection: %s", player, deed_uid, exc)
        try:
            report = capture_mod.expand_plot(config, player, deed_uid, include_collection=True)
            body = rank(report)
        except Exception as refresh_exc:
            logger.warning("live candidate card-pool refresh failed for %s/%s: %s", player, deed_uid, refresh_exc)
            return JSONResponse(
                status_code=202,
                content={
                    "status": "card_pool_refresh_retryable",
                    "source": "card_pool_refreshing",
                    "message": "Refreshing your cards…",
                    "candidates": [],
                    # Keep the fill chain from treating a pending Runi pool as an empty pool.
                    "total": -1,
                },
                headers={"Retry-After": "2"},
            )
    except KeyError as exc:
        if exc.args != (deed_uid,):
            raise
        raise HTTPException(status_code=404, detail=f"unknown deed_uid: {deed_uid}") from exc
    except LookupError as exc:
        # candidates.py's own message (e.g. an unavailable snapshot item family) -- domain
        # text describing what is missing, not a wrapped I/O exception.
        logger.warning("candidate source unavailable for %s/%s: %s", player, deed_uid, exc)
        if group_id in {"worker", "runi"}:
            raise HTTPException(
                status_code=503,
                detail="card collection could not be read; refresh the board and try again",
            ) from exc
        raise HTTPException(status_code=503, detail=f"candidate source unavailable: {exc}") from exc
    except ValueError as exc:
        # Authored request-validation reasons from rank_candidates (e.g. "unknown group_id: X"
        # "sort must be construction_pp, net_gain, base_pp, name, or boost") -- actionable for
        # the caller, not internal exception text.
        logger.warning("candidate request rejected for %s/%s: %s", player, deed_uid, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for candidate in body.get("candidates", []):
        candidate["land_abilities"] = land_ability_row(
            candidate.get("card_detail_id"), candidate.get("level"), details_by_id,
        ) or []
        candidate["sole_holder_for"] = ability_codes(candidate["land_abilities"])
    return JSONResponse(content=body)


@router.post("/api/plots/{deed_uid}/removal-impact")
def post_removal_impact(deed_uid: str, body: dict, request: Request, player: str | None = None):
    """The true plot-wide harvest-PP delta of removing one or more staked assets from this
    plot in one batch -- see `ceres.candidates.plot_removal_delta`. Read-only: this never
    stages, applies, or broadcasts anything, it only prices a hypothetical removal so the
    editor can show the real cost before the user stages it."""
    config = request.app.state.ceres_config
    player = player_for_request(request, player)
    asset_refs = body.get("asset_refs") if isinstance(body, dict) else None
    if not isinstance(asset_refs, list) or not asset_refs:
        raise HTTPException(status_code=400, detail="request body must contain a non-empty asset_refs array")
    try:
        report, _ref = capture_mod.latest_snapshot(config, player)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="no stored board is available; load the board before continuing") from exc
    if report is None:
        raise HTTPException(status_code=503, detail="no stored board is available; load the board before continuing")
    try:
        report = with_live_slots(config, player, deed_uid, report)
        body_out = plot_removal_delta(report, deed_uid, [str(ref) for ref in asset_refs], details_by_id=load_details_by_id())
    except KeyError as exc:
        if exc.args != (deed_uid,):
            raise
        raise HTTPException(status_code=404, detail=f"unknown deed_uid: {deed_uid}") from exc
    except ValueError as exc:
        # Authored reasons from plot_removal_delta (e.g. "requested asset_refs are not staked
        # on deed X") -- domain refusal text, not internal exception detail.
        logger.warning("removal-impact request rejected for %s/%s: %s", player, deed_uid, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(content=body_out)


@router.post("/api/plots/{deed_uid}/stage-impact")
def post_stage_impact(deed_uid: str, body: dict, request: Request, player: str | None = None):
    """Price the complete staged move list against one final roster.

    Each incoming or evicted asset contributes its own final-state harvest once;
    the shared ability, bloodline, cap, and redistribution residual is attributed
    to the first move in slot order, so per-move attribution sums to the server's
    `harvest_pp_delta` rather than double-counting plot-wide effects.
    """
    if not isinstance(body, dict) or not isinstance(body.get("moves"), list) or not body["moves"]:
        raise HTTPException(status_code=400, detail="request body must contain a non-empty moves array")
    if any(not isinstance(move, dict) for move in body["moves"]):
        raise HTTPException(status_code=400, detail="every staged move must be an object")
    moves = []
    for move in body["moves"]:
        copied = dict(move)
        copied.setdefault("deed_uid", deed_uid)
        moves.append(copied)
    config = request.app.state.ceres_config
    account = player_for_request(request, player)
    try:
        report, _ref = capture_mod.latest_snapshot(config, account)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="no stored board is available; load the board before continuing") from exc
    if report is None:
        raise HTTPException(status_code=503, detail="no stored board is available; load the board before continuing")
    try:
        report = with_live_slots(config, account, deed_uid, report)
        body_out = stage_impact(report, deed_uid, moves, details_by_id=load_details_by_id())
    except KeyError as exc:
        if exc.args != (deed_uid,):
            raise
        raise HTTPException(status_code=404, detail=f"unknown deed_uid: {deed_uid}") from exc
    except ValueError as exc:
        logger.warning("stage-impact request rejected for %s/%s: %s", account, deed_uid, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(content=body_out)
