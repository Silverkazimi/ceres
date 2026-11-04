"""Find only a Ceres-started action in the signer's own bounded recent history, and read its game verdict per op.

: the SERVER keeps the schedule, the budget and the clock of every own-history read (). One ledger record
per ACTION (account + exact operations) holds the shared read budget and a found transaction id; each signing ATTEMPT
inside it has its own begin, answer, closure, scan cursor and expiry result (fix round 3). Two tabs, a reload or a wrong
browser clock therefore never add reads or move a window. Reads: at most eleven of the account's latest operations in
the ten minutes after the wallet answered (the first ten at the reference swap's offsets), then ONE expiry read per
attempt that pages back over the signer's OWN history to the attempt's server-recorded, pre-sign begin, and counts as
proof of absence only for a closed wallet request whose same history response shows a newer own operation than the
transaction's latest possible inclusion -- , its 2026-11-03 addendum and .
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
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
# Fix round 3 (astra-review-r3 N1): the server clock against Hive block timestamps. NOT a latency allowance: the oldest
# edge of an absence proof is a begin the server recorded BEFORE the wallet was asked (a confirmed begin token).
CLOCK_MARGIN_S = 120
# "a few bounded reads within about 10 minutes", at these offsets (s) from the wallet's answer. Fix round 3
# (astra-review-r3 N6): the first ten are exactly the maintainer's reference swap finder (IN-0708, 15c6a6da5 wallet-lp.js
# 0/2/4/8/12/18/26/36/50/70 s), so an action whose wallet answer carried no id is found no later than the pre-fix swap
# one more read at 8 min. Then the one expiry read.
NORMAL_OFFSETS_S = (0, 2, 4, 8, 12, 18, 26, 36, 50, 70, 480)
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



# The per-ACTION ledger with per-ATTEMPT proofs (server clock, atomic budget; fix rounds 2 and 3).
# One record per ACTION (account + exact operations digest) holds what the action shares: the read budget (the
# ten-minute windows and their reads) and a FOUND transaction id (the exact operations with their nonce were seen on
# Hive: true for the action whoever asked). Everything that can prove "never sent" belongs to ONE ATTEMPT (fix round 3
# astra-review-r3 N2 / aletheia-verdict-r3 L1): its begin, its answer, its closure, its scan cursor and its expiry
# result. An event or an expiry worker of one attempt never closes, scans for or settles another attempt, and a worker
# whose attempt changed while it ran (a new generation, or a newer run of the same attempt) is ignored.
# Absence ("never sent") of an attempt is final only when (fix round 3, astra-review-r3 N1)
# (a) its begin is a guaranteed PRE-SIGN bound: the browser asked the server to record the begin and opened the wallet
# only after the server's answer (with its begin token) came back. The token is presented again later; a begin
# the browser did not wait for (lost, late, after the wallet opened) has no presented token and proves nothing
# (b) its wallet request is demonstrably CLOSED (a wallet answer that ends the request, timed by the server's clock)
# (c) its own history was read contiguously from the newest own operation of one latest-page response back past the
# begin (less CLOCK_MARGIN_S: the server clock against block timestamps, not a latency allowance), and that same
# response holds an own operation later than closure + Hive's longest lifetime (the newest edge).
_LOCK = threading.Lock()
_MEMORY: dict[str, dict] = {}
PAGES_PER_CALL = 10            # one bounded step of the scan (about 1,000 own operations)
CALL_WALL_S = 20.0             # stop paging after this (each page's socket is bounded at 15 s): < LEASE_S
LEASE_S = 60.0                 # a running scan step blocks a second one this long (CALL_WALL_S + one socket timeout)
OPEN_RETRY_S = 15 * 60         # a request still open / no newer own operation yet: look again this much later
CONTINUE_S = 5                 # the scan has not reached the signing moment yet: the next step follows quickly
MAX_WINDOWS = 3                # ten-minute windows per action (first answer, plus at most two reopenings)
MAX_ATTEMPTS = 8               # attempt records kept per action (older ones are dropped: they can then prove nothing)
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
            row = data[key] = {"digest": digest, "first_seen": time.time(), "reads": 0, "windows": 0, "attempt_ids": [], "attempts": {}}
        if row is None:
            return change(None)
        row["last_seen"] = time.time()
        value = change(row)
        _store(path, data)
        return value


def _attempts(row: dict) -> dict:
    attempts = row.get("attempts")
    if not isinstance(attempts, dict):
        attempts = row["attempts"] = {}       # a round-2 record: its action-wide fields prove nothing for any attempt
    return attempts


def _attempt(row: dict, attempt: str, now: float) -> tuple[dict, bool]:
    """This attempt's own record (created on first contact). A new attempt opens a new read window only when the
    PREVIOUS attempt was proven absent by this server (a genuine re-sign), at most MAX_WINDOWS times (MEDIUM-1)."""
    attempts = _attempts(row)
    record = attempts.get(attempt)
    if record is not None:
        return record, False
    order = [item for item in row.get("attempt_ids", []) if item != attempt]
    previous = attempts.get(order[-1]) if order else None
    record = attempts[attempt] = {"first_at": now, "gen": 0}
    row["attempt_ids"] = (order + [attempt])[-MAX_ATTEMPTS:]
    for stale in [item for item in attempts if item not in row["attempt_ids"]]:
        del attempts[stale]
    if previous is not None and (previous.get("expiry") or {}).get("status") == "absent" and row.get("windows", 0) < MAX_WINDOWS:
        row.update(window_start=None, reads=0)
    return record, True


def _begin_ok(record: dict | None) -> bool:
    """N1: the begin is a pre-sign bound only when the server recorded it as the attempt's first contact AND the
    browser later presented the token it received before opening the wallet."""
    return bool(record) and record.get("begin_at") is not None and record.get("begin_confirmed") is True


def _find_row(request: Request, account: str, predicate):
    path = _ledger_path(request)
    with _LOCK:
        data = _load(path)
        return next((row for key, row in data.items() if key.startswith(f"{account}:") and isinstance(row, dict) and predicate(row)), None)


def ledger_attempt(request: Request, account: str, attempt, begin_token=None) -> dict | None:
    """One attempt's server-clock facts for the known-id inclusion probe: {closed_at, begin_at (only when it is a
    confirmed pre-sign bound)}, or None. A presented begin token confirms the begin, as on the other routes."""
    if not isinstance(attempt, str) or not ATTEMPT.fullmatch(attempt):
        return None
    path = _ledger_path(request)
    with _LOCK:
        data = _load(path)
        row = next((row for key, row in data.items() if key.startswith(f"{account}:") and isinstance(row, dict)
                    and attempt in _attempts(row)), None)
        if row is None:
            return None
        record = row["attempts"][attempt]
        if isinstance(begin_token, str) and record.get("begin_token") and hmac.compare_digest(begin_token, record["begin_token"]):
            if not record.get("begin_confirmed"):
                record["begin_confirmed"] = True
                _store(path, data)
        return {"closed_at": record.get("closed_at"), "begin_at": record.get("begin_at") if _begin_ok(record) else None}


def ledger_anchor(request: Request, account: str, attempt) -> float | None:
    """The server-clock moment THIS attempt's wallet request was known CLOSED, or None (never another attempt's)."""
    facts = ledger_attempt(request, account, attempt)
    return facts.get("closed_at") if facts else None


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
    """The server's own expiry proof for THIS attempt of this exact action ({status, digest}), or None. A found id is
    the action's (status 'found'); 'absent' is only ever the named attempt's own proof (fix round 3, N2)."""
    if not isinstance(operations, list) or not isinstance(attempt, str) or not ATTEMPT.fullmatch(attempt):
        return None
    digest = _digest(account, operations)

    def read(row):
        if row is None or row.get("digest") != digest:
            return None
        if row.get("found"):
            return {"status": "found", "digest": digest}
        record = _attempts(row).get(attempt)
        if record is None:
            return None
        return {"status": (record.get("expiry") or {}).get("status"), "digest": digest}
    return ledger_update(request, account, operations, read, create=False)


def _body_attempt(body: dict) -> str:
    attempt = body.get("attempt")
    if not isinstance(attempt, str) or not ATTEMPT.fullmatch(attempt):
        raise HTTPException(422, "Invalid confirmation attempt")
    return attempt


def _apply_event(row: dict, body: dict, now: float, event: str, attempt: str) -> dict:
    """Server-clock facts of ONE attempt. A `begin` that is the attempt's first contact is recorded and answered with a
    begin token; only a later presentation of that token makes it a pre-sign bound (N1). `closed` and `answered` are
    timed by the server and touch only this attempt (N2/L1). Browser durations, expirations and `since` never narrow
    a proof (LOW-2). Returns {begin_token} for a recorded begin."""
    record, new = _attempt(row, attempt, now)
    out: dict = {}
    if event == "begin" and new:
        token = secrets.token_hex(16)
        record.update(begin_at=now, begin_token=token)
        out["begin_token"] = token
    token = body.get("begin_token")
    if isinstance(token, str) and record.get("begin_token") and hmac.compare_digest(token, record["begin_token"]):
        record["begin_confirmed"] = True
    if body.get("reanchor") is True and (record.get("expiry") or {}).get("status") == "absent":
        # A wallet answered after own history had decided "never sent": the proof was wrong; reopen (bounded windows).
        record.update(expiry=None, closed_at=None, gen=record.get("gen", 0) + 1)
        if row.get("windows", 0) < MAX_WINDOWS:
            row.update(window_start=None, reads=0)
    if (event == "answered" or body.get("answered") is True) and record.get("answered_at") is None:
        record["answered_at"] = now
    if body.get("closed") is True and record.get("closed_at") is None:
        record["closed_at"] = now
    return out


@router.post("/api/confirm/intent")
async def intent(request: Request, player: str | None = None):
    """The browser announces an attempt's begin (awaited, with a short bound, BEFORE the wallet is asked: the answer's
    begin token is the pre-sign proof) and the wallet's answer; zero outbound calls."""
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
    out = await asyncio.to_thread(ledger_update, request, account, operations, lambda row: _apply_event(row, body, now, event, attempt))
    return {"ok": True, "server_now": int(now * 1000), **out}


