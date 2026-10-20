"""Game-state verification for explicit card combines."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from fides.op import Op

from .ops import _bcx, _collection_rows, _foil, _record_index, _uid, survivor_rule

CollectionFetcher = Callable[[], Any]


class CardVerificationSpec:
    """Re-read collection state; a Hive transaction id is never game confirmation."""

    def __init__(
        self,
        fetch_collection: CollectionFetcher,
        *,
        expected_records: Any = None,
        intent: Any = None,
    ):
        self.fetch_collection = fetch_collection
        self.expected_records = _collection_rows(expected_records) if expected_records is not None else None
        self.intent = intent

    def check_domain(self, unit_id: str, ops: list[Any]) -> dict[str, Any]:
        expected = self._expected(ops)
        if expected is None:
            return {"status": "domain_could_not_verify", "evidence": {"reason": "combine inputs or pre-combine BCX unavailable"}}
        if expected["survivor_uid"] == "unknown":
            return {"status": "domain_could_not_verify", "evidence": {"reason": "survivor cannot be predicted from the pre-combine records"}}
        try:
            observed_rows = _collection_rows(self.fetch_collection())
        except Exception as exc:
            return {"status": "domain_could_not_verify", "evidence": {"reason": f"collection read failed: {exc}"}}
        try:
            observed = _record_index(observed_rows)
        except Exception as exc:
            return {"status": "domain_could_not_verify", "evidence": {"reason": str(exc)}}
        # the game may retain any selected Card ID. Exactly one input
        # must remain; its merged CC must be the sum, regardless of prediction.
        present = [uid for uid in expected["uids"] if uid in observed]
        survivor = present[0] if len(present) == 1 else expected["survivor_uid"]
        present_consumed = [uid for uid in expected["uids"] if uid != survivor and uid in observed]
        survivor_row = observed.get(survivor) if len(present) == 1 else None
        evidence = {
            "unit_id": unit_id,
            "consumed_uids": list(expected["uids"]),
            "survivor_uid": survivor,
            "predicted_survivor_uid": expected["survivor_uid"],
            "survivor_differs": survivor != expected["survivor_uid"],
            "present_consumed_uids": present_consumed,
            "observed_survivor": survivor_row,
            "expected_bcx": expected["bcx"],
            "expected_level_at_least": expected["survivor_level"],
        }
        if survivor_row is not None:
            evidence["observed_bcx"] = _bcx(survivor_row)
            evidence["observed_level"] = survivor_row.get("level")
            evidence["observed_collection_power"] = survivor_row.get("collection_power", survivor_row.get("cc"))
        owner = expected.get("owners", {}).get(survivor)
        observed_owner = (survivor_row or {}).get("player") or (survivor_row or {}).get("owner")
        owner_ok = not (owner and observed_owner and str(owner).lower() != str(observed_owner).lower())
        if self.intent and observed_owner:
            owner_ok = owner_ok and str(observed_owner).lower() == str(self.intent).lower()
        if not present_consumed and survivor_row is not None and owner_ok and self._matches_expected(survivor_row, expected):
            return {"status": "domain_verified", "evidence": evidence}
        return {
            "status": "domain_pending",
            "evidence": {**evidence, "reason": "collection read is available but the combine effect is not fully propagated"},
        }

    def _expected(self, ops: list[Any]) -> dict[str, Any] | None:
        if not isinstance(ops, list) or len(ops) != 1:
            return None
        raw = ops[0]
        if isinstance(raw, Op):
            operation_id = raw.operation_id
            payload = raw.json_payload
        elif isinstance(raw, Mapping):
            operation_id = raw.get("id")
            payload = raw.get("json", {})
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    return None
        else:
            return None
        uids = payload.get("cards") if isinstance(payload, Mapping) else None
        if operation_id != "sm_combine_cards" or not isinstance(uids, list) or len(uids) < 2:
            return None
        if self.expected_records is None:
            return None
        index = _record_index(self.expected_records)
        records = [index.get(str(uid)) for uid in uids]
        if any(record is None for record in records):
            return None
        records = [record for record in records if record is not None]
        bcx_values = [_bcx(record) for record in records]
        survivor_uid = survivor_rule(records)
        if survivor_uid == "unknown":
            return {"survivor_uid": "unknown"}
        survivor_record = index.get(survivor_uid)
        if any(value is None for value in bcx_values) or survivor_record is None:
            return None
        level = survivor_record.get("level")
        power_values = [record.get("collection_power", record.get("cc")) for record in records]
        expected_power = sum(power_values) if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in power_values) else None
        return {
            "uids": [str(uid) for uid in uids],
            "survivor_uid": survivor_uid,
            "bcx": sum(value for value in bcx_values if value is not None),
            "survivor_level": level,
            "collection_power": expected_power,
            "owners": {str(_uid(row)): row.get("player") or row.get("owner") for row in records},
        }

    @staticmethod
    def _matches_expected(row: Mapping[str, Any], expected: dict[str, Any]) -> bool:
        observed_bcx = _bcx(row)
        if observed_bcx != expected["bcx"]:
            return False
        if row.get("level") is None:
            return False
        if expected["survivor_level"] is not None:
            try:
                if int(row["level"]) < int(expected["survivor_level"]):
                    return False
            except (TypeError, ValueError):
                return False
        if expected.get("collection_power") is not None:
            observed_power = row.get("collection_power", row.get("cc"))
            if observed_power != expected["collection_power"]:
                return False
        return True

    def gate_status(self, unit_id: str) -> dict[str, Any]:
        return {"ready": True}


CombineVerificationSpec = CardVerificationSpec
