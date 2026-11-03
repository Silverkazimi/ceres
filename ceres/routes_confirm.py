"""Find only a Ceres-started action in the signer's own bounded recent history, and read its game verdict per op.

 fix round 1: the SERVER keeps the schedule, the budget and the clock of every own-history read ():
a per-attempt ledger (one record per signing attempt, keyed by the browser's random attempt id and bound to the exact
operations) records, by the server's own clock, when the attempt began, when the wallet answered, how many reads were
made and the one expiry read's result. Two tabs, a reload or a wrong browser clock therefore never add reads or move a
window. Reads: at most six of the account's latest operations in the ten minutes after the wallet answered, then ONE
read at the transaction's Hive expiry that pages back over the signer's OWN history only as far as the signing moment
and is valid only when the node's head is past the expiry (the newest edge) --  and its 2026-11-03 addendum.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
from time import monotonic as _monotonic   # bound at import: tests replace `time` with a frozen clock
from datetime import datetime, timezone
from http.client import HTTPSConnection
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from . import client_limits
from .routes_board import _require_same_origin
from .routes_wallet_lp import history_covers
from .security import player_for_request
from .game_lookup import lookup_trx_info
from praetoria.transport import FixedGetTransport

router = APIRouter()
TX_ID = re.compile(r"[a-fA-F0-9]{40}\Z")
ATTEMPT = re.compile(r"[a-f0-9]{8,32}\Z")
# Hive's longest transaction lifetime (3600 s) plus grace: the earliest moment absence can be final.
EXPIRY_AFTER_S = 3900
TX_LIFETIME_S = 3600
HISTORY_MARGIN_S = 120
# "a few bounded reads within about 10 minutes": six, at these offsets (s) from the wallet's answer. The first
# two are as fast as the maintainer's reference swap confirm (IN-0708), so a lost ack is no slower than the pre-fix swap.
NORMAL_OFFSETS_S = (0, 2, 4, 10, 60, 480)
WINDOW_S = 600
EXPIRY_RETRY_S = 60
MAX_HISTORY_PAGES = 50          # 5,000 of the signer's own operations between signing and expiry
LEDGER_KEEP_S = 2 * 86400
# Fides land/combine use ten alphanumeric characters; wallet routes use 16 hex
# the reviewed deed lock uses crypto.randomUUID(). All are still matched with
# the *complete* ordered operation list, never by nonce alone.
NONCE = re.compile(r"(?:[A-Za-z0-9]{10}|[a-fA-F0-9]{16}|[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})\Z")


def _canonical(operation: list, player: str) -> str | None:
    if not isinstance(operation, list) or len(operation) != 2 or operation[0] != "custom_json":
        return None
    body = operation[1]
    if not isinstance(body, dict) or not isinstance(body.get("id"), str):
        return None
    posting, active = body.get("required_posting_auths"), body.get("required_auths")
    if (posting, active) not in (([player], []), ([], [player])):
        return None
    try:
        payload = json.loads(body.get("json")) if isinstance(body.get("json"), str) else body.get("json")
    except ValueError:
        return None
    nonce = payload.get("n") if isinstance(payload, dict) else None
    if body["id"] == "sm_sps" and isinstance(payload, dict) and payload.get("action") == "token_transfer":
        nonce = payload.get("params", {}).get("n") if isinstance(payload.get("params"), dict) else None
    if not isinstance(payload, dict) or not NONCE.fullmatch(str(nonce or "")):
        return None
    return json.dumps((body["id"], payload, posting, active), sort_keys=True, separators=(",", ":"))


def find_exact_action(rows: list, player: str, expected: list) -> str | None:
    """Match the complete ordered op list, including each nonce; make no chain read."""
    wanted = [_canonical(op, player) for op in expected]
    if not wanted or len(wanted) > 5 or any(item is None for item in wanted):
        raise ValueError("Expected one to five own custom_json operations with nonces")
    grouped: dict[str, list[str]] = {}
    seen: set = set()
    for row in sorted(rows if isinstance(rows, list) else [], key=lambda r: r[0] if isinstance(r, list) and r and isinstance(r[0], int) else -1):
        if isinstance(row, list) and row and isinstance(row[0], int):
            if row[0] in seen:
                continue   # pages overlap at their edges
            seen.add(row[0])
        entry = row[1] if isinstance(row, list) and len(row) == 2 and isinstance(row[1], dict) else {}
        tx_id, op = entry.get("trx_id"), entry.get("op")
        if isinstance(tx_id, str) and TX_ID.fullmatch(tx_id):
            canonical = _canonical(op, player)
            # Any other operation in this tx makes the exact match fail.
            grouped.setdefault(tx_id.lower(), []).append(canonical or "")
    return next((tx_id for tx_id, ops in reversed(list(grouped.items())) if ops == wanted), None)


# F8: the game's record of each signed operation type (library/ceres-signing-options.md).
def expected_game_record(operation: list) -> tuple[str, object]:
    """(type, data) the game stores for one signed custom_json. `sm_sps` token transfers are stored as `token_transfer`
    with the transfer's params as data (the verified bridge shape, harvest_routing_bridge.settle); every other Ceres
    signing option (sm_land_operation, sm_combine_cards, sm_stake_change, sm_dec_powerup_region,
    sm_dec_powerdown_region, sm_claim_rewards, sm_lock_assets, sm_unlock_assets, sm_market_purchase) is stored under its
    id without the `sm_` prefix with the signed payload as data."""
    body = operation[1]
    payload = json.loads(body["json"]) if isinstance(body["json"], str) else body["json"]
    if body["id"] == "sm_sps" and isinstance(payload, dict) and payload.get("action") == "token_transfer":
        return "token_transfer", payload.get("params")
    return body["id"].removeprefix("sm_"), payload


def _decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def game_op_verdict(info: dict | None, player: str, operation: list, lookup_id: str) -> dict:
    """Classify one game's by-id record, retaining its result for the owning UI. A record whose id and player match but
    whose type or data do not is schema DRIFT: still pending (never a verdict), flagged so it is not mistaken for lag."""
    if info is None:
        return {"state": "pending", "message": "The game has not indexed this operation yet."}
    expected_type, expected_data = expected_game_record(operation)
    actual = _decoded(info.get("data"))
    result = _decoded(info.get("result"))
    if info.get("id") != lookup_id or info.get("player") != player:
        return {"state": "pending", "message": "The game record does not belong to this operation."}
    if info.get("type") != expected_type or actual != expected_data:
        return {"state": "pending", "drift": True,
                "message": f"The game stored this operation in an unexpected shape (type {info.get('type')!r}); Ceres keeps checking."}
    nested = _decoded(result.get("result")) if isinstance(result, dict) else None
    refusal = next((value for value in (info, result, nested)
                    if isinstance(value, dict) and value.get("success") is False), None)
    if refusal:
        return {"state": "refused", "error": str(refusal.get("error") or info.get("error") or "The game refused this operation"),
                "result": result}
    if info.get("success") is True or (info.get("success") is None and any(
            isinstance(value, dict) and value.get("success") is True for value in (result, nested))):
        return {"state": "confirmed", "result": result}
    return {"state": "pending", "message": "The game has not supplied a final result yet."}


@router.post("/api/confirm/verdict")
async def verdict(request: Request, player: str | None = None):
    _require_same_origin(request)
    account = player_for_request(request, player)
    client_limits.limit(request, "verify")
    try:
        body = await request.json()
        operations, tx_id = body["operations"], body["tx_id"]
        find_exact_action([], account, operations)
        if not isinstance(tx_id, str) or not TX_ID.fullmatch(tx_id):
            raise ValueError("Invalid transaction id")
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, "Invalid confirmation reference") from exc

    async def one(index: int, operation: list) -> dict:
        lookup_id = tx_id.lower() + (f"-{index}" if index else "")
        if not operation[1]["id"].startswith("sm_"):
            return {"index": index, "lookup_id": lookup_id, "state": "pending", "message": "This operation has no game result record."}
        try:
            info = await asyncio.to_thread(lookup_trx_info, FixedGetTransport(timeout=10, retries=1).get_json, lookup_id)
            state = game_op_verdict(info, account, operation, lookup_id)
        except Exception:
            state = {"state": "pending", "message": "Game lookup unavailable; checking will continue."}
        return {"index": index, "lookup_id": lookup_id, **state}
    # Astra shape 28: the operations are read together, so five slow lookups still answer within the client's bound.
    states = list(await asyncio.gather(*(one(index, operation) for index, operation in enumerate(operations))))
    return {"tx_id": tx_id.lower(), "operations": states, "server_now": int(time.time() * 1000)}


# The per-ACTION ledger (server clock, atomic budget; F6/F7; fix round 2).
# Fix round 2 (astra-review-r2 P0-1/P0-2/P1-6, aletheia-verdict-r2 MEDIUM-1/LOW-2)
# one record per ACTION (account + exact operations digest). The browser's attempt id only names attempts inside it
# a fresh attempt id never buys new reads (the read budget, the windows and the scan cursor belong to the action)
# absence ("never sent") is final only when (a) the wallet request is demonstrably CLOSED (a wallet answer that ends
# the request, reported by the browser and timed by the server's own clock -- wallet silence never counts), (b) the
# server recorded the attempt's begin BEFORE any other contact (the oldest edge is the server's own begin less a
# margin; browser times can only widen the search, never narrow it), and (c) the newest edge is proven by the SAME
# history response that starts the contiguous scan: its newest own operation is later than the latest moment the
# transaction could have been included (closure + Hive's longest lifetime). A head read from another node is never
# used. Every page must be index-contiguous and end exactly where it was asked to end
# the scan is incremental: a persisted cursor (top/bottom index and time) continues across calls, each call bounded
# in pages and wall time, so more than 5,000 own operations take more calls instead of ending discovery.
_LOCK = threading.Lock()
_MEMORY: dict[str, dict] = {}
PAGES_PER_CALL = 10            # one bounded step of the scan (about 1,000 own operations)
CALL_WALL_S = 20.0             # stop paging after this (each page's socket is bounded at 15 s): < LEASE_S
LEASE_S = 60.0                 # a running scan step blocks a second one this long (CALL_WALL_S + one socket timeout)
OPEN_RETRY_S = 15 * 60         # a request still open / no newer own operation yet: look again this much later
CONTINUE_S = 5                 # the scan has not reached the signing moment yet: the next step follows quickly
MAX_WINDOWS = 3                # ten-minute windows per action (first answer, plus at most two reopenings)
OVERLAP = 4                    # rows shared by adjacent pages, so a transaction of up to five ops is never split


def _ledger_path(request: Request) -> Path | None:
    root = getattr(getattr(getattr(getattr(request, "app", None), "state", None), "ceres_config", None), "root", None)
    return Path(root) / "confirm_ledger.json" if root else None


def _load(path: Path | None) -> dict:
    if path is None:
        return _MEMORY
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _store(path: Path | None, data: dict) -> None:
    now = time.time()
    for key in [key for key, row in data.items() if now - row.get("last_seen", row.get("first_seen", now)) > LEDGER_KEEP_S]:
        del data[key]
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def _digest(account: str, operations: list) -> str:
    return hashlib.sha256(json.dumps([account, operations], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _key(account: str, digest: str) -> str:
    return f"{account}:{digest}"


def ledger_update(request: Request, account: str, operations: list, change, *, create: bool = True):
    """Atomically read-modify-write one ACTION's record; returns change()'s value (change(None) when absent)."""
    path = _ledger_path(request)
    with _LOCK:
        data = _load(path)
        digest = _digest(account, operations)
        key = _key(account, digest)
        row = data.get(key)
        if row is None and create:
            row = data[key] = {"digest": digest, "first_seen": time.time(), "reads": 0, "windows": 0, "attempt_ids": []}
        if row is None:
            return change(None)
        row["last_seen"] = time.time()
        value = change(row)
        _store(path, data)
        return value


def _find_row(request: Request, account: str, predicate):
    path = _ledger_path(request)
    with _LOCK:
        data = _load(path)
        return next((row for key, row in data.items() if key.startswith(f"{account}:") and predicate(row)), None)


def ledger_anchor(request: Request, account: str, attempt) -> float | None:
    """The server-clock moment this attempt's wallet request was known CLOSED, or None. Only a closure is never before
    the signing (fix round 2): a begin is before it, and an answer that left the request open may precede a later one."""
    if not isinstance(attempt, str) or not ATTEMPT.fullmatch(attempt):
        return None
    row = _find_row(request, account, lambda row: attempt in row.get("attempt_ids", []))
    return row.get("closed_at") if row is not None else None


def tx_first_seen(request: Request, account: str, tx_id: str) -> float:
    """LOW-2/P1-7: the server's own first knowledge of a transaction id (a known id exists only after signing, so the
    moment the server first hears of it is never before the signing). Created on first use; never a browser time."""
    path = _ledger_path(request)
    key = f"{account}:tx:{tx_id.lower()}"
    with _LOCK:
        data = _load(path)
        row = data.setdefault(key, {"first_seen": time.time()})
        row["last_seen"] = time.time()
        _store(path, data)
        return row["first_seen"]


def ledger_absence(request: Request, account: str, attempt, operations: list) -> dict | None:
    """The server's own expiry proof for this exact action ({status, digest}), or None. The attempt id is not needed
    (MEDIUM-1: proof belongs to the action); it is accepted for compatibility."""
    if not isinstance(operations, list):
        return None
    digest = _digest(account, operations)

    def read(row):
        if row is None or row.get("digest") != digest:
            return None
        return {"status": (row.get("expiry") or {}).get("status"), "digest": digest}
    return ledger_update(request, account, operations, read, create=False)


def _body_attempt(body: dict) -> str:
    attempt = body.get("attempt")
    if not isinstance(attempt, str) or not ATTEMPT.fullmatch(attempt):
        raise HTTPException(422, "Invalid confirmation attempt")
    return attempt


def _apply_event(row: dict, body: dict, now: float, event: str, attempt: str) -> None:
    """Server-clock facts only. `begin` counts as the oldest edge only when it is the first contact of that attempt;
    `closed` (the wallet request ended with an answer) and `answered` are timed by the server; browser durations,
    expirations and `since` are never used to narrow the absence proof (LOW-2)."""
    new_attempt = attempt not in row.setdefault("attempt_ids", [])
    if new_attempt:
        row["attempt_ids"] = (row["attempt_ids"] + [attempt])[-8:]
        if len(row["attempt_ids"]) > 1:
            # A new attempt of the same action: its request is open again; the earliest edge only moves earlier (min).
            # MEDIUM-1: a fresh attempt id buys no reads by itself; a new window opens only when THIS server proved the
            # earlier attempt absent (a genuine re-sign of the same operations), and at most MAX_WINDOWS times.
            proven_absent = (row.get("expiry") or {}).get("status") == "absent"
            row.update(closed_at=None, expiry=None)
            if proven_absent and row.get("windows", 0) < MAX_WINDOWS:
                row.update(window_start=None, reads=0, answered_at=None)
        if event == "begin":
            row["begin_at"] = min(row.get("begin_at") or now, now)
            row.setdefault("begin_valid", True)
        elif row.get("begin_at") is None:
            row["begin_valid"] = False           # first contact after signing: no server-known oldest edge
    elif event == "begin" and row.get("begin_at") is None:
        row["begin_valid"] = False               # a begin that arrived after another contact proves nothing
    if body.get("reanchor") is True and (row.get("expiry") or {}).get("status") == "absent":
        # A wallet answered after own history had decided "never sent": the proof was wrong; reopen (bounded windows).
        row.update(expiry=None, closed_at=None)
        if row.get("windows", 0) < MAX_WINDOWS:
            row.update(window_start=None, reads=0, answered_at=None)
    if (event == "answered" or body.get("answered") is True) and row.get("answered_at") is None:
        row["answered_at"] = now
    if body.get("closed") is True and row.get("closed_at") is None:
        row["closed_at"] = now


@router.post("/api/confirm/intent")
async def intent(request: Request, player: str | None = None):
    """The browser announces an attempt (`begin`, sent as the wallet opens; never awaited) and the wallet's answer;
    zero outbound calls."""
    _require_same_origin(request)
    account = player_for_request(request, player)
    client_limits.limit(request, "verify")
    try:
        body = await request.json()
        operations = body["operations"]
        find_exact_action([], account, operations)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, "Invalid confirmation intent") from exc
    attempt = _body_attempt(body)
    event = body.get("event") if body.get("event") in ("begin", "answered") else "answered"
    now = time.time()
    await asyncio.to_thread(ledger_update, request, account, operations, lambda row: _apply_event(row, body, now, event, attempt))
    return {"ok": True, "server_now": int(now * 1000)}


# Own history: contiguous pages, newest edge from the same response, persisted cursor.
def _hive_rpc(method: str, params: list):
    from . import fixture_upstream
    connection = fixture_upstream.connection("api.hive.blog") if fixture_upstream.enabled() else HTTPSConnection("api.hive.blog", timeout=15)
    try:
        connection.request("POST", "/", json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}),
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(65537)
        if response.status != 200 or len(raw) > 65536:
            raise RuntimeError("Hive head unavailable")
        result = json.loads(raw)
        if result.get("error") or not isinstance(result.get("result"), dict):
            raise RuntimeError("Hive head unavailable")
        return result["result"]
    finally:
        connection.close()


def hive_head_time() -> float:
    """A node's head block time. Kept for other callers; the absence proof NEVER uses it (P0-2: a fresh head from one
    node says nothing about another node's history index)."""
    stamp = _hive_rpc("condenser_api.get_dynamic_global_properties", []).get("time")
    return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()


