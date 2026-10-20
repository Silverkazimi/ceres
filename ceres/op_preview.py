"""Build and validate the browser-facing Land operation preview."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fides import op as fides_op
from fides.op import chunk_ops, required_account, required_key_tier, requests_for_broadcast
from fides_land.intent import StakeIntent
from fides_land.ops import (
    STAKE_TYPE_POWER_CORE,
    STAKE_TYPE_RUNI,
    STAKE_TYPE_TITLE,
    STAKE_TYPE_TOTEM,
    STAKE_TYPE_WORKER,
    LandOpError,
    intents_to_ops,
)
from fides_land.render import allowlist_rules, evaluate_allowlist
from fides_land.waves import stake_type_is_cooling

from . import capture as capture_mod
from .board import RUNI_CARD_DETAIL_ID
from .candidates import (
    _plots_cached,
    _project_staged_plot_state,
    _projected_worksite_row,
    cooldown_state,
    power_satisfied,
)
from .capacity import potential_worker_capacity, powered_worker_slots, structure_capacity_index
from .metrics import ability_codes, classify_idle_availability, land_ability_row
from .notices import format_notice, notice_catalog
from .worksite_broadcast import build_worksite_change
from .worksite_swap import legal_targets

GROUP_STAKE_TYPES = {
    "worker": STAKE_TYPE_WORKER,
    "power_core": STAKE_TYPE_POWER_CORE,
    "totem": STAKE_TYPE_TOTEM,
    "title": STAKE_TYPE_TITLE,
    "runi": STAKE_TYPE_RUNI,
}
STAKE_TYPE_LABELS = {
    STAKE_TYPE_POWER_CORE: "Power Core",
    STAKE_TYPE_WORKER: "worker",
    STAKE_TYPE_TOTEM: "Totem",
    STAKE_TYPE_TITLE: "Title",
    STAKE_TYPE_RUNI: "Runi",
}
# Every stake type Ceres can address is stageable. Both remaining unknowns are about
# TIMING, not payload shape: Title's restake cooldown duration is unpublished. That is
# surfaced as a warning on the move, never as a refusal -- no asset is locked onto a plot.
UNVERIFIED_TYPES: set[str] = set()


class PreviewValidationError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors
        reasons = "; ".join(str(item.get("reason")) for item in errors if item.get("reason"))
        super().__init__(f"staged move validation failed: {reasons}" if reasons else "staged move validation failed")


def _first_clear_op_limit(max_ops_per_tx: int) -> int:
    return min(max_ops_per_tx, fides_op.DEFAULT_MAX_OPS_PER_TX)


def _uid(row: dict[str, Any]) -> str | None:
    facts = row.get("facts") if isinstance(row.get("facts"), dict) else {}
    value = row.get("uid") or row.get("card_uid") or row.get("item_uid") or facts.get("uid")
    return str(value) if value else None


def _merge_survival_facts(report: dict[str, Any], asset_ref: str, facts: dict[str, Any], card: Any = ...) -> dict[str, Any]:
    if card is ...:
        card = next((row for row in report.get("cards", []) if str(row.get("asset_ref")) == asset_ref), None)
    card_facts = card.get("facts") if isinstance(card, dict) else None
    if not isinstance(card_facts, dict):
        return facts
    for key in ("survival_date", "survival_bracket", "survival_mode_disabled"):
        if key in card_facts:
            facts[key] = card_facts[key]
    return facts


class AssetIndex:
    """: one pass over the collection, then O(1) lookups by asset_ref.

    Keeps `_asset_record`'s precedence exactly: the FIRST matching row of each list wins (as the
    linear scans did), and workers beat bonus_assets beat cards beat unassigned items. Build it per
    request from the report being checked; never keep it across captures.
    """

    __slots__ = ("workers", "bonus_assets", "cards", "unassigned")

    def __init__(self, report: dict[str, Any]):
        self.workers = self._first_by_ref(report.get("workers", []))
        self.bonus_assets = self._first_by_ref(report.get("bonus_assets", []))
        self.cards = self._first_by_ref(report.get("cards", []))
        unassigned: set[str] = set()
        for family in (report.get("unassigned_items") or {}).values():
            if isinstance(family, dict):
                unassigned.update(str(value) for value in family.get("uids", []))
        self.unassigned = unassigned

    @staticmethod
    def _first_by_ref(rows: Any) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for row in rows:
            index.setdefault(str(row.get("asset_ref")), row)
        return index


def _asset_record(report: dict[str, Any], asset_ref: str, group_id: str, index: AssetIndex | None = None) -> dict[str, Any] | None:
    wanted = GROUP_STAKE_TYPES[group_id]
    if index is not None:
        worker = index.workers.get(asset_ref)
        bonus = index.bonus_assets.get(asset_ref) if worker is None else None
        card = index.cards.get(asset_ref)
        survival_card: Any = card
    else:
        worker = next((row for row in report.get("workers", []) if str(row.get("asset_ref")) == asset_ref), None)
        bonus = next((row for row in report.get("bonus_assets", []) if str(row.get("asset_ref")) == asset_ref), None) if worker is None else None
        card = ...
        survival_card = ...
    if worker is not None:
        row = worker
        facts = _merge_survival_facts(report, asset_ref, dict(row), survival_card)
        return {
            "asset_ref": asset_ref, "uid": _uid(row), "stake_type_uid": row.get("stake_type_uid", STAKE_TYPE_WORKER),
            "deed_uid": row.get("stake_ref_uid"), "slot": row.get("slot"), "staked": True, "facts": facts,
            "bucket": "staked",
        }
    if bonus is not None:
        row = bonus
        facts = _merge_survival_facts(report, asset_ref, dict(row), survival_card)
        return {
            "asset_ref": asset_ref, "uid": _uid(row), "stake_type_uid": row.get("stake_type_uid", wanted),
            "deed_uid": row.get("stake_ref_uid"), "slot": None, "staked": True, "facts": facts,
            "bucket": "staked",
        }
    if card is ...:
        card = next((row for row in report.get("cards", []) if str(row.get("asset_ref")) == asset_ref), None)
    if card is not None:
        row = card
        facts = dict(row.get("facts") or {})
        if row.get("uid") and not facts.get("uid"):
            facts["uid"] = row["uid"]
        # A stake reference persists on a card after it is unstaked, so trusting it
        # alone reports an idle card as staked and emits an unstake nobody asked for.
        staked = bool(facts.get("stake_ref_uid")) and facts.get("stake_end_date") in (None, "")
        bucket = "staked" if staked else (row.get("classification", {}).get("bucket") or "unstaked_apparent")
        # An unstaked collection card never carries its own `stake_type_uid` -- classify
        # it from the card's own identity (card_detail_id) rather than defaulting to
        # whichever group the caller asked for. Defaulting to `wanted` here is what let a
        # plain monster card be staged and signed as a Runi ( finding 2): the class
        # check just below this call site can only catch a mismatch it was actually told.
        inferred = "STK-LND-RUNI" if facts.get("card_detail_id") == RUNI_CARD_DETAIL_ID else STAKE_TYPE_WORKER
        return {
            "asset_ref": asset_ref, "uid": _uid(row), "stake_type_uid": facts.get("stake_type_uid", inferred),
            "deed_uid": facts.get("stake_ref_uid"), "slot": facts.get("slot"),
            "staked": staked, "facts": facts, "bucket": bucket,
        }
    if index is not None:
        unassigned = asset_ref in index.unassigned
    else:
        unassigned = any(
            isinstance(family, dict) and asset_ref in {str(value) for value in family.get("uids", [])}
            for family in (report.get("unassigned_items") or {}).values()
        )
    if unassigned:
        return {
            "asset_ref": asset_ref, "uid": asset_ref, "stake_type_uid": wanted,
            "deed_uid": None, "slot": None, "staked": False, "facts": {},
            "bucket": "unstaked_apparent",
        }
    return None


def _record_error(errors: list[dict[str, Any]], index: int, move: dict[str, Any], reason: str, *, notice_code: str | None = None, error_code: str | None = None) -> None:
    error = {
        "index": index,
        "deed_uid": move.get("deed_uid"),
        "asset_ref": move.get("asset_ref"),
        "reason": reason,
    }
    if error_code:
        error["code"] = error_code  # lets the route answer without string matching
    if notice_code:
        error["notice"] = format_notice(notice_code, context={"deed_uid": move.get("deed_uid"), "reason": reason}, reason=reason)
    errors.append(error)


def _as_slot(move: dict[str, Any], group_id: str, action: str) -> int | None:
    if group_id != "worker":
        return None
    if action == "unstake":
        return None
    if move.get("slot") is not None:
        value = move["slot"]
    else:
        value = move.get("slot_index")
        if value is not None:
            value = int(value) + 1
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("worker moves require a positive slot or zero-based slot_index")
    return value


def _facts_for_eligibility(record: dict[str, Any]) -> dict[str, Any]:
    facts = dict(record.get("facts") or {})
    if record.get("deed_uid"):
        facts.setdefault("stake_ref_uid", record["deed_uid"])
    return facts


def _validate_asset(
    record: dict[str, Any],
    *,
    account: str,
    now: datetime,
) -> str | None:
    uid = record.get("uid")
    if not uid:
        return "snapshot has no asset UID; refresh with a full asset capture before signing"
    if record.get("stake_type_uid") not in GROUP_STAKE_TYPES.values():
        return f"snapshot has unknown stake type {record.get('stake_type_uid')!r}"
    available, reason = classify_idle_availability(_facts_for_eligibility(record), now, account)
    if not available:
        return reason or "asset is not currently eligible"
    return None


def _cooldown_refusal(record: dict[str, Any], stake_type: str, now: datetime) -> str | None:
    """Keep worker cooldowns; Runi and land items can move without that wait."""
    if not stake_type_is_cooling(stake_type):
        return None
    facts = _facts_for_eligibility(record)
    bucket = "staked" if record.get("staked") else (record.get("bucket") or "unstaked_apparent")
    cd = cooldown_state(bucket, facts.get("stake_end_date"), stake_type, now)
    if cd.get("ready"):
        return None
    name = facts.get("name") or record.get("asset_ref")
    ready_at = cd.get("ready_at")
    if ready_at:
        return f"{name} ({record.get('asset_ref')}) is in the ~3-day unstake cooldown until {ready_at}"
    reason = cd.get("reason") or "cooldown evidence unavailable"
    return f"{name} ({record.get('asset_ref')}) is in the ~3-day unstake cooldown ({reason}; refusing rather than guessing an expiry)"


def _survival_refusal(record: dict[str, Any], now: datetime) -> str | None:
    facts = _facts_for_eligibility(record)
    stake_type = str(record.get("stake_type_uid"))
    bucket = "staked" if record.get("staked") else (record.get("bucket") or "unstaked_apparent")
    cd = cooldown_state(
        bucket,
        facts.get("stake_end_date"),
        stake_type,
        now,
        survival_date=facts.get("survival_date"),
    )
    if cd.get("state") != "survival_locked":
        return None
    name = facts.get("name") or record.get("asset_ref")
    ready_at = cd.get("ready_at")
    if ready_at:
        return f"{name} ({record.get('asset_ref')}) is dead from Survival mode until {ready_at}; it may revive sooner at the season reset"
    return f"{name} ({record.get('asset_ref')}) is dead from Survival mode"


def sole_power_source_removed(
    plot: dict[str, Any] | None,
    deed_uid: str,
    core_delta: dict[str, int],
    runi_delta: dict[str, int],
) -> bool:
    """True when staging the batch's net effect leaves `deed_uid` with none of its three
    independently-sufficient power sources (the internal rules's measured rule: `is_powered =
    power_core OR energized OR runi`, 0 exceptions across 6,903 plots) while it still has
    seated workers or harvest PP at risk. DEC never substitutes for a power source and is
    never consulted here. Exposed as a standalone function (rather than inlined in the
    hazard loop) so it can be exercised directly in tests independent of the staging
    path -- `STK-LND-RUNI` is no longer in `UNVERIFIED_TYPES` (payload confirmed both
    directions), so a real Runi move reaches this hazard exactly as a Power Core move
    does."""
    if plot is None:
        return False
    sf = plot.get("staking_facts") or {}
    has_energized = bool(sf.get("is_energized"))
    after_core = (1 if sf.get("is_power_core_staked") else 0) + core_delta.get(deed_uid, 0) > 0
    after_runi = (1 if sf.get("is_runi_staked") else 0) + runi_delta.get(deed_uid, 0) > 0
    if after_core or has_energized or after_runi:
        return False
    current_workers = int(float(sf.get("worker_count") or 0))
    current_pp = float(sf.get("total_harvest_pp") or 0)
    return current_workers > 0 or current_pp > 0


def workers_staged_without_power(
    plot: dict[str, Any] | None,
    deed_uid: str,
    current_workers: int,
    worker_delta: int,
    core_delta: dict[str, int],
    runi_delta: dict[str, int],
) -> bool:
    """True when staging the batch's net effect leaves `deed_uid` with seated workers
    but neither a Power Core nor an Energized worker to power those slots. Exposed as a
    standalone function for the same reason: it can be exercised directly in tests
    independent of the staging path."""
    if plot is None:
        return False
    sf = plot.get("staking_facts") or {}
    after_core = (1 if sf.get("is_power_core_staked") else 0) + core_delta.get(deed_uid, 0) > 0
    after_runi = (1 if sf.get("is_runi_staked") else 0) + runi_delta.get(deed_uid, 0) > 0
    after_powered = after_core or after_runi or bool(sf.get("is_energized"))
    return current_workers + worker_delta > 0 and not after_powered


def _plot_state(report: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    plots = {str(row.get("deed_uid")): row for row in report.get("plots", []) if row.get("deed_uid")}
    workers: dict[str, list[dict[str, Any]]] = {}
    for worker in report.get("workers", []):
        if worker.get("stake_type_uid") == STAKE_TYPE_WORKER and worker.get("stake_ref_uid"):
            workers.setdefault(str(worker["stake_ref_uid"]), []).append(worker)
    return plots, workers


def _worker_roster_state(
    report: dict[str, Any],
    plot: dict[str, Any],
    deed_uid: str,
    workers_by_deed: dict[str, list[dict[str, Any]]],
    unstaked_refs: set[str],
) -> tuple[list[dict[str, Any]], int | None]:
    current_workers = [
        worker
        for worker in workers_by_deed.get(deed_uid, [])
        if str(worker.get("uid")) not in unstaked_refs
    ]
    bulk_worker_count = (plot.get("staking_facts") or {}).get("worker_count")
    if bulk_worker_count is None:
        return current_workers, None
    try:
        runi_rows = [
            worker
            for worker in report.get("workers", [])
            if worker.get("stake_type_uid") == STAKE_TYPE_RUNI
            and str(worker.get("stake_ref_uid")) == deed_uid
        ]
        runi_count = int(bool((plot.get("staking_facts") or {}).get("is_runi_staked")))
        runi_count = max(runi_count, len(runi_rows))
        unstaked_worker_count = sum(
            str(worker.get("uid")) in unstaked_refs
            for worker in workers_by_deed.get(deed_uid, [])
        )
        expected_workers = int(float(bulk_worker_count)) - runi_count - unstaked_worker_count
    except (TypeError, ValueError):
        return current_workers, None
    return current_workers, expected_workers


def _expand_plot_for_preview(
    deed_uid: str,
    account: str,
    config: Any | None,
    *,
    report: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Read one deed live when the snapshot cannot authorize a slot claim.

     / : a live read in memory only -- no plot patch, no reuse cache. The collection
    sweep stays off so preview latency scales with claimed deeds. A missing config or report, an
    unanswered read or a malformed result is an unavailable authority: ``None`` for the caller to refuse.
    """
    read_live = getattr(capture_mod, "read_plots_live", None)
    if config is None or not isinstance(report, dict) or not callable(read_live):
        return None
    try:
        result = read_live(config, account, [deed_uid], report)
    except Exception:
        return None
    if not isinstance(result, dict) or deed_uid in (result.get("failed") or []) or not isinstance(result.get("report"), dict):
        return None
    return result["report"]


def _serialized_bytes(operations: list[list[Any]]) -> int:
    return len(json.dumps(operations, separators=(",", ":")).encode("utf-8"))


def _normalise_staged_worksites(raw: Any) -> dict[str, str]:
    """Return the browser's unsigned worksite plans keyed by deed.

    The portfolio checkout sends a list; accepting one object as well keeps the
    plot-scoped preview useful to callers that do not have a portfolio checkout.
    First-clear intent is combined with its staged power/workers by . Existing
    worksite changes retain the separate post-stake signing sequence.
    """
    if raw in (None, [], {}):
        return {}
    entries = [raw] if isinstance(raw, dict) else raw
    if not isinstance(entries, list) or len(entries) > 50:
        raise ValueError("staged_worksites must be a list of at most 50 objects")
    result: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("every staged worksite must be an object")
        deed_uid = entry.get("deed_uid")
        target = entry.get("target", entry.get("worksite_type"))
        if not isinstance(deed_uid, str) or not deed_uid or len(deed_uid) > 256:
            raise ValueError("staged worksite deed_uid must be a non-empty string of at most 256 characters")
        if not isinstance(target, str) or not target or len(target) > 32:
            raise ValueError("staged worksite target must be a non-empty string of at most 32 characters")
        if deed_uid in result and result[deed_uid] != target:
            raise ValueError(f"staged worksite targets disagree for deed {deed_uid}")
        result[deed_uid] = target
    return result


def build_preview(
    report: dict[str, Any],
    moves: list[dict[str, Any]],
    *,
    account: str,
    config: Any | None = None,
    details_by_id: dict[int, dict[str, Any]] | None = None,
    staged_worksites: Any = None,
    now: datetime | None = None,
    app: str | None = None,
    # WHY: Hive enforces HIVE_CUSTOM_OP_BLOCK_LIMIT = 5 custom_json ops per account per block at
    # mempool admission (_push_transaction), not consensus; the configured default is 4 deliberately because the
    # budget is shared across Ceres's three independent broadcast paths with no coordination
    # leaving one spare op. 's block pacer would allow packing 5 later.
    max_ops_per_tx: int | None = None,
    max_bytes_per_tx: int = 6 * 1024,
) -> dict[str, Any]:
    if max_ops_per_tx is None:
        max_ops_per_tx = fides_op.DEFAULT_MAX_OPS_PER_TX
    if not isinstance(moves, list) or (not moves and not staged_worksites):
        raise ValueError("moves must be an array with moves or a staged worksite")
    if not isinstance(account, str) or not account:
        raise ValueError("account must be a non-empty string")
    if app is None:
        from .transaction_identity import app_identity
        app = app_identity()
    now = now or datetime.now(timezone.utc)
    staged_worksite_by_deed = _normalise_staged_worksites(staged_worksites)
    errors: list[dict[str, Any]] = []
    intents: list[StakeIntent] = []
    unstaked_refs: set[str] = set()
    staked_refs: set[str] = set()
    plots, workers_by_deed = _plot_state(report)
    slot_claims: dict[str, list[int]] = {}
    worker_count_delta: dict[str, int] = {}
    core_delta: dict[str, int] = {}
    runi_delta: dict[str, int] = {}
    worker_state_errors: set[str] = set()
    worker_stake_intents: dict[str, list[tuple[StakeIntent, dict[str, Any], int]]] = {}
    overlay_moves_by_deed: dict[str, list[dict[str, Any]]] = {}
    asset_move_indexes: dict[str, int] = {}
    capacity_worksite_deeds: set[str] = set()
    first_clear_worksites: dict[str, str] = {}
    intent_records: dict[int, dict[str, Any]] = {}

    for deed_uid, target in staged_worksite_by_deed.items():
        plot = plots.get(deed_uid)
        if plot is None:
            raise ValueError(f"staged worksite references unknown deed {deed_uid!r}")
        _projected, canonical_target = _projected_worksite_row(plot, target)
        allowed_targets = legal_targets(
            (plot.get("facts") or {}).get("terrain"),
            (plot.get("facts") or {}).get("resource"),
        )
        if allowed_targets is None:
            continue
        if canonical_target not in allowed_targets:
            raise ValueError(f"staged worksite target {canonical_target!r} is not legal for deed {deed_uid!r}")
        diagnostics = {item.get("type") for item in plot.get("diagnostics", []) if isinstance(item, dict)}
        if "no_worksite" in diagnostics:
            capacity_worksite_deeds.add(deed_uid)
            first_clear_worksites[deed_uid] = canonical_target

    energized_details = details_by_id
    if energized_details is None:
        try:
            from .board import load_details_by_id
            energized_details = load_details_by_id()
        except Exception:
            energized_details = {}

    def _energized_status(card: dict[str, Any] | None) -> bool | None:
        if not card:
            return False
        facts = card.get("facts") or card
        try:
            row = land_ability_row(
                int(facts.get("card_detail_id")), int(facts.get("level") or 1), energized_details or {},
            )
        except (TypeError, ValueError):
            return None
        if row is None:
            return None
        return "ENERGIZED" in ability_codes(row)

    def _is_energized(card: dict[str, Any] | None) -> bool:
        return _energized_status(card) is True

    def add_intent(move: dict[str, Any], record: dict[str, Any], direction: str, deed_uid: str, slot: int | None, index: int) -> None:
        uid = record.get("uid")
        stake_type = str(record.get("stake_type_uid"))
        if not uid:
            return
        if direction == "unstake":
            if uid in unstaked_refs:
                return
            unstaked_refs.add(uid)
        else:
            if uid in staked_refs:
                _record_error(errors, index, move, f"asset UID {uid!r} is staged for stake more than once")
                return
            staked_refs.add(uid)
        intent = StakeIntent(deed_uid=deed_uid, stake_type_uid=stake_type, direction=direction, asset_uid=uid, asset_ref=record.get("asset_ref"), slot=slot)
        intents.append(intent)
        intent_records[id(intent)] = record
        if stake_type == STAKE_TYPE_WORKER and direction == "stake":
            worker_stake_intents.setdefault(deed_uid, []).append((intent, move, index))
        if stake_type == STAKE_TYPE_WORKER:
            worker_count_delta[deed_uid] = worker_count_delta.get(deed_uid, 0) + (1 if direction == "stake" else -1)
        if stake_type == STAKE_TYPE_POWER_CORE:
            core_delta[deed_uid] = core_delta.get(deed_uid, 0) + (1 if direction == "stake" else -1)
        if stake_type == STAKE_TYPE_RUNI:
            runi_delta[deed_uid] = runi_delta.get(deed_uid, 0) + (1 if direction == "stake" else -1)

    for index, raw_move in enumerate(moves):
        move = raw_move if isinstance(raw_move, dict) else {}
        group_id = move.get("group_id")
        deed_uid = str(move.get("deed_uid") or "")
        action = move.get("action")
        if group_id not in GROUP_STAKE_TYPES:
            _record_error(errors, index, move, f"unknown group_id {group_id!r}")
            continue
        if not deed_uid or deed_uid not in plots:
            _record_error(errors, index, move, f"unknown deed_uid {deed_uid!r}")
            continue
        if action not in {"stake", "unstake", "replace"}:
            _record_error(errors, index, move, f"unknown action {action!r}")
            continue
        def linked_item_ref(ref: str) -> bool:
            uses = []
            for other in moves:
                if not isinstance(other, dict):
                    continue
                if str(other.get("asset_ref") or "") == ref:
                    uses.append((other, "out" if other.get("action") == "unstake" else "in"))
                if str(other.get("evict_asset_ref") or "") == ref:
                    uses.append((other, "out"))
            return (len(uses) == 2 and {direction for _, direction in uses} == {"in", "out"}
                and all(other.get("group_id") == group_id for other, _ in uses)
                and group_id in {"totem", "title", "power_core"}
                and uses[0][0].get("deed_uid") != uses[1][0].get("deed_uid"))

        reused_ref = next((ref for ref in {str(move.get("asset_ref") or ""), str(move.get("evict_asset_ref") or "")}
            if ref and ref in asset_move_indexes and asset_move_indexes[ref] != index and not linked_item_ref(ref)), None)
        if reused_ref is not None:
            _record_error(errors, index, move, f"asset_ref {reused_ref!r} is used by more than one staged move")
            continue
        for ref in {str(move.get("asset_ref") or ""), str(move.get("evict_asset_ref") or "")}:
            if ref:
                asset_move_indexes[ref] = index
        stake_type = GROUP_STAKE_TYPES[group_id]
        if stake_type in UNVERIFIED_TYPES:
            raise LandOpError(f"stake type {stake_type!r} has payload shape UNVERIFIED — refusing to emit")
        try:
            slot = _as_slot(move, group_id, action)
        except ValueError as exc:
            _record_error(errors, index, move, str(exc))
            continue
        incoming = None
        outgoing = None
        if action in {"stake", "replace"}:
            ref = str(move.get("asset_ref") or "")
            incoming = _asset_record(report, ref, group_id)
            if incoming is None:
                _record_error(errors, index, move, f"asset_ref {ref!r} is not present in the current snapshot", error_code="asset_absent")
                continue
            if incoming.get("stake_type_uid") != stake_type:
                _record_error(errors, index, move, f"asset {ref!r} has stake type {incoming.get('stake_type_uid')!r}, not {stake_type!r}")
                continue
            reason = _validate_asset(incoming, account=account, now=now)
            if reason:
                _record_error(errors, index, move, reason)
                continue
            survival_reason = _survival_refusal(incoming, now)
            if survival_reason:
                _record_error(errors, index, move, survival_reason, notice_code="survival_refusal")
                continue
            cooldown_reason = _cooldown_refusal(incoming, stake_type, now)
            if cooldown_reason:
                _record_error(errors, index, move, cooldown_reason, notice_code="cooldown_refusal")
                continue
            if incoming.get("staked") and incoming.get("deed_uid") != deed_uid:
                source_deed_uid = str(incoming["deed_uid"])
                add_intent(move, incoming, "unstake", source_deed_uid, None, index)
                explicit_removal = any(m.get("deed_uid") == source_deed_uid and m.get("group_id") == group_id
                    and ((m.get("action") == "unstake" and m.get("asset_ref") == ref)
                         or (m.get("action") == "replace" and m.get("evict_asset_ref") == ref))
                    for m in moves if isinstance(m, dict))
                if not explicit_removal:
                    overlay_moves_by_deed.setdefault(source_deed_uid, []).append({
                        "deed_uid": source_deed_uid,
                        "group_id": group_id,
                        "action": "unstake",
                        "asset_ref": incoming.get("asset_ref"),
                        "slot": incoming.get("slot"),
                    })
            if incoming.get("staked") and incoming.get("deed_uid") == deed_uid:
                add_intent(move, incoming, "unstake", deed_uid, None, index)
            add_intent(move, incoming, "stake", deed_uid, slot, index)
            if slot is not None:
                slot_claims.setdefault(deed_uid, []).append(slot)

        if action in {"unstake", "replace"}:
            outgoing_ref = str(move.get("evict_asset_ref") or (move.get("asset_ref") if action == "unstake" else ""))
            outgoing = _asset_record(report, outgoing_ref, group_id)
            if outgoing is None:
                _record_error(errors, index, move, f"outgoing asset_ref {outgoing_ref!r} is not present in the current snapshot", error_code="asset_absent")
                continue
            if outgoing.get("stake_type_uid") != stake_type:
                _record_error(errors, index, move, f"outgoing asset {outgoing_ref!r} has stake type {outgoing.get('stake_type_uid')!r}, not {stake_type!r}")
                continue
            if not outgoing.get("staked") or outgoing.get("deed_uid") != deed_uid:
                _record_error(errors, index, move, f"outgoing asset {outgoing_ref!r} is not currently staked on deed {deed_uid}")
                continue
            reason = _validate_asset(outgoing, account=account, now=now)
            if reason:
                _record_error(errors, index, move, reason, notice_code="cooldown_refusal" if "cooldown" in reason.lower() else None)
                continue
            add_intent(move, outgoing, "unstake", deed_uid, None, index)

    # Keep candidates.py's overlay semantics intact: a `replace` remains one
    # replace, so its eviction is applied before its incoming asset. Rebuilding
    # this from generated intents would turn it into two stakes/unstakes and can
    # incorrectly leave a replaced power source present in the projection.
    for raw_move in moves:
        if not isinstance(raw_move, dict):
            continue
        deed_uid = str(raw_move.get("deed_uid") or "")
        if deed_uid not in plots or raw_move.get("group_id") not in GROUP_STAKE_TYPES:
            continue
        if raw_move.get("action") not in {"stake", "unstake", "replace"}:
            continue
        overlay_moves_by_deed.setdefault(deed_uid, []).append(dict(raw_move))

    for deed_uid, claimed in slot_claims.items():
        if len(claimed) != len(set(claimed)):
            _record_error(errors, -1, {"deed_uid": deed_uid}, "two staged worker moves claim the same slot")
        plot = plots.get(deed_uid)
        current_workers, expected_workers = _worker_roster_state(
            report, plot or {}, deed_uid, workers_by_deed, unstaked_refs,
        )
        unknown_slots = [worker for worker in current_workers if worker.get("slot") is None]
        if expected_workers is None or len(current_workers) != expected_workers or unknown_slots:
            expanded = _expand_plot_for_preview(deed_uid, account, config, report=report)
            if expanded is not None:
                report = expanded
                plots, workers_by_deed = _plot_state(report)
                plot = plots.get(deed_uid)
                current_workers, expected_workers = _worker_roster_state(
                    report, plot or {}, deed_uid, workers_by_deed, unstaked_refs,
                )
                unknown_slots = [worker for worker in current_workers if worker.get("slot") is None]
        if expected_workers is None:
            _record_error(
                errors,
                -1,
                {"deed_uid": deed_uid},
                "worker count is unavailable in the current plot data; refusing rather than guessing",
                notice_code="worker_count_unknown",
            )
            worker_state_errors.add(deed_uid)
            continue
        if unknown_slots:
            _record_error(
                errors,
                -1,
                {"deed_uid": deed_uid},
                "worker slot occupancy is unknown after expanding the plot; refusing rather than guessing",
                notice_code="worker_slot_unknown",
            )
            worker_state_errors.add(deed_uid)
            continue
        if len(current_workers) != expected_workers:
            _record_error(
                errors,
                -1,
                {"deed_uid": deed_uid},
                "worker roster count does not match the current plot data; refusing rather than guessing",
                notice_code="worker_count_mismatch",
            )
            worker_state_errors.add(deed_uid)
            continue
        current_slots = {
            int(worker.get("slot"))
            for worker in current_workers
            if worker.get("slot") is not None
        }
        duplicate = current_slots & set(claimed)
        if duplicate:
            _record_error(
                errors,
                -1,
                {"deed_uid": deed_uid},
                f"worker slot(s) already occupied in the current snapshot: {sorted(duplicate)}",
                notice_code="worker_slot_taken",
            )

    capacity_notices: list[dict[str, Any]] = []
    refused_worker_uids: set[str] = set()
    excluded_moves: list[dict[str, Any]] = []

    def _worker_move_label(move: dict[str, Any], deed_uid: str) -> str:
        record = _asset_record(report, str(move.get("asset_ref") or ""), "worker") or {}
        facts = record.get("facts") or {}
        name = facts.get("name") or move.get("asset_ref") or "worker"
        slot = move.get("slot")
        if slot is None and move.get("slot_index") is not None:
            slot = int(move["slot_index"]) + 1
        # the notice always sits under its own plot, so it names the slot, not the deed.
        return f"{name}" + (f" in slot {slot}" if slot is not None else "")

    def _capacity_notice(code: str, deed_uid: str, move: dict[str, Any], reason: str, index: int) -> dict[str, Any]:
        return {
            "code": code,
            "kind": "refusal",
            "severity": "danger",
            "blocking": "none",
            "message": f"{_worker_move_label(move, deed_uid)} was left unsigned: {reason}",
            "reason": reason,
            "context": {"deed_uid": deed_uid, "asset_ref": move.get("asset_ref"), "index": index},
        }

    def _exclude_worker_intent(
        intent: StakeIntent, move: dict[str, Any], index: int, reason: str,
    ) -> None:
        refused_worker_uids.add(str(intent.asset_uid))
        if move.get("action") == "replace" and move.get("evict_asset_ref"):
            outgoing = _asset_record(report, str(move["evict_asset_ref"]), "worker")
            if outgoing and outgoing.get("uid"):
                refused_worker_uids.add(str(outgoing["uid"]))
        excluded_moves.append({
            "index": index,
            "deed_uid": intent.deed_uid,
            "action": move.get("action", "stake"),
            "group_id": "worker",
            "asset_ref": move.get("asset_ref"),
            "slot_index": move.get("slot_index"),
        })
        capacity_notices.append(_capacity_notice("worker_capacity_refusal", intent.deed_uid, move, reason, index))

    def _unpowered_notice(deed_uid: str, move: dict[str, Any], index: int, powered_slots: int) -> dict[str, Any]:
        slot = int(move.get("slot") or int(move.get("slot_index") or 0) + 1)
        reason = f"worker slot {slot} is beyond the plot's {powered_slots} powered slots"
        return format_notice(
            "worker_unpowered_inactive",
            context={
                "deed_uid": deed_uid,
                "asset_ref": move.get("asset_ref"),
                "index": index,
                "worker": _worker_move_label(move, deed_uid),
                "slot": slot,
                "powered_slots": powered_slots,
            },
            reason=reason,
        )

    unpowered_notices: list[dict[str, Any]] = []
    capacity_deeds = list(dict.fromkeys([*worker_count_delta, *core_delta, *runi_delta]))
    for deed_uid in capacity_deeds:
        if deed_uid in worker_state_errors:
            continue
        # `deed_uid` here can be a staked-origin deed pulled straight from the snapshot
        # (`_asset_record`'s own `stake_ref_uid`, added via `add_intent` at line ~282)
        # rather than a move's own target `deed_uid` -- the latter is checked against
        # `plots` above (line ~215), the former never was. A snapshot whose `workers[]`
        # `bonus_assets[]` reference a `stake_ref_uid` absent from `plots[]` would
        # otherwise raise an unhandled `KeyError` here -> HTTP 500 (Aletheia finding 5
        # 2026-08-24; no live path constructs this today, but the preview route must
        # never 500 on a malformed snapshot). Treat it as a validation error instead.
        plot = plots.get(deed_uid)
        if plot is None:
            _record_error(errors, -1, {"deed_uid": deed_uid}, f"snapshot references deed {deed_uid!r} with no matching plot entry")
            continue
        current_workers, expected_workers = _worker_roster_state(
            report, plot, deed_uid, workers_by_deed, unstaked_refs,
        )
        has_worker_moves = deed_uid in worker_count_delta
        if expected_workers is None:
            if has_worker_moves:
                _record_error(
                    errors,
                    -1,
                    {"deed_uid": deed_uid},
                    "worker count is unavailable in the current plot data; refusing rather than guessing",
                    notice_code="worker_count_unknown",
                )
                worker_state_errors.add(deed_uid)
            continue
        if len(current_workers) != expected_workers:
            if has_worker_moves:
                _record_error(
                    errors,
                    -1,
                    {"deed_uid": deed_uid},
                    "worker roster count does not match the current plot data; refusing rather than guessing",
                    notice_code="worker_count_mismatch",
                )
                worker_state_errors.add(deed_uid)
            # Power-only moves do not need a complete per-slot roster. Skip their
            # powered-slot notices, but let the independent source hazard below use
            # the authoritative aggregate power flags as it did before .
            continue
        post_unstake_workers = expected_workers
        capacity_index = structure_capacity_index(report)
        overlay_target = staged_worksite_by_deed.get(deed_uid) if deed_uid in capacity_worksite_deeds else None
        overlay_row, _ = _projected_worksite_row(plot, overlay_target)
        capacity = potential_worker_capacity(overlay_row, capacity_index)
        powered_slots = powered_worker_slots(overlay_row, capacity_index)
        has_staged_power_move = deed_uid in core_delta or deed_uid in runi_delta
        try:
            overlay_plot = _plots_cached(report, energized_details or {}).get(deed_uid)
            if overlay_plot is None:
                raise ValueError("plot projection is unavailable")
            overlay_state = _project_staged_plot_state(
                report, deed_uid, overlay_moves_by_deed.get(deed_uid, []),
                energized_details or {}, overlay_plot, overlay_row,
            )
            projected_powered = overlay_state.get("worker_capacity")
            if isinstance(projected_powered, bool) or not isinstance(projected_powered, int):
                raise ValueError("projected powered-slot count is invalid")
            powered_slots = max(0, min(capacity, projected_powered))
        except (KeyError, TypeError, ValueError):
            projected_flags = dict(overlay_row.get("staking_facts") or {})
            for staged_move in overlay_moves_by_deed.get(deed_uid, []):
                group_id = staged_move.get("group_id")
                action = staged_move.get("action")
                if group_id == "power_core":
                    projected_flags["is_power_core_staked"] = action != "unstake"
                elif group_id == "runi":
                    projected_flags["is_runi_staked"] = action != "unstake"
            powered_slots = min(capacity, powered_worker_slots(overlay_row, capacity_index, flags=projected_flags))
        affected = worker_stake_intents.get(deed_uid, [])
        for intent, move, index in affected:
            if intent.slot is not None and intent.slot > capacity:
                reason = f"worker slot {intent.slot} exceeds structure capacity {capacity}"
                _exclude_worker_intent(intent, move, index, reason)

        original_worker_uids = {
            str(worker.get("uid")) for worker in workers_by_deed.get(deed_uid, []) if worker.get("uid")
        }
        restored_workers = len(original_worker_uids & unstaked_refs & refused_worker_uids)
        remaining_incoming = [
            entry for entry in affected if str(entry[0].asset_uid) not in refused_worker_uids
        ]
        final_workers = post_unstake_workers + restored_workers + len(remaining_incoming)
        if final_workers > capacity:
            if not has_staged_power_move and deed_uid not in staged_worksite_by_deed:
                reason = f"staged workers exceed structure capacity {capacity}"
            else:
                reason = f"the staged worker roster would exceed the structure capacity {capacity}"
            for intent, move, index in remaining_incoming:
                _exclude_worker_intent(intent, move, index, reason)

        signable_incoming = [
            entry for entry in remaining_incoming if str(entry[0].asset_uid) not in refused_worker_uids
        ]
        warned: set[tuple[str, int]] = set()
        for intent, move, index in signable_incoming:
            if intent.slot is not None and powered_slots < intent.slot <= capacity:
                key = (str(intent.asset_uid), intent.slot)
                warned.add(key)
                unpowered_notices.append(_unpowered_notice(deed_uid, move, index, powered_slots))
        for worker in current_workers:
            slot = worker.get("slot")
            uid = str(worker.get("uid") or worker.get("asset_ref") or "")
            if isinstance(slot, int) and powered_slots < slot <= capacity and (uid, slot) not in warned:
                unpowered_notices.append(_unpowered_notice(
                    deed_uid,
                    {"asset_ref": worker.get("asset_ref"), "slot": slot},
                    -1,
                    powered_slots,
                ))

    if refused_worker_uids:
        intents = [intent for intent in intents if str(intent.asset_uid) not in refused_worker_uids]
        worker_count_delta = {}
        core_delta = {}
        runi_delta = {}
        for intent in intents:
            sign = 1 if intent.direction == "stake" else -1
            if intent.stake_type_uid == STAKE_TYPE_WORKER:
                worker_count_delta[intent.deed_uid] = worker_count_delta.get(intent.deed_uid, 0) + sign
            elif intent.stake_type_uid == STAKE_TYPE_POWER_CORE:
                core_delta[intent.deed_uid] = core_delta.get(intent.deed_uid, 0) + sign
            elif intent.stake_type_uid == STAKE_TYPE_RUNI:
                runi_delta[intent.deed_uid] = runi_delta.get(intent.deed_uid, 0) + sign

    hazards: list[dict[str, Any]] = []

    def add_hazard(code: str, deed_uid: str) -> None:
        notice = format_notice(code, context={"deed_uid": deed_uid})
        hazards.append({"type": code, "deed_uid": deed_uid, "message": notice["message"]})

    power_source_deed_uids = (
        set(core_delta) | set(runi_delta)
        | {intent.deed_uid for intent in intents if intent.stake_type_uid in (STAKE_TYPE_POWER_CORE, STAKE_TYPE_RUNI)}
    )
    for deed_uid in power_source_deed_uids:
        # Same defensive guard as the worker-capacity loop above -- see its comment.
        plot = plots.get(deed_uid)
        if plot is None:
            _record_error(errors, -1, {"deed_uid": deed_uid}, f"snapshot references deed {deed_uid!r} with no matching plot entry")
            continue
        # Sole-power-source removal is a HAZARD, never a refusal (2026-08-24
        # live multi-transaction testing ruling: a warning is fine, but a hard
        # blocker is not). Ceres stages only -- nothing broadcasts without the
        # user's own Review -> Sign -> Verify click, and that click IS the deliberate
        # confirmation the internal rules's "never one-click" rule calls for. Staging itself must
        # never be refused. The warning itself is suppressed when there is nothing left
        # to protect: a plot already carrying zero seated workers and zero harvest PP
        # loses no capacity and stops no production by losing its power source, so
        # flagging it anyway is pure noise that trains users to ignore warnings that
        # matter (observed live: a real plot tested this way already had zero workers
        # and zero PP). See `sole_power_source_removed()` for the math.
        if sole_power_source_removed(plot, deed_uid, core_delta, runi_delta):
            add_hazard("sole_power_source_removed", deed_uid)

    # swapping out the plot's only Energized worker removes its worker-slot
    # power. The worker slots remain, but cards beyond the remaining powered slots make 0 PP
    # (seen live: Tireless Farmhand swapped out, Toiling Ox left in slot 5 at NO POWER). The
    # `is_energized` flag above still reads the pre-swap state, so it can never catch this.
    pool_by_uid: dict[str, dict[str, Any]] = {}
    for deed_uid in {intent.deed_uid for intent in intents if intent.stake_type_uid == STAKE_TYPE_WORKER}:
        removed = {str(intent.asset_uid) for intent in intents if intent.deed_uid == deed_uid and intent.stake_type_uid == STAKE_TYPE_WORKER and intent.direction == "unstake"}
        current = workers_by_deed.get(deed_uid, [])
        if not any(str(worker.get("uid")) in removed and _is_energized(worker) for worker in current):
            continue
        plot = plots.get(deed_uid) or {}
        sf = plot.get("staking_facts") or {}
        if (1 if sf.get("is_power_core_staked") else 0) + core_delta.get(deed_uid, 0) > 0:
            continue
        if any(str(worker.get("uid")) not in removed and _is_energized(worker) for worker in current):
            continue
        if not pool_by_uid:
            for card in report.get("cards") or []:
                facts = card.get("facts") or {}
                uid = str(card.get("uid") or facts.get("uid") or "")
                if uid:
                    pool_by_uid[uid] = facts
        incoming = [pool_by_uid.get(str(intent.asset_uid)) for intent in intents if intent.deed_uid == deed_uid and intent.stake_type_uid == STAKE_TYPE_WORKER and intent.direction == "stake"]
        if any(_is_energized(card) for card in incoming):
            continue
        add_hazard("energized_source_removed", deed_uid)

    for deed_uid, delta in worker_count_delta.items():
        plot = plots.get(deed_uid)
        if plot is None:
            continue
        current_workers = sum(
            1 for worker in workers_by_deed.get(deed_uid, [])
            if str(worker.get("uid")) not in unstaked_refs
        )
        if workers_staged_without_power(plot, deed_uid, current_workers, delta, core_delta, runi_delta):
            add_hazard("workers_staged_without_power", deed_uid)

    if errors:
        raise PreviewValidationError(errors)
    if not intents and (not first_clear_worksites or capacity_notices):
        if capacity_notices:
            raise PreviewValidationError([{
                "index": item["context"].get("index", -1),
                "deed_uid": item["context"]["deed_uid"],
                "asset_ref": item["context"].get("asset_ref"),
                "reason": item["message"],
                "notice": item,
            } for item in capacity_notices])
        raise PreviewValidationError([{
            "index": -1,
            "deed_uid": None,
            "asset_ref": None,
            "reason": "no signable staged moves remain",
        }])
    normal_intents = list(intents)
    combined_chunks: list[tuple[list[Any], dict[str, Any]]] = []
    worksite_notices: list[dict[str, Any]] = []
    combined_worksite_deeds: list[str] = []
    parked_worksites: list[dict[str, str]] = []

    def _intent_move_label(intent: StakeIntent) -> str:
        record = intent_records.get(id(intent), {})
        facts = record.get("facts") or {}
        name = facts.get("name") or record.get("name")
        if not name and facts.get("card_detail_id") is not None:
            detail = (details_by_id or {}).get(facts.get("card_detail_id")) or {}
            name = detail.get("name")
        if not name and intent.asset_ref:
            for family in (report.get("unassigned_items") or {}).values():
                if not isinstance(family, dict) or str(intent.asset_ref) not in {str(uid) for uid in family.get("uids", [])}:
                    continue
                items = family.get("items") or []
                detail_id = str(intent.asset_ref).split("-", 2)[1] if str(intent.asset_ref).count("-") >= 2 else None
                item = next((entry for entry in items if isinstance(entry, dict) and str(entry.get("item_detail_id")) == detail_id), None)
                if item is None and len(items) == 1 and isinstance(items[0], dict):
                    item = items[0]
                if item is not None:
                    name = item.get("name")
                    break
        asset = str(name or intent.asset_ref or intent.asset_uid)
        if name and intent.asset_ref:
            asset = f"{name} ({intent.asset_ref})"
        stake_type = STAKE_TYPE_LABELS.get(intent.stake_type_uid, intent.stake_type_uid)
        return f"{asset} as {stake_type}"

    for deed_uid, target in first_clear_worksites.items():
        plot = plots[deed_uid]
        power_stakes = [
            intent for intent in intents
            if intent.deed_uid == deed_uid and intent.direction == "stake"
            and intent.stake_type_uid == STAKE_TYPE_POWER_CORE
        ]
        worker_stakes = [
            intent for intent in intents
            if intent.deed_uid == deed_uid and intent.direction == "stake"
            and intent.stake_type_uid == STAKE_TYPE_WORKER
        ]
        first_clear_runi_stakes = [
            intent for intent in intents
            if intent.deed_uid == deed_uid and intent.direction == "stake"
            and intent.stake_type_uid == STAKE_TYPE_RUNI
        ]
        first_clear_totem_stakes = [
            intent for intent in intents
            if intent.deed_uid == deed_uid and intent.direction == "stake"
            # STK-LND-TOT is allowed by official tx 6e7121a95b509e144ab21ac451de8254b7c8eb00.
            and intent.stake_type_uid == STAKE_TYPE_TOTEM
        ]
        first_clear_title_stakes = [
            intent for intent in intents
            if intent.deed_uid == deed_uid and intent.direction == "stake"
            and intent.stake_type_uid == STAKE_TYPE_TITLE
        ]
        # the maintainer's accepted first clear proves the five named stake classes.
        # Unknown future stake classes remain refused until their first-clear shape is known.
        other_target_stakes = [
            intent for intent in intents
            if intent.deed_uid == deed_uid and intent.direction == "stake"
            and intent.stake_type_uid not in {
                STAKE_TYPE_POWER_CORE, STAKE_TYPE_WORKER, STAKE_TYPE_RUNI,
                STAKE_TYPE_TOTEM, STAKE_TYPE_TITLE,
            }
        ]
        same_deed_unstakes = [
            intent for intent in intents
            if intent.deed_uid == deed_uid and intent.direction == "unstake"
        ]
        if other_target_stakes:
            offending = _intent_move_label(other_target_stakes[0])
            raise LandOpError(
                f"First-clear transaction for {deed_uid} cannot include {offending}. "
                "Undo that move; sign the items after the build."
            )
        if same_deed_unstakes:
            parked_worksites.append({"deed_uid": deed_uid, "reason": "Parked — finish removing assets from this plot before its first build. Other ready changes can still be signed."})
            continue
        staking_facts = plot.get("staking_facts") or {}
        live_powered = power_satisfied(staking_facts) or staking_facts.get("is_powered") is True
        staged_energized = any(
            _is_energized(_asset_record(report, str(intent.asset_ref or ""), "worker"))
            for intent in worker_stakes
        )
        staged_power = bool(power_stakes) or bool(first_clear_runi_stakes) or staged_energized
        worker_count = int(float(staking_facts.get("worker_count") or 0)) + len(worker_stakes) + len(first_clear_runi_stakes)
        missing = []
        if not live_powered and not staged_power:
            missing.append("a Power Core, Runi, or Energized worker")
        if worker_count == 0:
            missing.append("at least one worker")
        if missing:
            reason = "Parked — add " + " and ".join(missing) + " to this plot. Other ready changes can still be signed."
            parked_worksites.append({"deed_uid": deed_uid, "reason": reason})
            normal_intents = [intent for intent in normal_intents if intent.deed_uid != deed_uid]
            for index, move in enumerate(moves):
                if move.get("deed_uid") == deed_uid:
                    excluded_moves.append({**move, "index": index, "reason": reason})
            continue
        combined_intents = [
            *power_stakes, *worker_stakes, *first_clear_runi_stakes,
            *first_clear_totem_stakes, *first_clear_title_stakes,
        ]
        normal_intents = [intent for intent in normal_intents if intent not in combined_intents]
        worksite_op, _first_clear, built_notices = build_worksite_change(
            report,
            deed_uid,
            target,
            account=account,
            app=app,
            staged_power=staged_power,
            staged_worker_count=int(float((plot.get("staking_facts") or {}).get("worker_count") or 0)) + len(worker_stakes),
        )
        power_ops = intents_to_ops(power_stakes, account=account, app=app)
        worker_runi_ops = intents_to_ops(
            [*worker_stakes, *first_clear_runi_stakes], account=account, app=app,
        )
        item_ops = intents_to_ops(
            [*first_clear_totem_stakes, *first_clear_title_stakes], account=account, app=app,
        )
        first_clear_ops = [*power_ops, *worker_runi_ops, *item_ops, worksite_op]
        first_clear_op_limit = _first_clear_op_limit(max_ops_per_tx)
        packed = chunk_ops(
            first_clear_ops, max_ops_per_tx=first_clear_op_limit, max_bytes_per_tx=max_bytes_per_tx,
        )
        operations = requests_for_broadcast(first_clear_ops)
        if len(first_clear_ops) > fides_op.DEFAULT_MAX_OPS_PER_TX or len(packed) != 1 or _serialized_bytes(operations) > max_bytes_per_tx:
            raise LandOpError(
                f"Power source, workers, Runi, items, and first worksite for {deed_uid} do not fit in one Hive "
                "transaction. Nothing was split or signed; reduce the staged changes and try again."
            )
        worker_runi_groups = [
            group for group, stakes in (
                ("worker", worker_stakes), ("runi", first_clear_runi_stakes),
            ) if stakes
        ]
        item_groups = [
            group for group, stakes in (
                ("totem", first_clear_totem_stakes), ("title", first_clear_title_stakes),
            ) if stakes
        ]
        combined_chunks.append((first_clear_ops, {
            "purpose": "first_clear_build",
            "stage_groups": [
                group for group, stakes in (
                    ("power_core", power_stakes),
                    ("worker", worker_stakes),
                    ("runi", first_clear_runi_stakes),
                    ("totem", first_clear_totem_stakes),
                    ("title", first_clear_title_stakes),
                ) if stakes
            ],
            "op_groups": [
                *(["power_core"] * len(power_ops)),
                *([worker_runi_groups] * len(worker_runi_ops)),
                *([item_groups] * len(item_ops)),
                "worksite",
            ],
        }))
        combined_worksite_deeds.append(deed_uid)
        worksite_notices.extend(built_notices)

    normal_ops = intents_to_ops(normal_intents, account=account, app=app)
    ops = list(normal_ops)
    for first_clear_ops, _metadata in combined_chunks:
        ops.extend(first_clear_ops)
    if ops and (required_key_tier(ops) != "Posting" or required_account(ops) != account):
        raise LandOpError("preview produced an unexpected signing authority")
    chunks = []
    normal_chunks = chunk_ops(
        normal_ops,
        max_ops_per_tx=max_ops_per_tx,
        max_bytes_per_tx=max_bytes_per_tx,
    )
    packed_chunks = [(chunk, {}) for chunk in normal_chunks]
    packed_chunks.extend(combined_chunks)
    for index, (chunk, metadata) in enumerate(packed_chunks):
        operations = requests_for_broadcast(chunk)
        chunks.append({
            "index": index,
            "operations": operations,
            "op_count": len(operations),
            "serialized_bytes": _serialized_bytes(operations),
            **metadata,
        })
    # This browser signing path (Keychain requestBroadcast) is the one Ceres actually
    # uses -- the `fides_land/render.py` allowlist previously guarded only the
    # `python3 -m fides_land emit` CLI path, which nobody signs through. Run the SAME
    # rule source here so a malformed or mis-generated op is refused before it ever
    # reaches Keychain, not just when written to a bundle file nobody opens. Reusing
    # `evaluate_allowlist` (rather than a second rule set) is deliberate: two allowlists
    # can drift, and a drifted one is what let a payload-lie bug through earlier today.
    allowlist_check = evaluate_allowlist({
        "header": {"allowlist": allowlist_rules(account, app=app), "account": account},
        "waves": [{"chunks": [{"keychain_operations": chunk["operations"]} for chunk in chunks]}],
    })
    if ops and not allowlist_check["ok"]:
        raise LandOpError("allowlist refused: " + "; ".join(allowlist_check["violations"]))
    notices = []
    for hazard in hazards:
        code = hazard.get("type") if hazard.get("type") in ("sole_power_source_removed", "energized_source_removed") else "workers_staged_without_power"
        notices.append(format_notice(code, context={"deed_uid": hazard.get("deed_uid")}, reason=hazard.get("message")))
    return {
        "account": account,
        "key_tier": "Posting",
        "operation_count": len(ops),
        "chunk_count": len(chunks),
        "chunks": chunks,
        "operations": chunks[0]["operations"] if len(chunks) == 1 else None,
        "hazards": hazards,
        "notices": notices + worksite_notices + unpowered_notices + capacity_notices,
        "excluded_moves": excluded_moves,
        "combined_worksite_deeds": combined_worksite_deeds,
        "parked_worksites": parked_worksites,
        "notice_catalog": notice_catalog(),
    }
