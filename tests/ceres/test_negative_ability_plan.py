""": an upgrade plan whose card snapshot carries a negative ability value (Rationing Lite -0.03) is valid; before,
the browser called it malformed and every saved change stayed on "Checking" forever."""
import json
import subprocess
from pathlib import Path

MODULE = Path(__file__).parents[2] / "ceres" / "static" / "combine-chain.js"

_PLAN = {
    "version": 2, "id": "cc-x", "state": "cooling", "origin": {"kind": "tile", "deed_uid": "d", "slot_index": 0, "asset_uid": "a"},
    "card": {"name": "Hamford Sparkwright", "level": 4, "bcx": 30}, "expected": {"level": 5, "bcx": 61, "base_pp": 305},
    "inputs": [
        {"uid": "a", "cc": 30, "kind": "staked", "status": "active", "source": {"deed_uid": "d", "slot_index": 0},
         "asset": {"name": "Hamford Sparkwright", "land_abilities": [["BLOODLINE", 0.03, "Gnome"], ["RATIONING_LITE", -0.03]]}},
        {"uid": "b", "cc": 31, "kind": "free", "status": "active"},
    ],
}


def _valid(plan) -> bool:
    script = f"import {{ validChainPlan }} from '{MODULE.as_uri()}'; console.log(validChainPlan({json.dumps(plan)}));"
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True).stdout.strip()
    return out == "true"


def test_negative_ability_value_in_a_card_snapshot_is_valid():
    assert _valid(_PLAN)


def test_named_numeric_fields_still_must_not_be_negative():
    bad = json.loads(json.dumps(_PLAN))
    bad["inputs"][1]["cc"] = -5
    assert not _valid(bad)
    bad = json.loads(json.dumps(_PLAN))
    bad["expected"]["level"] = -1
    assert not _valid(bad)
