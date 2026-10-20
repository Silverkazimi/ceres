""": a small, hand-built fixture set for the fixture-upstream mechanism's own tests.

Built at test time from the existing synthetic land fixtures (tests/fixtures/land), with the account
renamed to a sample name, so no recording and no real account is involved.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LAND = Path(__file__).resolve().parents[1] / "fixtures" / "land"
SAMPLE = "sample01"
_SOURCE_ACCOUNT = "player0005"  # the synthetic fixtures' account name, replaced below


def _land(name: str) -> Any:
    return json.loads((LAND / name).read_text(encoding="utf-8").replace(_SOURCE_ACCOUNT, SAMPLE))


def entry(host: str, path: str, body: Any, *, query: str = "", method: str = "GET", status: int = 200,
          rpc: dict | None = None) -> dict:
    request: dict[str, Any] = {"method": method, "host": host, "path": path}
    if rpc is not None:
        request["rpc"] = rpc
    else:
        request["query"] = query
    return {"request": request, "status": status, "headers": {"Content-Type": "application/json"}, "body": body}


def board_entries(player: str = SAMPLE) -> list[dict]:
    bundle = _land("deeds_multi.json")
    deed_uid = bundle["data"]["deeds"][0]["deed_uid"]
    available = {"STK-LND-TOT": _land("available_totem_candidates.json"),
                 "STK-LND-TTL": _land("available_title_empty.json"),
                 "STK-LND-PCR": _land("available_power_core_candidates.json")}
    rows = [
        entry("vapi.splinterlands.com", "/land/deeds", bundle, query=f"player={player}"),
        entry("api.splinterlands.com", f"/cards/collection/{player}", _land("collection_sample.json")),
        entry("api.splinterlands.com", "/cards/get_details", _land("card_details_sample.json")),
        entry("api.splinterlands.com", "/players/item_details", []),
        entry("api.splinterlands.com", "/players/balances", [], query=f"username={player}"),
    ]
    for stake_type, body in available.items():
        rows.append(entry("vapi.splinterlands.com", f"/land/stake/items/{stake_type}/available", body,
                          query=f"deedUid={deed_uid}&player={player}&offset=0&limit=100"))
    return rows


def write_set(root: Path, entries: list[dict], *, name: str = "mini", recorded_at: str | None = None,
              time_shift: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"format": 1, "set": name, "time_shift": time_shift}
    if recorded_at is not None:
        manifest["recorded_at"] = recorded_at
    (root / "fixture-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for index, item in enumerate(entries):
        host = item["request"]["host"]
        folder = root / host / item["request"]["method"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{index:03d}.json").write_text(json.dumps(item), encoding="utf-8")
    return root
