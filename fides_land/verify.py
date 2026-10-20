"""fides_land/verify.py -- fides_land's `VerificationSpec` implementation (plan §6-land):
`GET /land/stake/deeds/{uid}/assets` re-verification, one call per touched deed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

LAND_ASSETS_URL_TEMPLATE = "https://vapi.splinterlands.com/land/stake/deeds/{deed_uid}/assets"

# deed_uid -> raw `/land/stake/deeds/{uid}/assets` JSON response. Injected so no network call
# happens in a test process -- the real implementation is Praetoria's existing read-only land
# client, unchanged (fides_land never adds its own network transport for reads).
AssetsFetcher = Callable[[str], dict[str, Any]]

# deed_uid -> that deed's `worksite_facts` dict (or None if unresolvable), already unwrapped
# Phase 4's worksite-change verification. A different fetcher from `AssetsFetcher`
# because the evidence lives in a completely different upstream shape (the account-wide deed
# bundle, not the per-deed `/assets` cards/items response) -- see `ceres.capture.
# fetch_worksite_facts`, which is deliberately UNCACHED so a just-broadcast change is never
# read back through an hour-old bundle.
WorksiteFactsFetcher = Callable[[str], dict[str, Any] | None]

PENDING_ESCALATION_ATTEMPTS = 5


class LandDomainVerificationError(Exception):
    pass


def interpret_assets_response(response: dict[str, Any]) -> dict[str, Any]:
    """Correctly interprets a land-assets response. `the internal rules`'s durable fact: HTTP 200
    `status:"success"` can still wrap a PostgreSQL `P0001` error object for some stake types
    -- accept a response only when `data` is a dict containing both `cards` and `items` lists;
    anything else is an upstream error, never "asset absent". An empty list is valid evidence
    that no assets of that kind are present; an absent or unusable list is not evidence at all."""
    if not isinstance(response, dict) or response.get("status") != "success":
        raise LandDomainVerificationError(f"land assets response is not a success envelope: {response!r}")
    data = response.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("cards"), list) or not isinstance(data.get("items"), list):
        raise LandDomainVerificationError(f"land assets response 'data' is not the expected {{cards: [...], items: [...]}} shape (possible upstream P0001 error): {response!r}")
    present = {c["uid"]: c for c in data["cards"] if isinstance(c, dict) and c.get("uid")}
    items = data["items"]
    present_items = {item["uid"]: item for item in items if isinstance(item, dict) and item.get("uid")}
    return {
        "present_card_uids": sorted(present),
        "present_item_uids": sorted(present_items),
        "observed_slots": {uid: c.get("slot") for uid, c in present.items()},
    }


class LandVerificationSpec:
    """Implements `fides.ledger.VerificationSpec`. `ready_at_by_unit` maps a wave-3
    `unit_id` -> ISO `ready_at` (computed by the caller, see `docs/fides.md` for how wave 1's
    actual chain-confirmed unstake time feeds this -- `fides_land` never trusts the local
    wall clock as evidence of when the chain saw it, per the plan's own honesty requirement).
    A `unit_id` absent from `ready_at_by_unit` (wave 1, wave 2, and any wave-3 card with no
    cooldown to wait out) is treated as always-ready.
    """

    def __init__(
        self,
        fetch_assets: AssetsFetcher,
        *,
        ready_at_by_unit: dict[str, str] | None = None,
        fetch_worksite_facts: WorksiteFactsFetcher | None = None,
        worksite_completion_accepted: bool = False,
    ):
        self.fetch_assets = fetch_assets
        self.ready_at_by_unit = ready_at_by_unit or {}
        self.fetch_worksite_facts = fetch_worksite_facts
        self.worksite_completion_accepted = worksite_completion_accepted is True

    def check_domain(
        self,
        unit_id: str,
        ops: list[dict[str, Any]],
        *,
        verification_attempt: int = 1,
    ) -> dict[str, Any]:
        # Three distinct non-success outcomes remain separate
        # mismatches -- the operation is structurally malformed. A genuine problem.
        # unverifiable -- no usable evidence could be obtained at all (fetcher missing
        # fetch raised, or the upstream returned nothing to read). We
        # do not know either way -- never asserted as a mismatch.
        # pending -- evidence WAS obtained but does not yet read back the expected
        # post-broadcast value; callers receive an escalation threshold
        # because pending never becomes confirmation by elapsed time.
        # Priority when returning status below: mismatches > unverifiable > pending > verified.
        if isinstance(verification_attempt, bool) or not isinstance(verification_attempt, int) or verification_attempt < 1:
            raise ValueError("verification_attempt must be a positive integer")
        evidence: dict[str, Any] = {}
        mismatches: list[str] = []
        unverifiable: list[str] = []
        pending: list[str] = []
        for raw_op in ops:
            payload = json.loads(raw_op["json"]) if isinstance(raw_op.get("json"), str) else (raw_op.get("json") or {})
            # `sm_land_operation` worksite-change ops ( Phase 4) are a structurally
            # different payload -- no `deed_uid`/`stake`/`unstake` at all; the target lives in
            # `op` itself and the affected plots are `deed_uids` (an array, plan §0.2's own
            # multi-plot-batching room, even though Phase 4 only ever stages one). Dispatched
            # on `op` ending `_construction` BEFORE the `deed_uid` check below, since that
            # check would otherwise treat every worksite-change op as malformed.
            op_name = str(payload.get("op") or "")
            if op_name == "cancel_construction":
                op_pending, op_unverifiable, op_mismatches = self._check_worksite_cancel(payload, evidence)
                pending.extend(op_pending)
                unverifiable.extend(op_unverifiable)
                mismatches.extend(op_mismatches)
                continue
            if op_name == "update_worksite":
                op_pending, op_unverifiable, op_mismatches = self._check_worksite_completion(payload, evidence)
                pending.extend(op_pending)
                unverifiable.extend(op_unverifiable)
                mismatches.extend(op_mismatches)
                continue
            if op_name.endswith("_construction"):
                op_pending, op_unverifiable, op_mismatches = self._check_worksite_change(op_name, payload, evidence)
                pending.extend(op_pending)
                unverifiable.extend(op_unverifiable)
                mismatches.extend(op_mismatches)
                continue
            deed_uid = payload.get("deed_uid")
            if not deed_uid:
                mismatches.append(f"op has no deed_uid: {raw_op!r}")
                continue
            try:
                observed = interpret_assets_response(self.fetch_assets(deed_uid))
            except LandDomainVerificationError as exc:
                # An upstream response error provides no domain evidence, so it is unverifiable.
                unverifiable.append(f"deed {deed_uid}: {exc}")
                continue
            evidence[deed_uid] = observed
            for entry in payload.get("stake", {}).get("cards", []):
                if entry["card_uid"] not in observed["present_card_uids"]:
                    # A missing post-stake card may be propagation lag, so it is pending.
                    pending.append(f"expected {entry['card_uid']} staked at {deed_uid}, not observed present (may still be propagating)")
            for entry in payload.get("unstake", {}).get("cards", []):
                if entry["card_uid"] in observed["present_card_uids"]:
                    # A present post-unstake card may be propagation lag, so it is pending.
                    pending.append(f"expected {entry['card_uid']} unstaked from {deed_uid}, still observed present (may still be propagating)")
            for entry in payload.get("stake", {}).get("items", []):
                if entry["item_uid"] not in observed["present_item_uids"]:
                    # A missing post-stake item may be propagation lag, so it is pending.
                    pending.append(f"expected {entry['item_uid']} staked at {deed_uid}, not observed present (may still be propagating)")
            for entry in payload.get("unstake", {}).get("items", []):
                if entry["item_uid"] in observed["present_item_uids"]:
                    # A present post-unstake item may be propagation lag, so it is pending.
                    pending.append(f"expected {entry['item_uid']} unstaked from {deed_uid}, still observed present (may still be propagating)")
        if mismatches:
            return {"status": "domain_mismatch", "evidence": {"mismatches": mismatches, "unverifiable": unverifiable, "pending": pending, "deeds": evidence}}
        if unverifiable:
            return {"status": "domain_could_not_verify", "evidence": {"unverifiable": unverifiable, "deeds": evidence}}
        if pending:
            return {
                "status": "domain_pending",
                "evidence": {
                    "pending": pending,
                    "pending_metadata": {
                        "attempt": verification_attempt,
                        "escalate_after_attempts": PENDING_ESCALATION_ATTEMPTS,
                        "requires_escalation": verification_attempt >= PENDING_ESCALATION_ATTEMPTS,
                    },
                    "deeds": evidence,
                },
            }
        return {"status": "domain_verified", "evidence": {"deeds": evidence}}

    def _check_worksite_change(
        self, op_name: str, payload: dict[str, Any], evidence: dict[str, Any]
    ) -> tuple[list[str], list[str], list[str]]:
        """A `custom_json` landing on-chain proves nothing about game effect (H3, standing
        project rule) -- re-reads `worksite_facts.project_type` on every touched deed and
        requires it to read BACK `op_name` itself (the internal rules's durable fact: `project_type`
        reads `worksite_<resource>_construction` while a change is in flight, the identical
        string to the chain op name -- never `worksite_<resource>` yet, since the 24h build
        has not completed by the time this runs moments after broadcast). Never a transaction
        id; this is the ONLY thing that counts as confirmation for a worksite change.

        Returns `(pending, unverifiable, mismatches)`:
          - a read that came back but does not (yet) match `op_name` is PENDING, never a
            mismatch outright -- moments after broadcast that is expected propagation lag,
            not proof of failure (the same H3 principle, read the other direction);
          - no fetcher configured, a raised fetch, or a fetch that returned `None` (nothing
            to compare against) is UNVERIFIABLE -- we simply don't know;
          - only a structurally malformed op (no `deed_uids` at all) is a genuine MISMATCH
            here, since that is a real defect in the payload, not a timing question.
        """
        deed_uids = payload.get("deed_uids")
        if not isinstance(deed_uids, list) or not deed_uids:
            return [], [], [f"worksite-change op has no deed_uids: {payload!r}"]
        if self.fetch_worksite_facts is None:
            return [], [f"no worksite_facts fetcher configured -- cannot domain-verify {op_name!r}"], []
        pending: list[str] = []
        unverifiable: list[str] = []
        for deed_uid in deed_uids:
            try:
                worksite_facts = self.fetch_worksite_facts(deed_uid)
            except Exception as exc:  # network flake or upstream error -- unknown, never "matches"
                unverifiable.append(f"deed {deed_uid}: failed to re-read worksite_facts ({exc})")
                continue
            if worksite_facts is None:
                # `fetch_worksite_facts` returning `None` means no evidence at all was
                # obtainable (deed absent from the fresh bundle) -- not "it disagrees".
                unverifiable.append(f"deed {deed_uid}: worksite_facts not available in the fresh bundle")
                continue
            observed_project_type = worksite_facts.get("project_type")
            evidence[deed_uid] = {"project_type": observed_project_type}
            if observed_project_type != op_name:
                pending.append(
                    f"deed {deed_uid}: expected worksite_facts.project_type={op_name!r}, observed {observed_project_type!r} (may still be propagating)"
                )
        return pending, unverifiable, []

    def _check_worksite_completion(
        self, payload: dict[str, Any], evidence: dict[str, Any]
    ) -> tuple[list[str], list[str], list[str]]:
        deed_uids = payload.get("deed_uids")
        if not isinstance(deed_uids, list) or not deed_uids:
            return [], [], [f"worksite completion op has no deed_uids: {payload!r}"]
        if payload.get("project_id") in (None, ""):
            return [], [], [f"worksite completion op has no project_id: {payload!r}"]
        if self.fetch_worksite_facts is None:
            return [], ["no worksite_facts fetcher configured -- cannot domain-verify Feed Worker"], []
        pending: list[str] = []
        unverifiable: list[str] = []
        for deed_uid in deed_uids:
            try:
                worksite_facts = self.fetch_worksite_facts(deed_uid)
            except Exception as exc:
                unverifiable.append(f"deed {deed_uid}: failed to re-read worksite_facts ({exc})")
                continue
            if worksite_facts is None:
                unverifiable.append(f"deed {deed_uid}: worksite_facts not available in the fresh bundle")
                continue
            observed_project_type = worksite_facts.get("project_type")
            observed_project_id = worksite_facts.get("project_id", worksite_facts.get("id"))
            evidence[deed_uid] = {"project_type": observed_project_type, "project_id": observed_project_id}
            if str(observed_project_type or "").endswith("_construction") or worksite_facts.get("is_construction") is True:
                pending.append(f"deed {deed_uid}: Feed Worker completion is not yet reflected in game state (may still be propagating)")
            elif not observed_project_type or observed_project_id is None:
                unverifiable.append(f"deed {deed_uid}: active worksite identity is unavailable")
            elif not self.worksite_completion_accepted:
                pending.append(f"deed {deed_uid}: active worksite observed; awaiting this transaction's game acceptance")
            else:
                # Completing construction creates a new active project; the submitted
                # project ID identifies the construction being completed, not its successor.
                evidence[deed_uid]["completed_project_id"] = payload["project_id"]
                evidence[deed_uid]["game_accepted"] = True
        return pending, unverifiable, []

    def _check_worksite_cancel(
        self, payload: dict[str, Any], evidence: dict[str, Any]
    ) -> tuple[list[str], list[str], list[str]]:
        deeds = payload.get("deed_uids")
        project_id = payload.get("project_id")
        if not isinstance(deeds, list) or not deeds or project_id in (None, ""):
            return [], [], ["cancel construction is missing deed or project identity"]
        if self.fetch_worksite_facts is None:
            return [], ["no worksite facts fetcher for cancel construction"], []
        pending, unverifiable = [], []
        for deed_uid in deeds:
            try:
                facts = self.fetch_worksite_facts(deed_uid)
            except Exception as exc:
                unverifiable.append(f"deed {deed_uid}: worksite read failed ({exc})")
                continue
            if facts is None:
                if self.worksite_completion_accepted:
                    evidence[deed_uid] = {"project_id": None, "game_accepted": True}
                else:
                    pending.append(f"deed {deed_uid}: awaiting this transaction's game acceptance")
                continue
            observed_id = facts.get("project_id", facts.get("id"))
            evidence[deed_uid] = {"project_id": observed_id, "project_type": facts.get("project_type")}
            if str(observed_id) == str(project_id):
                pending.append(f"deed {deed_uid}: construction {project_id} is still active")
            elif not self.worksite_completion_accepted:
                pending.append(f"deed {deed_uid}: awaiting this transaction's game acceptance")
        return pending, unverifiable, []

    def gate_status(self, unit_id: str) -> dict[str, Any]:
        if unit_id not in self.ready_at_by_unit:
            # no gate was ever declared for this unit (wave 1, wave 2, or any unit outside
            # fides_land's cooldown bookkeeping entirely) -- genuinely always-ready.
            return {"ready": True}
        ready_at = self.ready_at_by_unit[unit_id]
        if ready_at is None:
            # a gate IS required (this is a wave-3 unit) but its ready_at cannot be computed
            # yet -- e.g. wave 1 hasn't chain-confirmed. Must never default to "ready" here
            # that would let a same-card round-trip stake sign before its cooldown even starts.
            return {"ready": False, "reason": "gate not yet computable -- wave 1 has not chain-confirmed"}
        ready_dt = datetime.fromisoformat(ready_at.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) >= ready_dt:
            return {"ready": True}
        return {
            "ready": False,
            "ready_at": ready_at,
            "reason": "cooldown not yet elapsed (assumed margin, not a measured enforced minimum -- see docs/apis-and-data.md §9.3)",
        }


def wave1_ready_at(
    ledger_entries: list[Any],
    wave1_unit_id: str,
    *,
    cooldown_hours: float,
    margin_hours: float,
) -> tuple[str | None, bool]:
    """Computes wave 3's `ready_at` from wave 1's own ledger entries for `wave1_unit_id`.
    Prefers the on-chain confirmation timestamp (`chain_verified` evidence's
    `chain_timestamp`, if the chain transport supplied one) over the ledger's local
    wall-clock `broadcast_claimed` time, since the plan is explicit that the local clock is
    not evidence of when the chain actually saw the transaction. Returns
    `(ready_at_iso_or_None, assumed_from_local_clock)` -- the second value is `True` whenever
    the fallback fired, so a caller can flag it in the bundle's `assumptions` rather than
    presenting it as measured.
    """
    entries = [e for e in ledger_entries if e.unit_id == wave1_unit_id]
    chain_entry = next((e for e in entries if e.state == "chain_verified"), None)
    if chain_entry is not None:
        ts = chain_entry.detail.get("evidence", {}).get("chain_timestamp")
        if ts:
            base = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return (base + timedelta(hours=cooldown_hours + margin_hours)).isoformat(), False
    broadcast_entry = next((e for e in entries if e.state == "broadcast_claimed"), None)
    if broadcast_entry is None:
        return None, True
    base = datetime.fromisoformat(broadcast_entry.ts.replace("Z", "+00:00"))
    return (base + timedelta(hours=cooldown_hours + margin_hours)).isoformat(), True