# Own history: contiguous pages, newest edge from the same response, persisted covered ranges.
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


def _ranges_of(scan: dict) -> list[list]:
    """The covered index ranges [[low, high, low_time], ...], highest first. A round-2 cursor {top, bottom,
    bottom_time} is one range."""
    ranges = scan.get("ranges")
    if isinstance(ranges, list):
        return [list(item) for item in ranges if isinstance(item, list) and len(item) == 3]
    if scan.get("top") is not None and scan.get("bottom") is not None:
        return [[scan["bottom"], scan["top"], scan.get("bottom_time")]]
    return []


def _add_range(ranges: list[list], low: int, high: int, low_time) -> list[list]:
    """Merge [low, high] into the disjoint covered ranges (touching ranges join)."""
    merged = [low, high, low_time]
    rest = []
    for item in ranges:
        if item[1] + 1 < merged[0] or merged[1] + 1 < item[0]:
            rest.append(item)
            continue
        if item[0] < merged[0]:
            merged[0], merged[2] = item[0], item[2]
        merged[1] = max(merged[1], item[1])
    return sorted(rest + [merged], key=lambda item: -item[1])


def scan_own_history(account: str, operations: list, scan: dict | None, *, earliest: float, history=None,
                     max_pages: int = PAGES_PER_CALL, wall_s: float = CALL_WALL_S, monotonic=None) -> dict:
    """One bounded step of the expiry proof. Returns {status: found|covered|partial, tx_id?, scan, newest_time}.
    The scan keeps disjoint covered index ranges; every page read is merged and kept even when the step's budget ends
    in the middle of a gap (fix round 3, astra-review-r3 N7: a large new top gap can no longer be re-read for ever).
    Gaps are filled from the newest down: first new activity above the old cursor, then the old cursor's own gap, then
    back to the signing moment. `covered` = one range from this step's latest page back to before `earliest` (less
    CLOCK_MARGIN_S) or to the account's first operation. `newest_time` comes from this step's latest page."""
    if history is None:
        from .harvest_routing_bridge import _history as history
    clock = monotonic or _monotonic
    started = clock()
    scan = dict(scan or {})
    pages = 0

    def budget() -> bool:
        return pages < max_pages and clock() - started < wall_s

    def done(status, **extra):
        return {"status": status, "scan": {"ranges": ranges}, "newest_time": newest_time, **extra}

    latest = _page(history, account, -1)
    pages += 1
    newest_time = _row_time(latest[-1])
    top_now = latest[-1][0]
    ranges = _ranges_of(scan)
    if ranges and ranges[0][1] > top_now:
        raise PageShape("history went backwards (another node behind the cursor)")
    found = find_exact_action(latest, account, operations)
    ranges = _add_range(ranges, latest[0][0], top_now, _row_time(latest[0]))
    if found:
        return done("found", tx_id=found)
    while True:
        # The highest uncovered stretch below the top range: between it and the next range, or below the lowest one.
        upper = ranges[0]
        lower = ranges[1] if len(ranges) > 1 else None
        if lower is None and (upper[0] == 0 or (upper[2] is not None and upper[2] <= earliest - CLOCK_MARGIN_S)):
            return done("covered")
        if not budget():
            return done("partial")
        below = lower[1] if lower is not None else -1          # the highest index already covered below the gap
        # The page overlaps the range above by OVERLAP rows; a small gap is read in one page that also overlaps below.
        start = min(top_now, max(upper[0] - 1 + OVERLAP, below + 100 - OVERLAP))
        page = _page(history, account, start)
        pages += 1
        found = find_exact_action(page, account, operations)
        ranges = _add_range(ranges, page[0][0], page[-1][0], _row_time(page[0]))
        if found:
            return done("found", tx_id=found)


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


