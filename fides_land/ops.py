"""Land plan and intent conversion to `sm_stake_change` `fides.op.Op` instances.

The legacy plan path consumes `praetoria.land_reallocate.plan_to_dict()`'s output as plain
JSON; the intent path is asset-class agnostic and adds verified item support.
"""

from __future__ import annotations

import random
import re
import string
from collections.abc import Iterable
from typing import Any

from fides.op import Op

from .intent import StakeIntent
from .slots import assign_slots

STAKE_TYPE_WORKER = "STK-LND-WKR"
STAKE_TYPE_POWER_CORE = "STK-LND-PCR"
STAKE_TYPE_TOTEM = "STK-LND-TOT"
STAKE_TYPE_TITLE = "STK-LND-TTL"
STAKE_TYPE_RUNI = "STK-LND-RUNI"
CUSTOM_JSON_ID = "sm_stake_change"
# Phase 3 -- worksite-change op family. This is a DIFFERENT chain op family from
# every stake/unstake op above: `sm_land_operation`, not `sm_stake_change`. The target
# worksite lives in the op NAME (`worksite_<target>_construction`), never in a body field
# confirmed from a real captured op (a captured op)
# {"op":"worksite_aura_construction","region_uid":"PR-BRL-41"
# "deed_uids":["I-298-2dd53d9ac60331"],"time_crystals":0,"app":"splinterlands/0.7.60","n":"..."}
WORKSITE_CHANGE_OP_ID = "sm_land_operation"

# Every terrain supports exactly one natural resource; the three higher-tier worksites are
# terrain-free (support.splinterlands.com's per-resource FAQs, 2026-08-26). Duplicated here
# rather than imported from `ceres.worksite_swap` -- fides_land stays independent of ceres
# (a different consumer could build this op without ever running Ceres), and this table is
# primary-sourced, not derived by importing ceres's own copy.
_NATURAL_TERRAIN_RESOURCE = {
    "plains": "grain", "river": "grain", "bog": "grain", "lake": "grain",
    "swamp": "wood", "forest": "wood", "tundra": "wood", "jungle": "wood",
    "desert": "stone", "canyon": "stone", "hills": "stone",
    "mountain": "iron", "badlands": "iron", "caldera": "iron",
}
_NATURAL_WORKSITE_TARGETS = frozenset(_NATURAL_TERRAIN_RESOURCE.values())
# Every plot has exactly four legal worksites (its terrain-native resource, plus these
# three) -- but across all fourteen terrains that is exactly these seven possible targets.
WORKSITE_SWAP_TARGETS = frozenset(_NATURAL_WORKSITE_TARGETS | {"research", "sps", "aura"})
# Flat 50 TC to convert an EXISTING worksite; PP-scaled fresh-build pricing is out of scope
# (plan §1) and must never reach this builder.
_WORKSITE_TC_COSTS = frozenset({0, 50})


class LandOpError(Exception):
    pass


def _validate_app(app: str) -> str:
    if not isinstance(app, str) or re.fullmatch(r"ceres/\d+\.\d+\.\d+", app) is None:
        raise LandOpError(f"refusing third-party app identifier {app!r}; app must match 'ceres/<semver>'")
    return app


_ITEM_STAKE_TYPES = frozenset({STAKE_TYPE_POWER_CORE, STAKE_TYPE_TOTEM, STAKE_TYPE_TITLE})
# Title rides in items[] like a Totem or Power Core. Its cooldown DURATION is unpublished
# which is a warning carried elsewhere (`waves.stake_type_is_cooling`) -- never a gate here
# emptying `_UNVERIFIED_STAKE_TYPES` without adding Title to this set left `_intent_class`
# falling through to "unknown stake_type_uid", so Title stayed refused by a different path.
# No stake type is refused for want of a verified payload shape. Title's stake and
# unstake payloads are both observed (an isolated Title-only swap, Posting tier)
# what remains unpublished is its RESTAKE COOLDOWN duration, which is a warning
# the UI carries, not a reason to refuse to emit the op.
_UNVERIFIED_STAKE_TYPES: frozenset[str] = frozenset()
# The Runi rides in `cards[]` like a worker card, but it occupies the reserved slot 0
# rather than a monster-card slot -- its stake payload carries a literal `slot: null`
# (observed live), never an assigned integer.
_NO_SLOT_CARD_STAKE_TYPES = frozenset({STAKE_TYPE_RUNI})


