"""Payload preview and post-broadcast verification routes for the inline editor."""

from __future__ import annotations

import json
import math
import logging
import time
import threading
from contextlib import nullcontext
from datetime import datetime, timezone
from .transaction_diagnostics import record_verification
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from fides.op import Op
from fides import verify as fides_verify
from fides.game_verdict import game_verdict
from fides.verify import get_transaction, verify_chain
from fides_cards.ops import COMBINE_OP_ID
from fides_cards.verify import CardVerificationSpec
from fides_land.verify import LandVerificationSpec
from praetoria.api import SplinterlandsClient
from praetoria.land_analysis import LandClient
from mnemosyne.accounting import ROOT_CAUSES, operation_digest, phase_scope, run_scope

from . import client_limits
from . import capture as capture_mod
from . import combine_evidence
from . import capture_freshness
from .board import load_details_by_id
from .op_preview import PreviewValidationError, build_preview
from .worksite_broadcast import build_worksite_cancel_preview, build_worksite_completion_preview, build_worksite_preview
from .notices import format_notice, notice_catalog, verification_notice
from fides_land.ops import CUSTOM_JSON_ID, LandOpError
from .security import player_for_request
from .transaction_identity import app_identity
from .hive_rpc import ActivityReadError, _operation, _rpc

router = APIRouter()
_PLAN_COMBINE_VERIFY_LOCK = threading.Lock()  # one capture per known result.
logger = logging.getLogger("ceres.routes_op_preview")
NOT_APPLIED_SETTLE_BLOCKS = 20
NOT_APPLIED_MIN_ATTEMPTS = 2
NULL_ID_PROBE_SETTLE_SECONDS = 3900  # S10 round 2 / L2-04: no judgement before Hive's longest transaction expiration (1 h) plus a margin
# S3 / L2-02, tightened in S10: a node answering "not found" is not proof the transaction was never sent. A signed Hive transaction can still
# be included until ITS expiration, and the longest expiration Hive accepts is one hour; so a request with a transaction id is only called absent
# once it is older than that hour plus a margin (the same figure routing v2 uses). Until then the answer stays unknown.
TX_ABSENCE_GRACE_SECONDS = 3900
MAX_VERIFY_OPERATIONS = 64  # a transaction carries a handful of operations; the verifier's duplicate check is quadratic
# a narrow, short-timeout client for the one live read the "complete" (Feed Worker)
# preview needs -- mirrors ceres/routes_harvest.py's `_client`, kept separate on purpose (that
# module's cache/pacing is tuned for the harvest boxes' own read volume, not this one-off check).
_worksite_client = LandClient(timeout=5.0, retries=1)


@phase_scope("chain_head")
def _inclusion_age_blocks(chain: dict[str, Any]) -> int | None:
    try:
        evidence = chain.get("evidence")
        if not isinstance(evidence, dict):
            return None
        inclusion_block = evidence.get("block_num")
        if isinstance(inclusion_block, bool) or not isinstance(inclusion_block, int):
            return None
        properties = fides_verify._http_rpc_call(
            fides_verify.HIVE_NODE_DEFAULT,
            "condenser_api.get_dynamic_global_properties",
            [],
        )
        if not isinstance(properties, dict):
            return None
        head_block = properties.get("head_block_number")
        if isinstance(head_block, bool) or not isinstance(head_block, int):
            return None
        age_blocks = head_block - inclusion_block
        if age_blocks < 0:
            return None
        evidence["head_block"] = head_block
        return age_blocks
    except Exception:
        return None


def _snapshot(request: Request, player: str):
    config = request.app.state.ceres_config
    try:
        report, _ref = capture_mod.latest_snapshot(config, player)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="no stored board is available; load the board before continuing") from exc
    if report is None:
        raise HTTPException(status_code=503, detail="no stored board is available; load the board before continuing")
    return config, report


def _moves(body: dict[str, Any], default_deed_uid: str | None = None) -> list[dict[str, Any]]:
    raw = body.get("moves", body.get("staged_moves"))
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="request body must contain a moves array")
    result = []
    for move in raw:
        if not isinstance(move, dict):
            result.append(move)
            continue
        copied = dict(move)
        if default_deed_uid and not copied.get("deed_uid"):
            copied["deed_uid"] = default_deed_uid
        result.append(copied)
    return result