def _expiry_at(record: dict) -> float:
    anchor = record.get("closed_at") or record.get("answered_at") or record.get("begin_at") or record["first_at"]
    return anchor + EXPIRY_AFTER_S


@router.post("/api/confirm/find")
async def find(request: Request, player: str | None = None):
    """/: find one Ceres-started action in the signer's own operations, on the server's schedule and the
    ACTION's budget (aletheia-verdict-r2 MEDIUM-1); absence is the asking ATTEMPT's own proof (fix round 3)."""
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
        record = row["attempts"][attempt]
        if row.get("found"):
            return {"cached": {"status": "found", "tx_id": row["found"]}}
        expiry = record.get("expiry") or {}
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
        expiry_at = _expiry_at(record)
        if now < expiry_at:
            return {"wait": expiry_at - now}
        if expiry.get("status") == "running" and now - expiry.get("at", 0) < LEASE_S:
            return {"wait": 10}
        if expiry.get("retry_at") and now < expiry["retry_at"]:
            return {"wait": expiry["retry_at"] - now, "unproven": expiry.get("reason")}
        run = secrets.token_hex(8)
        record["expiry"] = {"status": "running", "at": now, "run": run}
        begin_ok = _begin_ok(record)
        # Without a confirmed pre-sign begin the scan still looks for the action (found stays possible) but can never
        # prove absence: `closed` is False, whatever the browser says.
        earliest = record["begin_at"] if begin_ok else (record.get("begin_at") or record["first_at"])
        closed = record.get("closed_at") is not None and begin_ok
        return {"read": "expiry", "earliest": earliest, "closed": closed, "run": run, "gen": record.get("gen", 0),
                "until": record["closed_at"] + TX_LIFETIME_S if record.get("closed_at") else None, "scan": record.get("scan")}

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
            await asyncio.to_thread(ledger_update, request, account, operations, lambda row: row.__setitem__("found", tx_id))
            return {"status": "found", "tx_id": tx_id, "server_now": server_now}
        nxt = await asyncio.to_thread(ledger_update, request, account, operations, lambda row: _next_in(row, attempt, time.time()))
        return {"status": "wait", "next_in_s": nxt, "covered": history_covers(rows, body.get("since")), "server_now": server_now}
    try:
        proof = await asyncio.to_thread(prove_own_action, account, operations, earliest=plan["earliest"], until=plan["until"],
                                        history=_history, scan=plan.get("scan"), closed=plan["closed"])
    except Exception:
        proof = {"status": "unproven", "reason": "unavailable", "next_in_s": EXPIRY_RETRY_S}
    settled = expiry_settle(proof, attempt, plan["run"], plan["gen"])
    applied = await asyncio.to_thread(ledger_update, request, account, operations, settled)
    if proof["status"] == "found":
        return {"status": "found", "tx_id": proof["tx_id"], "server_now": server_now}
    if proof["status"] == "absent" and applied:
        return {"status": "absent", "covered": True, "server_now": server_now}
    if proof["status"] == "absent":
        return {"status": "unproven", "reason": "stale", "next_in_s": CONTINUE_S, "server_now": server_now}
    return {"status": "unproven", "reason": proof.get("reason"), "next_in_s": proof.get("next_in_s", EXPIRY_RETRY_S), "server_now": server_now}


