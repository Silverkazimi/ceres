"""Observed Land portfolio capture, normalization, and diagnostics."""

from __future__ import annotations

import csv
import copy
import hashlib
import io
import json
import logging
import os
import re
import tempfile
import threading
import time
import urllib.parse
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable

from . import fastjson
from .api import SplinterlandsClient
from .cache import JsonCache, safe_name
from .collection_stream import DROPPED_COLLECTION_FIELDS, UNUSED_COLLECTION_FIELDS  # noqa: F401 -- re-exported
from .errors import CacheError, SchemaError, TransportError, UpstreamError
from .models import iso, json_number
from .transport import LAND_ORIGIN, FixedGetTransport, ResponseData, ResponseTooLarge
from mnemosyne.accounting import land_dispatch, wrap_injected_transport
from .land_recommend import estimate_card_pp, recommend_land, replay_estimator

LAND_DEEDS_PATH = "/land/deeds?player="
SINGLE_DEED_PATH = "/land/deeds/{plot_id}"
DEED_STAKING_PATH = "/land/stake/deed/details/{deed_uid}"
ASSETS_PATH = "/land/stake/deeds/{deed_uid}/assets"
PROJECT_HISTORY_PATH = "/land/projects/deed/{deed_uid}/list"
AVAILABLE_PATH = "/land/stake/items/{stake_type_uid}/available"
PROJECT_HISTORY_FIELDS = ("project_number", "project_type", "start_date", "completed_date")
# Player is a PATH SEGMENT here, not a query param ( whale-account performance
# batch, team-lead: found via grepping the game's own production JS bundle for
# "/land/..." strings -- ".../land/deeds/owned?player=X" returns `data: null`
# ".../land/deeds/owned/X" returns the real per-region counts). ~150 bytes vs.
# ~500KB+ for the full LAND_DEEDS_PATH bundle to learn the same total count
# see LandClient.owned_deed_counts() / preflight_deed_count() below.
OWNED_COUNTS_PATH = "/land/deeds/owned/{player}"
# harvest reads (GET only, fixed origin; work/notes/-harvest/data-dossier.md §1).
ACTIVE_PROJECT_PATH = "/land/projects/deed/{deed_uid}/active"
REGION_HARVESTABLE_PATH = "/land/resources/production/region/harvestable?player={player}&region_uid={region_uid}"
REGION_BALANCES_PATH = "/land/liquidity/region/{player}"
RESOURCE_HISTORY_PATH = "/land/resources/history/{trx_id}"
REWARD_ACTIONS_PATH = "/land/resources/rewardactions/{deed_uid}?limit={limit}"
# ( recs 1+2): three more live, credential-free /land/* reads, confirmed public
# (not bearer-gated despite the vapi swagger spec's blanket declaration on these families
# api-swagger-coverage.md's standing "spec says what it declares, not what it returns" note
# applies again here) by splinterlands-mcp's catalogue.json (measured 2026-09-07/08, authTier
# "public" on all four). This analysis does NOT use `/land/liquidity/pools/{player}/{token}`
# the same catalogue classifies it as fabricating plausible-looking content for a garbage
# player or token (a synthesised VESTING-<token> row), so it is not treated as a real
# per-player lookup here. Ceres's wallet LP routes do read it (: `_position` in
# ceres/routes_wallet_lp.py, on Withdraw open and in the remove preview), outside praetoria.
LIQUIDITY_ALLREWARDS_PATH = "/land/liquidity/allrewards"
STAKE_DEC_OVERALL_PATH = "/land/stake/dec/overall?player={player}"
STAKE_DEC_STAKED_PATH = "/land/stake/decstaked?player={player}"
STAKE_EVP_PENDING_PATH = "/land/stake/evp/pending-claim?player={player}"
TAXES_PATH = "/land/resources/taxes/{deed_uid}?deedUid={deed_uid}"  # a Keep/Castle's collected taxes
LAND_SCHEMA_VERSION = "praetoria.land.analysis.v1"
ASSET_REF_PREFIX = "praetoria:asset:v1:"
# (v1.0 plan C1): no built-in account. The name stays so existing signatures keep
# their default; every entry point that reads an account refuses a missing one.
DEFAULT_PLAYER: str | None = None
MIN_INTER_REQUEST_DELAY_SECONDS = 0.300
DEFAULT_REQUEST_BUDGET = 200
# Concurrency for the per-deed asset fan-out: almost the whole per-request cost is
# server think-time (idle wait), which overlapping requests reclaim -- 8 concurrent
# requests measured clean (no errors, no throttling) at 0.72s vs. 4.64s serial for the
# same 8. Kept conservative on purpose: this is a shared third-party API, not one we
# control capacity on.
DEFAULT_CONCURRENCY = 8
# Global dispatch-rate ceiling for the CONCURRENT per-deed fan-out specifically.
# `MIN_INTER_REQUEST_DELAY_SECONDS` (0.3s, ~3.3 req/s) is the ORIGINAL, still-unchanged
# floor for every serial call this module makes (concurrency=1, and every non-per-deed
# call: bundle/collection/unassigned-items) -- never lowered for that path. This is a
# SEPARATE, narrower ceiling: 8 concurrent requests measured clean (no errors, no
# throttling) at about 11.1 req/s; 0.10s (10 req/s) keeps a safety margin under that
# clean-measured ceiling. `LandCapture.capture()` applies this ONLY around
# the concurrent fan-out block itself, restoring the original min_delay immediately
# after for every other call the same capture makes.
CONCURRENT_DISPATCH_INTERVAL_SECONDS = 0.10
# Quick capture shares one third-party API across independent reads, so keep its
# fan-out bounded while `_request` continues to pace dispatches globally.
# Six top-level reads are scheduled by quick capture, and two of them (prior project
# history, unassigned-item families) fan out again underneath. Matches
# `DEFAULT_CONCURRENCY`: 8 concurrent requests are the measured-clean figure
# (/, 2026-09-07 -- 8 simultaneous vapi requests all 200 in 0.773 s
# no 429/503 across ~100 requests, no rate-limit headers advertised). Was 5, which
# left the sixth read queued behind the 10 MB collection sweep.
QUICK_CAPTURE_CONCURRENCY = 8
LOW_BASE_CAP_UTILIZATION_THRESHOLD = 0.5
VERIFIED_PP_COMPONENTS = (
    "deed_rarity_boost", "deed_status_token_boost", "runi_boost", "title_boost",
    "totem_boost", "card_abilities_boost", "card_bloodlines_boost",
)

# Unassigned/spare bonus-item inventory (Ceres board, 2026-08-21). Verified live
# against the real /available endpoint: `deedUid` is accepted but the result is
# identical across two different owned deeds -- this is an ACCOUNT-scoped pool
# not a deed-scoped one, so one call per stake type (paginated) is sufficient; a
# `deedUid` is still required as a query parameter and any owned deed works as the
# anchor. `limit=100` returns full pages; `limit=200` fails with the same
# malformed-P0001-in-200 shape `available_item_ids` already guards against, so page
# size is capped at 100 and pagination continues until a short page comes back.
# STK-LND-RUNI reliably fails with that same wrapped P0001 error for every deed
# tested -- Runi has no unassigned-item source at all; its idle inventory comes
# from `report["cards"]` (asset_kind "runi_worker_card") instead, never from here.
UNASSIGNED_ITEM_FAMILY = {
    "STK-LND-TOT": "totem", "STK-LND-TTL": "title", "STK-LND-PCR": "power_core",
}
# Runi is declared here rather than fetched. The comment above
# records what every test of it found: STK-LND-RUNI answers the available-items route with a
# wrapped P0001 error for every deed, because the upstream has no unassigned-item source for
# Runi at all. Asking anyway costs one guaranteed-useless request per cycle and, worse, an
# upstream that is UNSUPPORTED arrives dressed as a fetch that happened to fail. It stays in
# the result with the same shape and an explicit reason, so the section still shows Runi and
# still says why -- absent, never a silent empty (the rule).
UNSUPPORTED_ITEM_FAMILY = {
    "STK-LND-RUNI": ("runi", "the game exposes no unassigned-item source for Runi; idle Runi is read from the card collection instead"),
}
AVAILABLE_PAGE_SIZE = 100
MAX_UNASSIGNED_PAGES = 50
PROJECT_HISTORY_TTL = 86400
_ITEM_UID_RE = re.compile(r"^I-(\d+)-")


def _capture_failure(label: str, exc: Exception) -> dict[str, Any]:
    failure: dict[str, Any] = {"deed_uid": label, "reason": str(exc)}
    outcome = getattr(exc, "result", None)
    if isinstance(outcome, dict):
        failure["outcome"] = dict(outcome)
    return failure


def _response_too_large_capture(player: str, mode: str, exc: ResponseTooLarge, requests: int, cache_hits: int) -> dict[str, Any]:
    outcome = dict(exc.result)
    return {
        **outcome,
        "response_too_large": outcome,
        "query": {"player": player},
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "capture": {
            "status": "response_too_large",
            "mode": mode,
            "requests": requests,
            "cache_hits": cache_hits,
            "failures": [{"deed_uid": "bundle", "reason": str(exc), "outcome": outcome}],
            "outcome": outcome,
        },
    }

# Verified against /players/item_details on 2026-08-24. This keeps older snapshots
# useful until their next capture; a captured catalogue always takes precedence.
KNOWN_LAND_ITEM_DETAILS: dict[int, dict[str, Any]] = {
    318: {"name": "Common Totem", "boost": 0.10},
    319: {"name": "Rare Totem", "boost": 0.25},
    320: {"name": "Epic Totem", "boost": 0.50},
    321: {"name": "Legendary Totem", "boost": 1.00},
    322: {"name": "Power Core", "boost": 0.0},
    1: {"name": "The Untamed", "boost": 0.50},
    18: {"name": "The Praetorian", "boost": 0.50},
    19: {"name": "The Explorer", "boost": 0.25},
    24: {"name": "The Grandmaster", "boost": 0.50},
    25: {"name": "TBD", "boost": 0.50},
    90: {"name": "The Legionnaire", "boost": 0.10},
    222: {"name": "The Watcher", "boost": 0.10},
    255: {"name": "The Gambler", "boost": 0.25},
    256: {"name": "The High Roller", "boost": 0.50},
    309: {"name": "The Burninator", "boost": 0.50},
    310: {"name": "The Incinerator", "boost": 0.25},
    311: {"name": "The Scorcher", "boost": 0.10},
    323: {"name": "The Rebel", "boost": 0.10}, 324: {"name": "The Defiant", "boost": 0.25},
    325: {"name": "The Dawnbringer", "boost": 0.50}, 327: {"name": "The Tower Mage", "boost": 0.25},
    330: {"name": "The Renowned", "boost": 0.50}, 331: {"name": "The Veteran", "boost": 0.25},
    332: {"name": "The Proven", "boost": 0.10}, 334: {"name": "The Myth Hunter", "boost": 0.50},
    335: {"name": "The Fable Scout", "boost": 0.25}, 336: {"name": "The Saga Seeker", "boost": 0.10},
    337: {"name": "The Emissary", "boost": 0.25}, 345: {"name": "The Arcane", "boost": 0.50},
    346: {"name": "The Enigma", "boost": 0.25}, 347: {"name": "The Mystic", "boost": 0.10},
    525: {"name": "The Ascendant"},
    526: {"name": "The Rising"}, 527: {"name": "The Emergent"},
}


def item_detail_id_from_uid(uid: Any) -> int | None:
    """Recover the numeric item_detail_id embedded in an item uid ("I-318-..." -> 318).
    The /available endpoint returns bare uids with no name/detail_id field of its own."""
    match = _ITEM_UID_RE.match(str(uid))
    return int(match.group(1)) if match else None


def item_catalogue_lookup(report: dict[str, Any]) -> dict[int, dict[str, Any]]:
    lookup = {key: dict(value) for key, value in KNOWN_LAND_ITEM_DETAILS.items()}
    for item in report.get("item_catalogue", []):
        if isinstance(item, dict) and item.get("id") is not None:
            try:
                lookup[int(item["id"])] = dict(item)
            except (TypeError, ValueError):
                continue
    for item in report.get("bonus_assets", []):
        if isinstance(item, dict) and item.get("item_detail_id") is not None:
            try:
                detail_id = int(item["item_detail_id"])
            except (TypeError, ValueError):
                continue
            current = lookup.setdefault(detail_id, {})
            for key in ("name", "boost", "description", "image_filename"):
                if item.get(key) not in (None, ""):
                    current[key] = item[key]
    return lookup


def _number(value: Any, field: str, allow_negative: bool = True) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise SchemaError(f"{field} must be numeric, not boolean")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"{field} must be numeric") from exc
    if not allow_negative and result < 0:
        raise SchemaError(f"{field} must not be negative")
    return result


def _time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def stable_asset_ref(uid: str) -> str:
    return hashlib.sha256((ASSET_REF_PREFIX + str(uid)).encode()).hexdigest()[:24]


def _worker_asset_ref(worker: dict[str, Any]) -> str | None:
    asset_ref = worker.get("asset_ref")
    if asset_ref is not None:
        return str(asset_ref)
    uid = worker.get("uid") or worker.get("card_uid")
    return stable_asset_ref(str(uid)) if uid is not None else None


def _bundle_staked_worker_refs(bundle_payload: dict[str, Any]) -> dict[str, set[str]] | None:
    data = bundle_payload.get("data") if isinstance(bundle_payload, dict) else None
    if not isinstance(data, dict):
        return None
    refs_by_deed: dict[str, set[str]] = {}
    found_worker_rows = False
    sources = [data]
    staking_details = data.get("staking_details")
    if isinstance(staking_details, list):
        sources.extend(row for row in staking_details if isinstance(row, dict))
    for source in sources:
        parent_deed = source.get("deed_uid")
        for field in ("workers", "worker_cards", "staked_workers", "cards"):
            rows = source.get(field)
            if not isinstance(rows, list):
                continue
            for worker in rows:
                if not isinstance(worker, dict):
                    continue
                asset_ref = _worker_asset_ref(worker)
                deed_uid = worker.get("stake_ref_uid") or worker.get("deed_uid") or parent_deed
                if asset_ref is None or deed_uid is None:
                    continue
                found_worker_rows = True
                refs_by_deed.setdefault(str(deed_uid), set()).add(asset_ref)
    return refs_by_deed if found_worker_rows else None


def _card_pool_needed(
    bundle_payload: dict[str, Any],
    card_index: list[dict[str, Any]] | None,
    known_workers: list[dict[str, Any]] | None,
) -> bool:
    if not isinstance(card_index, list) or not card_index:
        return True
    indexed_refs = {
        str(row.get("asset_ref"))
        for row in card_index
        if isinstance(row, dict)
        and row.get("asset_ref") is not None
        and row.get("level") is not None
        and "skin" in row
    }
    if len(indexed_refs) != len(card_index):
        return True
    bundle_refs = _bundle_staked_worker_refs(bundle_payload)
    if bundle_refs is not None:
        return any(ref not in indexed_refs for refs in bundle_refs.values() for ref in refs)

    known_counts: dict[str, int] = {}
    for worker in known_workers or []:
        if not isinstance(worker, dict) or worker.get("stake_ref_uid") is None or _worker_asset_ref(worker) is None:
            continue
        deed_uid = str(worker["stake_ref_uid"])
        known_counts[deed_uid] = known_counts.get(deed_uid, 0) + 1
    data = bundle_payload.get("data") if isinstance(bundle_payload, dict) else None
    staking_details = data.get("staking_details", []) if isinstance(data, dict) else []
    for staking in staking_details:
        if not isinstance(staking, dict) or staking.get("deed_uid") is None:
            continue
        try:
            current_count = int(staking.get("worker_count") or 0)
        except (TypeError, ValueError):
            return True
        known_count = known_counts.get(str(staking["deed_uid"]), 0)
        if current_count > known_count:
            return True
        if current_count > 0 and known_count == 0:
            return True
    return False


def source_hash(value: Any) -> str:
    # (2026-09-07), v0.9.28: the fast codec, sorted keys. The digest changes once with
    # the encoder; it is recorded per record, never compared across captures (2.3 s of a
    # whale capture was this line with the stdlib encoder).
    return hashlib.sha256(fastjson.dumps(value, sort_keys=True)).hexdigest()