def _nonce() -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=10))


def card_uid_or_raise(card: dict[str, Any], *, context: str) -> str:
    """`fides_land` refuses to emit a partial bundle -- a plan lacking `card_uid` (i.e. not
    captured with `--include-asset-ids`) raises immediately rather than silently dropping
    the card or emitting an op with a missing side."""
    uid = card.get("card_uid")
    if not uid:
        raise LandOpError(f"missing card_uid for {context} -- was this plan captured without --include-asset-ids? card={card!r}")
    return uid


def card_entry(card: dict[str, Any], *, context: str) -> dict[str, Any]:
    return {"asset_ref": card.get("asset_ref"), "card_uid": card_uid_or_raise(card, context=context)}


def build_stake_unstake_maps(plan_dict: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """Walks `moves[]` and `backfill[]` per §3.2's table, accumulating per-deed STAKE/UNSTAKE
    entries (each carrying `asset_ref`, for `slots.py`'s lookup, and `card_uid`, for the
    eventual chain payload)."""
    stake: dict[str, list[dict[str, Any]]] = {}
    unstake: dict[str, list[dict[str, Any]]] = {}

    for move in plan_dict.get("moves", []):
        card = move["card"]
        dest = move["destination_uid"]
        stake.setdefault(dest, []).append(card_entry(card, context="a move's destination stake"))

        evicted = move.get("evicted")
        if evicted is not None:
            # the eviction happens AT THE DESTINATION deed, not at any source
            unstake.setdefault(dest, []).append(card_entry(evicted, context="an eviction"))

        if card.get("origin") == "staked":
            source = card.get("source_uid")
            if not source:
                raise LandOpError(f"staked-origin card is missing source_uid: {card!r}")
            unstake.setdefault(source, []).append(card_entry(card, context="a staked-origin move's source unstake"))

    for row in plan_dict.get("backfill", []):
        card = row["card"]
        plot_uid = row["plot_uid"]
        stake.setdefault(plot_uid, []).append(card_entry(card, context="a backfill"))

    return stake, unstake


def to_ops(
    stake_targets: dict[str, list[dict[str, Any]]],
    unstake_targets: dict[str, list[dict[str, Any]]],
    *,
    account: str,
    app: str,
) -> list[Op]:
    """Emit the legacy worker-card maps with the established split-sides policy.

    Item-capable callers use :func:`intents_to_ops`; this function remains unchanged for
    existing `plan_to_ops()` callers.
    """
    app = _validate_app(app)
    ops: list[Op] = []
    for deed in sorted(set(stake_targets) | set(unstake_targets)):
        unstake_cards = unstake_targets.get(deed, [])
        if unstake_cards:
            payload = {
                "unstake": {"cards": [{"card_uid": c["card_uid"]} for c in unstake_cards], "items": []},
                "stake": {"cards": [], "items": []},
                "deed_uid": deed,
                "auto_buy_grain": False,
                "app": app,
                "n": _nonce(),
            }
            ops.append(Op(operation_id=CUSTOM_JSON_ID, required_auths=[], required_posting_auths=[account], json_payload=payload))

        stake_cards = stake_targets.get(deed, [])
        if stake_cards:
            for c in stake_cards:
                stake_type = c.get("stake_type_uid", STAKE_TYPE_WORKER)
                if stake_type != STAKE_TYPE_WORKER:
                    raise LandOpError(f"fides_land only ever emits {STAKE_TYPE_WORKER!r}, got {stake_type!r} for card {c.get('card_uid')!r}")
                if c.get("slot") is None:
                    raise LandOpError(f"card {c.get('card_uid')!r} on deed {deed!r} has no assigned slot -- slots.assign_slots() must run before to_ops()")
            payload = {
                "unstake": {"cards": [], "items": []},
                "stake": {
                    "cards": [{"card_uid": c["card_uid"], "stake_type_uid": STAKE_TYPE_WORKER, "slot": str(c["slot"])} for c in stake_cards],
                    "items": [],
                },
                "deed_uid": deed,
                "auto_buy_grain": False,
                "app": app,
                "n": _nonce(),
            }
            ops.append(Op(operation_id=CUSTOM_JSON_ID, required_auths=[], required_posting_auths=[account], json_payload=payload))
    return ops


def _intent_class(stake_type_uid: str) -> str:
    if stake_type_uid in (STAKE_TYPE_WORKER, STAKE_TYPE_RUNI):
        return "card"
    if stake_type_uid in _ITEM_STAKE_TYPES:
        return "item"
    if stake_type_uid in _UNVERIFIED_STAKE_TYPES:
        raise LandOpError(
            f"stake type {stake_type_uid!r} has payload shape UNVERIFIED — see §11; refusing to emit"
        )
    raise LandOpError(f"unknown stake_type_uid {stake_type_uid!r}; refusing to emit")


def _validate_intent(intent: StakeIntent) -> str:
    if not isinstance(intent, StakeIntent):
        raise LandOpError(f"expected StakeIntent, got {type(intent).__name__}")
    for field in ("deed_uid", "stake_type_uid", "asset_uid"):
        if not isinstance(getattr(intent, field), str) or not getattr(intent, field):
            raise LandOpError(f"intent {field} must be a non-empty string: {intent!r}")
    if intent.direction not in {"stake", "unstake"}:
        raise LandOpError(f"intent direction must be 'stake' or 'unstake', got {intent.direction!r}")
    asset_class = _intent_class(intent.stake_type_uid)
    if intent.slot is not None and (not isinstance(intent.slot, int) or isinstance(intent.slot, bool) or intent.slot < 0):
        raise LandOpError(f"intent slot must be a non-negative integer or None: {intent!r}")
    no_slot_card = intent.stake_type_uid in _NO_SLOT_CARD_STAKE_TYPES
    if asset_class == "item" and intent.slot is not None:
        raise LandOpError(f"item intent {intent.asset_uid!r} must not carry a slot")
    if no_slot_card and intent.slot is not None:
        raise LandOpError(f"Runi intent {intent.asset_uid!r} must not carry a slot -- it occupies reserved slot 0, never a monster-card slot")
    if asset_class == "card" and not no_slot_card and intent.direction == "stake" and intent.slot is None:
        raise LandOpError(f"worker stake intent {intent.asset_uid!r} is missing slot")
    return asset_class


def _intent_entry(intent: StakeIntent, asset_class: str) -> dict[str, Any]:
    if intent.direction == "unstake":
        return ({"card_uid": intent.asset_uid} if asset_class == "card" else {"item_uid": intent.asset_uid})
    if asset_class == "card":
        if intent.stake_type_uid in _NO_SLOT_CARD_STAKE_TYPES:
            # Observed live: the Runi stake payload carries a literal JSON null, not a
            # stringified slot number -- it occupies reserved slot 0, never a monster-card slot.
            return {"card_uid": intent.asset_uid, "stake_type_uid": intent.stake_type_uid, "slot": None}
        return {
            "card_uid": intent.asset_uid,
            "stake_type_uid": intent.stake_type_uid,
            "slot": str(intent.slot),
        }
    return {"item_uid": intent.asset_uid, "stake_type_uid": intent.stake_type_uid}


def intents_to_ops(
    intents: Iterable[StakeIntent],
    *,
    account: str,
    app: str,
) -> list[Op]:
    """Convert validated land intents into split-side, per-deed stake-change ops."""
    if not isinstance(account, str) or not account:
        raise LandOpError("account must be a non-empty string")
    app = _validate_app(app)

    grouped: dict[tuple[str, str], list[tuple[StakeIntent, str]]] = {}
    for intent in intents:
        asset_class = _validate_intent(intent)
        grouped.setdefault((intent.deed_uid, intent.direction), []).append((intent, asset_class))

    result: list[Op] = []
    for direction in ("unstake", "stake"):
        for deed_uid in sorted({deed for deed, _direction in grouped}):
            if (deed_uid, direction) not in grouped:
                continue
            entries = sorted(
                grouped[(deed_uid, direction)],
                key=lambda pair: (pair[0].asset_uid, pair[0].stake_type_uid, pair[0].slot if pair[0].slot is not None else -1),
            )
            side = {"cards": [], "items": []}
            for intent, asset_class in entries:
                side["cards" if asset_class == "card" else "items"].append(_intent_entry(intent, asset_class))
            payload = {
                "unstake": side if direction == "unstake" else {"cards": [], "items": []},
                "stake": side if direction == "stake" else {"cards": [], "items": []},
                "deed_uid": deed_uid,
                "auto_buy_grain": False,
                "app": app,
                "n": _nonce(),
            }
            result.append(Op(operation_id=CUSTOM_JSON_ID, required_auths=[], required_posting_auths=[account], json_payload=payload))
    return result


def worksite_legal_targets(terrain: Any, current_resource: Any = None) -> frozenset[str] | None:
    """The four legal worksite targets for `terrain`: its native resource plus research/
    sps/aura. Returns None for an unrecognised terrain -- never a partial or guessed set.

    `current_resource`, when given, corrects the pre-1.75 legacy-grain trap (the internal rules): a
    grain farm built before Land 1.75 can still sit on a non-grain terrain, and the game only
    migrates it after its next harvest. Without this, a legacy grain plot's *actual* current
    resource would fall outside its own legal-target set. Mirrors `ceres.worksite_swap
    .legal_targets` (duplicated, not imported -- see the module-level comment)."""
    native = _NATURAL_TERRAIN_RESOURCE.get(str(terrain or "").strip().lower())
    if native is None:
        return None
    current = str(current_resource or "").strip().lower()
    natural_slot = current if current in _NATURAL_WORKSITE_TARGETS else native
    return frozenset({natural_slot, "research", "sps", "aura"})


def worksite_change_op(
    deed_uid: str,
    region_uid: str,
    target: str,
    *,
    account: str,
    eligible: bool,
    terrain: Any,
    current_resource: Any = None,
    time_crystals: int = 0,
    app: str,
) -> Op:
    """Builds the single `sm_land_operation` custom_json that IS a worksite change (plan
    §0.2, complete on its own -- `update_worksite` is the unrelated Feed Worker action and
    must never be emitted here). Auth tier is Posting, settled 2026-08-26 across 1,530 real
    ops on 3 accounts with zero exceptions.

    Refuses to build rather than emitting a doomed or illegal op:

    - `eligible=False` (H1) -- a zero-PP plot can never fund a worksite change; construction
      would strand permanently (two real plots have been stuck since January 2024). This
      function never re-derives eligibility -- the caller must pass the server-computed
      `ceres.worksite_swap.swap_eligibility()["eligible"]`, so the two can never disagree.
    - `target` outside the plot's actual four legal worksites for `terrain` (H5) -- an
      illegal target produces a silently-ignored broadcast, which is indistinguishable from
      H3 (a malformed op landing on-chain and proving nothing). `terrain` is required, not
      optional, so this guard can never be silently skipped by omission.
    - `time_crystals` outside {0, 50} -- the flat existing-worksite conversion cost; a
      fresh-build PP-scaled cost is out of scope and must never reach this builder (H6).
    """
    if not eligible:
        raise LandOpError(
            f"deed {deed_uid!r} is not swap-eligible (zero-PP plot -- a worksite change could "
            "never complete and would strand the plot in permanent construction); refusing to "
            "build a worksite-change op. Pass ceres.worksite_swap.swap_eligibility()['eligible']."
        )
    if not isinstance(deed_uid, str) or not deed_uid:
        raise LandOpError(f"deed_uid must be a non-empty string, got {deed_uid!r}")
    if not isinstance(region_uid, str) or not region_uid:
        raise LandOpError(f"region_uid must be a non-empty string, got {region_uid!r}")
    if not isinstance(account, str) or not account:
        raise LandOpError(f"account must be a non-empty string, got {account!r}")
    app = _validate_app(app)
    target_key = str(target or "").strip().lower()
    if target_key not in WORKSITE_SWAP_TARGETS:
        raise LandOpError(f"unknown worksite swap target {target!r}; expected one of {sorted(WORKSITE_SWAP_TARGETS)}")
    legal = worksite_legal_targets(terrain, current_resource)
    if legal is None:
        raise LandOpError(f"unrecognised terrain {terrain!r}; refusing to emit (cannot derive the legal-target table)")
    if target_key not in legal:
        raise LandOpError(f"{target_key!r} is not one of this plot's legal worksites for terrain {terrain!r} (legal: {sorted(legal)})")
    if time_crystals not in _WORKSITE_TC_COSTS:
        raise LandOpError(f"time_crystals must be 0 (free, 24h wait) or 50 (flat instant-conversion cost), got {time_crystals!r}")

    payload = {
        "op": f"worksite_{target_key}_construction",
        "region_uid": region_uid,
        "deed_uids": [deed_uid],
        "time_crystals": time_crystals,
        "app": app,
        "n": _nonce(),
    }
    return Op(operation_id=WORKSITE_CHANGE_OP_ID, required_auths=[], required_posting_auths=[account], json_payload=payload)


def worksite_feed_op(
    deed_uid: str, region_uid: str, project_id: Any, *, account: str, app: str,
) -> Op:
    """Builds the separate Feed Worker operation that completes a ready construction.

    This is intentionally not part of `worksite_change_op`: selecting a target is the
    `worksite_<target>_construction` operation, while feeding a finished construction is
    the observed `update_worksite` operation with its project id.
    """
    if not isinstance(deed_uid, str) or not deed_uid:
        raise LandOpError(f"deed_uid must be a non-empty string, got {deed_uid!r}")
    if not isinstance(region_uid, str) or not region_uid:
        raise LandOpError(f"region_uid must be a non-empty string, got {region_uid!r}")
    if not isinstance(account, str) or not account:
        raise LandOpError(f"account must be a non-empty string, got {account!r}")
    if isinstance(project_id, bool) or project_id in (None, ""):
        raise LandOpError("project_id must be present for Feed Worker")
    app = _validate_app(app)
    payload = {
        "op": "update_worksite", "region_uid": region_uid, "deed_uids": [deed_uid],
        "project_id": project_id, "time_crystals": 0, "auto_buy_grain": False, "app": app,
        "n": _nonce(),
    }
    return Op(operation_id=WORKSITE_CHANGE_OP_ID, required_auths=[], required_posting_auths=[account], json_payload=payload)


def worksite_cancel_op(
    deed_uid: str, region_uid: str, project_id: Any, *, account: str, app: str,
) -> Op:
    if not isinstance(deed_uid, str) or not deed_uid:
        raise LandOpError("deed_uid must be a non-empty string")
    if not isinstance(region_uid, str) or not region_uid:
        raise LandOpError("region_uid must be a non-empty string")
    if not isinstance(account, str) or not account:
        raise LandOpError("account must be a non-empty string")
    if isinstance(project_id, bool) or project_id in (None, ""):
        raise LandOpError("project_id must be present for cancel construction")
    app = _validate_app(app)
    payload = {
        "op": "cancel_construction", "region_uid": region_uid, "deed_uids": [deed_uid],
        "time_crystals": 0, "project_id": project_id, "auto_buy_grain": False,
        "app": app, "n": _nonce(),
    }
    return Op(operation_id=WORKSITE_CHANGE_OP_ID, required_auths=[], required_posting_auths=[account], json_payload=payload)


def plan_to_ops(
    plan_dict: dict[str, Any],
    *,
    account: str,
    assignments: list[dict[str, Any]],
    capacity_by_deed: dict[str, int],
    app: str,
) -> list[Op]:
    """The full plan-JSON -> Op-list pipeline for a single wave's worth of stake/unstake
    maps (see `waves.py` for how a plan is split into wave-scoped maps before this runs)."""
    stake, unstake = build_stake_unstake_maps(plan_dict)
    assign_slots(stake, unstake, assignments, capacity_by_deed)
    return to_ops(stake, unstake, account=account, app=app)
