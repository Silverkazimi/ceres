"""The generic Hive operation unit and batching/chunking logic.

An `Op` is the entire vocabulary the fides core understands -- it never inspects
`json_payload` contents, never validates a `custom_json` `id` against an allowlist, and
never assumes a particular required-auth tier is "the safe one"; those judgments belong
entirely to the consumer (fides_land, or any future consumer). Pure module: no I/O, no
network, no domain semantics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# WHY: Hive enforces HIVE_CUSTOM_OP_BLOCK_LIMIT = 5 custom_json ops per account per block at
# mempool admission (_push_transaction), not consensus; we pack 4 deliberately because the
# budget is shared across Ceres's three independent broadcast paths with no coordination
# leaving one spare op. 's block pacer would allow packing 5 later.
DEFAULT_MAX_OPS_PER_TX = 4
DEFAULT_MAX_BYTES_PER_TX = 6 * 1024  # 6 KB, serialized


@dataclass
class Op:
    """One Hive operation, shaped for `requestBroadcast`. Today this always means
    `custom_json`, since that is the only op type any consumer needs -- the shape does not
    preclude others, but nothing here assumes `operation_id == "custom_json"`."""

    operation_id: str
    required_auths: list[str] = field(default_factory=list)
    required_posting_auths: list[str] = field(default_factory=list)
    json_payload: dict[str, Any] = field(default_factory=dict)

    def as_custom_json(self) -> dict[str, Any]:
        """The raw opPayload shape for a `custom_json` Hive operation."""
        return {
            "id": self.operation_id,
            "json": json.dumps(self.json_payload, separators=(",", ":"), sort_keys=True),
            "required_auths": list(self.required_auths),
            "required_posting_auths": list(self.required_posting_auths),
        }

    def as_keychain_tuple(self) -> list[Any]:
        """The raw `[opName, opPayload]` shape `requestBroadcast`'s `operations[]` array
        expects (`hive-keychain.md` §2.3)."""
        return ["custom_json", self.as_custom_json()]

    def serialized_bytes(self) -> int:
        """UTF-8 byte size of this op as it would appear inside a `requestBroadcast` call --
        used by `chunk_ops` to respect the byte ceiling."""
        return len(json.dumps(self.as_keychain_tuple(), separators=(",", ":")).encode("utf-8"))


def op_key_tier(op: Op) -> str:
    """Which Keychain key tier a SINGLE op needs -- the per-op counterpart of
    `required_key_tier`. Read from the op's own `required_auths`/`required_posting_auths`,
    never inferred from `operation_id`: the same custom_json id has been observed live at
    more than one auth tier, so an id-keyed table would be unsound in principle."""
    if op.required_auths:
        return "Active"
    if op.required_posting_auths:
        return "Posting"
    raise ValueError("op declares no required_auths and no required_posting_auths -- cannot determine a key tier")


def chunk_ops(
    ops: list[Op],
    *,
    max_ops_per_tx: int | None = None,
    max_bytes_per_tx: int = DEFAULT_MAX_BYTES_PER_TX,
) -> list[list[Op]]:
    """Packs `ops` into chunks respecting BOTH ceilings, whichever binds first -- the
    generalized form of the ceiling-avoidance approach from the plan's first draft (§7): the
    actual Hive tx-size/op-count ceiling stays unverified and irrelevant, because a chunk
    never approaches it. Never splits a single Op: an Op larger than `max_bytes_per_tx` on
    its own is emitted as its own one-op chunk -- the caller's ceiling was too small for its
    own data, not something chunking can silently fix.

    Also never mixes tiers within one chunk -- a tier change against the previous op forces
    a new chunk exactly like either ceiling would. `requestBroadcast` signs one chunk with
    one key, so every op in a chunk is homogeneous in the tier it actually needs; without
    this, a single Posting-tier op appended after an Active-tier one would be silently
    swept into an Active-signed transaction it never asked for.
    """
    if max_ops_per_tx is None:
        max_ops_per_tx = DEFAULT_MAX_OPS_PER_TX
    if max_ops_per_tx < 1:
        raise ValueError("max_ops_per_tx must be >= 1")
    if max_bytes_per_tx < 1:
        raise ValueError("max_bytes_per_tx must be >= 1")

    chunks: list[list[Op]] = []
    current: list[Op] = []
    current_bytes = 2  # "[" + "]" for the enclosing operations[] array
    current_tier: str | None = None
    for op in ops:
        op_bytes = op.serialized_bytes()
        op_tier = op_key_tier(op)
        joiner = 1 if current else 0  # a "," between array elements
        projected = current_bytes + joiner + op_bytes
        if current and (len(current) >= max_ops_per_tx or projected > max_bytes_per_tx or op_tier != current_tier):
            chunks.append(current)
            current = []
            current_bytes = 2
            projected = current_bytes + op_bytes
        current.append(op)
        current_bytes = projected
        current_tier = op_tier
    if current:
        chunks.append(current)
    return chunks


def requests_for_broadcast(ops: list[Op]) -> list[list[Any]]:
    """The `operations[]` array argument for a single `requestBroadcast` call over this list
    of ops (all ops in one call are broadcast as one signed Hive transaction)."""
    return [op.as_keychain_tuple() for op in ops]


def required_key_tier(ops: list[Op]) -> str:
    """Which Keychain key tier a `requestBroadcast` call over `ops` needs -- purely
    mechanical, derived from Hive's own auth model (an op needing Active authority forces
    the whole call to Active; a call with only Posting-auth ops needs only Posting). This is
    NOT a policy choice a consumer makes; it is a fact about the ops themselves, so it lives
    in the core rather than being re-derived per consumer."""
    if any(op.required_auths for op in ops):
        return "Active"
    if any(op.required_posting_auths for op in ops):
        return "Posting"
    raise ValueError("ops declare no required_auths and no required_posting_auths -- cannot determine a key tier")


def required_account(ops: list[Op]) -> str:
    """A `requestBroadcast` call has exactly one signing `account`. Every op batched into one
    call must therefore name the same single account across its auth fields combined --
    raises if that is not the case, since silently picking one would be a signing-tier
    mistake waiting to happen."""
    accounts: set[str] = set()
    for op in ops:
        accounts.update(op.required_auths)
        accounts.update(op.required_posting_auths)
    if len(accounts) != 1:
        raise ValueError(f"a requestBroadcast call needs exactly one signing account, found {sorted(accounts)!r}")
    return next(iter(accounts))