def _staged_worksites(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the unsigned worksite overlay carried with an operation preview."""
    raw = body.get("staged_worksites", body.get("staged_worksite"))
    if raw in (None, [], {}):
        return []
    entries = [raw] if isinstance(raw, dict) else raw
    if not isinstance(entries, list) or len(entries) > 50:
        raise HTTPException(status_code=400, detail="staged_worksites must be a list of at most 50 objects")
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise HTTPException(status_code=400, detail="every staged worksite must be an object")
        deed_uid = entry.get("deed_uid")
        target = entry.get("target", entry.get("worksite_type"))
        if not isinstance(deed_uid, str) or not deed_uid or len(deed_uid) > 256:
            raise HTTPException(status_code=400, detail="staged worksite deed_uid must be a non-empty string of at most 256 characters")
        if not isinstance(target, str) or not target or len(target) > 32:
            raise HTTPException(status_code=400, detail="staged worksite target must be a non-empty string of at most 32 characters")
        result.append({"deed_uid": deed_uid, "target": target})
    return result


def _require_capture_provenance(body: dict[str, Any], moves: list[dict[str, Any]], report: dict[str, Any]) -> None:
    """: every stake/replace preview -- whatever UI origin staged it -- must say
    which board read it was prepared from (`capture_generated_at`), and is refused when
    the server holds a newer capture than that read. Before this, only the optional
    combine stamp was checked, so the ordinary picker route was ungated."""
    if not any(isinstance(move, dict) and move.get("action") in {"stake", "replace"} for move in moves):
        return
    captured_at = body.get("capture_generated_at")
    if not captured_at:
        raise HTTPException(status_code=409, detail="This stake needs a fresh board read; reload the board before continuing.")
    snapshot_at = report.get("generated_at") if isinstance(report, dict) else None
    try:
        snapshot_is_newer = capture_freshness.capture_is_newer_than(snapshot_at, captured_at)
    except capture_freshness.ValidationError as exc:
        raise HTTPException(status_code=409, detail="This stake's board freshness could not be verified; reload the board before continuing.") from exc
    if snapshot_is_newer:
        # coded so the page adopts the newer capture and re-prepares the preview
        # itself; the user never sees this as a "reload" refusal.
        raise HTTPException(status_code=409, detail={
            "code": "capture_superseded",
            "message": "This stake was prepared from an older board read; the board is updating.",
            "snapshot_generated_at": snapshot_at,
        })


def _touched_deeds(moves: list[Any], staged_worksites: list[dict[str, Any]], report: dict[str, Any]) -> list[str]:
    """: every deed a staged batch reads -- the plots it changes, staged worksites, and the plot a
    staged card currently sits on (a stake/replace moves it off that plot). Only deeds the board lists."""
    known = {str(plot.get("deed_uid")) for plot in report.get("plots", []) if plot.get("deed_uid")}
    touched = {str(entry["deed_uid"]) for entry in staged_worksites}
    refs: set[str] = set()
    for move in moves:
        if not isinstance(move, dict):
            continue
        if move.get("deed_uid"):
            touched.add(str(move["deed_uid"]))
        refs.update(str(move[key]) for key in ("asset_ref", "evict_asset_ref") if move.get(key))
    for key in ("workers", "bonus_assets"):
        for row in report.get(key, []) or []:
            if str(row.get("asset_ref")) in refs and row.get("stake_ref_uid"):
                touched.add(str(row["stake_ref_uid"]))
    return sorted(touched & known)


def _preview(deed_uid: str | None, body: dict[str, Any], request: Request, player: str | None):
    account = player_for_request(request, player)
    config, stored = _snapshot(request, account)
    try:
        report = capture_mod.load_card_pool(dict(stored))
    except LookupError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "collection_incomplete",
            "message": str(exc),
        }) from exc
    moves = _moves(body, deed_uid)
    staged_worksites = _staged_worksites(body)
    _require_capture_provenance(body, moves, report)
    # Capture-freshness gate for the worker-tile "combine idle copies, then stake the
    # survivor" flow -- the client already knows the combine's own verified timestamp
    # and sends it straight through; this refuses staging a stake from a snapshot that
    # predates that combine (Cooling/restake gate, `ceres/capture_freshness.py`).
    combine_verified_at = body.get("combine_verified_at")
    if combine_verified_at:
        capture_at = report.get("generated_at") if isinstance(report, dict) else None
        try:
            fresh = bool(capture_at) and capture_freshness.capture_is_newer_than(capture_at, combine_verified_at)
        except capture_freshness.ValidationError as exc:
            logger.warning("combine freshness check failed for %s: %s", account, exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not fresh:
            logger.warning("stale capture after combine for %s", account)
            raise HTTPException(status_code=409, detail="This card was combined after the board read; reload the board before continuing.")
    # ( "Fresh read before every signature"; "We ask the API or we have no
    # data"; ): every staged plot is read live from the game for this request, in memory
    # only. No answer yet -> a coded 503 the page re-asks silently; no text, no stored fallback.
    touched = _touched_deeds(moves, staged_worksites, report)
    if touched:
        live = capture_mod.read_plots_live(config, account, touched, stored)
        if live["failed"]:
            raise HTTPException(status_code=503, detail={"code": "live_read_pending"})
        try:
            report = capture_mod.load_card_pool(live["report"])
        except LookupError as exc:
            raise HTTPException(status_code=409, detail={
                "code": "collection_incomplete",
                "message": str(exc),
            }) from exc
    try:
        return build_preview(
            report,
            moves,
            account=account,
            config=config,
            details_by_id=load_details_by_id(),
            staged_worksites=staged_worksites,
            app=app_identity(),
        )
    except PreviewValidationError as exc:
        # exc.errors and str(exc) are both authored by build_preview's own move validation
        # (per-move reasons) -- domain refusal text, not exception internals.
        logger.warning("preview validation failed for %s: %s", account, exc)
        if exc.errors and all(error.get("code") == "asset_absent" for error in exc.errors):
            # the live read shows a staged card is no longer where the plan put it -- the
            # page marks those changes as no longer valid (kept-plan path); no raw text.
            raise HTTPException(status_code=409, detail={
                "code": "plan_changed",
                "moves": sorted({int(error["index"]) for error in exc.errors if isinstance(error.get("index"), int) and error["index"] >= 0}),
            }) from exc
        raise HTTPException(status_code=409, detail={"message": str(exc), "errors": exc.errors}) from exc
    except LandOpError as exc:
        # LandOpError's message is always authored in this codebase (fides_land/ops.py
        # op_preview.py) -- a domain refusal reason, never a wrapped external exception.
        logger.warning("operation construction refused for %s: %s", account, exc)
        raise HTTPException(status_code=400, detail=f"operation construction refused: {exc}") from exc
    except ValueError as exc:
        logger.warning("operation preview request rejected for %s: %s", account, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/op-preview")
def op_preview(body: dict[str, Any], request: Request, player: str | None = None):
    with run_scope(ROOT_CAUSES["op_preview"], phase="preview", sink=getattr(request.app.state, "mnemosyne_writer", None)):
        return JSONResponse(content=_preview(None, body, request, player))


@router.post("/api/plots/{deed_uid}/op-preview")
def plot_op_preview(deed_uid: str, body: dict[str, Any], request: Request, player: str | None = None):
    with run_scope(ROOT_CAUSES["plot_op_preview"], phase="preview", sink=getattr(request.app.state, "mnemosyne_writer", None)):
        return JSONResponse(content=_preview(deed_uid, body, request, player))


@router.post("/api/plots/{deed_uid}/worksite-preview")
def plot_worksite_preview(deed_uid: str, body: dict[str, Any], request: Request, player: str | None = None):
    with run_scope(ROOT_CAUSES["plot_worksite_preview"], phase="preview", sink=getattr(request.app.state, "mnemosyne_writer", None)):
        return _plot_worksite_preview(deed_uid, body, request, player)


def _plot_worksite_preview(deed_uid: str, body: dict[str, Any], request: Request, player: str | None = None):
    """ Phase 4 -- builds the single worksite-change op for `deed_uid`. Body:
    `{target: "research", time_crystals: 0}`. Never broadcasts; the response is the same
    shape `build_preview` returns for card moves (`account`/`chunks`/`operations`/`notices`)
    so the frontend's existing review rendering pattern applies unchanged."""
    config = request.app.state.ceres_config
    account = player_for_request(request, player)
    _config, report = _snapshot(request, account)
    if body.get("action") in ("complete", "cancel"):
        # the stored capture can predate the countdown finishing (the build
        # completed while the page was already open), so the readiness check itself is
        # re-run against a fresh worksite read rather than the board's own snapshot.
        try:
            payload = _worksite_client.active_project(deed_uid).data
            worksite_override = payload.get("data") if isinstance(payload, dict) else {}
            if worksite_override is None:
                worksite_override = {}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"live worksite read unavailable ({type(exc).__name__}); try again") from exc
        try:
            builder = build_worksite_cancel_preview if body["action"] == "cancel" else build_worksite_completion_preview
            return JSONResponse(content=builder(report, deed_uid, account=account, app=app_identity(), worksite_override=worksite_override))
        except LandOpError as exc:
            logger.warning("worksite completion refused for %s: %s", account, exc)
            raise HTTPException(status_code=400, detail=f"operation construction refused: {exc}") from exc
        except ValueError as exc:
            logger.warning("worksite completion request rejected for %s: %s", account, exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    target = body.get("target")
    if not isinstance(target, str) or not target:
        raise HTTPException(status_code=400, detail="target is required")
    time_crystals = body.get("time_crystals", 0)
    try:
        time_crystals = int(time_crystals)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="time_crystals must be an integer") from None
    try:
        return JSONResponse(content=build_worksite_preview(
            report, deed_uid, target, account=account, app=app_identity(), time_crystals=time_crystals,
        ))
    except LandOpError as exc:
        # LandOpError from build_worksite_preview is a domain refusal (e.g. the zero-PP
        # no-power-source reason and its remedy, from worksite_swap.py's swap_eligibility)
        # authored user-facing copy, never a wrapped exception -- must reach the player.
        logger.warning("worksite operation construction refused for %s: %s", account, exc)
        raise HTTPException(status_code=400, detail=f"operation construction refused: {exc}") from exc
    except ValueError as exc:
        logger.warning("worksite preview request rejected for %s: %s", account, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _expected_ops(raw_operations: Any) -> list[Op]:
    if not isinstance(raw_operations, list) or not raw_operations:
        raise ValueError("operations must be a non-empty Keychain operations array")
    if len(raw_operations) > MAX_VERIFY_OPERATIONS:  # S10 / L1-02: the verifier compares every nonce with every other
        raise ValueError("too many operations to verify in one request")
    expected: list[Op] = []
    for raw in raw_operations:
        if not isinstance(raw, list) or len(raw) != 2 or raw[0] != "custom_json" or not isinstance(raw[1], dict):
            raise ValueError("operations contain an unsupported Keychain operation")
        payload = raw[1]
        raw_json = payload.get("json")
        parsed = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
        if not isinstance(parsed, dict):
            raise ValueError("custom_json operation has invalid JSON payload")
        expected.append(Op(
            operation_id=payload.get("id", ""),
            required_auths=list(payload.get("required_auths", [])),
            required_posting_auths=list(payload.get("required_posting_auths", [])),
            json_payload=parsed,
        ))
    return expected


# The caller supplies the retry count because this endpoint is stateless; escalation never alters the verdict.
def _verification_attempt(body: dict[str, Any]) -> int:
    value = body.get("verification_attempt", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HTTPException(status_code=400, detail="verification_attempt must be a positive integer")
    return value


@router.post("/api/op-inclusion")
def op_inclusion(body: dict[str, Any], request: Request, player: str | None = None):
    """Cheap inclusion-only probe; domain verification stays on `/api/op-verify`."""
    client_limits.limit(request, "verify")
    account = player_for_request(request, player)
    tx_id = body.get("tx_id")
    if tx_id is None:
        return JSONResponse(content=_request_identity_inclusion(account, body))
    if not isinstance(tx_id, str) or not tx_id:
        raise HTTPException(status_code=400, detail="tx_id is required")
    transaction, reason_kind = get_transaction(tx_id)
    block_num = transaction.get("block_num") if transaction else None
    included = True if block_num is not None else False if reason_kind == "not_found" else None
    if included is False and not _old_enough_to_be_absent(body.get("created_at")):
        included = None  # too young: the first "not found" never frees the duplicate-sign guard
    return JSONResponse(content={"included": included, "block_num": block_num})


def _old_enough_to_be_absent(created_at: Any) -> bool:
    """True only when the request is provably past its lifetime; an unknown or unreadable age is never old enough. Zero outbound calls."""
    if not isinstance(created_at, str):
        return False
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds() >= TX_ABSENCE_GRACE_SECONDS


def _request_identity_inclusion(account: str, body: dict[str, Any]) -> dict[str, Any]:
    # A10 / : no automatic history discovery, even after expiry.
    # A missing transaction id stays unresolved; only known-id lookup is allowed.
    return {"included": None, "block_num": None}


@router.post("/api/op-verify")
def op_verify(body: dict[str, Any], request: Request, player: str | None = None):
    client_limits.limit(request, "verify")  # S10 / L1-01: every upstream-reading verification spends the shared bucket
    # INVARIANT -- the full `state` vocabulary this endpoint can return, and what each
    # means. A caller must treat any string it does not recognise as "not confirmed yet
    # check again", never as failure -- new states may be added here over time.
    # domain-confirmed -- the domain re-read affirmatively matches. Terminal success.
    # domain-mismatch -- the operation payload is structurally malformed. A genuine
    # problem, never asserted from a stale or missing read.
    # domain-pending -- evidence was obtained but does not yet read back the
    # expected value (e.g. a worksite `project_type` hasn't caught
    # up). It remains unconfirmed and carries an escalation threshold.
    # domain-not-applied -- a chain-verified stake/unstake has a live domain read that
    # does not show the effect. makes this terminal failure
    # worksite changes remain `domain-pending`.
    # domain-could-not-verify -- no usable evidence could be obtained at all (fetcher
    # missing, fetch raised, or upstream had nothing to compare
    # against). Genuinely unknown -- never success, never mismatch.
    # game-refused -- Splinterlands rejected the broadcast and supplied its own
    # refusal sentence. Terminal failure; never re-arm the move.
    # chain_absent -- the broadcast itself is not yet (or never) observed on
    # chain; see `chain.evidence.reason_kind` for why.
    # A stale post-broadcast read remains pending for operation families with a real propagation
    # lifecycle; chain-verified stake/unstake absence is converted to the terminal state above.
    config = request.app.state.ceres_config
    account = player_for_request(request, player)
    tx_id = body.get("tx_id")
    if not isinstance(tx_id, str) or not tx_id:
        raise HTTPException(status_code=400, detail="tx_id is required")
    try:
        expected = _expected_ops(body.get("operations"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        # These are shape complaints about the caller's own submitted `operations` array
        # (e.g. "operations contain an unsupported Keychain operation") -- not server
        # internals, safe to echo back to whoever is debugging their own request.
        logger.warning("verification operations rejected for %s: %s", account, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raw_operations = body["operations"]
    verification_attempt = _verification_attempt(body) if "verification_attempt" in body else None
    with run_scope(
        "verification", sink=getattr(request.app.state, "mnemosyne_writer", None),
        operation_key=operation_digest(account, tx_id), verification_attempt=verification_attempt,
    ):
        started = time.monotonic()
        try:
            with _PLAN_COMBINE_VERIFY_LOCK if body.get("plan_combine") is True else nullcontext():
                response = _verify_validated(config, account, tx_id, expected, raw_operations, verification_attempt, plan_combine=body.get("plan_combine") is True, recovery=body.get("combine_recovery"))
        except Exception:
            record_verification(account=account, tx_id=tx_id, attempt=verification_attempt,
                                elapsed_ms=(time.monotonic() - started) * 1000,
                                operation_count=len(expected), error=True)
            raise
        record_verification(account=account, tx_id=tx_id, attempt=verification_attempt,
                            elapsed_ms=(time.monotonic() - started) * 1000,
                            operation_count=len(expected), outcome=json.loads(response.body))
        return response


def _observed_call(phase, callback, *args, **kwargs):
    with phase_scope(phase):
        return callback(*args, **kwargs)


def _game_rows_at_hive_positions(game: dict[str, Any], positions: list[int]) -> dict[str, Any]:
    """: the verdict numbers game operations densely; give each row the position of its operation in the signed
    transaction (`index`), keeping the game numbering as `game_index`. Identity when every operation is a game op. No calls."""
    if positions == list(range(len(positions))):
        return game
    evidence = dict(game.get("evidence") or {})
    for key in ("operations", "op_outcomes", "refused"):
        rows = evidence.get(key)
        if isinstance(rows, list):
            evidence[key] = [{**row, "game_index": row["index"], "index": positions[row["index"]]}
                             if isinstance(row, dict) and isinstance(row.get("index"), int) and 0 <= row["index"] < len(positions)
                             else row for row in rows]
    return {**game, "evidence": evidence}


def _verify_validated(config, account, tx_id, expected, raw_operations, verification_attempt, *, plan_combine=False, recovery=None):
    plan_nonce = expected[0].json_payload.get("n") if plan_combine and len(expected) == 1 and expected[0].operation_id == COMBINE_OP_ID else None
    if plan_nonce:
        saved = combine_evidence.load_plan_result(config, plan_nonce, account=account, tx_id=tx_id, operations=raw_operations)
        if saved:
            return JSONResponse(content=saved)
    chain = _observed_call("chain", verify_chain, expected, tx_id)
    # the game files one record per Splinterlands game operation (`sm_*`) -- the bare id for the first
    # `<tx>-1`, `<tx>-2` ... for the following ones; another custom_json gets no record and is not counted.
    game_positions = [index for index, op in enumerate(expected) if str(op.operation_id).startswith("sm_")]
    expected_nonces = [
        expected[index].json_payload["n"]
        for index in game_positions
        if isinstance(expected[index].json_payload, dict) and expected[index].json_payload.get("n") is not None
    ]
    if len(tx_id) == 40 and all(character in "0123456789abcdefABCDEF" for character in tx_id):
        try:
            game = _observed_call(
                "game_verdict", game_verdict,
                tx_id,
                op_count=len(game_positions),
                expected_nonces=expected_nonces,
                account=account,
            )
            game = _game_rows_at_hive_positions(game, game_positions)
        except Exception as exc:
            logger.warning("game verdict lookup failed unexpectedly for tx %s (%s): %s", tx_id, account, exc)
            game = {"status": "game_unknown", "evidence": {"reason": "game verdict lookup failed; retry later"}}
    else:
        game = {
            "status": "game_unknown",
            "evidence": {
                "reason": "transaction id is not a 40-character game lookup id",
                "lookup_attempted": False,
            },
        }

    is_combine_batch = bool(expected) and all(op.operation_id == COMBINE_OP_ID for op in expected)
    is_stake_change_batch = bool(expected) and all(op.operation_id == CUSTOM_JSON_ID for op in expected)
    evidence = None if is_combine_batch else capture_mod.MnemosyneEvidence(account, attempt=verification_attempt)

    def fetch_assets(deed_uid: str) -> dict[str, Any]:
        if evidence is None:
            return capture_mod.fetch_land_assets(config, account, deed_uid)
        return evidence.assets(deed_uid).data

    def fetch_worksite(deed_uid: str) -> dict[str, Any] | None:
        if evidence is None:
            return capture_mod.fetch_worksite_facts(config, account, deed_uid)
        return evidence.worksite_facts(deed_uid)

    # THE FIX for the 500-on-the-happy-path bug (2026-08-24, verified against a real
    # confirmed batch): `check_domain` consumes body-form op dicts (each
    # with its own "json"/"id"/etc. keys, `raw_op.get("json")`), NOT the Keychain pair
    # form `["custom_json", {...}]` that `raw_operations` is validated to be by
    # `_expected_ops` above. Passing the pair form straight through raised
    # `AttributeError: 'list' object has no attribute 'get'`, unhandled -> HTTP 500
    # `domain-confirmed` was unreachable. Unwrap to the body dicts here.
    # Run the domain check regardless of chain status -- previously an early return on
    # `chain.status != "chain_verified"` meant the AUTHORITATIVE check (a live re-read of
    # actual game state, independent of Hive RPC indexing entirely -- the internal rules) never
    # even ran when the chain check merely raced the mempool. A `chain_absent` +
    # `domain_verified` pair is a CONFIRMATION, not a failure: in the reported case
    # domain verification would have succeeded immediately had it been allowed to run.
    # Guarded against an unexpected exception (a genuine network failure fetching
    # `/land/stake/deeds/{uid}/assets`, not the LandDomainVerificationError `check_domain`
    # already handles per-op) so this newly-unconditional call cannot become a NEW 500.
    # KNOWN, DELIBERATE POLICY DIVERGENCE (2026-08-24, not an oversight): this
    # ad-hoc path bypasses `fides.ledger.Ledger`
    # entirely and lets `domain_verified` stand regardless of chain status, per this
    # project's real-money semantics -- a `custom_json` landing on-chain proves nothing, only a
    # re-read of game state does. `fides/ledger.py`'s `Ledger.advance_with_domain_check()`
    # does NOT share this policy: it structurally requires `chain_verified` before it will
    # run a domain check at all. If a future Ledger-mediated consumer wants this route's
    # behavior, it needs its own bypass -- do not assume the two paths agree.
    # DISPATCH BY OPERATION FAMILY ( fix): a real `sm_combine_cards` payload
    # (`{"cards": [...], "app": ..., "n": ...}`) has no `deed_uid` and no `op` field
    # routing it through `LandVerificationSpec` unconditionally hit that spec's "op has
    # no deed_uid" mismatch branch and reported a SUCCESSFUL combine as domain-mismatch
    # every time, deterministically. A batch made entirely of `sm_combine_cards` ops goes
    # to `fides_cards.verify.CardVerificationSpec` instead; everything else (worker moves
    # worksite changes, Runi/Totem/Power Core stake changes) is unchanged and still goes
    # through `LandVerificationSpec`. This project's ops are never mixed-family in one
    # batch in practice (each call site builds one family at a time), so "all ops share
    # the combine id" is the correct and sufficient test -- a genuinely mixed batch falls
    # through to the land path unchanged, which already reports "op has no deed_uid" for
    # the combine op in it, same as before this fix (a mixed batch was never observed and
    # is not this IR's defect).
    combine_nonce: str | None = None
    try:
        if is_combine_batch:
            # fix -- a snapshot taken between the combine and this verify call
            # already reflects POST-combine state (the destroyed copies are gone by
            # construction), so `expected_records` can never be correctly derived from
            # `latest_snapshot()` here. Prefer the pre-combine records `/api/combine/build`
            # persisted at the one moment they were still live (keyed by the op's own
            # nonce, round-tripped verbatim by the client) -- see
            # ceres/combine_evidence.py. Only fall back to the stale-by-construction
            # snapshot read when no carried evidence exists at all (nonce missing, file
            # expired, or this route reached directly without a prior /api/combine/build
            # call, as every pre-existing test in this file does).
            for op in expected:
                if isinstance(op.json_payload, dict) and op.json_payload.get("n"):
                    combine_nonce = str(op.json_payload["n"])
                    break
            expected_records = combine_evidence.load(config, combine_nonce, account=account, unresolved=plan_combine) if combine_nonce else None
            if expected_records is None and plan_combine and recovery:
                # A04: verify the build seal, exact inputs and stored expectation.
                expected_records = combine_evidence.recover(config, recovery, account=account, nonce=combine_nonce,
                                                            uids=expected[0].json_payload.get("cards", []))
            if expected_records is None:
                try:
                    pre_combine_report, _ref = capture_mod.latest_snapshot(config, account)
                except Exception as exc:
                    logger.warning("pre-combine snapshot read failed for %s: %s", account, exc)
                    pre_combine_report = None
                if isinstance(pre_combine_report, dict):
                    try:
                        pre_combine_report = capture_mod.load_card_pool(pre_combine_report)
                    except LookupError:
                        pre_combine_report = None
                expected_records = pre_combine_report.get("cards") if isinstance(pre_combine_report, dict) else None

            def fetch_collection() -> Any:
                # A live re-read of the collection, independent of the board snapshot
                # the same "a custom_json landing on-chain proves nothing" principle as
                # LandVerificationSpec's own fetch_assets, just against the card, not the
                # land, endpoint.
                return SplinterlandsClient().collection(account)

            checker = CardVerificationSpec(fetch_collection, expected_records=expected_records, intent=account)
            domain = _observed_call("domain", checker.check_domain, "op-preview", [op[1] for op in raw_operations])
        else:
            checker = LandVerificationSpec(fetch_assets, fetch_worksite_facts=fetch_worksite, worksite_completion_accepted=game.get("status") == "game_accepted")
            if verification_attempt is None:
                domain = _observed_call("domain", checker.check_domain, "op-preview", [op[1] for op in raw_operations])
            else:
                domain = _observed_call(
                    "domain", checker.check_domain,
                    "op-preview", [op[1] for op in raw_operations], verification_attempt=verification_attempt,
                )
            if evidence is not None:
                domain["evidence"] = dict(domain.get("evidence") or {})
                domain["evidence"]["provenance"] = evidence.provenance()
    except Exception as exc:
        logger.warning("domain verification failed unexpectedly for tx %s (%s): %s", tx_id, account, exc)
        domain = {"status": "domain_check_failed", "evidence": {"reason": "domain verification failed; retry later"}}

    if game.get("status") == "game_rejected":
        state = "game-refused"
    elif domain.get("status") == "domain_verified":
        state = "domain-confirmed"
    elif (
        domain.get("status") == "domain_pending"
        and chain.get("status") == "chain_verified"
        and is_stake_change_batch
        and game.get("status") != "game_accepted"
        and (verification_attempt or 0) >= NOT_APPLIED_MIN_ATTEMPTS
        and (age := _inclusion_age_blocks(chain)) is not None
        and age >= NOT_APPLIED_SETTLE_BLOCKS
    ):
        # stake/unstake effects are immediate or absent. Once the matching custom_json
        # is included, settled on the server's chain head, and a second live deed-assets read
        # still lacks the requested effect, waiting would misrepresent a failed operation as
        # propagation. Worksite changes deliberately stay on the pending path because
        # `_check_worksite_change` has a real build lifecycle.
        domain_evidence = dict(domain.get("evidence") or {})
        domain_evidence.update({
            "inclusion_block": chain["evidence"]["block_num"],
            "head_block": chain["evidence"]["head_block"],
            "age_blocks": age,
            "age_seconds": age * 3,
            "attempt": verification_attempt,
        })
        domain["evidence"] = domain_evidence
        state = "domain-not-applied"
    elif domain.get("status") == "domain_pending":
        # The domain re-read came back but does not (yet) match the expected
        # post-broadcast value -- for example a worksite change's `project_type` hasn't
        # caught up yet. Stake/unstake operations with the same evidence were handled
        # above as `domain-not-applied`; this branch preserves the worksite propagation
        # model and the chain-inconclusive pending case.
        state = "domain-pending"
    elif domain.get("status") == "domain_could_not_verify":
        # No usable evidence could be obtained at all (fetcher missing, fetch raised, or
        # the upstream had nothing to compare against) -- genuinely unknown, never
        # reported as either success or mismatch.
        state = "domain-could-not-verify"
    elif domain.get("status") == "domain_check_failed":
        # A failed domain check has no disagreement evidence, so it is unverifiable.
        state = "domain-could-not-verify"
    elif domain.get("status") == "domain_mismatch":
        # Structural mismatch evidence remains a danger verdict even when chain lookup races.
        state = "domain-mismatch"
    else:
        # Chain is still inconclusive (a race, a lookup failure, or a genuine absence
        # `chain.evidence.reason_kind` distinguishes which) AND the domain re-read did not
        # affirmatively confirm either. Report the chain's own status rather than
        # asserting a mismatch we have no domain evidence for.
        state = chain.get("status", "chain_absent")

    refreshed_deeds: list[str] = []
    refresh_errors: list[dict[str, str]] = []
    if state in {"domain-confirmed", "domain-not-applied"}:
        # Reuse the attempt's fresh evidence for every touched deed, then persist one
        # complete report before returning the terminal verdict.
        # LOCAL INVARIANT: a confirmed response returns only after every touched deed is persisted.
        # A worksite-change op carries no singular `deed_uid` -- its touched plots are
        # `deed_uids` (an array, even though Phase 4 only ever stages one) -- so both shapes
        # are collected here, never just the card-move one.
        deed_uids: set[str] = set()
        for op in expected:
            if not isinstance(op.json_payload, dict):
                continue
            single = op.json_payload.get("deed_uid")
            if single:
                deed_uids.add(single)
            for uid in op.json_payload.get("deed_uids") or []:
                if uid:
                    deed_uids.add(uid)
        deed_uids = sorted(deed_uids)
        try:
            if evidence is None and deed_uids:
                raise RuntimeError("land evidence context was not created")
            refreshed_report = {"capture": {"refreshed_deeds": [], "refresh_errors": []}}
            if deed_uids:
                refreshed_report = _observed_call(
                    "post_confirmation_refresh", capture_mod.expand_plots,
                    config, account, deed_uids, evidence=evidence,
                    include_collection=not is_stake_change_batch,
                )
            refreshed_deeds.extend(refreshed_report.get("capture", {}).get("refreshed_deeds", []))
            failed_uids = set(refreshed_report.get("capture", {}).get("refresh_errors", []))
            refresh_errors.extend({"deed_uid": uid, "error": "board refresh failed; retry later"} for uid in sorted(failed_uids))
        except Exception as exc:
            logger.warning("post-broadcast board refresh failed for tx %s (%s): %s", tx_id, account, exc)
            refresh_errors.extend({"deed_uid": uid, "error": "board refresh failed; retry later"} for uid in deed_uids)

    # an accepted combine with one remaining owned input can expose a
    # mismatching result for a placement HOLD. It never upgrades the domain verdict.
    combine_result = None
    combine_observed_at = None
    if plan_combine and is_combine_batch and len(expected) == 1:
        observed = (domain.get("evidence") or {}).get("observed_survivor")
        present_consumed = (domain.get("evidence") or {}).get("present_consumed_uids")
        if (isinstance(observed, dict) and not present_consumed
                and str(observed.get("player") or observed.get("owner") or "").lower() == account.lower()
                and (state == "domain-confirmed" or (chain.get("status") == "chain_verified" and game.get("status") == "game_accepted"))):
            # B02: canonical result facts before returning/caching raw collection data.
            from .combine_chain import canonical_fact
            combine_result = dict(observed)
            for key in ("bcx", "level", "land_base_pp"):
                combine_result[key] = canonical_fact(observed.get(key))
            combine_observed_at = datetime.now(timezone.utc).isoformat()
    combine_refresh: dict[str, Any] | None = None
    if is_combine_batch and (state == "domain-confirmed" or combine_result is not None):
        # A confirmed combine mutates the account's CARD COLLECTION (uid/bcx/level)
        # never plot or worker state directly (combining a card that is currently
        # staked on land is game-blocked, the internal rules), so it carries no `deed_uid` for
        # the land refresh above to key on, and that leaves `deed_uids`
        # empty for a combine batch every time. Since e8ef677a (2026-08-30), the quick
        # capture fetches both the deeds bundle and the whole collection, so it is the
        # appropriate background refresh here; land refresh remains deed-keyed. This
        # response does not block on the quick capture; the
        # client polls `GET /api/capture/status`, same mechanism the existing "Full
        # capture" button already uses, then reloads the board once it lands. If a
        # A read already in flight may have fetched the pre-combine collection, so the
        # confirmed mutation must replace it; the client's poll follows this new read.
        already_running = capture_mod.is_running(account)
        triggered = capture_mod.start_refresh(config, account)
        combine_refresh = {"triggered": triggered, "mode": "quick", "already_running": already_running}
        # The carried evidence has done its job -- discard it rather than let it sit
        # until _MAX_AGE_SECONDS expiry. Best-effort; combine_evidence.discard never raises.
        if combine_nonce and not plan_combine:
            combine_evidence.discard(config, combine_nonce)

    notices = [verification_notice(state, chain=chain, domain=domain, game=game)]
    if domain.get("status") == "domain_check_failed":
        notices.append(verification_notice("domain_check_failed", domain=domain))
    notices.extend(
        format_notice("verification_refresh_failed", context={"reason": item.get("error", "unknown read error")}, reason=item.get("error"))
        for item in refresh_errors
    )
    result = {
        "state": state, "chain": chain, "domain": domain, "game": game,
        "op_results": list((game.get("evidence") or {}).get("operations") or []),
        "op_outcomes": list((game.get("evidence") or {}).get("op_outcomes") or []),
        "refreshed_deeds": refreshed_deeds, "refresh_errors": refresh_errors,
        "combine_refresh": combine_refresh,
        "combine_result": combine_result, "combine_observed_at": combine_observed_at,
        "notices": notices, "notice_catalog": notice_catalog(),
    }
    if plan_nonce and state == "game-refused":
        combine_evidence.discard(config, plan_nonce)
    if plan_nonce and combine_result is not None and (combine_result.get("bcx") or 0) > 0 and (combine_result.get("level") or 0) > 0 and all(type(combine_result.get(key)) in (int, float) and math.isfinite(combine_result[key]) for key in ("bcx", "level", "land_base_pp")):
        try:
            combine_evidence.save_plan_result(config, plan_nonce, account=account, tx_id=tx_id, operations=raw_operations, result=result)
        except OSError:
            logger.warning("Could not retain verified combine result for recovery")
    return JSONResponse(content=result)
