""": read-only snapshot previews for explicit worker upgrade plans."""

from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from . import client_limits
from .board import load_details_by_id
from .candidates import cooldown_state
from .combine_chain import ChainInputError, expected_card, resolve_upgrade_anchor, upgrade_inputs
from .routes_combine import _collection, _live_collection, _request_uids, _snapshot
from .level_cap import resolve_level_thresholds
from .security import player_for_request
from .worker_upgrade import _identity

router = APIRouter()


def _records(request: Request, player: str | None) -> tuple[str, list[dict[str, Any]], str, dict[str, dict[str, Any]]]:
    account = player_for_request(request, player)
    report, _ref, observed_at = _snapshot(request, account)
    records = _collection(report)
    uid_by_ref = {str(row.get("asset_ref")): str(row.get("uid") or row.get("card_uid"))
                  for row in records if row.get("asset_ref") and (row.get("uid") or row.get("card_uid"))}
    sources = {}
    for worker in report.get("workers", []):
        facts = {**worker, **worker.get("facts", {})} if isinstance(worker, dict) and isinstance(worker.get("facts", {}), dict) else worker if isinstance(worker, dict) else {}
        uid = facts.get("uid") or facts.get("card_uid") or uid_by_ref.get(str(facts.get("asset_ref") or worker.get("asset_ref")))
        if uid and facts.get("stake_ref_uid"):
            slot_index = facts.get("slot_index")
            if slot_index is None and str(facts.get("slot", "")).isdigit():
                slot_index = int(facts["slot"]) - 1
            sources[str(uid)] = {"deed_uid": facts["stake_ref_uid"], "slot_index": slot_index}
    return account, records, observed_at, sources


