"""Find only a Ceres-started action in the signer's own bounded recent history, and read its game verdict per op.

: the SERVER keeps the schedule, the budget and the clock of every own-history read (). One ledger record
per ACTION (account + exact operations) holds the shared read budget and a found transaction id; each signing ATTEMPT
inside it has its own begin, answer, closure, scan cursor and expiry result (fix round 3). Two tabs, a reload or a wrong
browser clock therefore never add reads or move a window. Reads: the account's latest operations at the reference swap's offsets
after the wallet answered (0-70 s, then every 15 s while the id is missing, fix round 4), then ONE expiry read per
attempt that pages back over the signer's OWN history to the attempt's server-recorded, pre-sign begin, and counts as
proof of absence only for a closed wallet request whose same history response shows a newer own operation than the
transaction's latest possible inclusion -- , its 2026-11-03 addendum and .
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
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
from .harvest_routing_bridge import _history as _PRODUCTION_HISTORY
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
# Own-history reads of an action whose wallet gave no id, at these offsets (s) from the first ask after the wallet's
# answer. Fix round 4 (astra-review-r4 : exactly the maintainer's reference swap finder (IN-0708, 15c6a6da5 wallet-lp.js
# VERIFY_SCHEDULE_MS 0/2/4/8/12/18/26/36/50/70 s, then RECHECK_MS 15 s while the id is missing), so no action is found
# later than the pre-fix swap: the ten offsets, then one read every RECHECK_S until the expiry read (: small
# deliberate, bounded reads of the signer's own latest operations).
NORMAL_OFFSETS_S = (0, 2, 4, 8, 12, 18, 26, 36, 50, 70)
RECHECK_S = 15
NORMAL_UNTIL_S = 3900           # the window of normal reads ends where the expiry read begins
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
    """Match the complete ordered op list, including each nonce; make no chain read. The newest match."""
    matches = find_exact_actions(rows, player, expected)
    return matches[-1] if matches else None


def find_exact_actions(rows: list, player: str, expected: list) -> list[str]:
    """Every transaction id whose complete ordered op list matches, oldest first (fix round 4, : two attempts of
    one action can both reach Hive)."""
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
    return [tx_id for tx_id, ops in grouped.items() if ops == wanted]


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
        if not charge_account_read(request, account, "G"):
            return {"index": index, "lookup_id": lookup_id, "state": "pending",
                    "message": "Game lookup budget is waiting for its rolling-hour window."}
        try:
            info = await asyncio.to_thread(lookup_trx_info, FixedGetTransport(timeout=10, retries=1).get_json, lookup_id)
            state = game_op_verdict(info, account, operation, lookup_id)
        except Exception:
            state = {"state": "pending", "message": "Game lookup unavailable; checking will continue."}
        return {"index": index, "lookup_id": lookup_id, **state}
    # Astra shape 28: the operations are read together, so five slow lookups still answer within the client's bound.
    states = list(await asyncio.gather(*(one(index, operation) for index, operation in enumerate(operations))))
    retain_game_verdict(request, account, operations, tx_id.lower(), states)
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
_HISTORY_POOL = ThreadPoolExecutor(max_workers=12, thread_name_prefix="ceres-confirm-history")
PAGES_PER_CALL = 10            # one bounded step of the scan (about 1,000 own operations)
CALL_WALL_S = 20.0             # stop paging after this (each page's socket is bounded at 15 s): < LEASE_S
LEASE_S = 60.0                 # a running scan step blocks a second one this long (CALL_WALL_S + one socket timeout)
OPEN_RETRY_S = 15 * 60         # a request still open / no newer own operation yet: look again this much later
CONTINUE_S = 5                 # the scan has not reached the signing moment yet: the next step follows quickly
MAX_WINDOWS = 3                # ten-minute windows per action (first answer, plus at most two reopenings)
MAX_ATTEMPTS = 8               # attempt records kept per action (older ones are dropped: they can then prove nothing)
OVERLAP = 4                    # rows shared by adjacent pages, so a transaction of up to five ops is never split
# Fix round 4 (astra-review-r4 : an attempt WITHOUT a confirmed pre-sign begin can never be proven absent, but its
# transaction may lie before the server's first record of it. Its discovery depth starts at Hive's longest lifetime
# below that first record and doubles every time the scan reaches it without a match, up to the ledger's own keep time.
TOKENLESS_DEPTH_S = EXPIRY_AFTER_S
TOKENLESS_MAX_DEPTH_S = LEDGER_KEEP_S


def normal_offset(index: int) -> float:
    """The offset (s) of the index-th normal read: the swap's ten offsets, then every RECHECK_S (fix round 4, ."""
    if index < len(NORMAL_OFFSETS_S):
        return NORMAL_OFFSETS_S[index]
    return NORMAL_OFFSETS_S[-1] + RECHECK_S * (index - len(NORMAL_OFFSETS_S) + 1)


