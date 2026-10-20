"""Generic append-only ledger for a Fides batch, driven by a caller-supplied
`VerificationSpec`. This is the core's central safety property, for every present and
future consumer: `chain_verified` is not `domain_verified`, and conflating them is the
whole failure mode. A `custom_json` op is opaque to Hive consensus -- a malformed or
illegal payload still lands on-chain as a perfectly successful transaction, and whatever
off-chain system is supposed to act on it may silently ignore it. A transaction id proves
nothing at all about whether the intended real-world effect occurred; only the consumer's
own re-read of its domain's ground truth proves the effect, via `VerificationSpec.check_domain`.

`ledger.jsonl` is append-only -- one JSON object per transition, flushed on write, never
rewritten -- so a batch interrupted at any point is resumable by replaying the file.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

VALID_STATES = {
    "pending",
    "broadcast_claimed",
    "chain_verified",
    "domain_verified",
    "chain_absent",
    "domain_mismatch",
    "domain_pending",
    "domain_could_not_verify",
}

TERMINAL_FAILURE_STATES = {"chain_absent", "domain_mismatch"}

# Evidence states require a chain or domain verifier; bare append() cannot claim them.
GUARDED_STATES = {
    "chain_verified",
    "chain_absent",
    "domain_verified",
    "domain_mismatch",
    "domain_pending",
    "domain_could_not_verify",
}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"broadcast_claimed"},
    "broadcast_claimed": {"chain_verified", "chain_absent"},
    "chain_verified": {"domain_verified", "domain_mismatch", "domain_pending", "domain_could_not_verify"},
    "domain_pending": {"domain_verified", "domain_mismatch", "domain_pending", "domain_could_not_verify"},
    "domain_could_not_verify": {"domain_verified", "domain_mismatch", "domain_pending", "domain_could_not_verify"},
    "chain_absent": set(),
    "domain_mismatch": set(),
    "domain_verified": set(),
}


class VerificationSpec(Protocol):
    """The interface a consumer supplies at emit time. `fides.ledger` calls these and never
    assumes what "verified" means beyond "the consumer's own callback said so"."""

    def check_domain(self, unit_id: str, ops: list[Any]) -> dict[str, Any]:
        """Re-read the domain's ground truth for the ops in this unit. A readable but
        not-yet-propagated result returns ``domain_pending`` and remains retryable."""
        ...

    def gate_status(self, unit_id: str) -> dict[str, Any]:
        """A readiness description for `unit_id` ahead of broadcasting it, e.g.
        `{"ready": True}` or `{"ready": False, "ready_at": "...", "reason": "..."}`. The
        core's own vocabulary is just "gate open / gate closed" -- any richer phrasing
        ("which cards are cooling") is the consumer's own status-renderer language."""
        ...


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class LedgerEntry:
    ts: str
    unit_id: str
    state: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "unit_id": self.unit_id, "state": self.state, "detail": self.detail}

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "LedgerEntry":
        return cls(ts=row["ts"], unit_id=row["unit_id"], state=row["state"], detail=row.get("detail", {}))


class LedgerError(Exception):
    pass


class Ledger:
    """Wraps one batch's `ledger.jsonl`. State for any `unit_id` is always derived by
    replaying the file from the start -- the file is the sole source of truth, never a
    cached in-memory value that could drift from it across a process restart."""

    def __init__(self, path: Path):
        self.path = path

    def append(self, unit_id: str, state: str, detail: dict[str, Any] | None = None) -> LedgerEntry:
        if state in GUARDED_STATES:
            raise LedgerError(f"{state!r} cannot be appended directly -- it must come from record_chain_result() or advance_with_domain_check(), never a bare claim")
        return self._append_internal(unit_id, state, detail)

    def _append_internal(self, unit_id: str, state: str, detail: dict[str, Any] | None = None) -> LedgerEntry:
        if state not in VALID_STATES:
            raise LedgerError(f"unknown ledger state: {state!r}")
        current = self.current_state(unit_id)
        if current is None:
            if state != "pending":
                raise LedgerError(f"unit {unit_id!r} has no entries yet; first state must be 'pending', got {state!r}")
        elif state not in ALLOWED_TRANSITIONS.get(current, set()):
            raise LedgerError(f"illegal transition for unit {unit_id!r}: {current!r} -> {state!r}")
        entry = LedgerEntry(ts=_now_iso(), unit_id=unit_id, state=state, detail=detail or {})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.as_dict(), sort_keys=True))
            fh.write("\n")
            fh.flush()
        return entry

    def replay(self) -> list[LedgerEntry]:
        if not self.path.exists():
            return []
        entries: list[LedgerEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            entries.append(LedgerEntry.from_dict(json.loads(line)))
        return entries

    def current_state(self, unit_id: str) -> str | None:
        state = None
        for entry in self.replay():
            if entry.unit_id == unit_id:
                state = entry.state
        return state

    def unit_ids(self) -> list[str]:
        seen: list[str] = []
        for entry in self.replay():
            if entry.unit_id not in seen:
                seen.append(entry.unit_id)
        return seen

    def is_domain_verified(self, unit_id: str) -> bool:
        return self.current_state(unit_id) == "domain_verified"

    def is_terminal_failure(self, unit_id: str) -> bool:
        return self.current_state(unit_id) in TERMINAL_FAILURE_STATES

    def record_broadcast(self, unit_id: str, tx_id: str) -> LedgerEntry:
        if self.current_state(unit_id) is None:
            self.append(unit_id, "pending")
        return self.append(unit_id, "broadcast_claimed", {"tx_id": tx_id})

    def record_chain_result(self, unit_id: str, result: dict[str, Any]) -> LedgerEntry:
        state = result.get("status")
        if state not in ("chain_verified", "chain_absent"):
            raise LedgerError(f"chain verification returned an invalid status: {state!r}")
        return self._append_internal(unit_id, state, {"evidence": result.get("evidence")})

    def advance_with_domain_check(self, unit_id: str, spec: VerificationSpec, ops: list[Any]) -> LedgerEntry:
        """Never advances a unit past `chain_verified` on chain evidence alone -- calls the
        consumer's `check_domain` and only records what it affirmatively returns.

        KNOWN, DELIBERATE POLICY DIVERGENCE (2026-08-24): this method gates the domain
        check on `current_state(unit_id) == "chain_verified"` by design -- it raises
        `LedgerError` rather than running `check_domain` on a merely-inconclusive chain
        state. `ceres/routes_op_preview.py`'s `op_verify` does NOT go through `Ledger` at
        all for its own chain/domain verification and deliberately does not share this
        gate: it runs the domain check regardless of chain status and lets
        `domain_verified` stand on its own (a `chain_absent` + `domain_verified` pair
        reads as a confirmation there, not a failure -- the internal rules's "a custom_json on
        chain proves nothing, only game state does"). This divergence is intentional as
        of this date, not an oversight to be "fixed" by whoever next notices it -- a
        future Ledger-mediated consumer that wants `op_verify`'s behavior needs its own
        bypass of this precondition, not a silent change here."""
        if self.current_state(unit_id) not in {"chain_verified", "domain_pending", "domain_could_not_verify"}:
            raise LedgerError(f"unit {unit_id!r} must be chain_verified or awaiting a domain retry (was {self.current_state(unit_id)!r})")
        result = spec.check_domain(unit_id, ops)
        state = result.get("status")
        if state not in ("domain_verified", "domain_mismatch", "domain_pending", "domain_could_not_verify"):
            raise LedgerError(f"VerificationSpec.check_domain returned an invalid status: {state!r}")
        return self._append_internal(unit_id, state, {"evidence": result.get("evidence")})