def _row_time(row) -> float | None:
    try:
        return datetime.fromisoformat(str(row[1].get("timestamp")).replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError, AttributeError, IndexError):
        return None


class PageShape(ValueError):
    """A history page that is not exactly the contiguous index range asked for (stale node, truncation, drift)."""


def _page(history, account: str, start: int) -> list:
    """One validated page: index-contiguous, ascending, every row timestamped; from `start` >= 0 it must end exactly at
    `start` and begin exactly at start - min(100, start). An empty or filtered page is never 'the start of history'."""
    rows = history(account, start)
    if not isinstance(rows, list) or not rows:
        raise PageShape("empty page")
    for row in rows:
        if not (isinstance(row, list) and len(row) == 2 and isinstance(row[0], int) and not isinstance(row[0], bool)
                and isinstance(row[1], dict) and _row_time(row) is not None):
            raise PageShape("malformed row")
    indexes = [row[0] for row in rows]
    if any(b != a + 1 for a, b in zip(indexes, indexes[1:])):
        raise PageShape("non-contiguous page")
    if start >= 0 and (indexes[-1] != start or indexes[0] != start - min(100, start)):
        raise PageShape("page does not cover the requested range")
    return rows


def scan_own_history(account: str, operations: list, scan: dict | None, *, earliest: float, history=None,
                     max_pages: int = PAGES_PER_CALL, wall_s: float = CALL_WALL_S, monotonic=None) -> dict:
    """One bounded step of the expiry proof. Returns {status: found|covered|partial, tx_id?, scan, newest_time}.
    `covered` = every own operation from the newest index of THIS step's latest page back to before `earliest` (less
    a margin) has been read contiguously (this step or earlier steps, by the persisted cursor). `newest_time` is the
    newest own operation's time in this step's latest page -- the newest edge comes from the same response."""
    if history is None:
        from .harvest_routing_bridge import _history as history
    clock = monotonic or _monotonic
    started = clock()
    scan = dict(scan or {})
    pages = 0

    def budget() -> bool:
        return pages < max_pages and clock() - started < wall_s

    latest = _page(history, account, -1)
    pages += 1
    found = find_exact_action(latest, account, operations)
    newest_time = _row_time(latest[-1])
    if found:
        return {"status": "found", "tx_id": found, "scan": scan, "newest_time": newest_time}
    top_now, low = latest[-1][0], latest[0][0]
    previous = latest
    if scan.get("top") is None:
        scan.update(top=top_now, bottom=low, bottom_time=_row_time(latest[0]))
    elif scan["top"] > top_now:
        raise PageShape("history went backwards (another node behind the cursor)")
    else:
        # Fill the gap between the cursor's top and this latest page (new own activity since the last step).
        while low > scan["top"] + 1:
            if not budget():
                return {"status": "partial", "scan": scan, "newest_time": newest_time}
            start = min(top_now, low - 1 + OVERLAP)
            page = _page(history, account, start)
            pages += 1
            found = find_exact_action(page + previous, account, operations)
            if found:
                return {"status": "found", "tx_id": found, "scan": scan, "newest_time": newest_time}
            previous, low = page, page[0][0]
        scan["top"] = top_now
    # Extend the bottom back to the signing moment (less a margin), or to the account's first operation.
    while scan["bottom"] > 0 and scan["bottom_time"] > earliest - HISTORY_MARGIN_S:
        if not budget():
            return {"status": "partial", "scan": scan, "newest_time": newest_time}
        start = min(scan["top"], scan["bottom"] - 1 + OVERLAP)
        page = _page(history, account, start)
        pages += 1
        found = find_exact_action(page, account, operations)
        if found:
            return {"status": "found", "tx_id": found, "scan": scan, "newest_time": newest_time}
        scan.update(bottom=page[0][0], bottom_time=_row_time(page[0]))
    return {"status": "covered", "scan": scan, "newest_time": newest_time}


