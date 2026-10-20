"""Read-only preview and operation-building routes for explicit card combines."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import logging
import math
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from fides_cards.ops import CombineError, build_combine_op, survivor_rule
from fides_cards.render import hazard_confirmation

from . import capture as capture_mod
from . import client_limits
from . import combine_evidence
from .security import player_for_request
from .transaction_identity import app_identity

router = APIRouter()
logger = logging.getLogger("ceres.routes_combine")


def _request_uids(body: Mapping[str, Any]) -> list[str]:
    if "uids" not in body:
        if "cards" in body:
            raise HTTPException(status_code=400, detail={
                "code": "explicit_uids_required",
                "message": "sm_combine_all selector shapes are excluded; send an explicit uids array",
            })
        raise HTTPException(status_code=400, detail="request body must contain a uids array")
    uids = body.get("uids")
    if not isinstance(uids, list):
        raise HTTPException(status_code=400, detail={
            "code": "explicit_uids_required",
            "message": "uids must be an array of explicit Card IDs",
        })
    if len(uids) < 2:
        raise HTTPException(status_code=400, detail="a combine requires at least two Card IDs")
    if any(isinstance(uid, Mapping) for uid in uids):
        raise HTTPException(status_code=400, detail={
            "code": "explicit_uids_required",
            "message": "sm_combine_all selector shapes are excluded; select explicit Card IDs",
        })
    if any(not isinstance(uid, str) or not uid for uid in uids):
        raise HTTPException(status_code=400, detail="every combine input must be a non-empty Card ID")
    if len(set(uids)) != len(uids):
        raise HTTPException(status_code=400, detail="combine input Card IDs must be unique")
    return uids


def _record_from_report_card(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    facts = entry.get("facts")
    if isinstance(facts, Mapping):
        record = dict(facts)
        for field in ("uid", "card_uid", "asset_ref"):
            if field not in record and entry.get(field) is not None:
                record[field] = entry[field]
        return record
    return dict(entry)


def _collection(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    cards = report.get("cards")
    coverage = report.get("coverage")
    collection_coverage = coverage.get("collection") if isinstance(coverage, Mapping) else None
    if not isinstance(cards, list) or not isinstance(collection_coverage, Mapping) or collection_coverage.get("complete") is not True:
        raise HTTPException(status_code=409, detail={
            "code": "collection_incomplete",
            "message": "the stored board does not contain a complete card collection; load the board again",
        })
    records = [_record_from_report_card(entry) for entry in cards]
    if any(record is None for record in records):
        raise HTTPException(status_code=409, detail={
            "code": "collection_incomplete",
            "message": "the stored board contains an incomplete card record",
        })
    return [record for record in records if record is not None]


def _observed_at(report: Mapping[str, Any], ref: Any) -> str:
    stamp = report.get("generated_at") or getattr(ref, "generated_at", None)
    if not stamp:
        raise HTTPException(status_code=409, detail={
            "code": "snapshot_incomplete",
            "message": "the stored board has no observation timestamp; load the board again",
        })
    return str(stamp)


def _bcx(record: Mapping[str, Any]) -> int | float | None:
    value = record.get("bcx")
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _future(value: Any) -> bool:
    if not value:
        return False
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp > datetime.now(timezone.utc)


def _state_reasons(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    for record in records:
        uid = record.get("uid") or record.get("card_uid")
        if not uid:
            continue
        if record.get("is_unstaking") or record.get("unstaking") or _future(record.get("stake_end_date")):
            reasons.append({"uid": str(uid), "reason": "Unstaking from Land"})
        elif record.get("stake_ref_uid") and record.get("stake_end_date") in (None, ""):
            # stake_ref_uid PERSISTS after a stake ends -- only a live stake (no
            # stake_end_date at all) still blocks; a past end date means the
            # field is stale and the card is free to combine.
            reasons.append({"uid": str(uid), "reason": "Staked on Land"})
    return reasons


def _snapshot(request: Request, player: str) -> tuple[dict[str, Any], Any, str]:
    config = request.app.state.ceres_config
    try:
        report, ref = capture_mod.latest_snapshot(config, player)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="no stored board is available; load the board before continuing") from exc
    if report is None:
        raise HTTPException(status_code=503, detail="no stored board is available; load the board before continuing")
    if not isinstance(report, Mapping):
        raise HTTPException(status_code=503, detail="the stored board is not a valid report")
    try:
        report = capture_mod.load_card_pool(dict(report))
    except LookupError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "collection_incomplete",
            "message": str(exc),
        }) from exc
    return dict(report), ref, _observed_at(report, ref)


def _preview_data(uids: list[str], report: Mapping[str, Any], observed_at: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    records = _collection(report)
    index = {}
    for record in records:
        uid = record.get("uid") or record.get("card_uid")
        if uid:
            index.setdefault(str(uid), record)
    missing = [uid for uid in uids if uid not in index]
    if missing:
        raise HTTPException(status_code=409, detail={
            "code": "selected_cards_missing",
            "message": "the complete stored board does not contain every selected Card ID",
            "missing_uids": missing,
        })
    selected = [index[uid] for uid in uids]
    try:
        predicted = survivor_rule(selected)
        hazard = hazard_confirmation(uids, selected, observed_at=observed_at)
    except (CombineError, ValueError) as exc:
        # CombineError/ValueError here come from fides_cards/ops.py's own domain checks
        # (e.g. "combine inputs must be the same edition") -- authored refusal text, never a
        # wrapped I/O exception -- the player needs the specific reason to fix their selection.
        logger.warning("combine preview validation failed: %s", exc)
        raise HTTPException(status_code=409, detail={
            "code": "combine_refused",
            "message": str(exc),
        }) from exc
    if predicted == "unknown":
        raise HTTPException(status_code=409, detail={
            "code": "card_facts_incomplete",
            "message": "each selected card needs a readable Card ID, card identity, and CC",
            "preview": {"uids": uids, "hazard": hazard},
        })
    bcx_values = [_bcx(record) for record in selected]
    if any(value is None for value in bcx_values):
        raise HTTPException(status_code=409, detail={
            "code": "card_facts_incomplete",
            "message": "each selected card needs a readable CC",
            "preview": {"uids": uids, "hazard": hazard},
        })
    total = sum(value for value in bcx_values if value is not None)
    preview = {
        "account": None,
        "uids": uids,
        "predicted_survivor_uid": predicted,
        "resulting_bcx": total,
        "hazard": hazard,
        "buildable": True,
        "observed_at": observed_at,
        # stable signing witness for callers that must compare a reviewed
        # combine with a fresh live rebuild. These identities stay in hidden data.
        "witness": [
            {
                "uid": str(record.get("uid") or record.get("card_uid")),
                "card_detail_id": record.get("card_detail_id"),
                "edition": record.get("edition"),
                "foil": record.get("foil", record.get("gold")),
                "bcx": _bcx(record),
            }
            for record in selected
        ],
    }
    return preview, selected, {"predicted": predicted, "hazard": hazard}


def _live_collection(account: str) -> tuple[dict[str, Any], str]:
    try:
        response = capture_mod.MnemosyneEvidence(account).collection()
        data = response.data
        if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
            raise ValueError("invalid collection envelope")
        stamp = str(response.retrieved_at)
        return {"cards": data["cards"], "coverage": {"collection": {"complete": True, "fetched_at": stamp}}}, stamp
    except Exception as exc:
        raise HTTPException(status_code=503, detail="The live card collection could not be checked. Nothing was sent; try again.") from exc


def _prepare(request: Request, body: dict[str, Any], player: str | None, *, fresh: bool = False) -> tuple[str, dict[str, Any], list[dict[str, Any]], Any]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="request body must be an object")
    uids = _request_uids(body)
    account = player_for_request(request, player)
    if fresh:
        report, observed_at = _live_collection(account)
    else:
        report, _ref, observed_at = _snapshot(request, account)
    preview, selected, _details = _preview_data(uids, report, observed_at)
    state_reasons = _state_reasons(selected)
    if state_reasons:
        preview["account"] = account
        preview["buildable"] = False
        raise HTTPException(status_code=409, detail={
            "code": "combine_refused",
            "message": "selected cards are not currently combinable",
            "reasons": state_reasons,
            "preview": preview,
        })
    if "plan_review" in body:
        # the plan witness is checked against this same live read.
        from .routes_combine_chain import validate_plan_combine
        preview["expected"] = validate_plan_combine(body["plan_review"], selected, account, observed_at)
    try:
        # The app identity is server-authoritative and is never taken from the client.
        build_records = selected
        if "plan_review" in body:
            # max CC caps production; only plan-mode allows over-combining.
            build_records = [{k: v for k, v in row.items() if k not in {"max_level", "is_max_level"}} for row in selected]
        operation = build_combine_op(uids, build_records, account=account, app=app_identity(), nonce=body.get("nonce"))
    except CombineError as exc:
        preview["account"] = account
        preview["buildable"] = False
        # Same as _preview_data's CombineError above -- an authored domain reason, e.g.
        # "cannot combine selected Card IDs: <uid> Staked on Land", never internal detail.
        logger.warning("combine operation construction failed: %s", exc)
        raise HTTPException(status_code=409, detail={
            "code": "combine_refused",
            "message": str(exc),
            "preview": preview,
        }) from exc
    preview["account"] = account
    return account, preview, selected, operation


@router.post("/api/combine/preview")
def combine_preview(body: dict[str, Any], request: Request, player: str | None = None):
    _account, preview, _selected, _operation = _prepare(request, body, player)
    return JSONResponse(content=preview)


@router.post("/api/combine/build")
def combine_build(body: dict[str, Any], request: Request, player: str | None = None):
    client_limits.limit(request, "preview")  # S10 / L1-01: it reads a whole card collection
    account, preview, selected, operation = _prepare(request, body, player, fresh=True)
    nonce = operation.json_payload.get("n")
    # fix -- persist the pre-combine records this call just read live, keyed
    # by the op's own nonce, so /api/op-verify can use them instead of re-deriving
    # expected_records from a snapshot that is stale or absent by the time it runs
    # (see ceres/combine_evidence.py's docstring for the full mechanism). Best-effort
    # a save failure must never block returning the built operation.
    if nonce:
        config = request.app.state.ceres_config
        try:
            combine_evidence.save(config, nonce, account=account, uids=preview["uids"], records=selected)
        except OSError as exc:
            logger.warning("combine evidence save failed for %s (nonce %s): %s", account, nonce, exc)
            if "plan_review" in body:
                raise HTTPException(503, "The combine evidence could not be saved. Nothing was sent.") from exc
    recovery = None
    if "plan_review" in body:
        # A04: sealed expected values travel with the unresolved local plan.
        try:
            recovery = combine_evidence.recovery_envelope(config, nonce, account=account, records=selected, expected=preview["expected"])
        except (OSError, ValueError) as exc:
            raise HTTPException(503, "Recovery evidence could not be prepared. Nothing was sent.") from exc
    return JSONResponse(content={"operation": operation.as_keychain_tuple(), "preview": preview, "recovery": recovery})