def available_item_ids(payload: dict[str, Any]) -> list[Any]:
    """Decode the documented candidate shape and reject nested database errors."""
    if not isinstance(payload, dict):
        raise UpstreamError("available-items response is not a JSON object")
    if payload.get("status") != "success":
        raise UpstreamError(f"available-items response returned status {payload.get('status')}")
    data = payload.get("data")
    if isinstance(data, dict) and set(data) == {"ids"} and isinstance(data["ids"], list):
        return data["ids"]
    reason = "malformed data"
    if isinstance(data, dict):
        code = data.get("code")
        if data.get("name") == "error" or code or data.get("routine") == "exec_stmt_raise":
            reason = f"nested upstream error{f' {code}' if code else ''}"
    raise UpstreamError(f"available-items response is not a candidate payload: {reason}")


def _project_number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _validate_project_history(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        status = payload.get("status") if isinstance(payload, dict) else None
        raise UpstreamError(f"Land project-history API returned status {status}")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise SchemaError("Land project-history response must contain a data array")
    for row in rows:
        if not isinstance(row, dict) or any(field not in row for field in PROJECT_HISTORY_FIELDS):
            raise SchemaError("Land project-history rows must contain project number, type, and lifecycle dates")
        if _project_number(row.get("project_number")) is None:
            raise SchemaError("Land project-history project_number must be finite numeric data")
        if not isinstance(row.get("project_type"), str) or not row["project_type"].strip():
            raise SchemaError("Land project-history project_type must be a non-empty string")
        if not isinstance(row.get("start_date"), (str, type(None))) or not isinstance(row.get("completed_date"), (str, type(None))):
            raise SchemaError("Land project-history lifecycle dates must be strings or null")
    return rows


def _same_time(left: Any, right: Any) -> bool:
    if left in (None, "") or right in (None, ""):
        return False
    left_time = _time(left)
    right_time = _time(right)
    return left_time == right_time if left_time is not None and right_time is not None else left == right


def _select_prior_project(projects: list[dict[str, Any]], active: dict[str, Any]) -> dict[str, Any] | None:
    active_number = _project_number(active.get("project_number"))
    candidates = [
        row for row in projects
        if _project_number(row.get("project_number")) is not None
        and (active_number is None or _project_number(row.get("project_number")) < active_number)
        and row.get("completed_date") not in (None, "")
    ]
    matching = [row for row in candidates if _same_time(row.get("completed_date"), active.get("start_date"))]
    if matching:
        return max(matching, key=lambda row: _project_number(row["project_number"]))
    return max(candidates, key=lambda row: _project_number(row["project_number"])) if candidates else None


def _project_resource(project: dict[str, Any]) -> str | None:
    for field in ("resource_symbol", "token_symbol"):
        value = project.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    project_type = project.get("project_type")
    if not isinstance(project_type, str) or not project_type.lower().startswith("worksite_"):
        return None
    resource = project_type.lower()[len("worksite_"):]
    if resource.endswith("_construction"):
        resource = resource[:-len("_construction")]
    return resource.upper() or None


@dataclass(frozen=True)
class LandPlotRecord:
    deed_uid: str
    facts: dict[str, Any]
    source_hash: str
    observed_at: str


@dataclass(frozen=True)
class WorksiteRecord:
    deed_uid: str
    facts: dict[str, Any]
    source_hash: str
    observed_at: str


@dataclass(frozen=True)
class StakingRecord:
    deed_uid: str
    facts: dict[str, Any]
    source_hash: str
    observed_at: str


@dataclass(frozen=True)
class WorkerCardRecord:
    asset_ref: str
    facts: dict[str, Any]
    source_hash: str
    observed_at: str


@dataclass(frozen=True)
class ItemRecord:
    asset_ref: str
    facts: dict[str, Any]
    source_hash: str
    observed_at: str


@dataclass(frozen=True)
class AvailabilityClassification:
    bucket: str
    confidence: str
    reason: str
    branch: int

    def as_dict(self) -> dict[str, Any]:
        return {"bucket": self.bucket, "confidence": self.confidence, "reason": self.reason, "branch": self.branch}


Deed = LandPlotRecord
Plot = LandPlotRecord
Worksite = WorksiteRecord
PlotStakingState = StakingRecord
WorkerCard = WorkerCardRecord
Item = ItemRecord
LandItem = ItemRecord


class LandClient:
    def __init__(self, timeout: float = 20.0, retries: int = 3, transport: FixedGetTransport | None = None):
        self.transport = wrap_injected_transport(transport) if transport is not None else FixedGetTransport(timeout=timeout, retries=retries)

    def bundle(self, player: str) -> ResponseData:
        encoded = urllib.parse.quote(player, safe="")
        result = self.transport.get_json(LAND_ORIGIN + LAND_DEEDS_PATH + encoded)
        payload = result.data
        if not isinstance(payload, dict) or payload.get("status") not in (None, "success"):
            raise UpstreamError(f"Land API returned status {payload.get('status') if isinstance(payload, dict) else None}")
        data = payload.get("data") if isinstance(payload, dict) else None
        # a player who owns no land gets `deeds: []` with `worksite_details`/`staking_details` null
        # that is a real, empty answer, not a broken one. Normalise it to empty arrays so the board reads as
        # "no land plots" instead of failing.
        if isinstance(data, dict) and data.get("deeds") == [] and all(data.get(key) in (None, []) for key in ("worksite_details", "staking_details")):
            data["worksite_details"] = data.get("worksite_details") or []
            data["staking_details"] = data.get("staking_details") or []
        required = ("deeds", "worksite_details", "staking_details")
        if not isinstance(data, dict) or any(not isinstance(data.get(key), list) for key in required):
            raise SchemaError("Land deeds response must contain deeds, worksite_details, and staking_details arrays")
        return result

    def deed(self, plot_id: int) -> ResponseData:
        result = self.transport.get_json(LAND_ORIGIN + SINGLE_DEED_PATH.format(plot_id=int(plot_id)))
        payload = result.data
        if not isinstance(payload, dict) or payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
            raise SchemaError("Land single-deed response must contain a deed object")
        if payload["data"].get("plot_id") != int(plot_id):
            raise SchemaError("Land single-deed response names a different plot")
        return result

    def deed_staking(self, deed_uid: str) -> ResponseData:
        encoded = urllib.parse.quote(deed_uid, safe="")
        result = self.transport.get_json(LAND_ORIGIN + DEED_STAKING_PATH.format(deed_uid=encoded))
        payload = result.data
        if not isinstance(payload, dict) or payload.get("status") != "success" or not isinstance(payload.get("data"), dict):
            raise SchemaError("Land deed-staking response must contain a staking object")
        if payload["data"].get("deed_uid") != deed_uid:
            raise SchemaError("Land deed-staking response names a different deed")
        return result

    def project_history(self, deed_uid: str) -> ResponseData:
        encoded = urllib.parse.quote(deed_uid, safe="")
        result = self.transport.get_json(LAND_ORIGIN + PROJECT_HISTORY_PATH.format(deed_uid=encoded))
        _validate_project_history(result.data)
        return result

    def owned_deed_counts(self, player: str) -> ResponseData:
        """Per-region deed COUNTS only ( whale-account performance batch) --
        `{"status":"success","data":[{"count":N,"uid":"PR-<territory>-<region>"},...]}`,
        ~150 bytes regardless of account size, vs. ~500KB+ (and ~10MB+ for a
        6,000-plot whale) for the full `bundle()` fetch to learn the same total.
        Verified live 2026-08-21: sum(counts) matches the known deed count exactly
        for both player0005 (130) and player0001 (470). `player` is a PATH segment --
        `?player=` on this endpoint returns `data: null`, not a 4xx, so the
        malformed-response check below is load-bearing, not defensive boilerplate."""
        encoded = urllib.parse.quote(player, safe="")
        result = self.transport.get_json(LAND_ORIGIN + OWNED_COUNTS_PATH.format(player=encoded))
        payload = result.data
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise UpstreamError(f"Land owned-deeds API returned status {payload.get('status') if isinstance(payload, dict) else None}")
        data = payload.get("data")
        if not isinstance(data, list) or not all(
            isinstance(row, dict) and isinstance(row.get("count"), int) and isinstance(row.get("uid"), str)
            for row in data
        ):
            raise SchemaError("Land owned-deeds response must be a list of {count, uid} rows")
        return result

    # harvest readers: narrow, one request each, schema-checked by ceres.harvest.
    def active_project(self, deed_uid: str) -> ResponseData:
        encoded = urllib.parse.quote(deed_uid, safe="")
        result = self.transport.get_json(LAND_ORIGIN + ACTIVE_PROJECT_PATH.format(deed_uid=encoded))
        payload = result.data
        if not isinstance(payload, dict) or payload.get("status") not in (None, "success"):
            raise UpstreamError("Land active-project API did not return success")
        data = payload.get("data")
        if data is not None and (not isinstance(data, dict) or data.get("deed_uid") != deed_uid):
            raise SchemaError("Land active-project row does not name the requested deed")
        return result

    def region_harvestable(self, player: str, region_uid: str) -> ResponseData:
        url = LAND_ORIGIN + REGION_HARVESTABLE_PATH.format(player=urllib.parse.quote(player, safe=""), region_uid=urllib.parse.quote(region_uid, safe=""))
        return self.transport.get_json(url)

    def taxes(self, deed_uid: str) -> ResponseData:
        # the taxes a Keep/Castle holds -- `data.taxes: [{token, balance, player}]` + `data.capacity`.
        result = self.transport.get_json(LAND_ORIGIN + TAXES_PATH.format(deed_uid=urllib.parse.quote(deed_uid, safe="")))
        payload = result.data
        if not isinstance(payload, dict) or payload.get("status") not in (None, "success"):
            raise UpstreamError("Land taxes API did not return success")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("taxes"), list):
            raise SchemaError("Land taxes response must carry a taxes list")
        return result

    def regional_balances(self, player: str) -> ResponseData:
        return self.transport.get_json(LAND_ORIGIN + REGION_BALANCES_PATH.format(player=urllib.parse.quote(player, safe="")))

    def liquidity_allrewards(self) -> ResponseData:
        # pool-level reward totals, one row per (liquidity_pool_id, token) -- the
        # upstream carries no player field at all, so this is never player-scoped.
        return self.transport.get_json(LAND_ORIGIN + LIQUIDITY_ALLREWARDS_PATH)

    def stake_dec_overall(self, player: str) -> ResponseData:
        # `data` is a bare JSON number (the only scalar-payload route in this corpus).
        return self.transport.get_json(LAND_ORIGIN + STAKE_DEC_OVERALL_PATH.format(player=urllib.parse.quote(player, safe="")))

    def stake_dec_staked(self, player: str) -> ResponseData:
        # already used by ceres/routes_dec.py's /api/dec overview for a different
        # purpose (per-worksite staked-vs-required); this call is independent and read-only.
        return self.transport.get_json(LAND_ORIGIN + STAKE_DEC_STAKED_PATH.format(player=urllib.parse.quote(player, safe="")))

    def stake_evp_pending(self, player: str) -> ResponseData:
        return self.transport.get_json(LAND_ORIGIN + STAKE_EVP_PENDING_PATH.format(player=urllib.parse.quote(player, safe="")))

    def resource_history(self, trx_id: str) -> ResponseData:
        return self.transport.get_json(LAND_ORIGIN + RESOURCE_HISTORY_PATH.format(trx_id=urllib.parse.quote(trx_id, safe="")))

    def reward_actions(self, deed_uid: str, limit: int = 20) -> ResponseData:
        return self.transport.get_json(LAND_ORIGIN + REWARD_ACTIONS_PATH.format(deed_uid=urllib.parse.quote(deed_uid, safe=""), limit=int(limit)))

    def assets(self, deed_uid: str) -> ResponseData:
        encoded = urllib.parse.quote(deed_uid, safe="")
        result = self.transport.get_json(LAND_ORIGIN + ASSETS_PATH.format(deed_uid=encoded))
        payload = result.data
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("status") not in (None, "success"):
            raise UpstreamError(f"Land assets API returned status {payload.get('status') if isinstance(payload, dict) else None}")
        if not isinstance(data, dict) or not isinstance(data.get("cards"), list) or not isinstance(data.get("items"), list):
            raise SchemaError("Land assets response must contain cards and items arrays")
        return result

    def available_raw(self, stake_type_uid: str, deed_uid: str, player: str, offset: int = 0, limit: int = 5) -> ResponseData:
        code = urllib.parse.quote(stake_type_uid, safe="")
        deed = urllib.parse.quote(deed_uid, safe="")
        account = urllib.parse.quote(player, safe="")
        url = LAND_ORIGIN + AVAILABLE_PATH.format(stake_type_uid=code) + f"?deedUid={deed}&player={account}&offset={offset}&limit={limit}"
        return self.transport.get_json(url)

    def available(self, stake_type_uid: str, deed_uid: str, player: str, offset: int = 0, limit: int = 5) -> list[Any]:
        return available_item_ids(self.available_raw(stake_type_uid, deed_uid, player, offset, limit).data)


def _deed_stats(deed: dict[str, Any]) -> dict[str, Any]:
    raw = deed.get("stats")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _record_fields(raw: dict[str, Any]) -> dict[str, Any]:
    return dict(raw)