def normal_reads_until(seconds: float) -> int:
    """How many normal reads the schedule allows in the first `seconds` of a window (tests and budget bounds)."""
    count = 0
    while normal_offset(count) <= min(seconds, NORMAL_UNTIL_S):
        count += 1
    return count


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
    for key in [key for key, row in data.items() if not key.startswith("__")
                and isinstance(row, dict) and row.get("final") is True
                and now - row.get("last_seen", row.get("first_seen", now)) > LEDGER_KEEP_S]:
        data.setdefault("__final_actions__", {})[data[key]["action_id"]] = {"at": now}
        del data[key]
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _digest(account: str, operations: list) -> str:
    return hashlib.sha256(json.dumps([account, operations], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _key(account: str, digest: str) -> str:
    return f"{account}:{digest}"


def _nonces(operations: list) -> list[str]:
    values = []
    for kind, body in operations:
        if kind != "custom_json":
            continue
        payload = json.loads(body["json"]) if isinstance(body.get("json"), str) else body.get("json")
        if not isinstance(payload, dict):
            continue
        nonce = (payload.get("params") or {}).get("n") if body.get("id") == "sm_sps" and payload.get("action") == "token_transfer" else payload.get("n")
        if isinstance(nonce, str):
            values.append(nonce)
    return values


def _semantic(operation: list, account: str) -> str | None:
    """The exact signed operation except for its designated attempt nonce."""
    if _canonical(operation, account) is None:
        return None
    body = operation[1]
    payload = json.loads(body["json"]) if isinstance(body["json"], str) else dict(body["json"])
    payload = dict(payload)
    if body["id"] == "sm_sps" and payload.get("action") == "token_transfer":
        payload["params"] = dict(payload["params"])
        payload["params"].pop("n", None)
    else:
        payload.pop("n", None)
    return json.dumps((body["id"], payload, body["required_posting_auths"], body["required_auths"]),
                      sort_keys=True, separators=(",", ":"))


def _migrate_actions(data: dict) -> None:
    """Convert digest rows without merging unrelated rows; unsafe finder ownership remains disputed."""
    migrated = {}
    for key, row in list(data.items()):
        if key.startswith("__") or not isinstance(row, dict) or "digest" not in row:
            continue
        if "action_id" in row:
            continue
        action_id = secrets.token_hex(16)
        row["action_id"] = action_id
        row["revisions"] = [{"digest": row.get("digest"), "operations": None, "revision": 1}]
        row["revision"] = 1
        row["legacy_unsafe"] = bool(row.get("found_by")) or any(
            not rec.get("operations") for rec in _attempts(row).values())
        if row["legacy_unsafe"]:
            row["disputed"] = True
            for attempt in _attempts(row).values():
                if (attempt.get("expiry") or {}).get("status") == "absent":
                    attempt["expiry"] = {"status": "retry", "reason": "migration", "retry_at": 0}
        data[f"{key.split(':', 1)[0]}:action:{action_id}"] = row
        migrated[(key.split(':', 1)[0], row.get("digest"))] = action_id
        del data[key]
    for key, owner in data.get("__nonce_owners__", {}).items():
        if isinstance(owner, list) and len(owner) == 2:
            account = key.split(":", 1)[0]
            action_id = migrated.get((account, owner[0]))
            if action_id:
                owner[0] = action_id


def _action_row(data: dict, account: str, operations: list, action_id: str | None,
                attempt: str | None, create: bool) -> tuple[str | None, dict | None]:
    digest = _digest(account, operations)
    prefix = f"{account}:action:"
    if action_id is not None:
        key = prefix + action_id
        row = data.get(key)
        if row is None:
            raise HTTPException(409, "Unknown confirmation action")
    else:
        pair = next(((key, row) for key, row in data.items() if key.startswith(prefix)
                     and isinstance(row, dict) and attempt in _attempts(row)), None) if attempt else None
        if pair is None and attempt:
            owners = data.get("__nonce_owners__", {})
            claims = [owners.get(f"{account}:{nonce}") for nonce in _nonces(operations)]
            if claims and all(isinstance(owner, list) and len(owner) == 2 and owner[1] == attempt
                              and owner == claims[0] for owner in claims):
                owner_id = claims[0][0]
                pair = next(((key, row) for key, row in data.items() if key.startswith(prefix)
                             and isinstance(row, dict) and row.get("action_id") == owner_id), None)
        key, row = pair if pair else (None, None)
        if row is None and not create:
            pair = next(((key, row) for key, row in data.items() if key.startswith(prefix)
                         and isinstance(row, dict) and row.get("digest") == digest), None)
            key, row = pair if pair else (None, None)
        if row is None and create:
            action_id = secrets.token_hex(16)
            key = prefix + action_id
            row = data[key] = {"action_id": action_id, "digest": digest, "first_seen": time.time(),
                               "revision": 0, "revisions": [], "reads": 0, "windows": 0,
                               "attempt_ids": [], "attempts": {}, "matches": {}}
    if row is not None and attempt in _attempts(row) and _attempts(row)[attempt].get("operations"):
        if _digest(account, _attempts(row)[attempt]["operations"]) != digest:
            raise HTTPException(409, "A signing attempt cannot change its reviewed operations")
    if row is not None and create and all(rev.get("digest") != digest for rev in row.get("revisions", [])):
        row["revision"] = row.get("revision", 0) + 1
        row.setdefault("revisions", []).append({"revision": row["revision"], "digest": digest,
                                                 "operations": operations})
        row["digest"] = digest
    return key, row


def ledger_update(request: Request, account: str, operations: list, change, *, create: bool = True,
                  register_attempt: str | None = None, action_id: str | None = None,
                  attempt: str | None = None, with_account: bool = False,
                  wallet_tx_id: str | None = None):
    """Atomically read-modify-write one ACTION's record; returns change()'s value (change(None) when absent)."""
    path = _ledger_path(request)
    with _LOCK:
        data = _load(path)
        before = json.dumps(data, sort_keys=True, separators=(",", ":"))
        _migrate_actions(data)
        digest = _digest(account, operations)
        key, row = _action_row(data, account, operations, action_id, attempt or register_attempt, create)
        if register_attempt is not None:
            # These tombstones outlive action-row pruning. A repeated begin for the same attempt is idempotent
            # another attempt, even with identical operations, must obtain a fresh signed nonce vector.
            owners = data.setdefault("__nonce_owners__", {})
            markers = _nonces(operations)
            if len(markers) != len(set(markers)) or any(
                    owners.get(f"{account}:{nonce}") not in (None, [digest, register_attempt],
                                                               [row["action_id"], register_attempt]) for nonce in markers):
                raise HTTPException(409, "Signed nonce already belongs to another attempt")
            for nonce in markers:
                owners[f"{account}:{nonce}"] = [row["action_id"], register_attempt]
        if row is None:
            return change(None)
        budget = data.setdefault("__accounts__", {}).setdefault(account, {"fast_calls": [], "tail_calls": [],
                                                                   "fast_remaining": {}, "inflight": {},
                                                                   "tail_last": None, "coverage_calls": [],
                                                                   "coverage_queue": [], "coverage_active": None})
        value = change(row, budget) if with_account else change(row)
        if wallet_tx_id is not None:
            tx = wallet_tx_id.lower()
            rec = _attempts(row).get(attempt)
            if (rec is None or not _begin_ok(rec) or not rec.get("operations")
                    or _digest(account, rec["operations"]) != digest):
                raise HTTPException(409, "Wallet transaction has no confirmed signing attempt")
            if rec.get("wallet_tx_id") not in (None, tx):
                row["disputed"] = True
                row["final"] = False
                data.get("__final_actions__", {}).pop(row["action_id"], None)
                _store(path, data)
                raise HTTPException(409, "Wallet returned conflicting transaction ids")
            owners = data.setdefault("__wallet_tx_owners__", {})
            owner = owners.get(f"{account}:{tx}")
            current = [row["action_id"], attempt]
            chain_owner = data.get("__tx_owners__", {}).get(f"{account}:{tx}")
            if owner not in (None, current) or chain_owner not in (None, row["action_id"]):
                row["disputed"] = True
                row["final"] = False
                data.get("__final_actions__", {}).pop(row["action_id"], None)
                for other in data.values():
                    if isinstance(other, dict) and other.get("action_id") and other.get("action_id") in (owner[0] if owner else None, chain_owner):
                        other["disputed"] = True
                        other["final"] = False
                        data.get("__final_actions__", {}).pop(other["action_id"], None)
                _store(path, data)
                raise HTTPException(409, "Wallet transaction belongs to another attempt")
            owners[f"{account}:{tx}"] = current
            rec["wallet_tx_id"] = tx
        if isinstance(value, dict) and value.pop("_reanchor_fast", False):
            rec = _attempts(row).get(attempt)
            if rec is not None and rec.get("read_mode") == "fast":
                epoch = f"{row['action_id']}:{attempt}"
                # The first anchor's unused opportunities are released; ten were
                # reserved at begin for an answer at any later instant.
                budget["fast_remaining"][epoch] = 10
        if register_attempt is not None:
            epoch = f"{row['action_id']}:{register_attempt}"
            rec = _attempts(row).get(register_attempt)
            if rec is not None and "read_mode" not in rec:
                _trim_account_budget(budget, time.time())
                reserved = sum(budget["fast_remaining"].values())
                if len(budget["fast_calls"]) + reserved + 20 <= 360:
                    rec["read_mode"] = "fast"
                    budget["fast_remaining"][epoch] = 20
                else:
                    rec["read_mode"] = "overflow"
                rec["fast_anchor"] = rec.get("begin_at", time.time())
                rec["fast_index"] = 0
            if isinstance(value, dict) and rec is not None:
                value["read_mode"] = rec.get("read_mode")
                value["overflow"] = rec.get("read_mode") == "overflow"
                if value["overflow"]:
                    value["status_line"] = "Many actions at once: checking every 15 seconds"
        if json.dumps(data, sort_keys=True, separators=(",", ":")) != before:
            row["last_seen"] = time.time()
            _store(path, data)
        return value


def _trim_account_budget(budget: dict, now: float) -> None:
    for name in ("fast_calls", "tail_calls", "coverage_calls"):
        budget[name] = [stamp for stamp in budget.get(name, []) if stamp > now - 3600]
    for token, lease in list(budget.get("inflight", {}).items()):
        if lease["at"] + CALL_WALL_S <= now:
            del budget["inflight"][token]


def charge_account_read(request: Request, account: str, category: str) -> bool:
    """Durable rolling-hour admission for one by-id or game read; no hidden retries."""
    if category not in ("V", "G"):
        raise ValueError("Invalid confirmation read category")
    path = _ledger_path(request)
    with _LOCK:
        data = _load(path)
        budget = data.setdefault("__accounts__", {}).setdefault(account, {})
        key = "by_id_calls" if category == "V" else "game_calls"
        now = time.time()
        charges = [stamp for stamp in budget.get(key, []) if stamp > now - 3600]
        if len(charges) >= 240:
            budget[key] = charges
            _store(path, data)
            return False
        charges.append(now)
        budget[key] = charges
        _store(path, data)
        return True


def _account_read_plan(row: dict, budget: dict, attempt: str, now: float) -> dict:
    """Atomically admit one due latest page; a slot or budget miss retains the due offset."""
    _trim_account_budget(budget, now)
    rec = _attempts(row)[attempt]
    epoch = f"{row['action_id']}:{attempt}"
    own_busy = any(lease["epoch"] == epoch for lease in budget["inflight"].values())
    if own_busy:
        return {"wait": 1.0}
    mode = rec.get("read_mode", "overflow")
    anchor = rec.get("fast_anchor", rec.get("begin_at", rec["first_at"]))
    index = rec.get("fast_index", 0)
    fast = mode == "fast" and index < len(NORMAL_OFFSETS_S)
    if fast:
        due = anchor + NORMAL_OFFSETS_S[index]
        if now < due:
            return {"wait": due - now}
        if len(budget["inflight"]) >= 3:
            return {"wait": 1.0}
        if not budget["fast_remaining"].get(epoch):
            return {"wait": RECHECK_S, "reason": "budget"}
        budget["fast_remaining"][epoch] -= 1
        budget["fast_calls"].append(now)
        next_index = index + 1
        while next_index < len(NORMAL_OFFSETS_S) and anchor + NORMAL_OFFSETS_S[next_index] <= now:
            next_index += 1
        rec["fast_index"] = next_index
        if next_index == len(NORMAL_OFFSETS_S):
            rec["tail_due"] = max(anchor + NORMAL_OFFSETS_S[-1] + RECHECK_S, now + RECHECK_S)
        kind = "fast"
    else:
        tail_due = rec.get("tail_due", rec.get("first_at", now))
        if now < tail_due:
            return {"wait": tail_due - now}
        last = budget.get("tail_last")
        if last is not None and now < last + RECHECK_S:
            return {"wait": last + RECHECK_S - now}
        if len(budget["inflight"]) >= 3 or any(
                lease["kind"] == "tail" for lease in budget["inflight"].values()):
            return {"wait": 1.0}
        if len(budget["tail_calls"]) >= 240:
            return {"wait": max(RECHECK_S, budget["tail_calls"][0] + 3600 - now), "reason": "budget"}
        budget["tail_calls"].append(now)
        budget["tail_last"] = now
        kind = "tail"
    token = secrets.token_hex(8)
    budget["inflight"][token] = {"epoch": epoch, "kind": kind, "at": now}
    return {"read": kind, "token": token, "overflow": mode == "overflow"}


def _coverage_plan(row: dict, budget: dict, attempt: str, now: float) -> dict:
    _trim_account_budget(budget, now)
    epoch = f"{row['action_id']}:{attempt}"
    queue = budget.setdefault("coverage_queue", [])
    if epoch not in queue:
        queue.append(epoch)
    active = budget.get("coverage_active")
    if active and active.get("at", 0) + CALL_WALL_S <= now:
        budget["coverage_active"] = None
        active = None
    if queue[0] != epoch or active:
        return {"wait": CONTINUE_S, "reason": "coverage-queue"}
    if len(budget.setdefault("coverage_calls", [])) >= 240:
        return {"wait": budget["coverage_calls"][0] + 3600 - now, "reason": "coverage-budget"}
    run = secrets.token_hex(8)
    budget["coverage_active"] = {"epoch": epoch, "at": now, "run": run}
    return {"run": run, "epoch": epoch}


def _coverage_history(request: Request, account: str, run: str, deadline: float, start: int):
    """Charge each actual page at admission; stale jobs cannot issue another RPC."""
    path = _ledger_path(request)
    with _LOCK:
        data = _load(path)
        budget = data.get("__accounts__", {}).get(account, {})
        active = budget.get("coverage_active")
        now = time.time()
        _trim_account_budget(budget, now)
        if now >= deadline or not active or active.get("run") != run:
            raise TimeoutError("Coverage job expired")
        if len(budget["coverage_calls"]) >= 240:
            raise RuntimeError("Coverage budget exhausted")
        budget["coverage_calls"].append(now)
        _store(path, data)
    from .harvest_routing_bridge import _history
    return _history(account, start)


def _finish_coverage(request: Request, account: str, run: str, *, requeue: bool) -> bool:
    path = _ledger_path(request)
    with _LOCK:
        data = _load(path)
        budget = data.get("__accounts__", {}).get(account, {})
        if not _coverage_release(budget, run, requeue=requeue):
            return False
        _store(path, data)
        return True


def _coverage_release(budget: dict, run: str, *, requeue: bool) -> bool:
    active = budget.get("coverage_active")
    if not active or active.get("run") != run:
        return False
    queue = budget.get("coverage_queue", [])
    epoch = active["epoch"]
    if epoch in queue:
        queue.remove(epoch)
    if requeue:
        queue.append(epoch)
    budget["coverage_active"] = None
    return True


def _finish_account_read(request: Request, account: str, token: str) -> bool:
    """One publication fence: a deadline-released generation can never publish."""
    path = _ledger_path(request)
    with _LOCK:
        data = _load(path)
        budget = data.get("__accounts__", {}).get(account, {})
        lease = budget.get("inflight", {}).pop(token, None)
        if lease is None or time.time() > lease["at"] + CALL_WALL_S:
            _store(path, data)
            return False
        _store(path, data)
        return True


def _attempts(row: dict) -> dict:
    attempts = row.get("attempts")
    if not isinstance(attempts, dict):
        attempts = row["attempts"] = {}       # a round-2 record: its action-wide fields prove nothing for any attempt
    return attempts


def _attempt(row: dict, attempt: str, now: float, *, begin: bool = False) -> tuple[dict, bool]:
    """This attempt's own record (created on first contact). A new attempt opens a new read window when it is a
    genuine re-sign -- its first contact is the pre-sign begin (fix round 4, astra-review-r4 : a retry after "your
    wallet never asked you" gets the swap's fast reads again) or the PREVIOUS attempt was proven absent by this server
    -- at most MAX_WINDOWS times per action (MEDIUM-1: an attempt id alone still buys no reads)."""
    attempts = _attempts(row)
    record = attempts.get(attempt)
    if record is not None:
        return record, False
    order = [item for item in row.get("attempt_ids", []) if item != attempt]
    previous = attempts.get(order[-1]) if order else None
    record = attempts[attempt] = {"first_at": now, "gen": 0}
    # Pending evidence and old attempts remain until action finality. An arbitrary count cap
    # would let a later retry erase the earlier transaction's safety evidence.
    row["attempt_ids"] = order + [attempt]
    resign = begin or (previous is not None and (previous.get("expiry") or {}).get("status") == "absent")
    if previous is not None and resign and row.get("window_start") is not None and row.get("windows", 0) < MAX_WINDOWS:
        row.update(window_start=None, reads=0, window_attempt=None)
    return record, True


def _found_ids(row: dict) -> dict:
    """Fix round 4 (astra-review-r4 : the transaction id each attempt of the action was given by a server read."""
    found = row.get("found_by")
    if not isinstance(found, dict):
        found = row["found_by"] = {}
    return found


def _excluded(row: dict, attempt: str, body: dict | None = None) -> tuple[set, set]:
    """(ids this attempt may not be given, ids the SERVER gave other attempts). An id the server gave to ANOTHER attempt
    of this action is that attempt's outcome: it is neither found for this attempt nor does it keep this attempt from
    being proven absent (two attempts of one action can both reach Hive, . The browser may add ids it knows belong
    to other attempts (an id its wallet returned); those only narrow discovery and never help prove absence."""
    server = {tx for other, tx in _found_ids(row).items() if other != attempt and isinstance(tx, str)}
    client = set()
    for tx in (body or {}).get("exclude_tx_ids") or []:
        if isinstance(tx, str) and TX_ID.fullmatch(tx):
            client.add(tx.lower())
    return server | client, server


def _unanswered_for(record: dict, now: float) -> float | None:
    """Fix round 4 (aletheia-verdict-r4 1 and 2): seconds by the SERVER's clock since its first record of this attempt
    (the pre-sign begin when it arrived in time; never earlier than the real signing request), while no wallet answer
    and no closure is recorded for it; None once one is. The browser's IN-0888 "never asked" lifetime ending is
    measured with this, never with the browser's own clock."""
    if record.get("answered_at") is not None or record.get("closed_at") is not None:
        return None
    return round(max(0.0, now - record["first_at"]), 3)


def _begin_ok(record: dict | None) -> bool:
    """N1: the begin is a pre-sign bound only when the server recorded it as the attempt's first contact AND the
    browser later presented the token it received before opening the wallet."""
    return bool(record) and record.get("begin_at") is not None and record.get("begin_confirmed") is True


def _find_row(request: Request, account: str, predicate):
    path = _ledger_path(request)
    with _LOCK:
        data = _load(path)
        return next((row for key, row in data.items() if key.startswith(f"{account}:") and isinstance(row, dict) and predicate(row)), None)


def _history_candidates(rows: list, account: str, revisions: list[dict], first_begin: float) -> list[dict]:
    """Keep every covered candidate and its index, time, and comparison provenance."""
    grouped: dict[str, dict] = {}
    for index, entry in rows if isinstance(rows, list) else []:
        if not isinstance(entry, dict) or not isinstance(index, int):
            continue
        tx = entry.get("trx_id")
        stamp = _row_time([index, entry])
        if not isinstance(tx, str) or not TX_ID.fullmatch(tx) or stamp is None:
            continue
        item = grouped.setdefault(tx.lower(), {"tx_id": tx.lower(), "indexes": [], "times": [],
                                               "exact": [], "semantic": []})
        item["indexes"].append(index)
        item["times"].append(stamp)
        item.setdefault("ops", []).append(entry.get("op"))
    for item in grouped.values():
        item["time"] = min(item.pop("times"))
        ops = item.pop("ops")
        item["signed_operations"] = ops
        item["operation_fingerprints"] = [_canonical(op, account) for op in ops]
        for rev in revisions:
            expected = rev.get("operations")
            if not expected or len(expected) != len(ops):
                continue
            exact = [_canonical(op, account) for op in ops] == [_canonical(op, account) for op in expected]
            semantic = [_semantic(op, account) for op in ops] == [_semantic(op, account) for op in expected]
            if exact:
                item["exact"].append(rev["revision"])
            if semantic:
                item["semantic"].append(rev["revision"])
        item["eligible"] = item["time"] >= first_begin - CLOCK_MARGIN_S
    return [item for item in grouped.values() if item["exact"] or item["semantic"]]


def _final_other_owner(request: Request, account: str, action_id: str, tx_id: str) -> bool:
    """Read-only ownership check for a staged scan, before any proof is published."""
    path = _ledger_path(request)
    with _LOCK:
        data = _load(path)
        owner = data.get("__tx_owners__", {}).get(f"{account}:{tx_id}")
        if not owner or owner == action_id:
            return False
        return owner in data.get("__final_actions__", {}) or any(
            isinstance(row, dict) and row.get("action_id") == owner and row.get("final") is True
            for row in data.values())


def retain_candidates(request: Request, account: str, operations: list, attempt: str,
                      rows: list, *, action_id: str | None = None, fence: tuple | None = None) -> dict:
    """Atomically retain candidate provenance and one conclusive tx maintainer per account."""
    path = _ledger_path(request)
    with _LOCK:
        data = _load(path)
        _migrate_actions(data)
        _, row = _action_row(data, account, operations, action_id, attempt, False)
        if row is None:
            return {"matches": [], "action_id": None}
        if fence is not None:
            run, gen, deadline = fence
            rec = _attempts(row).get(attempt, {})
            if (time.time() > deadline or rec.get("gen", 0) != gen
                    or (rec.get("expiry") or {}).get("run") != run):
                return {"matches": [], "action_id": row["action_id"], "stale": True}
        first_begin = min((r.get("begin_at", r.get("first_at", row["first_seen"]))
                           for r in _attempts(row).values()), default=row["first_seen"])
        candidates = _history_candidates(rows, account, row.get("revisions", []), first_begin)
        owners = data.setdefault("__tx_owners__", {})
        matches = row.setdefault("matches", {})
        for candidate in candidates:
            tx = candidate["tx_id"]
            owner_key = f"{account}:{tx}"
            owner = owners.get(owner_key)
            callback_owner = data.get("__wallet_tx_owners__", {}).get(owner_key)
            eligible_attempts = [attempt_id for attempt_id, rec in _attempts(row).items()
                                 if rec.get("operations") and
                                 [_canonical(op, account) for op in rec["operations"]]
                                 == candidate["operation_fingerprints"]]
            candidate["attempt_id"] = eligible_attempts[0] if len(eligible_attempts) == 1 else None
            if callback_owner and callback_owner != [row["action_id"], candidate["attempt_id"]]:
                row["disputed"] = True
                row["final"] = False
                data.get("__final_actions__", {}).pop(row["action_id"], None)
                for other in data.values():
                    if isinstance(other, dict) and other.get("action_id") == callback_owner[0]:
                        other["disputed"] = True
                        other["final"] = False
                        data.get("__final_actions__", {}).pop(other["action_id"], None)
            if owner is None and candidate["exact"] and candidate["eligible"] and candidate["attempt_id"] and not row.get("disputed"):
                owners[owner_key] = row["action_id"]
                owner = row["action_id"]
            elif owner and owner != row["action_id"] and candidate["exact"]:
                row["disputed"] = True
                row["final"] = False
                data.get("__final_actions__", {}).pop(row["action_id"], None)
                for other in data.values():
                    if isinstance(other, dict) and other.get("action_id") == owner:
                        other["disputed"] = True
                        other["final"] = False
                        data.get("__final_actions__", {}).pop(other["action_id"], None)
            candidate["owner"] = owner
            candidate["excluded_final_owner"] = bool(owner and owner != row["action_id"]
                                                     and (owner in data.get("__final_actions__", {})
                                                          or any(other.get("action_id") == owner and other.get("final") is True
                                                                 for other in data.values() if isinstance(other, dict))))
            previous = matches.get(tx, {})
            candidate["indexes"] = sorted(set(previous.get("indexes", []) + candidate["indexes"]))
            candidate["time"] = min(candidate["time"], previous.get("time", candidate["time"]))
            candidate["verdicts"] = previous.get("verdicts") or data.get("__pending_verdicts__", {}).get(f"{account}:{tx}")
            matches[tx] = candidate
        active_match = next((tx for tx, item in matches.items()
                             if item.get("eligible") and not item.get("excluded_final_owner")), None)
        if active_match:
            row["final"] = False
            row["found"] = active_match
            account_budget = data.get("__accounts__", {}).get(account, {})
            for known_attempt in _attempts(row):
                account_budget.get("fast_remaining", {}).pop(f"{row['action_id']}:{known_attempt}", None)
            for rec in _attempts(row).values():
                if (rec.get("expiry") or {}).get("status") == "absent":
                    rec["expiry"] = {"status": "retracted", "reason": "retained-match"}
        elif not row.get("legacy_unsafe"):
            row.pop("found", None)
        _store(path, data)
        return {"matches": list(matches.values()), "action_id": row["action_id"],
                "found": row.get("found")}


def retain_game_verdict(request: Request, account: str, operations: list, tx_id: str, states: list) -> None:
    """Keep each distinct transaction's game result, including refusals and duplicates."""
    path = _ledger_path(request)
    with _LOCK:
        data = _load(path)
        data.setdefault("__pending_verdicts__", {})[f"{account}:{tx_id}"] = states
        digest = _digest(account, operations)
        for key, row in data.items():
            if not key.startswith(f"{account}:action:") or not isinstance(row, dict):
                continue
            if tx_id in row.get("matches", {}) or any(rev.get("digest") == digest for rev in row.get("revisions", [])):
                if tx_id in row.get("matches", {}):
                    row["matches"][tx_id]["verdicts"] = states
                    _refresh_action_finality(row)
        _store(path, data)


def _refresh_action_finality(row: dict) -> bool:
    """A final maintainer has no open prompt, dispute, uncovered expiry, or pending verdict."""
    if row.get("disputed") or not _attempts(row):
        row["final"] = False
        return False
    for rec in _attempts(row).values():
        expiry = rec.get("expiry") or {}
        if rec.get("closed_at") is None or expiry.get("status") not in ("absent", "found"):
            row["final"] = False
            return False
        if not expiry.get("covered"):
            row["final"] = False
            return False
    for candidate in row.get("matches", {}).values():
        if not candidate.get("eligible") or candidate.get("excluded_final_owner"):
            continue
        verdicts = candidate.get("verdicts") or []
        if not verdicts or any(op.get("state") not in ("confirmed", "refused") for op in verdicts):
            row["final"] = False
            return False
    row["final"] = True
    return True


def retain_account_page(request: Request, account: str, rows: list) -> None:
    """One shared tail response is checked against every open action of the account."""
    path = _ledger_path(request)
    with _LOCK:
        data = _load(path)
        targets = [(row["action_id"], rev["operations"], next(iter(_attempts(row)), None))
                   for key, row in data.items() if key.startswith(f"{account}:action:")
                   and isinstance(row, dict) and not row.get("final")
                   for rev in row.get("revisions", []) if rev.get("operations") and _attempts(row)]
    for action_id, operations, attempt in targets:
        retain_candidates(request, account, operations, attempt, rows, action_id=action_id)


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
        active_match = bool(row.get("found") or any(item.get("eligible") and not item.get("excluded_final_owner")
                                                  for item in row.get("matches", {}).values()))
        return {"closed_at": record.get("closed_at"),
                "begin_at": record.get("begin_at") if _begin_ok(record) else None,
                "action_id": row.get("action_id"), "action_match": active_match}


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
        if row is None or not any(rev.get("digest") == digest for rev in row.get("revisions", [])):
            return None
        if row.get("found") or any(item.get("eligible") and not item.get("excluded_final_owner")
                                   for item in row.get("matches", {}).values()):
            return {"status": "found", "digest": digest}
        record = _attempts(row).get(attempt)
        if record is None:
            return None
        return {"status": (record.get("expiry") or {}).get("status"), "digest": digest}
    return ledger_update(request, account, operations, read, create=False, attempt=attempt)


def ledger_routing_release(request: Request, account: str, operations: list) -> dict | None:
    """The sole server-owned release proof for an id-less held routing claim."""
    if not isinstance(operations, list):
        return None
    digest = _digest(account, operations)
    path = _ledger_path(request)
    with _LOCK:
        data = _load(path)
        _migrate_actions(data)
        rows = [row for key, row in data.items() if key.startswith(f"{account}:action:")
                and isinstance(row, dict) and any(rev.get("digest") == digest for rev in row.get("revisions", []))]
        if len(rows) != 1:
            return None
        row = rows[0]
        if row.get("disputed") or row.get("found") or any(
                item.get("eligible") and not item.get("excluded_final_owner")
                for item in row.get("matches", {}).values()):
            return None
        attempts = list(_attempts(row).values())
        if not attempts or any(not _begin_ok(rec) or rec.get("wallet_tx_id")
                               or not rec.get("verified_envelope_expiry")
                               or (rec.get("expiry") or {}).get("status") != "absent"
                               or not (rec.get("expiry") or {}).get("covered") for rec in attempts):
            return None
        return {"status": "absent", "digest": digest, "action_id": row["action_id"],
                "verified_expiry": min((rec["verified_envelope_expiry"] for rec in attempts
                                        if rec.get("verified_envelope_expiry")), default=None)}


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
    record, new = _attempt(row, attempt, now, begin=event == "begin")
    out: dict = {"action_id": row["action_id"], "revision": row.get("revision", 1)}
    if event == "begin" and new:
        record["revision"] = row.get("revision", 1)
        record["digest"] = _digest(body.get("account", ""), body["operations"]) if body.get("account") else row["digest"]
        record["operations"] = body["operations"]
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
        if record.get("read_mode") == "fast":
            record["fast_anchor"] = now
            record["fast_index"] = 0
            out["_reanchor_fast"] = True
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
    action_id = body.get("action_id")
    if action_id is not None and (not isinstance(action_id, str) or not re.fullmatch(r"[a-f0-9]{32}", action_id)):
        raise HTTPException(422, "Invalid action id")
    wallet_tx_id = body.get("wallet_tx_id")
    if wallet_tx_id is not None and (not isinstance(wallet_tx_id, str) or not TX_ID.fullmatch(wallet_tx_id)):
        raise HTTPException(422, "Invalid wallet transaction id")
    out = ledger_update(request, account, operations, lambda row: _apply_event(row, body, now, event, attempt),
                        register_attempt=attempt if event == "begin" else None, action_id=action_id,
                        attempt=attempt, with_account=False, wallet_tx_id=wallet_tx_id)
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
                     max_pages: int = PAGES_PER_CALL, wall_s: float = CALL_WALL_S, monotonic=None,
                     exclude: set | None = None, absence_exclude: set | None = None,
                     on_page=None) -> dict:
    """One bounded step of the expiry proof. Returns {status: found|covered|partial, tx_id?, scan, newest_time}.
    The scan keeps disjoint covered index ranges; every page read is merged and kept even when the step's budget ends
    in the middle of a gap (fix round 3, astra-review-r3 N7: a large new top gap can no longer be re-read for ever).
    Gaps are filled from the newest down: first new activity above the old cursor, then the old cursor's own gap, then
    back to the signing moment. `covered` = one range from this step's latest page back to before `earliest` (less
    CLOCK_MARGIN_S) or to the account's first operation. `newest_time` comes from this step's latest page.
    Fix round 4 (: a match in `exclude` (another attempt's id) is not found for this attempt; a match outside
    `absence_exclude` (the ids the SERVER gave other attempts) still keeps the scan from ending `covered`."""
    if history is None:
        from .harvest_routing_bridge import _history as history
    clock = monotonic or _monotonic
    started = clock()
    scan = dict(scan or {})
    pages = 0

    def budget() -> bool:
        return pages < max_pages and clock() - started < wall_s

    exclude = set(exclude or ())
    absence_exclude = set(absence_exclude or ())
    seen_other = []
    seen_found = []

    def match(page):
        if on_page is not None:
            retained = on_page(page)
            found = retained.get("found")
            if found and found not in seen_found:
                seen_found.append(found)
            return found
        ids = find_exact_actions(page, account, operations)
        seen_other.extend(tx for tx in ids if tx in exclude and tx not in absence_exclude)
        return next((tx for tx in reversed(ids) if tx not in exclude), None)

    def done(status, **extra):
        if status == "covered" and seen_other:
            status = "partial-other"     # a match only the browser attributed elsewhere: never proof of absence
        if on_page is not None and seen_found:
            extra.update(tx_id=seen_found[0], covered=status == "covered", found_ids=list(seen_found))
            status = "found"
        return {"status": status, "scan": {"ranges": ranges}, "newest_time": newest_time, **extra}

    latest = _page(history, account, -1)
    pages += 1
    newest_time = _row_time(latest[-1])
    top_now = latest[-1][0]
    ranges = _ranges_of(scan)
    if ranges and ranges[0][1] > top_now:
        raise PageShape("history went backwards (another node behind the cursor)")
    found = match(latest)
    ranges = _add_range(ranges, latest[0][0], top_now, _row_time(latest[0]))
    if found and on_page is None:
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
        found = match(page)
        ranges = _add_range(ranges, page[0][0], page[-1][0], _row_time(page[0]))
        if found and on_page is None:
            return done("found", tx_id=found)


def prove_own_action(account: str, operations: list, *, earliest: float, until: float | None, history=None, head=None,
                     scan: dict | None = None, closed: bool = True, max_pages: int = PAGES_PER_CALL,
                     exclude: set | None = None, absence_exclude: set | None = None, on_page=None) -> dict:
    """found / absent / unproven(reason) / partial. Absence needs: a closed wallet request (`until` known), coverage back
    to `earliest`, and the newest own operation of the same latest page later than `until`. `head` is ignored (P0-2)."""
    try:
        step = scan_own_history(account, operations, scan, earliest=earliest, history=history, max_pages=max_pages,
                                exclude=exclude, absence_exclude=absence_exclude, on_page=on_page)
    except PageShape as exc:
        return {"status": "unproven", "reason": "shape", "detail": str(exc), "next_in_s": EXPIRY_RETRY_S, "scan": scan or {}}
    if step["status"] == "found":
        return {"status": "found", "tx_id": step["tx_id"], "scan": step["scan"],
                "covered": step.get("covered", False), "found_ids": step.get("found_ids", [step["tx_id"]])}
    if step["status"] == "partial":
        return {"status": "unproven", "reason": "depth", "next_in_s": CONTINUE_S, "scan": step["scan"]}
    if step["status"] == "partial-other":
        return {"status": "unproven", "reason": "open", "next_in_s": OPEN_RETRY_S, "scan": step["scan"]}
    if not closed or until is None:
        return {"status": "unproven", "reason": "open", "next_in_s": OPEN_RETRY_S, "scan": step["scan"]}
    if step["newest_time"] is None or step["newest_time"] <= until:
        return {"status": "unproven", "reason": "newest-edge", "next_in_s": OPEN_RETRY_S, "scan": step["scan"]}
    return {"status": "absent", "covered": True, "scan": step["scan"]}


def _expiry_at(record: dict) -> float:
    if record.get("closed_at") is None and record.get("verified_envelope_expiry"):
        return record["verified_envelope_expiry"] + CLOCK_MARGIN_S
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
    action_id = body.get("action_id")
    if action_id is not None and (not isinstance(action_id, str) or not re.fullmatch(r"[a-f0-9]{32}", action_id)):
        raise HTTPException(422, "Invalid action id")

    root = getattr(getattr(request.app.state, "ceres_config", None), "root", None)
    verified_expiry = None
    if root is not None:
        from . import routing_runs_v2
        verified_expiry = await asyncio.to_thread(routing_runs_v2.verified_envelope_expiry, root, account, operations)
    def reserve(row, budget):
        event_result = _apply_event(row, body, now, "answered" if body.get("answered") is True else "find", attempt)
        record = row["attempts"][attempt]
        if (verified_expiry is not None and record.get("begin_at") is not None
                and verified_expiry > record["begin_at"]):
            record["verified_envelope_expiry"] = verified_expiry
        if "read_mode" not in record:
            _trim_account_budget(budget, now)
            epoch = f"{row['action_id']}:{attempt}"
            if len(budget["fast_calls"]) + sum(budget["fast_remaining"].values()) + 20 <= 360:
                record["read_mode"] = "fast"
                budget["fast_remaining"][epoch] = 20
            else:
                record["read_mode"] = "overflow"
            record["fast_anchor"] = record.get("first_at", now)
            record["fast_index"] = 0
        if event_result.get("_reanchor_fast"):
            budget["fast_remaining"][f"{row['action_id']}:{attempt}"] = 10
        unanswered = _unanswered_for(record, now)
        silent = {"unanswered_for_s": unanswered} if unanswered is not None else {}
        common = {"action_id": row["action_id"], "overflow": record.get("read_mode") == "overflow",
                  "read_mode": record.get("read_mode", "overflow"), **silent}
        excluded, proof_excluded = _excluded(row, attempt, body)
        if row.get("matches"):
            matched_ids = [tx for tx, item in row["matches"].items()
                           if item.get("eligible") and not item.get("excluded_final_owner")]
            retained = next((tx for tx, item in row["matches"].items() if item.get("eligible")
                             and not item.get("excluded_final_owner")), None)
            if retained and now < _expiry_at(record):
                return {"cached": {"status": "found", "tx_id": retained,
                                    "matched_tx_ids": matched_ids,
                                    "matched_transactions": [{"tx_id": tx, "operations": row["matches"][tx].get("signed_operations")}
                                                             for tx in matched_ids],
                                    "watch_duplicates": True, "next_in_s": RECHECK_S}, **common}
        own = _found_ids(row).get(attempt)
        if own and now < _expiry_at(record):
            return {"cached": {"status": "found", "tx_id": own,
                                "watch_duplicates": True, "next_in_s": RECHECK_S}, **common}
        if row.get("found") and row["found"] not in excluded and now < _expiry_at(record):
            return {"cached": {"status": "found", "tx_id": row["found"],
                                "watch_duplicates": True, "next_in_s": RECHECK_S}, **common}
        expiry = record.get("expiry") or {}
        if expiry.get("status") == "absent" or (expiry.get("status") == "found" and expiry.get("covered")
                                                 and expiry.get("tx_id") not in excluded):
            return {"cached": expiry, **common}
        expiry_at = _expiry_at(record)
        if now < expiry_at:
            return {**_account_read_plan(row, budget, attempt, now), "excluded": sorted(excluded), **common}
        if expiry.get("status") == "running" and now - expiry.get("at", 0) < LEASE_S:
            return {"wait": 10, **common}
        if expiry.get("retry_at") and now < expiry["retry_at"]:
            return {"wait": expiry["retry_at"] - now, "unproven": expiry.get("reason"), **common}
        slot = _coverage_plan(row, budget, attempt, now)
        if "wait" in slot:
            return {**slot, **common}
        run = slot["run"]
        record["expiry"] = {"status": "running", "at": now, "run": run}
        begin_ok = _begin_ok(record)
        # Without a confirmed pre-sign begin the scan still looks for the action (found stays possible) but can never
        # prove absence: `closed` is False, whatever the browser says. Its discovery reaches below the server's first
        # record of the attempt, deeper on every pass that did not find it (fix round 4, .
        first = record.get("begin_at") or record["first_at"]
        earliest = record["begin_at"] if begin_ok else first - record.get("discovery_depth_s", TOKENLESS_DEPTH_S)
        closed = (record.get("closed_at") is not None or
                  (record.get("verified_envelope_expiry") is not None
                   and now >= record["verified_envelope_expiry"] + CLOCK_MARGIN_S)) and begin_ok
        return {"read": "expiry", "earliest": earliest, "closed": closed, "run": run, "gen": record.get("gen", 0),
                "begin_ok": begin_ok, "excluded": sorted(excluded), "proof_excluded": sorted(proof_excluded),
                "until": (record["closed_at"] + TX_LIFETIME_S if record.get("closed_at") else
                          record.get("verified_envelope_expiry")), "scan": record.get("scan"), **common}

    plan = ledger_update(request, account, operations, reserve,
                         action_id=action_id, attempt=attempt, with_account=True)
    silent = {"unanswered_for_s": plan["unanswered_for_s"]} if plan.get("unanswered_for_s") is not None else {}
    identity = {"action_id": plan.get("action_id"), "overflow": plan.get("overflow", False),
                "read_mode": plan.get("read_mode", "overflow")}
    if identity["overflow"]:
        identity["status_line"] = "Many actions at once: checking every 15 seconds"
    if "cached" in plan:
        cached = plan["cached"]
        return {"status": cached["status"], **({"tx_id": cached["tx_id"]} if cached.get("tx_id") else {}),
                **({"matched_tx_ids": cached["matched_tx_ids"]} if cached.get("matched_tx_ids") else {}),
                **({"matched_transactions": cached["matched_transactions"]} if cached.get("matched_transactions") else {}),
                **({"watch_duplicates": True, "next_in_s": RECHECK_S} if cached.get("watch_duplicates") else {}),
                "server_now": server_now, **silent, **identity}
    if "wait" in plan:
        return {"status": "unproven" if plan.get("unproven") else "wait", "next_in_s": round(max(0.0, plan["wait"]), 3),
                **({"reason": plan["unproven"]} if plan.get("unproven") else {}), "server_now": server_now,
                **silent, **identity}
    from .harvest_routing_bridge import _history
    excluded = set(plan.get("excluded") or [])
    if plan["read"] in ("fast", "tail"):
        try:
            # The injected offline transport used by the virtual-clock suite is
            # synchronous and bounded; the real socket read uses the fenced pool.
            if _history is _PRODUCTION_HISTORY:
                rows = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(
                    _HISTORY_POOL, _history, account, -1), timeout=CALL_WALL_S)
            else:
                rows = _history(account, -1)
        except Exception as exc:
            _finish_account_read(request, account, plan["token"])
            raise HTTPException(502, "Own account history unavailable") from exc
        if not _finish_account_read(request, account, plan["token"]):
            return {"status": "unproven", "reason": "deadline", "next_in_s": RECHECK_S,
                    "server_now": server_now, **silent, **identity}
        if plan["read"] == "tail":
            retain_account_page(request, account, rows)
        retained = retain_candidates(request, account, operations, attempt, rows,
                                     action_id=action_id)
        tx_id = retained.get("found")
        if tx_id:
            return {"status": "found", "tx_id": tx_id, "server_now": server_now, **silent,
                    **identity, "watch_duplicates": True,
                    "matched_tx_ids": [item["tx_id"] for item in retained.get("matches", [])
                                       if item.get("eligible") and not item.get("excluded_final_owner")],
                    "matched_transactions": [{"tx_id": item["tx_id"], "operations": item.get("signed_operations")}
                                             for item in retained.get("matches", [])
                                             if item.get("eligible") and not item.get("excluded_final_owner")],
                    "next_in_s": RECHECK_S}
        nxt = ledger_update(request, account, operations, lambda row: _next_in(row, attempt, time.time()),
                            attempt=attempt)
        return {"status": "wait", "next_in_s": max(0.5, min(RECHECK_S, nxt)),
                "covered": history_covers(rows, body.get("since")), "server_now": server_now, **silent, **identity}
    try:
        snapshot = ledger_update(request, account, operations,
            lambda row: {"action_id": row["action_id"], "revisions": list(row.get("revisions", [])),
                         "first_begin": min((rec.get("begin_at", rec.get("first_at", row["first_seen"]))
                                             for rec in _attempts(row).values()), default=row["first_seen"])},
            create=False, action_id=action_id, attempt=attempt)
        staged_pages = []
        def stage(page):
            staged_pages.append(page)
            candidates = _history_candidates(page, account, snapshot["revisions"], snapshot["first_begin"])
            return {"found": next((item["tx_id"] for item in candidates if item["eligible"]
                                    and not _final_other_owner(request, account, snapshot["action_id"],
                                                               item["tx_id"])), None)}
        proof = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(
            _HISTORY_POOL, lambda: prove_own_action(
                account, operations, earliest=plan["earliest"], until=plan["until"],
                history=lambda a, start: _coverage_history(request, a, plan["run"], now + CALL_WALL_S, start),
                scan=plan.get("scan"), closed=plan["closed"], max_pages=2,
                exclude=excluded, absence_exclude=set(plan.get("proof_excluded") or []),
                on_page=stage)), timeout=CALL_WALL_S)
        if time.time() >= now + CALL_WALL_S:
            raise TimeoutError("Coverage publication deadline")
        covered_rows = {entry[0]: entry for page in staged_pages for entry in page}
        published = retain_candidates(request, account, operations, attempt,
                                      [covered_rows[index] for index in sorted(covered_rows)],
                                      action_id=action_id,
                                      fence=(plan["run"], plan["gen"], now + CALL_WALL_S))
        if published.get("action_id") is None or published.get("stale"):
            raise TimeoutError("Coverage publication lost its action")
        if time.time() >= now + CALL_WALL_S:
            raise TimeoutError("Coverage publication deadline")
    except Exception:
        proof = {"status": "unproven", "reason": "unavailable", "next_in_s": EXPIRY_RETRY_S}
    _finish_coverage(request, account, plan["run"],
                     requeue=proof.get("status") not in ("absent",) and not proof.get("covered"))
    settled = expiry_settle(proof, attempt, plan["run"], plan["gen"], widen=not plan.get("begin_ok", True))
    applied = ledger_update(request, account, operations, settled, attempt=attempt)
    if proof["status"] == "found":
        return {"status": "found", "tx_id": proof["tx_id"], "server_now": server_now,
                "matched_tx_ids": [item["tx_id"] for item in published.get("matches", [])
                                   if item.get("eligible") and not item.get("excluded_final_owner")],
                "matched_transactions": [{"tx_id": item["tx_id"], "operations": item.get("signed_operations")}
                                         for item in published.get("matches", [])
                                         if item.get("eligible") and not item.get("excluded_final_owner")],
                "watch_duplicates": not proof.get("covered", False),
                **({"next_in_s": CONTINUE_S} if not proof.get("covered") else {}), **silent, **identity}
    if proof["status"] == "absent" and applied:
        if isinstance(applied, dict) and applied.get("found"):
            return {"status": "found", "tx_id": applied["found"], "watch_duplicates": False,
                    "matched_tx_ids": [item["tx_id"] for item in published.get("matches", [])
                                       if item.get("eligible") and not item.get("excluded_final_owner")],
                    "matched_transactions": [{"tx_id": item["tx_id"], "operations": item.get("signed_operations")}
                                             for item in published.get("matches", [])
                                             if item.get("eligible") and not item.get("excluded_final_owner")],
                    "server_now": server_now, **silent, **identity}
        root = getattr(getattr(request.app.state, "ceres_config", None), "root", None)
        if root is not None:
            from . import routing_runs_v2
            await asyncio.to_thread(routing_runs_v2.replay_account_claims, root, account,
                                    lambda ops: ledger_routing_release(request, account, ops))
        return {"status": "absent", "covered": True, "server_now": server_now, **silent, **identity}
    if proof["status"] == "absent":
        return {"status": "unproven", "reason": "stale", "next_in_s": CONTINUE_S, "server_now": server_now, **silent, **identity}
    nxt = applied.get("next_in_s") if isinstance(applied, dict) else None
    return {"status": "unproven", "reason": proof.get("reason"), "next_in_s": nxt if nxt is not None else proof.get("next_in_s", EXPIRY_RETRY_S),
            "server_now": server_now, **silent, **identity}


