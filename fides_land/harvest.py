""" harvest operation builders and strict validator (plan §7).

Pure: no I/O, no signing. Two observed payload shapes only, both `sm_land_operation`, Posting:
- whole region: `harvest_all` with `deed_uids: []` -- ONE op covers every eligible plot of the
  region, however many (: an op limit is never a plot limit);
- single plot: `harvest_resources` with exactly one deed (multi-deed packing stays disabled until
  its own on-chain test, plan §10.4).
`auto_buy_grain` is always false; no transfer, swap, donation or time-crystal field can appear.
"""

from __future__ import annotations

import json
import re
import secrets
import string

from fides.op import Op, chunk_ops

from .ops import LandOpError, _validate_app

HARVEST_OP_ID = "sm_land_operation"
MAX_OPS_PER_TX = 4
MAX_TX_OPS_BYTES = 6144
MAX_OP_JSON_BYTES = 8192
_ALPHABET = string.ascii_letters + string.digits
_REGION_RE = re.compile(r"^PR-[A-Z]{2,4}-\d{1,4}$")
_DEED_RE = re.compile(r"^I-\d{1,4}-[0-9a-f]{8,32}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9]{10}$")
_ACCOUNT_RE = re.compile(r"^[a-z][a-z0-9.-]{2,15}$")
_KEYS = {"op", "region_uid", "deed_uids", "auto_buy_grain", "resource_symbol", "app", "n"}


def nonce() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(10))


def _check_account(account: str) -> None:
    if not isinstance(account, str) or not _ACCOUNT_RE.match(account):
        raise LandOpError(f"invalid Hive account {account!r}")


def _check_region(region_uid: str) -> None:
    if not isinstance(region_uid, str) or not _REGION_RE.match(region_uid):
        raise LandOpError(f"invalid region_uid {region_uid!r}")


def harvest_all_op(account: str, region_uid: str, *, app: str, n: str | None = None) -> Op:
    _check_account(account)
    _check_region(region_uid)
    app = _validate_app(app)
    payload = {"op": "harvest_all", "region_uid": region_uid, "deed_uids": [], "auto_buy_grain": False,
               "resource_symbol": "ALL", "app": app, "n": n or nonce()}
    op = Op(operation_id=HARVEST_OP_ID, required_auths=[], required_posting_auths=[account], json_payload=payload)
    validate_harvest_op(op, account)
    return op


def harvest_resources_op(account: str, region_uid: str, deed_uid: str, *, app: str, n: str | None = None) -> Op:
    _check_account(account)
    _check_region(region_uid)
    if not isinstance(deed_uid, str) or not _DEED_RE.match(deed_uid):
        raise LandOpError(f"invalid deed_uid {deed_uid!r}")
    app = _validate_app(app)
    payload = {"op": "harvest_resources", "region_uid": region_uid, "deed_uids": [deed_uid], "auto_buy_grain": False,
               "resource_symbol": "", "app": app, "n": n or nonce()}
    op = Op(operation_id=HARVEST_OP_ID, required_auths=[], required_posting_auths=[account], json_payload=payload)
    validate_harvest_op(op, account)
    return op


def tax_collection_op(account: str, region_uid: str, deed_uid: str, *, app: str, n: str | None = None) -> Op:
    """: collect a Keep/Castle's taxes. Shape evidenced on chain (player0001, 28 transactions, e.g.
    <tx id>): the harvest_resources payload with `op: tax_collection` and exactly one deed."""
    _check_account(account)
    _check_region(region_uid)
    if not isinstance(deed_uid, str) or not _DEED_RE.match(deed_uid):
        raise LandOpError(f"invalid deed_uid {deed_uid!r}")
    app = _validate_app(app)
    payload = {"op": "tax_collection", "region_uid": region_uid, "deed_uids": [deed_uid], "auto_buy_grain": False,
               "resource_symbol": "", "app": app, "n": n or nonce()}
    op = Op(operation_id=HARVEST_OP_ID, required_auths=[], required_posting_auths=[account], json_payload=payload)
    validate_harvest_op(op, account)
    return op


def validate_harvest_op(op: Op, account: str) -> None:
    """Exact-shape allowlist for the two phase-1 harvest payloads; raises LandOpError."""
    if op.operation_id != HARVEST_OP_ID:
        raise LandOpError(f"not a harvest op id: {op.operation_id!r}")
    if op.required_auths or op.required_posting_auths != [account]:
        raise LandOpError("harvest ops are Posting-only and bound to the reviewed account")
    payload = op.json_payload
    if not isinstance(payload, dict) or set(payload) != _KEYS:
        raise LandOpError(f"harvest payload keys must be exactly {sorted(_KEYS)}")
    if not isinstance(payload["app"], str) or re.fullmatch(r"ceres/\d+\.\d+\.\d+", payload["app"]) is None:
        raise LandOpError("harvest payload app identity must match 'ceres/<semver>'")
    if payload["auto_buy_grain"] is not False:
        raise LandOpError("auto_buy_grain must be false")
    if not isinstance(payload["n"], str) or not _NONCE_RE.match(payload["n"]):
        raise LandOpError("nonce must be ten alphanumerics")
    _check_region(payload["region_uid"])
    deeds = payload["deed_uids"]
    if payload["op"] == "harvest_all":
        if deeds != [] or payload["resource_symbol"] != "ALL":
            raise LandOpError("harvest_all must carry deed_uids [] and resource_symbol ALL")
    elif payload["op"] in ("harvest_resources", "tax_collection"):
        if not (isinstance(deeds, list) and len(deeds) == 1 and isinstance(deeds[0], str) and _DEED_RE.match(deeds[0])):
            raise LandOpError(f"{payload['op']} carries exactly one deed until multi-deed packing is evidenced")
        if payload["resource_symbol"] != "":
            raise LandOpError(f"{payload['op']} resource_symbol must be empty")
    else:
        raise LandOpError(f"unsupported harvest op {payload['op']!r}")
    if len(json.dumps(payload, separators=(",", ":")).encode("utf-8")) > MAX_OP_JSON_BYTES:
        raise LandOpError("harvest op json exceeds 8192 bytes")


def plan_chunks(ops: list[Op], account: str) -> list[list[Op]]:
    """≤4 ops and ≤6 KiB per signature; one oversized op is refused, never emitted alone.
    Subtypes are not mixed inside one transaction (plan §7)."""
    nonces = [op.json_payload.get("n") for op in ops]
    if len(set(nonces)) != len(nonces):
        raise LandOpError("duplicate nonce in one harvest plan")
    kinds = {op.json_payload.get("op") for op in ops}
    if len(kinds) > 1:
        raise LandOpError("one harvest plan never mixes harvest_all and harvest_resources")
    for op in ops:
        validate_harvest_op(op, account)
        if op.serialized_bytes() + 2 > MAX_TX_OPS_BYTES:
            raise LandOpError("a single harvest op exceeds the per-transaction byte budget")
    return chunk_ops(ops, max_ops_per_tx=MAX_OPS_PER_TX, max_bytes_per_tx=MAX_TX_OPS_BYTES)


def signature_count(op_count: int) -> int:
    return -(-op_count // MAX_OPS_PER_TX) if op_count > 0 else 0