def normalize_bundle(payload: dict[str, Any], observed_at: str | None = None) -> tuple[list[LandPlotRecord], list[WorksiteRecord], list[StakingRecord]]:
    if payload.get("status") not in (None, "success"):
        raise UpstreamError(f"Land API returned status {payload.get('status')}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise SchemaError("Land response data must be an object")
    stamp = observed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    deeds, worksites, staking = [], [], []
    for raw in data.get("deeds", []):
        if not isinstance(raw, dict) or not raw.get("deed_uid"):
            raise SchemaError("Land deed is missing deed_uid")
        deeds.append(LandPlotRecord(str(raw["deed_uid"]), _record_fields(raw), source_hash(raw), stamp))
    for raw in data.get("worksite_details", []):
        if not isinstance(raw, dict) or not raw.get("deed_uid"):
            raise SchemaError("Land worksite is missing deed_uid")
        worksites.append(WorksiteRecord(str(raw["deed_uid"]), _record_fields(raw), source_hash(raw), stamp))
    for raw in data.get("staking_details", []):
        if not isinstance(raw, dict) or not raw.get("deed_uid"):
            raise SchemaError("Land staking record is missing deed_uid")
        staking.append(StakingRecord(str(raw["deed_uid"]), _record_fields(raw), source_hash(raw), stamp))
    return deeds, worksites, staking


def _bulk_bundle_completeness(payload: Any) -> tuple[bool, str | None]:
    """Check plot-level board inputs before publishing a quick-capture board.

    A bundle is incomplete when more than 20% of deeds lack `plot_status`, or
    more than 20% of staked plots lack a staking row or any of the Totem, Title,
    DEC-needed, or DEC-staked fields. A missing field is distinct from a valid
    numeric zero. Staked plots are identified by deed `in_use` or positive
    staking worker/Base PP facts.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return True, "bundle data is missing"
    deeds = data.get("deeds")
    stakes = data.get("staking_details")
    if not isinstance(deeds, list) or not isinstance(stakes, list):
        return True, "deed or staking section is missing"
    deed_rows = [row for row in deeds if isinstance(row, dict)]
    stake_by_uid = {
        str(row.get("deed_uid")): row for row in stakes
        if isinstance(row, dict) and row.get("deed_uid") is not None
    }
    missing_status = sum(row.get("plot_status") in (None, "") for row in deed_rows)
    status_incomplete = bool(deed_rows) and missing_status / len(deed_rows) > 0.20
    staked = []
    for deed in deed_rows:
        uid = str(deed.get("deed_uid") or "")
        stake = stake_by_uid.get(uid)
        has_workers_or_base = False
        if isinstance(stake, dict):
            try:
                has_workers_or_base = any(
                    float(stake.get(key) or 0) > 0 for key in ("worker_count", "total_base_pp")
                )
            except (TypeError, ValueError):
                has_workers_or_base = True
        if deed.get("in_use") is True or (
            isinstance(stake, dict) and has_workers_or_base
        ):
            staked.append((uid, stake))
    required_staking_fields = ("totem_boost", "title_boost", "total_dec_stake_needed", "total_dec_staked")
    missing_staking = sum(
        not isinstance(stake, dict) or any(stake.get(key) is None for key in required_staking_fields)
        for _, stake in staked
    )
    staking_incomplete = bool(staked) and missing_staking / len(staked) > 0.20
    if not status_incomplete and not staking_incomplete:
        return False, None
    issues = []
    if status_incomplete:
        issues.append(f"{missing_status} of {len(deed_rows)} deeds lack plot status")
    if staking_incomplete:
        issues.append(f"{missing_staking} of {len(staked)} staked plots lack staking facts or boosts")
    return True, "; ".join(issues)


def project_worksite_facts(
    payload: dict[str, Any], deed_uid: str, retrieved_at: str,
) -> dict[str, Any] | None:
    """Return one deed's worksite facts from a freshly retrieved bundle."""
    deeds, worksites, _ = normalize_bundle(payload, observed_at=retrieved_at)
    if deed_uid not in {deed.deed_uid for deed in deeds}:
        return None
    worksite_map = {worksite.deed_uid: worksite.facts for worksite in worksites}
    return worksite_map.get(deed_uid)


def normalize_worker(raw: dict[str, Any], observed_at: str | None = None) -> WorkerCardRecord:
    if not raw.get("uid"):
        raise SchemaError("Land worker card is missing uid")
    stamp = observed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    facts = _record_fields(raw)
    digest = source_hash(raw)
    facts["source_hash"] = digest
    facts["observed_at"] = stamp
    facts["asset_ref"] = stable_asset_ref(str(raw["uid"]))
    facts["asset_kind"] = "runi_worker_card" if raw.get("stake_type_uid") == "STK-LND-RUNI" else "worker_card"
    facts["counts_toward_worker_count"] = True
    return WorkerCardRecord(facts["asset_ref"], facts, digest, stamp)


def normalize_collection_worker(
    raw: dict[str, Any],
    observed_at: str | None = None,
    card_names: Mapping[int, str] | None = None,
) -> WorkerCardRecord:
    """Normalize a currently staked collection card into the roster shape.

    Collection rows provide card identity and `stake_ref_uid`, but not the deed asset
    fields such as slot, per-card PP decomposition, or per-worker power state. Those
    fields are overlaid by `normalize_worker` output when the deed assets are fetched.
    """
    if not raw.get("uid"):
        raise SchemaError("Collection worker card is missing uid")
    stamp = observed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    facts = _record_fields(raw)
    if card_names is not None:
        facts.pop("name", None)
    if card_names is not None and raw.get("card_detail_id") is not None:
        try:
            name = card_names.get(int(raw["card_detail_id"]))
        except (TypeError, ValueError):
            name = None
        if name not in (None, ""):
            facts["name"] = name
    stake_type_uid = raw.get("stake_type_uid") or ("STK-LND-RUNI" if raw.get("card_detail_id") == 505 else "STK-LND-WKR")
    digest = source_hash(raw)
    facts["source_hash"] = digest
    facts["observed_at"] = stamp
    facts["asset_ref"] = stable_asset_ref(str(raw["uid"]))
    facts["stake_type_uid"] = stake_type_uid
    facts["asset_kind"] = "runi_worker_card" if stake_type_uid == "STK-LND-RUNI" else "worker_card"
    facts["counts_toward_worker_count"] = True
    return WorkerCardRecord(facts["asset_ref"], facts, digest, stamp)


def normalize_item(raw: dict[str, Any], observed_at: str | None = None) -> ItemRecord:
    if not raw.get("uid"):
        raise SchemaError("Land item is missing uid")
    stamp = observed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    facts = _record_fields(raw)
    digest = source_hash(raw)
    facts["source_hash"] = digest
    facts["observed_at"] = stamp
    facts["asset_ref"] = stable_asset_ref(str(raw["uid"]))
    facts["asset_kind"] = {
        "STK-LND-PCR": "power_core", "STK-LND-TOT": "totem", "STK-LND-TTL": "title", "STK-LND-RUNI": "runi",
    }.get(raw.get("stake_type_uid"), "unknown")
    return ItemRecord(facts["asset_ref"], facts, digest, stamp)


class _CachedAssetResponses(Mapping[str, dict[str, Any]]):
    def __init__(self, cache: JsonCache, keys_by_deed: Mapping[str, str]):
        self._cache = cache
        self._keys_by_deed = dict(keys_by_deed)

    def __getitem__(self, deed_uid: str) -> dict[str, Any]:
        envelope, _ = self._cache.read(self._keys_by_deed[deed_uid], ttl=0, offline=True)
        if not isinstance(envelope, dict) or not isinstance(envelope.get("data"), dict):
            raise SchemaError("cached Land assets response must contain a data object")
        return envelope["data"]

    def __iter__(self):
        return iter(self._keys_by_deed)

    def __len__(self) -> int:
        return len(self._keys_by_deed)


def _asset_map(assets_by_deed: Mapping[str, dict[str, Any]]) -> tuple[dict[str, tuple[str, dict[str, Any]]], dict[str, bool]]:
    assigned: dict[str, tuple[str, dict[str, Any]]] = {}
    for deed_uid, response in assets_by_deed.items():
        for card in response.get("cards", []):
            if isinstance(card, dict) and card.get("uid"):
                assigned[str(card["uid"])] = (deed_uid, {"is_powered": card.get("is_powered")})
    return assigned, {key: True for key in assets_by_deed}


# 's `UNUSED_COLLECTION_FIELDS` now lives in `collection_stream` () and is
# re-exported above; the streaming parser drops those keys before a row ever exists.
_SKIP_FACT_KEYS = frozenset(UNUSED_COLLECTION_FIELDS)
_SKIP_FACT_KEYS_NO_UID = _SKIP_FACT_KEYS | {"uid"}
logger = logging.getLogger("praetoria.land_analysis")


def classify_card(
    card: dict[str, Any],
    as_of: datetime,
    player: str = DEFAULT_PLAYER,
    assigned: tuple[str, dict[str, Any]] | None = None,
    assets_checked: bool = False,
) -> AvailabilityClassification:
    delegated_to = card.get("delegated_to")
    if delegated_to not in (None, "", player):
        return AvailabilityClassification("blocked", "blocked", f"delegated to {delegated_to}", 1)
    # A card rented IN is delegated to the player by another maintainer and keeps the rental's market_id and listing
    # expiration_date; it is the renter's to stake (, ), so the rental and listing checks do not apply.
    rented_in = delegated_to == player and card.get("player") not in (None, "", player)
    if card.get("rental_type") not in (None, "") and not rented_in:
        return AvailabilityClassification("blocked", "blocked", f"rented ({card.get('rental_type')})", 2)
    if card.get("market_id") not in (None, "") and not rented_in:
        return AvailabilityClassification("blocked", "blocked", "listed on market", 3)
    unlock = _time(card.get("unlock_date"))
    if unlock and unlock > as_of:
        return AvailabilityClassification("blocked", "blocked", f"locked until {card.get('unlock_date')}", 4)
    expiration = _time(card.get("expiration_date"))
    if expiration and expiration < as_of and not rented_in:
        return AvailabilityClassification("blocked", "blocked", f"expired {card.get('expiration_date')}", 5)
    if assigned is not None and assigned[0] == str(card.get("stake_ref_uid")) and assigned[1].get("is_powered") is True:
        return AvailabilityClassification("assigned_active", "confident", "assigned and powered", 6)
    if assigned is not None and assigned[0] == str(card.get("stake_ref_uid")) and assigned[1].get("is_powered") is False:
        return AvailabilityClassification("assigned_inactive", "confident", "assigned and unpowered", 7)
    ref = card.get("stake_ref_uid")
    start_raw, end_raw = card.get("stake_start_date"), card.get("stake_end_date")
    start, end = _time(start_raw), _time(end_raw)
    # A `stake_end_date` is only ever set once an unstake has begun (R-127
    # confirmed against his live pool): a FUTURE end is an in-progress 72h cooldown, a
    # PAST end is a cooldown already elapsed. Either way the card is already off its
    # deed -- this must be decided before the assets_checked/assigned cross-check gates
    # below, or an un-cross-checked cooling card falls through to
    # `stake_active_unconfirmed` as if it were still a live, unverified stake. That was
    # the defect: 104 winding-down cards (41-63h from clearing) fed a plot-side
    # `collection_join` assignment, so their deeds read as still occupied instead of
    # 100% clear and immediately restakeable. `stake_active_unconfirmed` is now reserved
    # for a card with NO end date at all -- genuinely staked, just not cross-checked.
    if end_raw is not None and end is not None and assigned is None:
        if end > as_of:
            return AvailabilityClassification("stake_cooling_down", "confident", f"unstaked; cooling until {end_raw}", 8)
        return AvailabilityClassification("stake_expired_or_historical", "confident", "stake ended before snapshot", 9)
    active_evidence = ref is not None and (start_raw is None or start is not None) and end_raw is None and (not start or start <= as_of)
    if active_evidence and (not assets_checked or assigned is None):
        return AvailabilityClassification("stake_active_unconfirmed", "uncertain", "active stake not cross-checked", 8)
    if ref is None and start_raw is None and end_raw is None:
        return AvailabilityClassification("unstaked_apparent", "uncertain", "no stake evidence", 10)
    # `ambiguous` is in neither `IDLE_BUCKETS` nor `COOLING_BUCKETS`, so the card is never built
    # into a candidate row and silently disappears from the picker with nothing shown to the player ( / ).
    return AvailabilityClassification("ambiguous", "uncertain", "stake evidence is incomplete or contradictory", 11)


def _metric(value: float | None, denominator: str, reason: str | None = None) -> dict[str, Any]:
    return {"value": json_number(value), "denominator": denominator, "reason": reason} if value is None else {"value": json_number(value), "denominator": denominator}


def _plot_metrics(stake: dict[str, Any], worksite: dict[str, Any] | None) -> dict[str, Any]:
    after = _number(stake.get("total_base_pp_after_cap"), "total_base_pp_after_cap")
    cap = _number(stake.get("total_base_pp_cap"), "total_base_pp_cap")
    workers = _number(stake.get("worker_count"), "worker_count")
    max_workers = _number(stake.get("max_workers_allowed"), "max_workers_allowed")
    powered = _number(stake.get("powered_worker_count"), "powered_worker_count")
    construction = _number(stake.get("total_construction_pp"), "total_construction_pp")
    boost_pp = _number(stake.get("total_boost_pp"), "total_boost_pp")
    harvest = _number(stake.get("total_harvest_pp"), "total_harvest_pp")
    metrics: dict[str, Any] = {
        "base_cap_utilization_after_cap_base_pp_over_base_pp_cap": _metric(after / cap, "total_base_pp_after_cap / total_base_pp_cap", "undefined denominator" if cap in (None, 0) else None) if cap not in (None, 0) and after is not None else _metric(None, "total_base_pp_after_cap / total_base_pp_cap", "undefined denominator"),
        "worker_slot_utilization_worker_count_over_max_workers_allowed": _metric(workers / max_workers, "worker_count / max_workers_allowed") if workers is not None and max_workers not in (None, 0) else _metric(None, "worker_count / max_workers_allowed", "undefined denominator"),
        "power_utilization_powered_worker_count_over_worker_count": _metric(powered / workers, "powered_worker_count / worker_count") if powered is not None and workers not in (None, 0) else _metric(None, "powered_worker_count / worker_count", "undefined denominator"),
        "boost_leverage_total_boost_pp_over_total_construction_pp": _metric(boost_pp / construction, "total_boost_pp / total_construction_pp") if boost_pp is not None and construction not in (None, 0) else _metric(None, "total_boost_pp / total_construction_pp", "undefined denominator"),
        "total_harvest_pp_per_powered_worker_count": _metric(harvest / powered, "total_harvest_pp / powered_worker_count") if harvest is not None and powered not in (None, 0) else _metric(None, "total_harvest_pp / powered_worker_count", "undefined denominator"),
        "upstream_efficiency": {"value": None, "reason": "UNVERIFIED denominator"},
        "site_efficiency": {"value": None, "reason": "UNVERIFIED denominator"},
        "dec_relationship": {"value": None, "reason": "UNVERIFIED relationship"},
    }
    if worksite:
        metrics["reported_rewards_per_hour"] = {"value": worksite.get("rewards_per_hour"), "unit": "upstream unit", "status": "observed"}
    return metrics


def _diagnostics(deed: dict[str, Any], stake: dict[str, Any], worksite: dict[str, Any] | None, metrics: dict[str, Any]) -> list[dict[str, Any]]:
    uid = str(deed["deed_uid"])
    flags: list[tuple[str, str]] = []
    worker_count = stake.get("worker_count", 0)
    max_workers = stake.get("max_workers_allowed", 0)
    if worker_count == 0:
        flags.append(("zero_workers", "plot has zero observed workers"))
    if isinstance(max_workers, (int, float)) and max_workers > worker_count:
        flags.append(("open_worker_slots", "plot has open observed worker slots"))
    if stake.get("is_powered") is False:
        flags.append(("unpowered", "plot is not powered"))
    if deed.get("is_construction") is True:
        flags.append(("construction_incomplete", "deed is marked under construction"))
    if worksite is None:
        flags.append(("no_worksite", "no bundled worksite record was returned"))
    elif worksite.get("is_construction") is True or worksite.get("completed_date") is not None:
        flags.append(("unfinished_worksite", "worksite lifecycle is unfinished"))
    utilization = metrics["base_cap_utilization_after_cap_base_pp_over_base_pp_cap"].get("value")
    if utilization is not None and utilization < LOW_BASE_CAP_UTILIZATION_THRESHOLD:
        flags.append(("low_base_cap_utilization", f"base cap utilization is below {LOW_BASE_CAP_UTILIZATION_THRESHOLD:g}"))
    for name, source in (("runi", "total_runi_boost"), ("title", "total_title_boost"), ("totem", "total_totem_boost")):
        if stake.get(source) in (None, 0, 0.0, "0", "0.000"):
            flags.append((f"missing_{name}_bonus", f"observed {source} is zero"))
    return [{"type": kind, "deed_uid": uid, "message": message, "evidence": "observed bundle field"} for kind, message in flags]


def analyze_bundle(
    player: str,
    bundle_payload: dict[str, Any],
    assets_by_deed: Mapping[str, dict[str, Any]],
    failed_assets: list[dict[str, str]] | None = None,
    collection_payload: dict[str, Any] | None = None,
    as_of: datetime | None = None,
    include_asset_ids: bool = False,
    include_raw: bool = False,
    observed_at: str | None = None,
    prior_resources_by_deed: dict[str, Any] | None = None,
    card_names: Mapping[int, str] | None = None,
    assets_observed_at: Mapping[str, str] | None = None,
    collection_observed_at: str | None = None,
) -> dict[str, Any]:
    """Project bulk deeds, collection, and optional deed assets into one report.

    The account collection is the roster authority: active rows are joined by
    `stake_ref_uid`, and a non-empty `stake_end_date` excludes a card that is leaving.
    Deed assets only enrich those joined rows with slot, per-card PP, power, and item
    fields; when the collection is unavailable, their worker rows remain the fallback.
    """
    as_of = as_of or datetime.now(timezone.utc)
    stamp = observed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    assets_observed_at = assets_observed_at or {}
    deeds, worksites, staking = normalize_bundle(bundle_payload, stamp)
    deed_map = {record.deed_uid: record.facts for record in deeds}
    deed_source_hashes = {record.deed_uid: record.source_hash for record in deeds}
    worksite_map = {record.deed_uid: record.facts for record in worksites}
    stake_map = {record.deed_uid: record.facts for record in staking}
    failed_assets = failed_assets or []
    prior_resources_by_deed = prior_resources_by_deed or {}
    failed_by_deed = {item["deed_uid"]: item["reason"] for item in failed_assets}
    assigned, _ = _asset_map(assets_by_deed)
    asset_cards: list[dict[str, Any]] = []
    asset_items: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    asset_cards_by_ref: dict[str, dict[str, Any]] = {}
    for deed_uid in sorted(assets_by_deed):
        response = assets_by_deed[deed_uid]
        for raw in response.get("cards", []):
            card = normalize_worker(raw, assets_observed_at.get(deed_uid, stamp)).facts
            if not include_asset_ids:
                card.pop("uid", None)
            if not include_raw:
                card.pop("raw", None)
            asset_cards.append(card)
            asset_cards_by_ref[card["asset_ref"]] = card
        for raw in response.get("items", []):
            item = normalize_item(raw, assets_observed_at.get(deed_uid, stamp)).facts
            if not include_asset_ids:
                item.pop("uid", None)
            asset_items.append(item)
    collection_cards = []
    collection_workers: list[dict[str, Any]] = []
    if collection_payload is not None:
        if not isinstance(collection_payload, dict) or not isinstance(collection_payload.get("cards"), list):
            raise SchemaError("collection response must contain cards array")
        for raw in collection_payload["cards"]:
            if not isinstance(raw, dict) or not raw.get("uid"):
                raise SchemaError("collection card is missing uid")
            card = raw  # the parsed payload is not kept or mutated; a second copy per card was a whale's biggest cost
            classification = classify_card(card, as_of, player, assigned.get(str(card["uid"])), str(card.get("stake_ref_uid")) in assets_by_deed)
            # built at final size. Popping keys from a full copy left every dict at its
            # original ~70-key table -- 125,097 of them held ~270 MB on player0017.
            if DROPPED_COLLECTION_FIELDS.isdisjoint(card):
                # a stream-parsed row is already at its final key set; one plain copy
                # keeps the report's facts independent of the ETag memo's rows.
                facts = dict(card)
                if not include_asset_ids:
                    facts.pop("uid", None)
            else:
                skip = _SKIP_FACT_KEYS if include_asset_ids else _SKIP_FACT_KEYS_NO_UID
                facts = {key: value for key, value in card.items() if key not in skip}
            result = {"asset_ref": stable_asset_ref(str(card["uid"])), "classification": classification.as_dict(), "facts": facts}
            if include_asset_ids:
                result["uid"] = card["uid"]
            if include_raw:
                result["raw"] = card
            collection_cards.append(result)
            if card.get("stake_ref_uid") and not card.get("stake_end_date") and str(card["stake_ref_uid"]) in deed_map:
                worker = normalize_collection_worker(card, collection_observed_at or stamp, card_names).facts
                if not include_asset_ids:
                    worker.pop("uid", None)
                collection_workers.append(worker)

    if collection_payload is None:
        workers = asset_cards
        assignments = [
            {"asset_ref": worker["asset_ref"], "deed_uid": worker.get("stake_ref_uid"), "slot": worker.get("slot"),
             "asset_kind": worker["asset_kind"], "confidence": "confident", "source": "land assets"}
            for worker in workers
        ]
    else:
        workers = []
        for collection_worker in collection_workers:
            worker = dict(collection_worker)
            asset_worker = asset_cards_by_ref.get(worker["asset_ref"])
            if asset_worker is not None:
                worker.update({key: value for key, value in asset_worker.items() if key not in {"stake_ref_uid", "uid"}})
            workers.append(worker)
            assignments.append({
                "asset_ref": worker["asset_ref"], "deed_uid": worker.get("stake_ref_uid"),
                "slot": worker.get("slot"), "asset_kind": worker["asset_kind"],
                "confidence": "confident" if asset_worker is not None else "collection_join",
                "source": "land assets" if asset_worker is not None else "collection join",
            })
    plots: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for deed_uid in sorted(deed_map):
        deed = deed_map[deed_uid]
        stake = stake_map.get(deed_uid, {})
        worksite = worksite_map.get(deed_uid)
        metrics = _plot_metrics(stake, worksite)
        diagnostics = _diagnostics(deed, stake, worksite, metrics)
        findings.extend(diagnostics)
        plots.append({
            "deed_uid": deed_uid,
            "source_hash": deed_source_hashes[deed_uid],
            "observed_at": assets_observed_at.get(deed_uid, stamp) if deed_uid in assets_by_deed else stamp,
            "facts": {"plot": f"{deed.get('region_number')}-{deed.get('tract_number')}-{deed.get('plot_number')}", "plot_id": deed.get("plot_id"), "region": deed.get("region_uid"), "region_name": deed.get("region_name"), "territory": deed.get("territory"), "resource": deed.get("resource_symbol"), "prior_resource": prior_resources_by_deed.get(deed_uid), "rarity": deed.get("rarity"), "status": deed.get("plot_status"), "worksite_type": deed.get("worksite_type"), "owner": deed.get("player"), "listed": deed.get("listed"), "in_use": deed.get("in_use"), "lock_days": deed.get("lock_days"), "unlock_date": deed.get("unlock_date"), "is_construction": deed.get("is_construction"), "worksite_present": worksite is not None, "staking_present": deed_uid in stake_map, "terrain": deed.get("deed_type"), "magic_type": deed.get("magic_type") or None, "deed_stats": _deed_stats(deed), "tax_rate": deed.get("tax_rate"), "time_crystal_value": deed.get("time_crystal_value")},
            "staking_facts": stake,
            "worksite_facts": worksite,
            "metrics": metrics,
            "diagnostics": diagnostics,
            # Asset fetch is retrieved, not_requested, or failed; Ceres merge may add
            # dropped_stale when a later bulk count proves retrieved detail stale.
            "asset_fetch": {"status": "failed", "reason": failed_by_deed[deed_uid]} if deed_uid in failed_by_deed else {"status": "retrieved" if deed_uid in assets_by_deed else "not_requested"},
        })
    warnings = []
    if failed_assets:
        warnings.append("PARTIAL DATA: " + "; ".join(f"deed {item['deed_uid']}: {item['reason']}" for item in failed_assets))
    total_deeds = len(deeds)
    retrieved_assets = len(assets_by_deed)
    summary = {
        "deeds": total_deeds,
        "total_harvest_pp": json_number(sum(_number(item.facts.get("total_harvest_pp"), "total_harvest_pp") or 0 for item in staking)),
        "plots_with_zero_workers": sum(1 for row in plots if row["staking_facts"].get("worker_count") == 0),
        "plots_idle": sum(1 for row in plots if row["staking_facts"].get("worker_count") == 0),
        "plots_unpowered": sum(1 for row in plots if row["staking_facts"].get("is_powered") is False),
        "plots_incomplete": sum(1 for row in plots if any(item["type"] in {"no_worksite", "construction_incomplete", "unfinished_worksite"} for item in row["diagnostics"])),
        "findings": len(findings),
    }
    report = {
        "schema_version": LAND_SCHEMA_VERSION,
        "kind": "observed_portfolio",
        "query": {"player": player, "as_of": as_of.isoformat().replace("+00:00", "Z")},
        "generated_at": stamp,
        "snapshot": {"status": "live_or_offline_capture", "schema_version": LAND_SCHEMA_VERSION},
        "sources": {"land_deeds_url": "https://vapi.splinterlands.com/land/deeds?player={player}", "land_assets_url": "https://vapi.splinterlands.com/land/stake/deeds/{deed_uid}/assets", "collection_url": "https://api.splinterlands.com/cards/collection/{player}"},
        "coverage": {"deeds": {"retrieved": total_deeds, "expected": total_deeds}, "assets": {"retrieved": retrieved_assets, "expected": total_deeds, "complete": retrieved_assets == total_deeds}, "collection": {"retrieved": len(collection_cards), "expected": None, "complete": collection_payload is not None, "fetched_at": (collection_observed_at or stamp) if collection_payload is not None else None}},
        "assumptions": {"verified_formulas": ["plot aggregate PP is sum of assigned asset rows", "base_cap_utilization_after_cap_base_pp_over_base_pp_cap is not clamped", "Runi card rows count toward worker_count", "highest-slot worker absorbs a measured base-PP cap shortfall"], "unverified": ["upstream_efficiency denominator", "site_efficiency denominator", "DEC relationships", "unassigned bonus inventory and eligibility"]},
        "warnings": warnings,
        "summary": summary,
        "plots": plots,
        "workers": workers,
        "cards": collection_cards,
        "bonus_assets": asset_items,
        "assignments": assignments,
        "findings": findings,
    }
    if include_raw:
        raw_assets = assets_by_deed if isinstance(assets_by_deed, dict) else {deed_uid: assets_by_deed[deed_uid] for deed_uid in assets_by_deed}
        report["raw"] = {"bundle": bundle_payload, "assets": raw_assets, "collection": collection_payload}
    return report


@dataclass(frozen=True)
class AssetRepairContext:
    player: str
    bundle: dict[str, Any]
    bundle_observed_at: str | None
    collection: dict[str, Any] | None
    collection_observed_at: str | None
    prior_resources_by_deed: dict[str, Any]
    card_names: Mapping[int, str] | None
    item_catalogue: list[dict[str, Any]] | None
    unassigned_items: dict[str, Any] | None
    include_asset_ids: bool


class CollectionMemo:
    """: the last stream-parsed collection per player, keyed by the response ETag.

    Held in this process and mirrored to `<cache>/collection_unbinding_v2_<player>.etag` (the ETag, one
    line) + `.etag.json` (the narrowed payload), because the Ceres board read runs in a worker
    child that lives for one read (). The payload is reused ONLY when the current read's
    LIVE response carries the same ETag -- the request is still made every time (); the
    memo replaces the inflate+parse, never the fetch. The disk copy is loaded lazily, only on
    an ETag match, so a changed collection never pays for reading a stale file."""

    def __init__(self, root: Path | None, keep: int = 2):
        self.root = root
        self.keep = max(1, int(keep))
        self._lock = threading.Lock()
        self._memory: dict[str, tuple[str, dict[str, Any]]] = {}

    def _paths(self, player: str) -> tuple[Path, Path] | None:
        if self.root is None:
            return None
        # Older narrowed memos omitted bcx_unbound and cannot establish card eligibility.
        stem = self.root / f"collection_unbinding_v2_{safe_name(player)}"
        return stem.with_suffix(".etag"), stem.with_suffix(".etag.json")

    def previous(self, player: str) -> tuple[str, Callable[[], dict[str, Any]]] | None:
        """(etag, loader) for the last parse, or None. The loader materialises the payload."""
        with self._lock:
            held = self._memory.get(player)
        if held is not None:
            etag, payload = held
            return etag, (lambda: payload)
        paths = self._paths(player)
        if paths is None:
            return None
        tag_path, payload_path = paths
        try:
            etag = tag_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not etag or not payload_path.exists():
            return None

        def load() -> dict[str, Any]:
            payload = fastjson.loads(payload_path.read_bytes())
            if not isinstance(payload, dict) or not isinstance(payload.get("cards"), list):
                raise SchemaError("collection memo is invalid")
            # S6 / L3-03: the payload carries the ETag it was parsed under; a tag file and a payload file written at different
            # moments (another writer, a crash between the two writes) never pair up by accident.
            if payload.pop("_memo_etag", None) != etag:
                raise SchemaError("collection memo does not match its tag")
            return payload

        return etag, load

    def remember(self, player: str, etag: str, payload: dict[str, Any], persist: bool = True) -> None:
        with self._lock:
            self._memory[player] = (etag, payload)
            while len(self._memory) > self.keep:
                self._memory.pop(next(iter(self._memory)))
        paths = self._paths(player)
        if paths is None or not persist:
            return
        tag_path, payload_path = paths
        try:
            tag_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=f".{payload_path.name}.", suffix=".tmp", dir=payload_path.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(fastjson.dumps({**payload, "_memo_etag": etag}))
            os.replace(temporary, payload_path)
            tag_path.write_text(etag + "\n", encoding="utf-8")
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("collection memo not persisted player=%s: %s", player, exc)


def _require_player(player: str | None) -> None:
    """: a capture names its account; there is no built-in default."""
    if not player:
        raise ValueError("player required: name the Splinterlands account to read")


class LandCapture:
    def __init__(
        self,
        land_client: LandClient | None = None,
        collection_client: SplinterlandsClient | None = None,
        cache: JsonCache | None = None,
        min_delay: float = MIN_INTER_REQUEST_DELAY_SECONDS,
        request_budget: int = DEFAULT_REQUEST_BUDGET,
        sleeper: Callable[[float], None] = time.sleep,
        concurrency: int = DEFAULT_CONCURRENCY,
    ):
        self.land_client = land_client or LandClient()
        self.collection_client = collection_client or SplinterlandsClient()
        self.cache = cache or JsonCache()  # the default root honours PRAETORIA_CACHE_DIR
        self.min_delay = max(0.0, float(min_delay))
        self.request_budget = max(1, int(request_budget))
        self.sleeper = sleeper
        self.concurrency = max(1, int(concurrency))
        self._last_request = 0.0
        self._requests = 0
        self._cache_hits = 0
        # Thread safety for the concurrent per-deed fan-out (2026-08-21): every field
        # `_request`/`_cached` touch is shared across worker threads, so all of it is
        # guarded by ONE lock. The lock is held only around bookkeeping (budget check
        # pacing wait, counters) -- never around the actual `fetch()` network call
        # so pacing/budget stay correct under concurrency while the I/O itself overlaps.
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        # (2026-09-14): a caller may opt a cache key out of persistence. `_cached`
        # still fetches and returns the data; it just does not write the file. The default
        # keeps every other caller's behaviour (the CLI persists everything).
        self.persist_cache: Callable[[str], bool] = lambda key: True
        self._throttle_until = 0.0
        self._asset_repair_context: AssetRepairContext | None = None
        # ETag memo beside the JSON cache; `persist_collection_memo=False` keeps it in memory only.
        self.collection_memo = CollectionMemo(getattr(self.cache, "root", None))
        self.persist_collection_memo = True

    def _collection_read(self, player: str) -> ResponseData:
        """One LIVE `/cards/collection/<player>` read (), stream-parsed to the board's
        key set; the parse is skipped only when the live response's ETag equals the memo's."""
        if not getattr(self.collection_client, "supports_narrow", False):
            return self.collection_client.collection(player)  # an injected client without the path
        previous = self.collection_memo.previous(player)
        result = self.collection_client.collection(player, narrow=True, previous=previous)
        if result.reused:
            logger.info("collection unchanged (etag), parse skipped player=%s etag=%s", player, result.etag)
        elif result.etag and isinstance(result.data, dict):
            self.collection_memo.remember(player, result.etag, result.data, self.persist_collection_memo)
        return result

    @contextmanager
    def _dispatch_ceiling(self, concurrent: bool):
        """Apply `CONCURRENT_DISPATCH_INTERVAL_SECONDS` for the duration of a concurrent
        fan-out and restore the serial gap afterwards, whatever happens inside. The
        full capture has done exactly this around its per-deed fan-out since 2026-08-21;
         (2026-09-07) gives the quick read the same treatment, because its reads
        ran on a pool while every dispatch still queued behind the 0.300 s serial sleep."""
        original = self.min_delay
        if concurrent:
            self.min_delay = min(self.min_delay, CONCURRENT_DISPATCH_INTERVAL_SECONDS)
        try:
            yield
        finally:
            self.min_delay = original

    # a caller may hand in a sizing rule (`budget_for_deeds(count) -> int`); the budget is then raised
    # to fit the account the moment its deed list is known -- also on a player's first-ever read, where no stored
    # board exists to size it from. Only ever raises; None/absent keeps the fixed budget.
    def fit_request_budget(self, bundle_payload: Any) -> None:
        rule = getattr(self, "budget_for_deeds", None)
        if rule is None:
            return
        data = bundle_payload.get("data") if isinstance(bundle_payload, dict) else None
        deeds = data.get("deeds") if isinstance(data, dict) else None
        if not isinstance(deeds, list):
            return
        try:
            wanted = int(rule(len(deeds)))
        except Exception:
            return
        with self._lock:
            if wanted > self.request_budget:
                self.request_budget = wanted

    def _request(self, fetch: Callable[[], ResponseData]) -> ResponseData:
        with self._lock:
            if self._requests >= self.request_budget:
                raise TransportError(f"Land request budget exhausted at {self.request_budget} requests")
            now = time.monotonic()
            # `min_delay` now paces DISPATCH starts globally (across every worker
            # thread), not full request/response cycles -- this is the "global
            # throughput ceiling" replacing the old fully-serial sleep-after-each
            # request model, so concurrent workers can overlap their I/O wait while
            # the rate NEW requests are fired at the shared API stays bounded.
            wait = (self.min_delay - (now - self._last_request)) if self._last_request else 0.0
            # Adaptive backoff: a burst of consecutive failures (very likely
            # FixedGetTransport exhausting its own 429/5xx retries) widens the
            # dispatch ceiling for a cooldown window instead of retrying at full
            # speed into a rate limit. Resets to normal pacing the moment a request
            # succeeds again.
            if self._throttle_until > now:
                wait = max(wait, self._throttle_until - now)
            if wait > 0:
                self.sleeper(wait)
                now = time.monotonic()
            self._last_request = now
            # Reserved BEFORE the network call, not after (unlike the old serial
            # design) -- under concurrency, crediting budget only on success would let
            # multiple in-flight threads all pass this check before any of them
            # finishes, overselling the budget. A failed dispatch still cost a real
            # round trip against the API, so it should count too.
            self._requests += 1
        try:
            with land_dispatch():
                result = fetch()
        except Exception:
            with self._lock:
                self._consecutive_failures += 1
                if self._consecutive_failures >= 3:
                    backoff = min(2 ** (self._consecutive_failures - 2), 30)
                    self._throttle_until = max(self._throttle_until, time.monotonic() + backoff)
            raise
        with self._lock:
            self._consecutive_failures = 0
        return result

    def fresh_read(self, fetch: Callable[[], ResponseData]) -> ResponseData:
        """Dispatch one uncached read through this capture's budget boundary."""
        return self._request(fetch)

    def _cached(self, key: str, fetch: Callable[[], ResponseData], offline: bool, ttl: int = 0) -> Any:
        if offline:
            data, _ = self.cache.read(key, ttl=0, offline=True)
            return data
        if ttl > 0:
            # Resume support (Ceres whale captures, 2026-08-21): a positive ttl lets an
            # interrupted capture resume from JsonCache's own on-disk entries rather than
            # re-fetching everything -- a re-run within `ttl` seconds of a partial capture
            # serves already-fetched deeds from cache (no network call, no budget spend)
            # and only fetches what is still missing. ttl=0 (the default) preserves the
            # original always-fetch behavior everywhere else. Cache reads themselves are
            # local file I/O, safe to call from any worker thread -- only the shared
            # counter below needs the lock.
            cached, info = self.cache.read(key, ttl=ttl, offline=False)
            if cached is not None:
                with self._lock:
                    self._cache_hits += 1
                return cached
        result = self._request(fetch)
        if self.persist_cache(key):
            self.cache.write(key, result.data, result.retrieved_at)
        return result.data

    @staticmethod
    def _bundle_for_deeds(bundle: dict[str, Any], deed_uids: set[str]) -> dict[str, Any]:
        """ (2026-09-07): the bundle's per-deed sections restricted to `deed_uids`, so a
        single-deed expand or a self-heal batch analyses those deeds only. `analyze_bundle`
        over the whole account cost 1.9-9.5 s of CPU per call on a 6,303-deed account, for
        one to eight deeds' worth of output; the callers merge only those deeds anyway."""
        if not isinstance(bundle, dict) or not isinstance(bundle.get("data"), dict):
            return bundle
        data = bundle["data"]
        narrowed = dict(data)
        for section in ("deeds", "worksite_details", "staking_details"):
            rows = data.get(section)
            if isinstance(rows, list):
                narrowed[section] = [row for row in rows if isinstance(row, dict) and str(row.get("deed_uid")) in deed_uids]
        out = dict(bundle)
        out["data"] = narrowed
        return out

    @staticmethod
    def _collection_for_deeds(collection: Any, deed_uids: set[str]) -> Any:
        """: the collection restricted to cards staked on `deed_uids`. Only the roster
        join for those deeds is needed by a self-heal batch; classifying all 124,974 cards of a
        whale collection for it cost ~8 s per batch. The caller's patch path never publishes
        this partial list as the account's card pool."""
        if not isinstance(collection, dict) or not isinstance(collection.get("cards"), list):
            return collection
        out = dict(collection)
        out["cards"] = [card for card in collection["cards"] if isinstance(card, dict) and str(card.get("stake_ref_uid")) in deed_uids]
        return out

    def _card_names(self, offline: bool, failed: list[dict[str, str]] | None = None) -> dict[int, str] | None:
        try:
            details = self._cached("card_details", self.collection_client.details, offline, ttl=86400)
        except Exception as exc:
            if failed is not None:
                failed.append(_capture_failure("card_names", exc))
            return None
        if not isinstance(details, list):
            return None
        names: dict[int, str] = {}
        for item in details:
            if not isinstance(item, dict) or item.get("id") is None or not item.get("name"):
                continue
            try:
                names[int(item["id"])] = str(item["name"])
            except (TypeError, ValueError):
                continue
        return names

    def _capture_prior_resources(
        self, bundle: dict[str, Any], offline: bool, deed_uids: set[str] | None = None,
        failed: list[dict[str, str]] | None = None,
    ) -> dict[str, str | None]:
        # (2026-09-14): the per-deed project-history fetch ( the assistant task
        # 2026-08-26) filled pending_change.from/from_label, which no page script or server
        # module reads; on player0006 (4357 plots, 3146 flagged is_construction because
        # undeveloped land carries the flag) it cost ~3150 requests and five minutes on first
        # load, and 4147 of 4194 histories on a production server were empty. Returns {} so
        # facts.prior_resource stays None and the report shape is unchanged.
        # LandClient.project_history and the _select_prior_project/_project_resource helpers
        # stay for tooling; nothing calls them from the capture path any more.
        return {}

    def preflight_deed_count(self, player: str = DEFAULT_PLAYER, offline: bool = False, ttl: int = 0) -> int:
        """One cheap call to learn how many plots this player owns, before committing
        to a full capture -- or before showing a capture-cost estimate the user may
        never act on ( whale-account performance batch, superseding the earlier
        design). Was: fetch the FULL deeds bundle (~500KB-10MB+ depending on account
        size) just to count its rows, cached with `ttl` specifically so a capture()
        call made shortly after could reuse it rather than re-fetching -- but
        `GET /api/capture/estimate` (the frontend's "Full capture" button) calls this
        on every click, including every time the user reads the estimate and does NOT
        proceed, which was paying the full bundle cost for a number alone. Now uses
        `LandClient.owned_deed_counts()` (~150 bytes) instead -- verified live 2026-08-21
        to match the bundle-derived count exactly for both player0005 and player0001. This
        does mean an eventual `capture()` no longer gets a pre-warmed bundle cache from
        preflight (the old "preflight-then-capture costs exactly one bundle request"
        property) -- but that was never guaranteed anyway (preflight is not always
        followed by a capture, which is exactly the case this change optimizes for);
        `capture()` fetches its own bundle when it actually runs, same as always."""
        _require_player(player)
        counts = self._cached(f"land_owned_counts_{safe_name(player)}", lambda: self.land_client.owned_deed_counts(player), offline, ttl=ttl)
        rows = counts.get("data", []) if isinstance(counts, dict) else []
        return sum(row.get("count", 0) for row in rows if isinstance(row, dict))

    def capture(
        self,
        player: str = DEFAULT_PLAYER,
        offline: bool = False,
        include_asset_ids: bool = False,
        include_raw: bool = False,
        as_of: datetime | None = None,
        progress: Callable[[int, int], None] | None = None,
        resume: bool = False,
        resume_ttl: int = 21600,
        concurrency: int | None = None,
    ) -> dict[str, Any]:
        """`resume=True` (Ceres whale captures) makes the per-deed asset fetch cache-first
        with a `resume_ttl`-second window (default 6h), so re-running `capture()` after an
        interruption picks up already-fetched deeds from `JsonCache` instead of re-hitting
        the network for them. `progress(fetched, total)`, when given, is called once per
        deed (success or failure) so a caller can surface "N/M plots" while a long capture
        runs.

        The per-deed asset fan-out runs through a bounded thread pool
        (`concurrency`, default `self.concurrency` -- see `DEFAULT_CONCURRENCY`) since
        almost the entire per-request cost is idle server think-time, not local work
        (measured live 2026-08-21: ~0.48s of a ~0.60s request). `_request`'s own lock
        still paces DISPATCHES globally across every worker thread (the "global
        throughput ceiling"), so concurrency overlaps I/O wait without uncapping how
        fast new requests are fired at the shared API. Output is deterministic
        regardless of completion order or worker count: results are collected into
        `assets`/`failed` keyed by deed_uid, and `analyze_bundle` always builds the
        final `plots` list sorted by deed_uid -- see `tests/test_land_capture_concurrency.py`.
        `concurrency=1` (or `offline=True`, which never touches the network at all)
        takes the plain serial path -- no thread-pool overhead for a fully-cached or
        single-worker run."""
        _require_player(player)
        try:
            bundle = self._cached(f"land_deeds_{safe_name(player)}", lambda: self.land_client.bundle(player), offline, ttl=300 if resume else 0)
        except ResponseTooLarge as exc:
            return _response_too_large_capture(player, "full", exc, self._requests, self._cache_hits)
        failed: list[dict[str, Any]] = []
        asset_cache_keys: dict[str, str] = {}
        deed_rows = bundle.get("data", {}).get("deeds", []) if isinstance(bundle, dict) else []
        prior_resources_by_deed = self._capture_prior_resources(bundle, offline)
        total_deeds = len(deed_rows)
        workers = max(1, int(concurrency if concurrency is not None else self.concurrency))

        def _fetch_one(row: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
            deed_uid = str(row.get("deed_uid"))
            key = f"land_assets_{stable_asset_ref(deed_uid)}"
            try:
                self._cached(
                    key, lambda: self.land_client.assets(deed_uid), offline,
                    ttl=resume_ttl if resume else 0,
                )
                return deed_uid, key, None
            except Exception as exc:
                return deed_uid, key, _capture_failure(deed_uid, exc)

        def _record(deed_uid: str, key: str, err: dict[str, Any] | None, completed: int) -> None:
            if err is not None:
                failed.append(err)
            else:
                asset_cache_keys[deed_uid] = key
            if progress is not None:
                progress(completed, total_deeds)

        if workers <= 1 or offline:
            for index, row in enumerate(deed_rows):
                deed_uid, key, err = _fetch_one(row)
                _record(deed_uid, key, err, index + 1)
        else:
            # The batch boundary bounds both futures and response objects; the dispatch
            # ceiling is restored immediately after the fan-out.
            original_min_delay = self.min_delay
            self.min_delay = min(self.min_delay, CONCURRENT_DISPATCH_INTERVAL_SECONDS)
            try:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    completed = 0
                    for offset in range(0, total_deeds, workers):
                        futures = [pool.submit(_fetch_one, row) for row in deed_rows[offset:offset + workers]]
                        for future in as_completed(futures):
                            deed_uid, key, err = future.result()
                            completed += 1
                            _record(deed_uid, key, err, completed)
            finally:
                self.min_delay = original_min_delay
        collection = None
        try:
            collection = self._cached(f"collection_{safe_name(player)}", lambda: self._collection_read(player), offline)
        except Exception as exc:
            failed.insert(0, _capture_failure("collection", exc))
        assets = _CachedAssetResponses(self.cache, asset_cache_keys)
        card_names = self._card_names(offline)
        report = analyze_bundle(player, bundle, assets, failed, collection, as_of, include_asset_ids, include_raw, prior_resources_by_deed=prior_resources_by_deed, card_names=card_names)
        report["item_catalogue"] = self._fetch_item_catalogue(offline)
        catalogue_lookup = item_catalogue_lookup(report)
        name_lookup = {detail_id: item["name"] for detail_id, item in catalogue_lookup.items() if item.get("name")}
        report["unassigned_items"] = self._capture_unassigned_items(deed_rows, player, offline, name_lookup, include_asset_ids, catalogue_lookup)
        report["capture"] = {
            "requests": self._requests,
            "cache_hits": self._cache_hits,
            "min_inter_request_delay_seconds": self.min_delay,
            "request_budget": self.request_budget,
            "offline": offline,
            "failures": failed,
        }
        self._asset_repair_context = AssetRepairContext(
            player=player,
            bundle=bundle,
            bundle_observed_at=report.get("generated_at"),
            collection=collection,
            collection_observed_at=report.get("coverage", {}).get("collection", {}).get("fetched_at"),
            prior_resources_by_deed=prior_resources_by_deed,
            card_names=card_names,
            item_catalogue=report.get("item_catalogue"),
            unassigned_items=report.get("unassigned_items"),
            include_asset_ids=include_asset_ids,
        )
        return report

    def capture_quick(
        self,
        player: str = DEFAULT_PLAYER,
        offline: bool = False,
        include_asset_ids: bool = False,
        include_raw: bool = False,
        as_of: datetime | None = None,
        on_bulk_ready: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Bulk-only capture -- other tools against this same API return a full board much
        faster by skipping the per-deed asset fan-out. The deeds bundle's `staking_details` and
        `worksite_details` sections carry every PP field, every boost category (including
        the numeric `card_abilities_boost`/`card_bloodlines_boost` totals), `worker_count`/
        `max_workers_allowed`, DEC figures, and construction/power state for EVERY plot in
        ONE request -- this is the entire main board table. Prior project history is fetched per
        construction deed, but served from a one-day cache after retrieval because it describes
        completed work rather than live state. Only per-worker IDENTITY (which
        specific card sits in which slot, its own PP/abilities, totem/title naming) needs
        the per-deed `/assets` fan-out that `capture()` performs -- that is what makes
        `capture()` cost one request per deed (6,303 for a whale account). This quick read still
        scales with the account's construction deeds for uncached project history, but repeated
        reads within the history TTL make no project-history network calls. No per-deed asset fetch happens here at
        all: `assets_by_deed={}`, so every plot's `asset_fetch.status` is "not_requested"
        (the existing, honest signal `analyze_bundle` already produces). The joined worker
        roster is available immediately from the collection sweep (`report["cards"]`), while
        asset-only fields (slot, per-card PP decomposition, per-worker power, and item identity)
        remain not-yet-loaded rather than a measured zero. Idle-card classification still waits
        for full asset coverage. Also captures the account-wide collection sweep (`report["cards"]`)
        and the Source A unassigned-item families (totem/title/power_core/runi availability --
        an account-scoped `/available` call, independent of deed count, so it's cheap enough to
        include here too)."""
        _require_player(player)
        failed: list[dict[str, Any]] = []
        # v0.9.29: the hook may also be set as an attribute (`capture.on_bulk_ready = ...`), so a
        # caller's existing `capture_quick(player, include_asset_ids=...)` call shape is unchanged.
        on_bulk_ready = on_bulk_ready or getattr(self, "on_bulk_ready", None)
        def _run_read(label: str, fetch: Callable[[list[dict[str, Any]]], Any]) -> tuple[Any, list[dict[str, Any]]]:
            read_failures: list[dict[str, Any]] = []
            try:
                return fetch(read_failures), read_failures
            except Exception as exc:
                if not read_failures:
                    read_failures.append(_capture_failure(label, exc))
                return None, read_failures

        quick_workers = min(max(1, self.concurrency), QUICK_CAPTURE_CONCURRENCY)

        # (2026-09-07), v0.9.27: the two reads keyed on deed uids used to wait for the
        # fresh bundle -- the one call on the critical path Ceres cannot shorten. The previous
        # capture's bundle (the cache entry, stale allowed) already knows the construction
        # deeds and an owned deed to anchor the account-scoped `/available` reads, so both
        # start at once and are reconciled against the fresh bundle when it lands. Cold, with
        # no previous bundle, the behaviour is exactly the old one.
        previous_bundle: dict[str, Any] | None = None
        if not offline:
            try:
                previous_bundle, _ = self.cache.read(f"land_deeds_{safe_name(player)}", ttl=0, offline=True)
            except Exception:
                previous_bundle = None
            if not isinstance(previous_bundle, dict) or not isinstance(previous_bundle.get("data"), dict):
                previous_bundle = None

        def _construction_uids(payload: Any) -> set[str]:
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            worksites = {str(r.get("deed_uid")): r for r in (data.get("worksite_details") or []) if isinstance(r, dict)}
            out: set[str] = set()
            for deed in data.get("deeds") or []:
                if not isinstance(deed, dict) or not deed.get("deed_uid"):
                    continue
                uid = str(deed["deed_uid"])
                if deed.get("is_construction") is True or worksites.get(uid, {}).get("is_construction") is True:
                    out.add(uid)
            return out

        def _capture_prior_resources_after_bundle(read_failures: list[dict[str, str]]) -> dict[str, Any]:
            early: dict[str, str | None] = {}
            if previous_bundle is not None:
                early = self._capture_prior_resources(previous_bundle, offline, failed=read_failures)
            bundle_payload = futures["bundle"].result()
            self.fit_request_budget(bundle_payload)
            wanted = _construction_uids(bundle_payload)
            missing = wanted - set(early)
            late = self._capture_prior_resources(bundle_payload, offline, deed_uids=missing, failed=read_failures) if (missing or previous_bundle is None) else {}
            return {uid: early.get(uid, late.get(uid)) for uid in wanted if uid in early or uid in late}

        def _capture_unassigned_after_catalogue(_: list[dict[str, str]]) -> dict[str, Any]:
            item_catalogue, _ = futures["item_catalogue"].result()
            catalogue_lookup = item_catalogue_lookup({"item_catalogue": item_catalogue})
            name_lookup = {detail_id: item["name"] for detail_id, item in catalogue_lookup.items() if item.get("name")}
            previous_rows = previous_bundle.get("data", {}).get("deeds", []) if previous_bundle is not None else []
            # S6 / L3-04: spare (unassigned) items are read live on every quick capture -- never from a five-minute-old cache.
            if previous_rows:
                families = self._capture_unassigned_items(
                    previous_rows, player, offline, name_lookup, include_asset_ids, catalogue_lookup, ttl=0,
                )
                bundle_payload = futures["bundle"].result()
                fresh_uids = {str(r.get("deed_uid")) for r in (bundle_payload.get("data", {}).get("deeds", []) if isinstance(bundle_payload, dict) else []) if isinstance(r, dict)}
                anchor_still_owned = str(previous_rows[0].get("deed_uid")) in fresh_uids
                if anchor_still_owned and all(isinstance(f, dict) and f.get("capturable") is not False or f.get("reason") for f in families.values()):
                    return families
            bundle_payload = futures["bundle"].result()
            self.fit_request_budget(bundle_payload)
            deed_rows = bundle_payload.get("data", {}).get("deeds", []) if isinstance(bundle_payload, dict) else []
            return self._capture_unassigned_items(
                deed_rows, player, offline, name_lookup, include_asset_ids, catalogue_lookup, ttl=0,
            )

        bundle_error: ResponseTooLarge | None = None
        with self._dispatch_ceiling(quick_workers > 1 and not offline), ThreadPoolExecutor(max_workers=quick_workers) as pool:
            futures: dict[str, Any] = {}
            futures["bundle"] = pool.submit(
                self._cached,
                f"land_deeds_{safe_name(player)}",
                lambda: self.land_client.bundle(player),
                offline,
            )
            futures["prior_resources"] = pool.submit(
                _run_read,
                "prior_resources",
                _capture_prior_resources_after_bundle,
            )
            futures["collection"] = pool.submit(
                _run_read,
                "collection",
                lambda read_failures: self._cached(
                    f"collection_{safe_name(player)}",
                    lambda: self._collection_read(player),
                    offline,
                ),
            )
            futures["card_names"] = pool.submit(
                _run_read,
                "card_names",
                lambda read_failures: self._card_names(offline, failed=read_failures),
            )
            futures["item_catalogue"] = pool.submit(
                _run_read,
                "item_catalogue",
                lambda read_failures: self._fetch_item_catalogue(offline, failed=read_failures),
            )
            futures["unassigned_items"] = pool.submit(
                _run_read,
                "unassigned_items",
                _capture_unassigned_after_catalogue,
            )
            try:
                bundle = futures["bundle"].result()
                self.fit_request_budget(getattr(bundle, "data", bundle))
                # attribute hook like on_bulk_ready -- the deed count is known here, one request in.
                hook = getattr(self, "on_deed_count", None)
                if hook is not None:
                    try:
                        payload = getattr(bundle, "data", bundle)
                        rows = payload.get("data", {}).get("deeds", []) if isinstance(payload, dict) else []
                        hook(len(rows) if isinstance(rows, list) else 0)
                    except Exception:  # a progress courtesy, never the capture's failure
                        pass
            except ResponseTooLarge as exc:
                bundle_error = exc
            if bundle_error is None:
                bundle_partial_reason = None
                is_incomplete, incompleteness = _bulk_bundle_completeness(bundle)
                if is_incomplete:
                    bundle_partial_reason = incompleteness
                    if not offline:
                        try:
                            # a completeness retry must be a new live request
                            # never a cache read or a saved snapshot.
                            refreshed = self.fresh_read(lambda: self.land_client.bundle(player))
                            bundle = refreshed.data
                            still_incomplete, retry_reason = _bulk_bundle_completeness(bundle)
                            if still_incomplete:
                                bundle_partial_reason = retry_reason or incompleteness
                            else:
                                bundle_partial_reason = None
                        except Exception as exc:
                            bundle_partial_reason = f"{incompleteness}; live retry failed ({exc})"
                partial_bundle_warning = (
                    f"PARTIAL DATA: Some plot data is missing from the live land bundle ({bundle_partial_reason}). "
                    "Refresh the board."
                    if bundle_partial_reason else None
                )
                prior_resources_by_deed, prior_failures = futures["prior_resources"].result()
                card_names, card_name_failures = futures["card_names"].result()
                item_catalogue, catalogue_failures = futures["item_catalogue"].result()
                unassigned_items, unassigned_failures = futures["unassigned_items"].result()
                if on_bulk_ready is not None and not futures["collection"].done():
                    # (2026-09-07), v0.9.27: everything but the collection sweep is in.
                    # The bundle carries every plot-level figure the board table shows; only
                    # per-worker identity waits on the sweep. Hand the caller a board built
                    # from live bulk data NOW (no cards, `coverage.collection` incomplete, so a
                    # merge carries the previous pool and any retrieved roster forward exactly
                    # as a quick refresh always has), then finish with the sweep below. On the
                    # maintainer's account the sweep was the last thing on the critical path; on a
                    # whale account it is ten seconds of it.
                    try:
                        interim = analyze_bundle(
                            player, bundle, {}, list(failed) + list(prior_failures) + list(card_name_failures) + list(catalogue_failures) + list(unassigned_failures),
                            None, as_of, include_asset_ids, include_raw,
                            prior_resources_by_deed=prior_resources_by_deed, card_names=card_names,
                        )
                        if partial_bundle_warning:
                            interim.setdefault("warnings", []).append(partial_bundle_warning)
                        interim["item_catalogue"] = item_catalogue
                        interim["unassigned_items"] = unassigned_items
                        interim["capture"] = {
                            "requests": self._requests, "cache_hits": self._cache_hits,
                            "min_inter_request_delay_seconds": self.min_delay, "request_budget": self.request_budget,
                            "offline": offline, "mode": "quick_bulk", "failures": list(failed),
                        }
                        on_bulk_ready(interim)
                    except Exception as exc:  # the interim board is a courtesy; the full one follows regardless
                        failed.append(_capture_failure("interim_board", exc))
                collection, collection_failures = futures["collection"].result()

        if bundle_error is not None:
            return _response_too_large_capture(player, "quick", bundle_error, self._requests, self._cache_hits)

        deed_rows = bundle.get("data", {}).get("deeds", []) if isinstance(bundle, dict) else []

        failed.extend(collection_failures)
        failed.extend(card_name_failures)
        failed.extend(catalogue_failures)
        failed.extend(prior_failures)
        failed.extend(unassigned_failures)
        report = analyze_bundle(player, bundle, {}, failed, collection, as_of, include_asset_ids, include_raw, prior_resources_by_deed=prior_resources_by_deed, card_names=card_names)
        if bundle_partial_reason:
            report.setdefault("warnings", []).append(partial_bundle_warning)
        # S6 / L3-04: the account-scoped spare-item inventory is read live on every quick capture (it used to be cached for
        # five minutes, so an item staked, sold or bought a moment ago still showed as available).
        report["item_catalogue"] = item_catalogue
        report["unassigned_items"] = unassigned_items
        report["capture"] = {
            "requests": self._requests,
            "cache_hits": self._cache_hits,
            "min_inter_request_delay_seconds": self.min_delay,
            "request_budget": self.request_budget,
            "offline": offline,
            "mode": "quick",
            "failures": failed,
        }
        self._asset_repair_context = AssetRepairContext(
            player=player,
            bundle=bundle,
            bundle_observed_at=report.get("generated_at"),
            collection=collection,
            collection_observed_at=report.get("coverage", {}).get("collection", {}).get("fetched_at"),
            prior_resources_by_deed=prior_resources_by_deed,
            card_names=card_names,
            item_catalogue=item_catalogue,
            unassigned_items=unassigned_items,
            include_asset_ids=include_asset_ids,
        )
        return report

    def repair_assets(
        self,
        deed_uids: list[str] | tuple[str, ...] | set[str],
        player: str = DEFAULT_PLAYER,
    ) -> dict[str, Any]:
        """Refresh selected deed assets using the bulk evidence from this capture.

        The context is populated by ``capture`` and ``capture_quick`` and is valid only
        for this capture instance. The selected asset reads remain serial so the existing
        request pacing and budget govern the whole run.
        """
        _require_player(player)
        context = self._asset_repair_context
        if context is None or context.player != str(player):
            raise ValueError("asset repair requires a matching capture context")
        requested: list[str] = []
        seen: set[str] = set()
        for raw_uid in deed_uids:
            uid = str(raw_uid)
            if uid and uid not in seen:
                requested.append(uid)
                seen.add(uid)
        bundle = context.bundle
        if not isinstance(bundle, dict):
            raise SchemaError("asset repair context has no valid bundle")
        # the repair shares this capture's request count with the quick read before it; a large
        # account (player0001, 474 plots) ran the shared 200 out mid-repair and every remaining plot failed with
        # "budget exhausted". Make room for exactly the reads this repair is about to make.
        with self._lock:
            needed = self._requests + len(requested) + 10
            if needed > self.request_budget:
                self.request_budget = needed
        if not isinstance(context.collection, dict) or not isinstance(context.collection.get("cards"), list):
            raise SchemaError("asset repair requires valid held collection evidence")
        assets: dict[str, dict[str, Any]] = {}
        failures: list[dict[str, Any]] = []
        asset_times: dict[str, str] = {}
        for deed_uid in requested:
            try:
                response = self.fresh_read(lambda deed_uid=deed_uid: self.land_client.assets(deed_uid))
                envelope = response.data
                if not isinstance(envelope, dict) or envelope.get("status") != "success":
                    raise UpstreamError("Land assets API returned an invalid status")
                payload = envelope.get("data")
                if not isinstance(payload, dict) or not isinstance(payload.get("cards"), list) or not isinstance(payload.get("items"), list):
                    raise SchemaError("Land assets response must contain cards and items arrays")
                assets[deed_uid] = payload
                asset_times[deed_uid] = response.retrieved_at
            except Exception as exc:
                failures.append(_capture_failure(deed_uid, exc))
        target_uids = set(requested)
        report = analyze_bundle(
            str(player), self._bundle_for_deeds(bundle, target_uids), assets, failures,
            self._collection_for_deeds(context.collection, target_uids),
            include_asset_ids=context.include_asset_ids,
            observed_at=context.bundle_observed_at,
            assets_observed_at=asset_times,
            collection_observed_at=context.collection_observed_at,
            prior_resources_by_deed=context.prior_resources_by_deed,
            card_names=context.card_names,
        )
        report["item_catalogue"] = context.item_catalogue
        report["unassigned_items"] = context.unassigned_items
        report["capture"] = {
            "requests": self._requests,
            "cache_hits": self._cache_hits,
            "min_inter_request_delay_seconds": self.min_delay,
            "request_budget": self.request_budget,
            "offline": False,
            "mode": "asset_repair",
            "deed_uids": requested,
            "failures": failures,
        }
        return report

    def expand_deed(
        self,
        deed_uid: str,
        player: str = DEFAULT_PLAYER,
        offline: bool = False,
        bundle_ttl: int = 0,
        include_collection: bool = False,
        narrow: bool = False,
        plot_id: int | None = None,
    ) -> dict[str, Any]:
        """Read one deed's assets and analyze its current deed/worksite/staking state.

        Ordinary Ceres opens pass `narrow=True` and a numeric board `plot_id`: one live
        deed, active-project, staking-details, and assets GET, with no deed cache read.
        Legacy and explicit collection callers retain the account bundle path and its
        optional `bundle_ttl`. The narrow report contains one plot; the bundle report
        contains every plot with only the requested deed's assets retrieved."""
        _require_player(player)
        if narrow and plot_id is not None and not offline:
            deed = self.fresh_read(lambda: self.land_client.deed(plot_id)).data["data"]
            if deed.get("deed_uid") != deed_uid or deed.get("player") != player:
                raise SchemaError("Land single-deed response does not match the requested player and deed")
            worksite = self.fresh_read(lambda: self.land_client.active_project(deed_uid)).data.get("data")
            staking = self.fresh_read(lambda: self.land_client.deed_staking(deed_uid)).data["data"]
            bundle = {"status": "success", "data": {"deeds": [deed], "worksite_details": [worksite] if worksite else [], "staking_details": [staking]}}
        else:
            bundle = self._cached(f"land_deeds_{safe_name(player)}", lambda: self.land_client.bundle(player), offline, ttl=bundle_ttl)
        if narrow and (plot_id is None or offline):
            # `narrow=True` (Ceres' plain row expand) analyses this one deed, not the
            # account, and the caller lays the one-plot result over its stored snapshot as a
            # patch. The default keeps this method's contract: a full report in which only
            # `deed_uid` has `asset_fetch.status == "retrieved"`.
            bundle = self._bundle_for_deeds(bundle, {str(deed_uid)})
        failed: list[dict[str, Any]] = []
        assets: dict[str, dict[str, Any]] = {}
        key = f"land_assets_{stable_asset_ref(deed_uid)}"
        try:
            assets[deed_uid] = self._cached(key, lambda: self.land_client.assets(deed_uid), offline).get("data", {})
        except Exception as exc:
            failed.append(_capture_failure(deed_uid, exc))
        prior_resources_by_deed = self._capture_prior_resources(bundle, offline, {deed_uid})
        collection = None
        if include_collection:
            try:
                collection = self._cached(
                    f"collection_{safe_name(player)}",
                    lambda: self._collection_read(player),
                    offline,
                )
            except Exception as exc:
                failed.append(_capture_failure("collection", exc))
        card_names = self._card_names(offline) if include_collection else None
        report = analyze_bundle(player, bundle, assets, failed, collection, as_of=None, include_asset_ids=True, prior_resources_by_deed=prior_resources_by_deed, card_names=card_names)
        report["capture"] = {
            "requests": self._requests,
            "cache_hits": self._cache_hits,
            "min_inter_request_delay_seconds": self.min_delay,
            "request_budget": self.request_budget,
            "offline": offline,
            "mode": "expand",
            "deed_uid": deed_uid,
            "failures": failed,
        }
        return report

    def expand_deeds(
        self,
        deed_uids: list[str] | tuple[str, ...] | set[str],
        player: str = DEFAULT_PLAYER,
        *,
        evidence: Any,
        include_collection: bool = False,
        include_asset_ids: bool = True,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Freshly assemble one report for several deeds using one attempt context.

        ``evidence`` is intentionally duck-typed so the signing layer can own the
        account-bound observation holder without making this capture module depend on
        Ceres. It must expose bundle(), assets(uid), and collection() methods returning
        response records; acquisition failures are raised and memoized by the holder.
        """
        _require_player(player)
        player = str(player)
        if getattr(evidence, "account", None) != player or getattr(evidence, "capture", None) is not self:
            raise ValueError("evidence account does not match requested player")
        requested = sorted({str(uid) for uid in deed_uids if uid})
        bundle_response = evidence.bundle()
        bundle = bundle_response.data
        bundle_at = bundle_response.retrieved_at
        asset_responses = {}
        asset_failures: list[dict[str, Any]] = []
        asset_times = {}
        for uid in requested:
            try:
                response = evidence.assets(uid)
                envelope = response.data
                if not isinstance(envelope, dict) or envelope.get("status") != "success":
                    raise UpstreamError("Land assets API returned an invalid status")
                payload = envelope.get("data")
                if not isinstance(payload, dict) or not isinstance(payload.get("cards"), list) or not isinstance(payload.get("items"), list):
                    raise SchemaError("Land assets response must contain cards and items arrays")
                asset_responses[uid] = payload
                asset_times[uid] = response.retrieved_at
            except Exception as exc:
                asset_failures.append(_capture_failure(uid, exc))
        collection = None
        collection_at = None
        if include_collection:
            try:
                response = evidence.collection()
                if not isinstance(response.data, dict) or not isinstance(response.data.get("cards"), list):
                    raise SchemaError("collection response must contain cards array")
                collection = response.data
                collection_at = response.retrieved_at
            except Exception as exc:
                asset_failures.append(_capture_failure("collection", exc))
        if not isinstance(bundle, dict):
            raise SchemaError("Land bundle response must be an object")
        bundle_data = bundle
        failures = list(asset_failures)
        bundle_uids = {
            str(row.get("deed_uid")) for row in (bundle_data.get("data", {}).get("deeds", []) if isinstance(bundle_data.get("data"), dict) else [])
            if isinstance(row, dict) and row.get("deed_uid")
        }
        failures.extend({"deed_uid": uid, "reason": "deed was not present in fresh bundle"} for uid in requested if uid not in bundle_uids)
        asset_responses = {uid: payload for uid, payload in asset_responses.items() if uid in bundle_uids}
        asset_times = {uid: stamp for uid, stamp in asset_times.items() if uid in bundle_uids}
        before_history = self._requests
        prior_resources = self._capture_prior_resources(bundle_data, False, set(requested), failures)
        history_requests = self._requests - before_history
        before_card_metadata = self._requests
        card_names = self._card_names(False, failures) if include_collection else None
        card_metadata_requests = self._requests - before_card_metadata
        report = analyze_bundle(
            player, bundle_data, asset_responses, failures, collection,
            include_asset_ids=include_asset_ids, include_raw=include_raw,
            observed_at=bundle_at, assets_observed_at=asset_times,
            collection_observed_at=collection_at,
            prior_resources_by_deed=prior_resources, card_names=card_names,
        )
        report["capture"] = {
            "requests": self._requests,
            "cache_hits": self._cache_hits,
            "min_inter_request_delay_seconds": self.min_delay,
            "request_budget": self.request_budget,
            "offline": False,
            "mode": "expand_batch",
            "deed_uids": requested,
            "failures": failures,
            "history_requests": history_requests,
            "card_metadata_requests": card_metadata_requests,
            "core_requests": self._requests - history_requests - card_metadata_requests,
        }
        provenance = getattr(evidence, "provenance", None)
        if callable(provenance):
            report["provenance"] = provenance()
        return report

    def _fetch_available_family(self, stake_type_uid: str, deed_uid: str, player: str, offline: bool, ttl: int = 0) -> list[Any]:
        """Paginate the account-scoped /available pool for one stake type. `available_item_ids`
        (called on every page) raises UpstreamError on the documented P0001-wrapped-in-200
        failure shape -- this propagates rather than being swallowed as "zero available".
        `ttl` ( whale-account performance batch, team-lead: "check it is not being
        re-run per player switch when a cached result would do") -- this is account-scoped
        inventory (spare totems/titles/power cores/Runi), not per-plot data, so it does not
        change from one quick refresh to the next within a short window the way board state
        can. Default `ttl=0` (always fresh) is unchanged for `capture()` (full); `capture_quick()`
        passes a positive ttl so repeated refreshes/player-switches within that window reuse
        the cached pages instead of re-running all 3-4 requests every time."""
        class AvailableItems(list):
            def __init__(self, values: list[Any], pages_fetched: int, page_cap_hit: bool):
                super().__init__(values)
                self.pages_fetched = pages_fetched
                self.page_cap_hit = page_cap_hit

        ids: list[Any] = []
        offset = 0
        pages_fetched = 0
        for _ in range(MAX_UNASSIGNED_PAGES):
            key = f"land_available_{safe_name(stake_type_uid)}_{safe_name(player)}_{offset}"
            payload = self._cached(
                key,
                lambda stake_type_uid=stake_type_uid, offset=offset: self.land_client.available_raw(
                    stake_type_uid, deed_uid, player, offset, AVAILABLE_PAGE_SIZE
                ),
                offline,
                ttl=ttl,
            )
            page = available_item_ids(payload)
            pages_fetched += 1
            ids.extend(page)
            if len(page) < AVAILABLE_PAGE_SIZE:
                return AvailableItems(ids, pages_fetched, False)
            offset += AVAILABLE_PAGE_SIZE
        return AvailableItems(ids, pages_fetched, True)

    def _fetch_item_catalogue(self, offline: bool, failed: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
        fetch = getattr(self.collection_client, "item_details", None)
        if fetch is None:
            return []
        try:
            data = self._cached("item_details", fetch, offline, ttl=86400)
        except (CacheError, SchemaError, TransportError, UpstreamError) as exc:
            if failed is not None:
                failed.append({"deed_uid": "item_catalogue", "reason": str(exc)})
            return []
        catalogue = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            item_data = item.get("data")
            if isinstance(item_data, dict) and normalized.get("boost") is None:
                normalized["boost"] = item_data.get("boost")
            catalogue.append(normalized)
        return catalogue

    def _capture_unassigned_items(
        self, deed_rows: list[dict[str, Any]], player: str, offline: bool,
        name_lookup: dict[int, str], include_asset_ids: bool,
        catalogue_lookup: dict[int, dict[str, Any]] | None = None, ttl: int = 0,
    ) -> dict[str, Any]:
        """Unassigned/spare totem, title, power-core, and Runi inventory -- Source A of the
        Ceres unassigned-bonus-assets section (Source B, idle Land/Verico cards and idle Runi,
        is read from report["cards"] instead; it needs no new fetch). See UNASSIGNED_ITEM_FAMILY
        for the account-scope and pagination findings this method encodes. `ttl` forwards to
        `_fetch_available_family` -- see that method's own comment."""
        families: dict[str, Any] = {}
        for family, reason in UNSUPPORTED_ITEM_FAMILY.values():
            families[family] = {"capturable": False, "count": None, "items": [], "reason": reason}
        if not deed_rows:
            for family in UNASSIGNED_ITEM_FAMILY.values():
                families[family] = {"capturable": False, "count": None, "items": [], "reason": "no owned deed available to anchor the required deedUid query parameter"}
            return families
        deed_uid = str(deed_rows[0].get("deed_uid"))

        def _fetch(stake_type_uid: str) -> list[Any] | Exception:
            try:
                return self._fetch_available_family(stake_type_uid, deed_uid, player, offline, ttl=ttl)
            except (UpstreamError, TransportError, CacheError) as exc:
                return exc

        # (2026-09-07): the families are independent account-scoped reads and were
        # fetched one after another, each dispatch held `min_delay` apart. They overlap now
        # `_request` still paces dispatches globally. Family order is preserved.
        stake_type_uids = list(UNASSIGNED_ITEM_FAMILY)
        workers = min(len(stake_type_uids), max(1, self.concurrency), QUICK_CAPTURE_CONCURRENCY)
        if workers <= 1 or offline:
            fetched = [_fetch(uid) for uid in stake_type_uids]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                fetched = list(pool.map(_fetch, stake_type_uids))
        for stake_type_uid, ids in zip(stake_type_uids, fetched):
            family = UNASSIGNED_ITEM_FAMILY[stake_type_uid]
            if isinstance(ids, Exception):
                families[family] = {"capturable": False, "count": None, "items": [], "reason": str(ids)}
                continue
            counts: dict[int, int] = {}
            for entry in ids:
                detail_id = item_detail_id_from_uid(entry.get("uid") if isinstance(entry, dict) else entry)
                if detail_id is None:
                    continue
                counts[detail_id] = counts.get(detail_id, 0) + 1
            items = []
            for detail_id, count in sorted(counts.items()):
                catalogue_item = (catalogue_lookup or {}).get(detail_id, {})
                fallback_name = f"{family.replace('_', ' ').title()} item {detail_id}"
                item = {"item_detail_id": detail_id, "name": name_lookup.get(detail_id, fallback_name), "count": count}
                for key in ("boost", "description", "image_filename"):
                    if catalogue_item.get(key) not in (None, ""):
                        item[key] = catalogue_item[key]
                items.append(item)
            page_cap_hit = bool(getattr(ids, "page_cap_hit", False))
            entry_out: dict[str, Any] = {
                "capturable": True,
                "count": len(ids),
                "items": items,
                "pages_fetched": getattr(ids, "pages_fetched", None),
                "page_cap_hit": page_cap_hit,
            }
            if page_cap_hit:
                entry_out["warnings"] = [f"pagination stopped at the {MAX_UNASSIGNED_PAGES}-page cap; inventory may be truncated"]
            if include_asset_ids:
                entry_out["uids"] = [e.get("uid") if isinstance(e, dict) else e for e in ids]
            families[family] = entry_out
        return families


def snapshot_path(path: Path | None = None) -> Path:
    return path or Path.home() / ".praetoria" / "snapshots" / "land-latest.json"


def save_snapshot(report: dict[str, Any], path: Path | None = None) -> Path:
    target = snapshot_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    # One-shot encoding avoids Python-level streaming overhead, measured 2.20s faster on the
    # maintainer's 12.8MB snapshot, at the cost of holding the encoded payload in memory (+12.8MB
    # measured there; +37MB estimated, unmeasured, for the 6,303-deed whale capture).
    try:
        with os.fdopen(fd, "wb") as f:
            payload = fastjson.dumps(report) + b"\n"  # 2.4 s -> ~0.3 s on a 38 MB snapshot
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def load_snapshot(path: Path, *, include_cards: bool = True) -> dict[str, Any]:
    try:
        report = fastjson.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise CacheError(f"invalid Land snapshot: {path}") from exc
    if not isinstance(report, dict) or report.get("schema_version") != LAND_SCHEMA_VERSION:
        raise SchemaError("snapshot is not a Praetoria Land analysis v1 document")
    if include_cards and (isinstance(report.get("cards"), list) or isinstance(report.get("card_pool"), dict)):
        inline_cards = report.get("cards")
        expected = (report.get("card_pool") or {}).get("fetched_at")
        sidecar = path.with_name(path.name.replace("-latest.json", "-cards-latest.json"))
        try:
            if sidecar == path:
                # A snapshot not named `*-latest.json` has no sidecar name of its own
                # never read the snapshot back as its own card pool.
                raise FileNotFoundError(sidecar)
            pool = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            if isinstance(inline_cards, list) and isinstance(exc, OSError):
                report.setdefault("snapshot", {})["status"] = "snapshot_replay"
                return report
            raise CacheError(f"card collection sidecar unavailable: {sidecar}") from exc
        if not isinstance(pool, dict) or not isinstance(pool.get("cards"), list) or _time(pool.get("fetched_at")) is None:
            raise CacheError(f"card collection sidecar is stale or invalid: {sidecar}")
        if isinstance(inline_cards, list):
            coverage = report.get("coverage")
            collection = coverage.get("collection") if isinstance(coverage, dict) else None
            inline_fetched_at = _time(collection.get("fetched_at") if isinstance(collection, dict) else None)
            sidecar_fetched_at = _time(pool.get("fetched_at"))
            if inline_fetched_at is None or sidecar_fetched_at is None:
                raise CacheError("both card collections exist without comparable fetched_at provenance")
            if sidecar_fetched_at > inline_fetched_at:
                report["cards"] = pool["cards"]
        elif pool.get("fetched_at") != expected or not expected:
            raise CacheError(f"card collection sidecar is stale or invalid: {sidecar}")
        else:
            report["cards"] = pool["cards"]
    report.setdefault("snapshot", {})["status"] = "snapshot_replay"
    return report


PLOT_CSV_FIELDS = ["schema_version", "deed_uid", "plot", "region", "resource", "worksite_state", "is_powered", "worker_count", "max_workers_allowed", "total_base_pp", "total_base_pp_after_cap", "total_base_pp_cap", "base_cap_utilization_after_cap_base_pp_over_base_pp_cap", "total_harvest_pp", "finding_codes", "warnings"]
WORKER_CSV_FIELDS = ["schema_version", "asset_ref", "uid", "deed_uid", "slot", "asset_kind", "stake_type_uid", "is_powered", "land_base_pp", "base_pp_after_cap", "total_construction_pp", "total_harvest_pp", "work_per_hour", "warnings"]
DIAGNOSTIC_CSV_FIELDS = ["schema_version", "code", "count", "affected_plots", "affected_plot_count", "warnings"]
RECOMMENDATION_CSV_FIELDS = ["schema_version", "rank", "action", "objective", "target_plot", "source_plot", "asset_ref", "slot", "delta", "delta_low", "delta_high", "delta_reason", "opportunity_cost", "opportunity_cost_reason", "confidence", "estimate_class", "conflicts", "assumptions", "candidate_pool_pruned", "candidate_pruning_criterion", "candidate_evaluations", "candidate_evaluation_limit", "plots_truncated", "beam_width", "beam_pruned_states", "optimality"]
DISPLAY_VIEWS = {"status", "plots", "workers", "diagnostics", "analyze", "recommend"}


def _decimal_text(value: Any, places: int = 3) -> str:
    if value is None or value == "":
        return ""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    quantum = Decimal(1).scaleb(-places)
    return format(number.quantize(quantum, rounding=ROUND_HALF_UP), f".{places}f")


def _is_measure_key(key: str) -> bool:
    lowered = key.lower()
    return "pp" in lowered or "boost" in lowered or lowered in {"work_per_hour", "total_work_per_hour"}


def _display_measure(value: Any) -> str:
    return _decimal_text(value) if value is not None else ""


def recommendation_measure_text(value: Any, reason: str | None = None) -> str:
    if value is not None:
        return _decimal_text(value)
    if reason and "cap" in reason.lower():
        return "unknown (above cap)"
    return "unknown (not observed)"


def recommendation_opportunity_text(opportunity: dict[str, Any]) -> str:
    reason = str(opportunity.get("reason") or "")
    if reason.startswith("none ("):
        return reason
    return recommendation_measure_text(opportunity.get("value"), reason)


def _number_for_sort(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _sorted_plots(report: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(report.get("plots", []), key=lambda row: (-_number_for_sort(row.get("staking_facts", {}).get("total_harvest_pp")), str(row.get("facts", {}).get("plot", "")), str(row.get("deed_uid", ""))))


def _assignment_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("asset_ref")): row for row in report.get("assignments", [])}


def _sorted_workers(report: dict[str, Any]) -> list[dict[str, Any]]:
    assignments = _assignment_map(report)
    return sorted(report.get("workers", []), key=lambda row: (str(assignments.get(str(row.get("asset_ref")), {}).get("deed_uid", "")), _number_for_sort(assignments.get(str(row.get("asset_ref")), {}).get("slot")), str(row.get("asset_ref", ""))))


def _worksite_state(row: dict[str, Any]) -> str:
    worksite = row.get("worksite_facts")
    if worksite is None:
        return "none"
    if worksite.get("is_construction") is True:
        return "construction"
    if worksite.get("is_empty") is True:
        return "empty"
    if worksite.get("is_harvesting") is True:
        return "harvesting"
    if worksite.get("is_active") is True:
        return "active"
    return "observed"


def _plot_table_rows(report: dict[str, Any], limit: int | None = None) -> tuple[list[dict[str, Any]], int]:
    rows = _sorted_plots(report)
    total = len(rows)
    if limit is not None:
        rows = rows[:limit]
    result = []
    for row in rows:
        facts = row.get("facts", {})
        stake = row.get("staking_facts", {})
        metrics = row.get("metrics", {})
        deed_uid = str(row.get("deed_uid", ""))
        runi_count = sum(1 for assignment in report.get("assignments", []) if str(assignment.get("deed_uid")) == deed_uid and assignment.get("asset_kind") == "runi_worker_card")
        worker_display = f"{stake.get('worker_count', '')}/{stake.get('max_workers_allowed', '')}"
        if runi_count:
            # "5/5+R" (not the old "5+R/5"): Runi is counted inside worker_count but does not
            # consume a monster-card slot -- max_workers_allowed IS the five-slot capacity, and
            # Runi genuinely occupies a real sixth slot beyond it (worker_count=6 over
            # max_workers_allowed=5 on player0005's live Runi plots). "5/5+R" reads as five monsters
            # in five slots plus a Runi; the "+R" therefore belongs on the capacity side, not
            # inflating the occupancy count. Aligned to Ceres's board display convention
            # (see ceres/board.py's `_plot_row`).
            ordinary = _number_for_sort(stake.get("worker_count")) - runi_count
            ordinary_display = int(ordinary) if ordinary.is_integer() else ordinary
            worker_display = f"{ordinary_display}/{stake.get('max_workers_allowed', '')}+R"
        result.append({
            "plot": facts.get("plot", ""),
            "region": facts.get("region", ""),
            "resource": facts.get("resource") or "",
            "worksite": _worksite_state(row),
            "powered": str(stake.get("is_powered", "unknown")),
            "worker_count": stake.get("worker_count", ""),
            "max_workers_allowed": stake.get("max_workers_allowed", ""),
            "workers": worker_display,
            "base_pp": _display_measure(stake.get("total_base_pp")),
            "after_cap_pp": _display_measure(stake.get("total_base_pp_after_cap")),
            "cap_pp": _display_measure(stake.get("total_base_pp_cap")),
            "cap_util": _display_measure(metrics.get("base_cap_utilization_after_cap_base_pp_over_base_pp_cap", {}).get("value")),
            "harvest_pp": _display_measure(stake.get("total_harvest_pp")),
            "findings": ";".join(item.get("type", "") for item in row.get("diagnostics", [])),
        })
    return result, total


def _worker_table_rows(report: dict[str, Any], limit: int | None = None) -> tuple[list[dict[str, Any]], int]:
    assignments = _assignment_map(report)
    rows = _sorted_workers(report)
    total = len(rows)
    if limit is not None:
        rows = rows[:limit]
    result = []
    for row in rows:
        assignment = assignments.get(str(row.get("asset_ref")), {})
        result.append({
            "card_ref": row.get("asset_ref", ""),
            "uid": row.get("uid", "") if "uid" in row else "",
            "deed": assignment.get("deed_uid", row.get("stake_ref_uid", "")),
            "base_pp": _display_measure(row.get("land_base_pp")),
            "after_cap_pp": _display_measure(row.get("base_pp_after_cap")),
            "construction_pp": _display_measure(row.get("total_construction_pp")),
            "harvest_pp": _display_measure(row.get("total_harvest_pp")),
            "work_per_hour": _display_measure(row.get("work_per_hour")),
            "powered": str(row.get("is_powered", "unknown")),
            "slot": assignment.get("slot", row.get("slot", "")),
            "asset_kind": row.get("asset_kind", ""),
            "stake_type": row.get("stake_type_uid", ""),
        })
    return result, total


def _diagnostic_groups(report: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for finding in report.get("findings", []):
        code = str(finding.get("type", "unknown"))
        group = grouped.setdefault(code, {"code": code, "count": 0, "plots": []})
        group["count"] += 1
        deed_uid = str(finding.get("deed_uid", ""))
        plot = next((row.get("facts", {}).get("plot", deed_uid) for row in report.get("plots", []) if row.get("deed_uid") == deed_uid), deed_uid)
        if plot not in group["plots"]:
            group["plots"].append(plot)
    return [grouped[key] for key in sorted(grouped)]


def _affected_plots_text(group: dict[str, Any], maximum: int = 8, show_all: bool = False) -> str:
    plots = [str(plot) for plot in group.get("plots", [])]
    if show_all or len(plots) <= maximum:
        return ";".join(plots)
    omitted = len(plots) - maximum
    return ";".join(plots[:maximum]) + f";… +{omitted} more (true count {len(plots)}; use --all or JSON)"


def _prefix_lines(report: dict[str, Any]) -> list[str]:
    coverage = report.get("coverage", {}).get("assets", {})
    lines = list(report.get("warnings", []))
    lines.append(f"Coverage: {coverage.get('retrieved', 0)}/{coverage.get('expected', 0)} deeds retrieved")
    lines.append(f"Player: {report.get('query', {}).get('player', '')}")
    lines.append("Workers/slots: Runi is included in worker_count; plots with Runi show ordinary workers/maximum+R (for example 5/5+R).")
    return lines


def _summary_lines(report: dict[str, Any]) -> list[str]:
    summary = report.get("summary", {})
    return [
        f"Deeds: {summary.get('deeds', 0)} | Total harvest PP: {_display_measure(summary.get('total_harvest_pp'))}",
        f"Idle: {summary.get('plots_idle', 0)} | Incomplete: {summary.get('plots_incomplete', 0)} | Unpowered: {summary.get('plots_unpowered', 0)}",
    ]


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    values = [[str(value) for value in row] for row in rows]
    widths = [max(len(headers[index]), *(len(row[index]) for row in values)) if values else len(headers[index]) for index in range(len(headers))]
    line = " | ".join(headers[index].ljust(widths[index]) for index in range(len(headers)))
    divider = "-+-".join("-" * width for width in widths)
    body = [" | ".join(row[index].ljust(widths[index]) for index in range(len(headers))) for row in values]
    return [line, divider, *body]


def _truncation_footer(label: str, shown: int, total: int) -> list[str]:
    return [f"showing {shown} of {total} {label}"] if shown < total else []


def _format_json_tree(value: Any, key: str = "", inside_raw: bool = False) -> Any:
    if inside_raw:
        return value
    if isinstance(value, dict):
        return {name: _format_json_tree(item, name, name == "raw") for name, item in value.items()}
    if isinstance(value, list):
        return [_format_json_tree(item, key, False) for item in value]
    if isinstance(value, float):
        return _decimal_text(value)
    if key in {"generated_at", "observed_at", "fetched_at"} and isinstance(value, str):
        return re.sub(r"\.\d{6,}(?=Z$)", "", value)
    if _is_measure_key(key) and isinstance(value, (int, str)) and not isinstance(value, bool):
        try:
            return _decimal_text(value)
        except (InvalidOperation, ValueError):
            return value
    return value


def _json_view(report: dict[str, Any], view: str, limit: int | None) -> dict[str, Any]:
    result = copy.deepcopy(report)
    result["view"] = view
    if view in {"plots", "analyze"}:
        rows, total = _plot_table_rows(report, limit)
        source = _sorted_plots(report)
        result["plots"] = [source[index] for index in range(len(rows))]
        result["view_rows"] = {"shown": len(rows), "total": total, "entity": "plots"}
    elif view == "workers":
        rows, total = _worker_table_rows(report, limit)
        result["workers"] = [_sorted_workers(report)[index] for index in range(len(rows))]
        result["view_rows"] = {"shown": len(rows), "total": total, "entity": "workers"}
    elif view == "diagnostics":
        groups = _diagnostic_groups(report)
        result["diagnostic_groups"] = groups[:limit] if limit is not None else groups
        result["view_rows"] = {"shown": len(result["diagnostic_groups"]), "total": len(groups), "entity": "diagnostic codes"}
    elif view == "recommend":
        recommendations = report.get("recommendations", [])
        result["recommendations"] = recommendations[:limit] if limit is not None else recommendations
        result["view_rows"] = {"shown": len(result["recommendations"]), "total": len(recommendations), "entity": "recommendations"}
    return _format_json_tree(result)


def _csv_output(report: dict[str, Any], view: str, csv_view: str, limit: int | None) -> str:
    selected = "diagnostics" if view == "diagnostics" else "workers" if view == "workers" or csv_view in {"workers", "cards"} else "plots"
    stream = io.StringIO(newline="")
    if selected == "diagnostics":
        fields = DIAGNOSTIC_CSV_FIELDS
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        warnings = ";".join(report.get("warnings", []))
        groups = _diagnostic_groups(report)
        if limit is not None:
            groups = groups[:limit]
        for row in groups:
            writer.writerow({"schema_version": report["schema_version"], "code": row["code"], "count": row["count"], "affected_plots": ";".join(row["plots"]), "affected_plot_count": len(row["plots"]), "warnings": warnings})
        return stream.getvalue()
    if view == "recommend":
        writer = csv.DictWriter(stream, fieldnames=RECOMMENDATION_CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        rows = report.get("recommendations", [])[:limit] if limit is not None else report.get("recommendations", [])
        search = report.get("recommendation_summary", {})
        pool = search.get("global_candidate_pool", {})
        ceilings = search.get("ceilings", {})
        for row in rows:
            delta = row.get("delta", {})
            opportunity = row.get("opportunity_cost", {})
            writer.writerow({"schema_version": report["schema_version"], "rank": row.get("rank"), "action": row.get("action"), "objective": row.get("objective"), "target_plot": row.get("proposal", {}).get("target_plot"), "source_plot": row.get("proposal", {}).get("source_plot"), "asset_ref": row.get("proposal", {}).get("asset_ref"), "slot": row.get("proposal", {}).get("slot"), "delta": _display_measure(delta.get("value")), "delta_low": _display_measure(delta.get("low")), "delta_high": _display_measure(delta.get("high")), "delta_reason": delta.get("reason"), "opportunity_cost": _display_measure(opportunity.get("value")), "opportunity_cost_reason": opportunity.get("reason"), "confidence": row.get("confidence_grade"), "estimate_class": row.get("estimate_class"), "conflicts": ";".join(row.get("conflicts", [])), "assumptions": ";".join(row.get("assumptions", [])), "candidate_pool_pruned": pool.get("pruned_cards"), "candidate_pruning_criterion": pool.get("criterion"), "candidate_evaluations": search.get("candidate_evaluations"), "candidate_evaluation_limit": search.get("candidate_evaluation_ceiling"), "plots_truncated": ceilings.get("plots_truncated"), "beam_width": ceilings.get("beam_width"), "beam_pruned_states": ceilings.get("beam_pruned_states"), "optimality": search.get("search", {}).get("optimality")})
        return stream.getvalue()
    if selected == "workers":
        fields = WORKER_CSV_FIELDS
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        rows, _ = _worker_table_rows(report, limit)
        for row in rows:
            writer.writerow({"schema_version": report["schema_version"], "asset_ref": row["card_ref"], "uid": row["uid"], "deed_uid": row["deed"], "slot": row["slot"], "asset_kind": row["asset_kind"], "stake_type_uid": row["stake_type"], "is_powered": row["powered"], "land_base_pp": row["base_pp"], "base_pp_after_cap": row["after_cap_pp"], "total_construction_pp": row["construction_pp"], "total_harvest_pp": row["harvest_pp"], "work_per_hour": row["work_per_hour"], "warnings": ";".join(report.get("warnings", []))})
        return stream.getvalue()
    fields = PLOT_CSV_FIELDS
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    rows, _ = _plot_table_rows(report, limit)
    for row in rows:
        writer.writerow({"schema_version": report["schema_version"], "deed_uid": next((item.get("deed_uid") for item in report.get("plots", []) if item.get("facts", {}).get("plot") == row["plot"]), ""), "plot": row["plot"], "region": row["region"], "resource": row["resource"], "worksite_state": row["worksite"], "is_powered": row["powered"], "worker_count": row["worker_count"], "max_workers_allowed": row["max_workers_allowed"], "total_base_pp": row["base_pp"], "total_base_pp_after_cap": row["after_cap_pp"], "total_base_pp_cap": row["cap_pp"], "base_cap_utilization_after_cap_base_pp_over_base_pp_cap": row["cap_util"], "total_harvest_pp": row["harvest_pp"], "finding_codes": row["findings"], "warnings": ";".join(report.get("warnings", []))})
    return stream.getvalue()


def render_land_analysis(report: dict[str, Any], fmt: str, csv_view: str = "plots", view: str = "status", limit: int | None = None, show_all: bool = False, include_prefix: bool = True) -> str:
    if view not in DISPLAY_VIEWS:
        raise ValueError(f"unknown Land display view: {view}")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if fmt == "json":
        return json.dumps(_json_view(report, view, limit), ensure_ascii=False, indent=2) + "\n"
    if fmt == "csv":
        return _csv_output(report, view, csv_view, limit)

    lines = _prefix_lines(report) if include_prefix else []
    if view == "status":
        lines.extend(_summary_lines(report))
        return "\n".join(lines) + "\n"
    if view == "workers":
        rows, total = _worker_table_rows(report, limit)
        lines.append("")
        lines.extend(_table(["Card ref", "UID", "Deed", "Base PP", "After-cap PP", "Construction PP", "Harvest PP", "Work/hour", "Powered"], [[row["card_ref"], row["uid"], row["deed"], row["base_pp"], row["after_cap_pp"], row["construction_pp"], row["harvest_pp"], row["work_per_hour"], row["powered"]] for row in rows]))
        lines.extend(_truncation_footer("workers", len(rows), total))
        return "\n".join(lines) + "\n"
    if view == "diagnostics":
        all_groups = _diagnostic_groups(report)
        groups = all_groups[:limit] if limit is not None else all_groups
        lines.extend(["", "Diagnostics by finding code:"])
        lines.extend(_table(["Code", "Count", "Affected plots (bounded; --all/JSON for full)"], [[row["code"], row["count"], _affected_plots_text(row, maximum=limit or 8, show_all=show_all)] for row in groups]))
        lines.extend(_truncation_footer("diagnostic codes", len(groups), len(all_groups)))
        return "\n".join(lines) + "\n"
    if view == "recommend":
        recommendations = report.get("recommendations", [])
        rows = recommendations if show_all else recommendations[:limit]
        summary = report.get("recommendation_summary", {})
        legend = lines.pop() if lines and lines[-1].startswith("Workers/slots:") else None
        if include_prefix:
            lines.extend(_summary_lines(report))
            if legend:
                lines.append(legend)
        lines.extend(["", f"Recommendations (goal: {summary.get('goal', 'raw-pp')}; selected {summary.get('selected_count', len(recommendations))}):"])
        lines.extend(_table(["Rank", "Action", "Target", "Source", "Asset", "Slot", "Delta PP", "Opportunity cost", "Confidence", "Estimate"], [[row.get("rank"), row.get("action"), row.get("proposal", {}).get("target_plot"), row.get("proposal", {}).get("source_plot") or "", row.get("proposal", {}).get("asset_ref"), row.get("proposal", {}).get("slot"), recommendation_measure_text(row.get("delta", {}).get("value"), row.get("delta", {}).get("reason")), recommendation_opportunity_text(row.get("opportunity_cost", {})), row.get("confidence_grade"), row.get("estimate_class")] for row in rows]))
        pool = summary.get("global_candidate_pool", {})
        ceilings = summary.get("ceilings", {})
        lines.append(f"Candidate pool: {pool.get('retained_cards', 0)} retained of {pool.get('eligible_numeric_cards', 0)} eligible numeric cards; pruned {pool.get('pruned_cards', 0)} by {pool.get('criterion', 'disclosed cheap dominance ordering')}.")
        lines.append(f"Candidates evaluated: {summary.get('candidate_evaluations', 0)} of ceiling {summary.get('candidate_evaluation_ceiling', 0)}; raw pool candidates before ceilings {summary.get('raw_candidate_count_before_ceilings', 0)}; plots truncated by per-plot ceiling {ceilings.get('plots_truncated', 0)}.")
        lines.append(f"Search: top-K pruning plus bounded conflict-aware beam search (beam width {ceilings.get('beam_width', 0)}, beam states pruned {ceilings.get('beam_pruned_states', 0)}); result is good, not proven optimal.")
        diagnosis = summary.get("pruning_diagnosis", {})
        lines.append(f"Suppression: {diagnosis.get('conflict_suppressed_by_selected_actions', 0)} candidate actions conflict with selected higher-ranked actions; dominance rules pruned {diagnosis.get('dominance_rules_pruned_candidates', 0)}.")
        lines.append("Assumption: target plot's observed aggregate terrain and seven-category boost rates are applied as a proxy for the candidate card.")
        if len(rows) < len(recommendations):
            lines.append(f"showing {len(rows)} of {len(recommendations)} recommendations; use --all for full output")
        return "\n".join(lines) + "\n"
    plot_rows, total = _plot_table_rows(report, limit)
    if view == "analyze":
        lines.extend(["", * _summary_lines(report), "", "Diagnostics by finding code:"])
        all_groups = _diagnostic_groups(report)
        groups = all_groups[:limit] if limit is not None else all_groups
        lines.extend(_table(["Code", "Count", "Affected plots (bounded; --all/JSON for full)"], [[row["code"], row["count"], _affected_plots_text(row, maximum=limit or 8, show_all=show_all)] for row in groups]))
        lines.extend(_truncation_footer("diagnostic codes", len(groups), len(all_groups)))
        lines.append("")
    else:
        lines.append("")
    lines.append("Plots by harvest PP descending:")
    lines.extend(_table(["Plot", "Region", "Resource", "Worksite", "Powered", "Workers/slots", "Base PP", "After-cap PP", "Cap", "Cap util", "Harvest PP", "Finding codes"], [[row["plot"], row["region"], row["resource"], row["worksite"], row["powered"], row["workers"], row["base_pp"], row["after_cap_pp"], row["cap_pp"], row["cap_util"], row["harvest_pp"], row["findings"]] for row in plot_rows]))
    lines.extend(_truncation_footer("plots", len(plot_rows), total))
    return "\n".join(lines) + "\n"
