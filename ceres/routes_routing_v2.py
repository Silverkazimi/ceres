"""Account-scoped v2 routing endpoints; release kinds come from configuration.

Create, state, attach and stop are local. LP claim reads metered fresh pool and
maintainer DEC context. Reconcile calls one metered chain or game lookup per request.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from fastapi import APIRouter, HTTPException, Request, Response

from . import client_limits, harvest_evidence, harvest_routing as routing, routing_runs_v2 as runs
from . import __version__
from .routes_board import _config, _require_same_origin
from .security import player_for_request


router = APIRouter()
_log = logging.getLogger("ceres.routing")
_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_STAGES = {"start", "layout", "wallet", "claim", "head", "prepare", "sign", "attach", "release", "reconcile", "unknown"}


def _clean(value, limit=300) -> str:
    """One bounded printable line; control characters never reach the log."""
    return re.sub(r"[^\x20-\x7e]", " ", str(value))[:limit]


def _refused(request: Request, player: str, exc: Exception) -> HTTPException:
    """: a 409 used to log only its status; keep the reason (a plain RoutingError text, never a payload)."""
    _log.warning("refused path=%s player=%s reason=%s", request.url.path.rsplit("/", 1)[-1], _clean(player, 40), _clean(exc))
    return HTTPException(409, str(exc))


@router.get("/api/harvest/routing/v2/kinds")
async def enabled_kinds(request: Request, player: str | None = None):
    """Expose server release switches without a chain or game context read."""
    player_for_request(request, player)
    return {"enabled_kinds": sorted(_config(request).routing_kinds),
            "offer_only_kinds": sorted(_config(request).routing_offer_kinds),
            "signing_enabled": bool(_config(request).harvest_routing_signing_enabled)}


async def _body(request: Request) -> dict:
    """Read one same-origin JSON body; makes zero outbound calls."""
    _require_same_origin(request)
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(422, "Invalid v2 routing request") from exc
    if not isinstance(body, dict):
        raise HTTPException(422, "Invalid v2 routing request")
    return body


def _execute(request: Request, player: str, run_id: str, action):
    """Lock one account, read one run, and invoke one local or bounded action."""
    with runs.locked(_config(request).root, player) as folder:
        record = runs.read(folder, player, run_id)
        runs.verify_token(record, request.headers.get("x-run-token"))   # the run's own token; a name or a run id alone opens nothing
        return runs.public(action(folder, record))


@router.post("/api/harvest/routing/v2/run")
async def create_run(request: Request, player: str | None = None):
    """Load one server preview and persist its manifest; makes zero outbound calls."""
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        preview = harvest_evidence.load_preview(player, body.get("preview_id"), body.get("preview_digest"))
        if preview is None:
            raise routing.RoutingError("Routing preview expired or changed")
        return await asyncio.to_thread(_create, request, player, preview)
    except routing.RoutingError as exc:
        raise _refused(request, player, exc) from exc


def _create(request: Request, player: str, preview: dict) -> dict:
    """Serialize creation for one account; makes zero outbound calls."""
    with runs.locked(_config(request).root, player) as folder:
        record = runs.create(folder, player, preview, set(_config(request).routing_kinds), request.headers.get("x-run-token"))
        return runs.public(record)   # keeps `run_token`: shown once, to the page that created the run


@router.post("/api/harvest/routing/v2/state")
async def state(request: Request, player: str | None = None):
    """Read one run; a declared reload stops unsent work, with zero calls."""
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        return await asyncio.to_thread(_execute, request, player, body.get("run_id"),
                                       lambda folder, record: runs.stop(folder, record)
                                       if body.get("reload") is True else record)
    except routing.RoutingError as exc:
        raise _refused(request, player, exc) from exc


@router.post("/api/harvest/routing/v2/claim")
async def claim_bundle(request: Request, player: str | None = None):
    """Claim one immutable bundle; meter LP reads and return unsigned ops."""
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        return await asyncio.to_thread(_execute, request, player, body.get("run_id"),
            lambda folder, record: runs.claim(folder, record, body.get("bundle_id"),
                set(_config(request).routing_kinds), bool(_config(request).harvest_routing_signing_enabled),
                body.get("excluded_route_ids", [])))
    except routing.RoutingError as exc:
        raise _refused(request, player, exc) from exc


@router.post("/api/harvest/routing/v2/attach")
async def attach_transaction(request: Request, player: str | None = None):
    """Bind one wallet transaction id to all claimed members; zero outbound calls."""
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        return await asyncio.to_thread(_execute, request, player, body.get("run_id"),
            lambda folder, record: runs.attach(folder, record, body.get("bundle_id"), body.get("tx_id")))
    except routing.RoutingError as exc:
        raise _refused(request, player, exc) from exc


@router.post("/api/harvest/routing/v2/find")
async def find_signed(request: Request, player: str | None = None):
    """Owner-pressed: find a transaction the wallet signed but never reported (one metered account-history read), then attach it."""
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        return await asyncio.to_thread(_execute, request, player, body.get("run_id"),
            lambda folder, record: runs.find_signed(folder, record, body.get("bundle_id")))
    except routing.RoutingError as exc:
        raise _refused(request, player, exc) from exc


@router.post("/api/harvest/routing/v2/release")
async def release_unsigned(request: Request, player: str | None = None):
    """Return a claimed bundle the wallet never signed to planned; zero outbound calls."""
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        return await asyncio.to_thread(_execute, request, player, body.get("run_id"),
            lambda folder, record: runs.release(folder, record, body.get("bundle_id")))
    except routing.RoutingError as exc:
        raise _refused(request, player, exc) from exc


@router.post("/api/harvest/routing/v2/head")
async def counted_hive_head(request: Request, player: str | None = None):
    """Return one server-metered head for the existing signed queue's preparation."""
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        return await asyncio.to_thread(_execute, request, player, body.get("run_id"),
            lambda folder, record: runs.head(folder, record, body.get("bundle_id")))
    except routing.RoutingError as exc:
        raise _refused(request, player, exc) from exc