def prove_own_action(account: str, operations: list, *, earliest: float, until: float | None, history=None, head=None,
                     scan: dict | None = None, closed: bool = True, max_pages: int = PAGES_PER_CALL) -> dict:
    """found / absent / unproven(reason) / partial. Absence needs: a closed wallet request (`until` known), coverage back
    to `earliest`, and the newest own operation of the same latest page later than `until`. `head` is ignored (P0-2)."""
    try:
        step = scan_own_history(account, operations, scan, earliest=earliest, history=history, max_pages=max_pages)
    except PageShape as exc:
        return {"status": "unproven", "reason": "shape", "detail": str(exc), "next_in_s": EXPIRY_RETRY_S, "scan": scan or {}}
    if step["status"] == "found":
        return {"status": "found", "tx_id": step["tx_id"], "scan": step["scan"]}
    if step["status"] == "partial":
        return {"status": "unproven", "reason": "depth", "next_in_s": CONTINUE_S, "scan": step["scan"]}
    if not closed or until is None:
        return {"status": "unproven", "reason": "open", "next_in_s": OPEN_RETRY_S, "scan": step["scan"]}
    if step["newest_time"] is None or step["newest_time"] <= until:
        return {"status": "unproven", "reason": "newest-edge", "next_in_s": OPEN_RETRY_S, "scan": step["scan"]}
    return {"status": "absent", "covered": True, "scan": step["scan"]}