def _verdicts(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Use the board's observed card facts through the existing cooldown verdict."""
    now = datetime.now(timezone.utc)
    return {
        str(uid): cooldown_state(
            row["classification"].get("bucket", "") if isinstance(row.get("classification"), Mapping) else "",
            row.get("stake_end_date"), row.get("stake_type_uid") or (
                "STK-LND-WKR" if _identity(row) is not None and (row.get("uid") or row.get("card_uid")) else None), now, # B05: collection card identity
            survival_date=row.get("survival_date"),
        )
        for row in records
        if (uid := row.get("uid") or row.get("card_uid"))
    }


@router.post("/api/combine-chain/inputs")
def combine_chain_inputs(body: dict[str, Any], request: Request, player: str | None = None):
    client_limits.limit(request, "preview")
    if not isinstance(body, dict) or not isinstance(body.get("uid"), str) or not body["uid"]:
        raise HTTPException(400, "send one explicit Card ID as uid")
    account, records, observed_at, sources = _records(request, player)
    # one explicit live collection read after a visible unstake confirmation.
    if body.get("live") is True:
        report, observed_at = _live_collection(account)
        records = _collection(report)
    anchor_uid = body["uid"]
    try:
        anchor_uid = resolve_upgrade_anchor(records, anchor_uid)
    except ChainInputError as exc:
        if exc.code != "anchor_missing" or "plan_uids" not in body:
            raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc
    if "plan_uids" in body:
        plan_uids = body["plan_uids"]
        if not isinstance(plan_uids, list) or len(plan_uids) > 200 or any(not isinstance(uid, str) or not uid for uid in plan_uids):
            raise HTTPException(400, "plan_uids must contain at most 200 explicit card references")
        present = {str(row.get("uid") or row.get("card_uid")) for row in records}
        anchor_uid = next((uid for uid in [anchor_uid, *plan_uids] if uid in present), None)
        if anchor_uid is None:
            return {"account": account, "copies": [], "thresholds": [], "observed_at": observed_at}
    try:
        copies = upgrade_inputs(records, anchor_uid, account=account, details_by_id=load_details_by_id(), sources_by_uid=sources, verdicts_by_uid=_verdicts(records))
    except ChainInputError as exc:
        raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc
    index = {str(row.get("uid") or row.get("card_uid")): row for row in records}
    anchor = index[anchor_uid]
    detail = load_details_by_id().get(str(anchor.get("card_detail_id"))) or load_details_by_id().get(anchor.get("card_detail_id")) or {}
    rarity = {1: "common", 2: "rare", 3: "epic", 4: "legendary"}.get(detail.get("rarity"), anchor.get("rarity"))
    thresholds = resolve_level_thresholds(anchor.get("card_detail_id"), anchor.get("edition"), rarity,
                                         bool(anchor.get("gold") or anchor.get("foil") == 1), anchor.get("card_set"))
    for copy in copies:
        row = index.get(str(copy["uid"]), {})
        copy["asset"] = {key: row.get(key) for key in ("uid", "asset_ref", "card_detail_id", "edition", "foil", "gold", "card_set", "rarity", "level", "bcx", "land_base_pp")}
    return {"account": account, "copies": copies, "thresholds": thresholds, "observed_at": observed_at}


@router.post("/api/combine-chain/expected-card")
def combine_chain_expected_card(body: dict[str, Any], request: Request, player: str | None = None):
    client_limits.limit(request, "preview")
    if not isinstance(body, dict):
        raise HTTPException(400, "request body must be an object")
    uids = _request_uids(body)
    if len(uids) > 200:
        raise HTTPException(400, "a plan can contain at most 200 Card IDs")
    account, records, observed_at, sources = _records(request, player)
    index = {str(row.get("uid") or row.get("card_uid")): row for row in records}
    if any(uid not in index for uid in uids):
        raise HTTPException(409, {"code": "selected_cards_missing", "message": "a selected Card ID is absent"})
    selected = [index[uid] for uid in uids]
    try:
        details = load_details_by_id()
        copies = upgrade_inputs(records, uids[0], account=account, details_by_id=details, sources_by_uid=sources, verdicts_by_uid=_verdicts(records))
        by_uid = {copy["uid"]: copy for copy in copies}
        invalid = [uid for uid in uids if uid not in by_uid or not by_uid[uid]["eligible"]]
        if invalid:
            raise ChainInputError("inputs_unusable", "selected cards are not usable plan inputs")
        expected = expected_card(selected, details_by_id=details, capture_generated_at=observed_at, computed_at=observed_at)
    except ChainInputError as exc:
        raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc
    return {"account": account, "uids": uids, "expected": expected, "observed_at": observed_at}


def validate_plan_combine(review, selected, account, observed_at):
    """ : fail closed on changed inputs, printing, readiness or projection."""
    if not isinstance(review, dict) or review.get("state") != "ready":
        raise HTTPException(409, "Review the upgrade plan again before signing.")
    inputs = review.get("inputs")
    if not isinstance(inputs, list) or not 2 <= len(inputs) <= 200 or len(inputs) != len(selected):
        raise HTTPException(409, "The selected cards changed. Review the plan again.")
    if any(not isinstance(item, dict) for item in inputs):
        raise HTTPException(400, "Invalid plan inputs.")
    by_uid = {item.get("uid"): item for item in inputs}
    details = load_details_by_id()
    copies = upgrade_inputs(selected, str(selected[0].get("uid") or selected[0].get("card_uid")),
                            account=account, details_by_id=details, verdicts_by_uid=_verdicts(selected))
    if len(by_uid) != len(selected) or len(copies) != len(selected):
        raise HTTPException(409, "The selected cards changed. Review the plan again.")
    for row, copy in zip(selected, copies):
        item = by_uid.get(copy["uid"], {})
        owner = row.get("player") or row.get("owner")
        for field in ("stake_end_date", "survival_date", "unlock_date"):
            if row.get(field):
                try:
                    datetime.fromisoformat(str(row[field]).replace("Z", "+00:00"))
                except ValueError as exc:
                    raise HTTPException(409, "A selected copy has unreadable readiness. Review the plan again.") from exc
        verdict = cooldown_state("unstaked_apparent" if not row.get("stake_ref_uid") and not row.get("stake_end_date") else "",
                                 row.get("stake_end_date"), row.get("stake_type_uid") or "STK-LND-WKR",
                                 datetime.now(timezone.utc), survival_date=row.get("survival_date"))
        if (not owner or str(owner).lower() != account.lower() or not copy["eligible"]
                or copy["kind"] != "free" or verdict.get("ready") is not True
                or item.get("cc") != copy["bcx"]):
            raise HTTPException(409, "A selected copy changed or is not free. Review the plan again.")
        card = review.get("card") or {}
        if any(str(row.get(key)) != str(card.get(key)) for key in ("card_detail_id", "edition")):
            raise HTTPException(409, "The card printing changed. Review the plan again.")
        if bool(row.get("gold") or row.get("foil") == 1) != bool(card.get("gold") or card.get("foil") == 1):
            raise HTTPException(409, "The card printing changed. Review the plan again.")
    try:
        expected = expected_card(selected, details_by_id=details, computed_at=observed_at, capture_generated_at=observed_at)
    except ChainInputError as exc:
        raise HTTPException(409, "The result could not be checked. Review the plan again.") from exc
    wanted = review.get("expected") or {}
    if any(expected.get(key) != wanted.get(key) for key in ("bcx", "level", "base_pp", "survivor_uid")):
        raise HTTPException(409, "The combine result changed. Review the plan again.")
    return expected