def expiry_settle(proof: dict, attempt: str, run: str, gen: int):
    """The expiry worker's result, applied only to the attempt and run that started it (fix round 3, N2): a worker of an
    older run (its lease expired and another run started), of an attempt that was reopened since (new generation) or of
    another attempt never records an absence. A found id is the action's, whoever found it."""
    def apply(row) -> bool:
        if proof.get("status") == "found":
            row["found"] = proof["tx_id"]
        record = _attempts(row).get(attempt) if row is not None else None
        if record is None or record.get("gen", 0) != gen or (record.get("expiry") or {}).get("run") != run:
            return False
        if proof.get("scan") is not None:
            record["scan"] = proof["scan"]
        if proof["status"] == "found":
            record["expiry"] = {"status": "found", "tx_id": proof.get("tx_id"), "at": time.time()}
        elif proof["status"] == "absent":
            if record.get("closed_at") is None or not _begin_ok(record):
                return False
            record["expiry"] = {"status": "absent", "at": time.time()}
        else:
            record["expiry"] = {"status": "retry", "reason": proof.get("reason"), "retry_at": time.time() + proof.get("next_in_s", EXPIRY_RETRY_S)}
        return True
    return apply


def _next_in(row: dict, attempt: str, now: float) -> float:
    reads = row.get("reads", 0)
    if reads < len(NORMAL_OFFSETS_S) and row.get("window_start") is not None:
        return round(max(0.0, row["window_start"] + NORMAL_OFFSETS_S[reads] - now), 3)
    record = _attempts(row).get(attempt) or {"first_at": row.get("first_seen", now)}
    return round(max(0.0, _expiry_at(record) - now), 3)


def own_absence_since(account: str, operations: list, *, earliest: float, until: float, history=None, head=None) -> dict:
    """Shared by other confirm paths: the same bounded own-history proof (one step; callers persist nothing)."""
    return prove_own_action(account, operations, earliest=earliest, until=until, history=history, head=head)