def _expiry_at(row: dict) -> float:
    anchor = row.get("closed_at") or row.get("answered_at") or row.get("begin_at") or row["first_seen"]
    return anchor + EXPIRY_AFTER_S


@router.post("/api/confirm/find")
async def find(request: Request, player: str | None = None):
    """/: find one Ceres-started action in the signer's own operations, on the server's schedule and the
    ACTION's budget (aletheia-verdict-r2 MEDIUM-1)."""
    _require_same_origin(request)
    account = player_for_request(request, player)
    client_limits.limit(request, "wallet_lp")
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(422, "Invalid confirmation intent") from exc
    if not isinstance(body, dict) or not isinstance(body.get("operations"), list):
        raise HTTPException(422, "Invalid confirmation intent")
    operations = body["operations"]
    try:
        find_exact_action([], account, operations)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    attempt = _body_attempt(body)
    now = time.time()
    server_now = int(now * 1000)

    def reserve(row):
        _apply_event(row, body, now, "answered" if body.get("answered") is True else "find", attempt)
        expiry = row.get("expiry") or {}
        if expiry.get("status") in ("found", "absent"):
            return {"cached": expiry}
        if row.get("window_start") is None and row.get("windows", 0) < MAX_WINDOWS:
            row["window_start"] = now
            row["windows"] = row.get("windows", 0) + 1
        reads = row.get("reads", 0)
        if row.get("window_start") is not None and now - row["window_start"] <= WINDOW_S + 60 and reads < len(NORMAL_OFFSETS_S):
            due = row["window_start"] + NORMAL_OFFSETS_S[reads]
            if now + 0.5 < due:
                return {"wait": due - now}
            row["reads"] = reads + 1
            row.setdefault("read_times", []).append(round(now, 3))
            return {"read": "normal"}
        expiry_at = _expiry_at(row)
        if now < expiry_at:
            return {"wait": expiry_at - now}
        if expiry.get("status") == "running" and now - expiry.get("at", 0) < LEASE_S:
            return {"wait": 10}
        if expiry.get("retry_at") and now < expiry["retry_at"]:
            return {"wait": expiry["retry_at"] - now, "unproven": expiry.get("reason")}
        row["expiry"] = {"status": "running", "at": now}
        begin_ok = row.get("begin_valid") is True and row.get("begin_at") is not None
        earliest = row.get("begin_at") or row["first_seen"]
        closed = row.get("closed_at") is not None and begin_ok
        return {"read": "expiry", "earliest": earliest, "closed": closed,
                "until": row["closed_at"] + TX_LIFETIME_S if row.get("closed_at") else None, "scan": row.get("scan")}

    plan = await asyncio.to_thread(ledger_update, request, account, operations, reserve)
    if "cached" in plan:
        cached = plan["cached"]
        return {"status": cached["status"], **({"tx_id": cached["tx_id"]} if cached.get("tx_id") else {}), "server_now": server_now}
    if "wait" in plan:
        return {"status": "unproven" if plan.get("unproven") else "wait", "next_in_s": round(max(0.0, plan["wait"]), 3),
                **({"reason": plan["unproven"]} if plan.get("unproven") else {}), "server_now": server_now}
    from .harvest_routing_bridge import _history
    if plan["read"] == "normal":
        try:
            rows = await asyncio.to_thread(_history, account, -1)
        except Exception as exc:
            raise HTTPException(502, "Own account history unavailable") from exc
        tx_id = find_exact_action(rows, account, operations)
        if tx_id:
            return {"status": "found", "tx_id": tx_id, "server_now": server_now}
        nxt = await asyncio.to_thread(ledger_update, request, account, operations, lambda row: _next_in(row, time.time()))
        return {"status": "wait", "next_in_s": nxt, "covered": history_covers(rows, body.get("since")), "server_now": server_now}
    try:
        proof = await asyncio.to_thread(prove_own_action, account, operations, earliest=plan["earliest"], until=plan["until"],
                                        history=_history, scan=plan.get("scan"), closed=plan["closed"])
    except Exception:
        proof = {"status": "unproven", "reason": "unavailable", "next_in_s": EXPIRY_RETRY_S}

    def record(row):
        if proof.get("scan") is not None:
            row["scan"] = proof["scan"]
        if proof["status"] in ("found", "absent"):
            row["expiry"] = {"status": proof["status"], "tx_id": proof.get("tx_id"), "at": time.time()}
        else:
            row["expiry"] = {"status": "retry", "reason": proof.get("reason"), "retry_at": time.time() + proof.get("next_in_s", EXPIRY_RETRY_S)}

    await asyncio.to_thread(ledger_update, request, account, operations, record)
    if proof["status"] == "found":
        return {"status": "found", "tx_id": proof["tx_id"], "server_now": server_now}
    if proof["status"] == "absent":
        return {"status": "absent", "covered": True, "server_now": server_now}
    return {"status": "unproven", "reason": proof.get("reason"), "next_in_s": proof.get("next_in_s", EXPIRY_RETRY_S), "server_now": server_now}


def _next_in(row: dict, now: float) -> float:
    reads = row.get("reads", 0)
    if reads < len(NORMAL_OFFSETS_S) and row.get("window_start") is not None:
        return round(max(0.0, row["window_start"] + NORMAL_OFFSETS_S[reads] - now), 3)
    return round(max(0.0, _expiry_at(row) - now), 3)


def own_absence_since(account: str, operations: list, *, earliest: float, until: float, history=None, head=None) -> dict:
    """Shared by other confirm paths: the same bounded own-history proof (one step; callers persist nothing)."""
    return prove_own_action(account, operations, earliest=earliest, until=until, history=history, head=head)