@router.post("/api/harvest/routing/v2/reconcile")
async def reconcile_transaction(request: Request, player: str | None = None):
    """Meter at most one physical probe or verdict lookup per explicit request."""
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        return await asyncio.to_thread(_execute, request, player, body.get("run_id"),
            lambda folder, record: runs.reconcile(folder, record, body.get("bundle_id"),
                body.get("mode"), body.get("operation_index"), manual=body.get("manual") is True))
    except routing.RoutingError as exc:
        raise _refused(request, player, exc) from exc


@router.post("/api/harvest/routing/v2/stop")
async def stop_run(request: Request, player: str | None = None):
    """Stop unclaimed work while preserving claimed reservations; zero calls."""
    body = await _body(request)
    player = player_for_request(request, player)
    try:
        return await asyncio.to_thread(_execute, request, player, body.get("run_id"), runs.stop)
    except routing.RoutingError as exc:
        raise _refused(request, player, exc) from exc


@router.post("/api/harvest/routing/v2/discard")
async def discard_run(request: Request, player: str | None = None):
    """: the page is leaving (refresh, close) or starting over: delete this run now. Whatever may already have been sent is kept as a tiny safety record.
    Idempotent: a run that is already gone answers ok."""
    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "routing")

    def work():
        with runs.locked(_config(request).root, player) as folder:
            try:
                record = runs.read(folder, player, body.get("run_id"))
                runs.verify_token(record, request.headers.get("x-run-token") or body.get("run_token"))   # a beacon cannot set headers
            except routing.RoutingError:
                return {"ok": True}   # missing, or not yours: the same answer
            runs.retire(folder, record)
            return {"ok": True}
    return await asyncio.to_thread(work)


@router.get("/api/harvest/routing/v2/safety")
async def safety_list(request: Request, player: str | None = None):
    """Compatibility response for stale pages; silent reservations are never exposed to the UI."""
    player = player_for_request(request, player)
    client_limits.limit(request, "routing")
    return {"records": []}


@router.post("/api/harvest/routing/v2/safety/check")
async def safety_check(request: Request, player: str | None = None):
    """Compatibility endpoint for stale pages; no manual safety controls remain."""
    body = await _body(request)
    player = player_for_request(request, player)
    client_limits.limit(request, "routing")
    record_id = body.get("record_id")
    if not isinstance(record_id, str) or len(record_id) > 100:
        raise HTTPException(422, "Invalid record")
    return {"state": "gone", "records": []}


@router.post("/api/harvest/routing/v2/client-report")
async def client_report(request: Request, player: str | None = None):
    """: the browser reports why a routing step failed (the wallet part never reaches the server otherwise).

    Allowlisted fields only; operation bodies, signed bytes and keys are never accepted. Best effort: always 204.
    """
    body = await _body(request)
    player = player_for_request(request, player)
    def token(key):
        value = body.get(key)
        return value if isinstance(value, str) and _TOKEN.fullmatch(value) else None
    def flag(key):
        return body.get(key) if isinstance(body.get(key), bool) else None
    def count(key):
        value = body.get(key)
        return value if type(value) is int and 0 <= value <= 10_000_000 else None
    stage = token("stage")
    record = {
        "version": __version__, "player": _clean(player, 40), "stage": stage if stage in _STAGES else "unknown",
        "run_id": token("run_id"), "bundle_id": token("bundle_id"), "wallet": token("wallet"),
        "operations": count("operations"), "bytes": count("bytes"), "error_name": token("error_name"),
        "error": _clean(body.get("error", "")), "declined": flag("declined"), "broadcasted": flag("broadcasted"),
        "retryable": flag("retryable"), "has_tx_id": flag("has_tx_id"), "wallet_asked": flag("wallet_asked"),
    }
    # Keep the report shape useful while ensuring the account field is tagged
    # before it enters any handler (including non-redacting dev handlers).
    from .logging_setup import redact, redacting
    payload = json.dumps(record, separators=(",", ":"))
    _log.warning("client_report %s", redact(payload) if redacting() else payload)
    return Response(status_code=204)