def _record_found(row: dict, attempt: str, tx_id: str) -> None:
    """A server read found this attempt's transaction: it is this attempt's (found_by) and, for the first one, the
    action's (routing /v2/unsent and the round-3 readers)."""
    candidate = row.get("matches", {}).get(tx_id, {})
    if candidate.get("attempt_id") == attempt and candidate.get("owner") == row.get("action_id"):
        _found_ids(row).setdefault(attempt, tx_id)
    row.setdefault("found", tx_id)


def expiry_settle(proof: dict, attempt: str, run: str, gen: int, *, widen: bool = False):
    """The expiry worker's result, applied only to the attempt and run that started it (fix round 3, N2): a worker of an
    older run (its lease expired and another run started), of an attempt that was reopened since (new generation) or of
    another attempt never records an absence. A found id is this attempt's (and the action's first). With `widen` (no
    confirmed pre-sign begin, fix round 4  a scan that reached its discovery depth without a match goes deeper on
    the next pass, which follows at once. Returns False, True, or {next_in_s} when the retry was brought forward."""
    def apply(row):
        record = _attempts(row).get(attempt) if row is not None else None
        if record is None or record.get("gen", 0) != gen or (record.get("expiry") or {}).get("run") != run:
            return False
        if proof.get("status") == "found":
            _record_found(row, attempt, proof["tx_id"])
        if proof.get("scan") is not None:
            record["scan"] = proof["scan"]
        if proof["status"] == "found":
            record["expiry"] = {"status": "found", "tx_id": proof.get("tx_id"),
                                "covered": proof.get("covered", False), "at": time.time(),
                                "retry_at": time.time() + CONTINUE_S if not proof.get("covered") else None}
        elif proof["status"] == "absent":
            if row.get("found") and proof.get("covered"):
                record["expiry"] = {"status": "found", "tx_id": row["found"],
                                    "covered": True, "at": time.time()}
                _refresh_action_finality(row)
                return {"found": row["found"], "covered": True}
            if (record.get("closed_at") is None or not _begin_ok(record) or row.get("disputed") or row.get("found")
                    or any(item.get("eligible") and not item.get("excluded_final_owner")
                           for item in row.get("matches", {}).values())):
                return False
            record["expiry"] = {"status": "absent", "covered": proof.get("covered", False),
                                "at": time.time()}
        else:
            delay = proof.get("next_in_s", EXPIRY_RETRY_S)
            depth = record.get("discovery_depth_s", TOKENLESS_DEPTH_S)
            deeper = widen and proof.get("reason") in ("open", "newest-edge") and depth < TOKENLESS_MAX_DEPTH_S
            if deeper:
                record["discovery_depth_s"] = min(TOKENLESS_MAX_DEPTH_S, depth * 2)
                delay = CONTINUE_S
            record["expiry"] = {"status": "retry", "reason": proof.get("reason"), "retry_at": time.time() + delay}
            if deeper:
                return {"next_in_s": delay}
        _refresh_action_finality(row)
        return True
    return apply


def _next_in(row: dict, attempt: str, now: float) -> float:
    record = _attempts(row).get(attempt)
    if record and record.get("read_mode") == "fast" and record.get("fast_index", 0) < len(NORMAL_OFFSETS_S):
        return round(max(0.0, record.get("fast_anchor", record["first_at"])
                         + NORMAL_OFFSETS_S[record["fast_index"]] - now), 3)
    reads = row.get("reads", 0)
    if (row.get("window_start") is not None and row.get("window_attempt") in (None, attempt)
            and normal_offset(reads) <= NORMAL_UNTIL_S):
        return round(max(0.0, row["window_start"] + normal_offset(reads) - now), 3)
    record = _attempts(row).get(attempt) or {"first_at": row.get("first_seen", now)}
    return round(max(0.0, _expiry_at(record) - now), 3)


def own_absence_since(account: str, operations: list, *, earliest: float, until: float, history=None, head=None) -> dict:
    """Shared by other confirm paths: the same bounded own-history proof (one step; callers persist nothing)."""
    return prove_own_action(account, operations, earliest=earliest, until=until, history=history, head=head)
