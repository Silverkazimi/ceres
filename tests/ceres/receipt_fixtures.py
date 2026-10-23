"""The synthetic game results behind the receipt-model tests, built here and written to
tests/ceres/fixtures/receipts.

Every identifier is synthetic, with the conventions of tests/ceres/board_basic_recording.py: the account is
`sample01`, deed uids are `I-<deed type>-<hash of a key in this file>`, transaction ids are hashes of keys
in this file, and region uids come from ceres/data/praetoria_regions.json. Nothing here was recorded from a
real account. The signed payloads are built with the same fides_land builders Ceres signs with, and the game
result rows carry the fields ceres/receipt_model.py reads, in the shapes it accepts:

- lookup-harvest_all.json: `/transactions/lookup` for one regional `harvest_all` (8 plots, GRAIN eaten
  above GRAIN received, an SPS reward on two plots);
- lookup-harvest_resources.json: one plot's `harvest_resources` that consumed GRAIN, WOOD, STONE and IRON;
- lookup-tax_collection-op2.json / fragment-history-tax_collection-op2.json: a `tax_collection` filed as
  operation 2 of its transaction (lookup id `<tx>-2`) and its fragment history with one real fragment.

Every harvest row reconciles (resource_amount - tax_amount - grain_rewards_eaten = received_amount), and the
totals the tests assert are checked here while building, so a change that breaks them fails at regeneration.

Regenerate with `python -m tests.ceres.receipt_fixtures`; test_receipt_model.py checks that the files on disk
match this module.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from fides_land.harvest import harvest_all_op, harvest_resources_op, tax_collection_op

ROOT = Path(__file__).resolve().parents[2]
TARGET = Path(__file__).parent / "fixtures" / "receipts"
PLAYER = "sample01"
APP = "ceres/1.0.0"
REGIONS = {r["region_uid"].rsplit("-", 1)[1]: r["region_uid"] for r in json.loads(
    (ROOT / "ceres" / "data" / "praetoria_regions.json").read_text(encoding="utf-8"))["regions"]}


def _hex(key: str, size: int) -> str:
    return hashlib.sha256(f"ceres-synthetic:{key}".encode()).hexdigest()[:size]


def tx(key: str) -> str:
    """A synthetic 40-hex transaction id."""
    return _hex(f"tx:{key}", 40)


def deed(key: str, deed_type: int = 294) -> str:
    return f"I-{deed_type}-{_hex('deed:' + key, 14)}"


def _nonce(key: str) -> str:
    return _hex(f"n:{key}", 10)


D = Decimal


def _harvest_row(deed_uid: str, region: int, symbol: str, received: str, tax: str | None = None,
                 grain_eaten: str | None = None, inputs: dict[str, str] | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"deed_uid": deed_uid, "region_number": region, "resource_symbol": symbol}
    if tax is not None:
        row["resource_amount"] = float(D(received) + D(tax))
        row["tax_amount"] = float(D(tax))
    row["received_amount"] = float(D(received))
    if grain_eaten is not None:
        row["grain_eaten"] = float(D(grain_eaten))
    if inputs:
        row["inputs_consumed"] = [{"symbol": symbol_, "qty": float(D(qty))} for symbol_, qty in inputs.items()]
    row["result_message"] = "Successful"
    return row


def _lookup(tx_id: str, index: int, payload: dict[str, Any], data: Any, block: int, created: str) -> dict[str, Any]:
    result = {"success": True, "result": {"success": True, "data": data}}
    return {"trx_info": {
        "id": tx_id + (f"-{index}" if index else ""), "block_id": _hex(f"block:{block}", 40),
        "prev_block_id": _hex(f"block:{block - 1}", 40), "type": "land_operation", "player": PLAYER,
        "data": json.dumps(payload, separators=(",", ":")), "success": True, "error": None,
        "block_num": block, "created_date": created, "result": json.dumps(result, separators=(",", ":")),
        "steem_price": None, "sbd_price": None, "is_owner_auth": False}}


def harvest_all() -> dict[str, Any]:
    region = 23
    payload = harvest_all_op(PLAYER, REGIONS[str(region)], app=APP, n=_nonce("harvest_all")).json_payload
    eaten = "5239.282"   # every worksite eats GRAIN; the last row takes the remainder of 41914.259
    plots = [  # key, deed type, symbol, received, tax, SPS reward
        ("ha-grain-1", 290, "GRAIN", "20000.000", "1680.491", None),
        ("ha-grain-2", 300, "GRAIN", "15000.000", "1260.368", None),
        ("ha-wood-1", 294, "WOOD", "21000.000", "7000.000", "100.000"),
        ("ha-wood-2", 296, "WOOD", "18985.523", "6328.508", None),
        ("ha-stone-1", 298, "STONE", "8000.000", "2666.667", "85.836"),
        ("ha-stone-2", 297, "STONE", "6863.611", "2287.870", None),
        ("ha-iron-1", 295, "IRON", "3000.000", "1000.000", None),
        ("ha-iron-2", 303, "IRON", "2870.434", "956.811", None),
    ]
    rows = []
    for index, (key, deed_type, symbol, received, tax, sps) in enumerate(plots):
        uid = deed(key, deed_type)
        last = index == len(plots) - 1
        rows.append(_harvest_row(uid, region, symbol, received, tax,
                                 grain_eaten=str(D("41914.259") - 7 * D(eaten)) if last else eaten))
        if sps:
            rows.append(_harvest_row(uid, region, "SPS", sps))
    lookup = _lookup(tx("harvest_all"), 0, payload, {"results": rows}, 99_000_123, "2026-10-12T08:15:21.000Z")
    _check(rows, net={"GRAIN": "-6914.259", "WOOD": "39985.523", "STONE": "14863.611", "IRON": "5870.434",
                      "SPS": "185.836"}, tax_total="23180.715")
    return lookup


def harvest_resources() -> dict[str, Any]:
    region = 23
    uid = deed("hr-plot", 294)
    payload = harvest_resources_op(PLAYER, REGIONS[str(region)], uid, app=APP, n=_nonce("harvest_resources")).json_payload
    row = _harvest_row(uid, region, "SPS", "56.619", "6.291", grain_eaten="5690.925",
                       inputs={"WOOD": "2845.463", "STONE": "1138.185", "IRON": "284.546"})
    return _lookup(tx("harvest_resources"), 0, payload, {"results": [row]}, 99_000_456, "2026-10-12T09:02:48.000Z")


def tax_collection() -> dict[str, Any]:
    region = 23
    uid = deed("tc-keep", 293)
    payload = tax_collection_op(PLAYER, REGIONS[str(region)], uid, app=APP, n=_nonce("tax_collection")).json_payload
    row = {"deed_uid": uid, "region_number": region, "kingdom_grain_cost": 1000.0,
           "tokens": {"tokens": [{"token": "GRAIN", "received": 3721.62}, {"token": "WOOD", "received": 1488.648},
                                 {"token": "STONE", "received": 595.459}, {"token": "IRON", "received": 148.865}]},
           "result_message": "Successful"}
    return _lookup(tx("tax_collection"), 2, payload, {"results": [row]}, 99_000_789, "2026-10-12T10:30:03.000Z")


def tax_collection_fragments() -> dict[str, Any]:
    lookup_id = tx("tax_collection") + "-2"
    return {"status": "success", "data": [{
        "trx_id": lookup_id, "deed_uid": deed("tc-keep", 293), "reward_action_id": 4_100_017,
        "fragment_found": True, "fragment_type": "TOTEMFC", "labors_luck_uid": None,
        "created_date": "2026-10-12T10:30:03.000Z"}]}


def _check(rows: list[dict[str, Any]], *, net: dict[str, str], tax_total: str) -> None:
    from ceres.receipt_model import amounts

    values = amounts(rows)
    assert values["net"] == {symbol: float(D(value)) for symbol, value in net.items()}, values["net"]
    assert round(sum(values["tax"].values()), 3) == float(D(tax_total)), values["tax"]


def render() -> dict[str, str]:
    """File name -> file text, exactly as written to TARGET."""
    files = {
        "lookup-harvest_all.json": harvest_all(),
        "lookup-harvest_resources.json": harvest_resources(),
        "lookup-tax_collection-op2.json": tax_collection(),
        "fragment-history-tax_collection-op2.json": tax_collection_fragments(),
    }
    return {name: json.dumps(body, indent=1, sort_keys=True) + "\n" for name, body in files.items()}


def write(target: Path = TARGET) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    for name, text in render().items():
        (target / name).write_text(text, encoding="utf-8")
    return target


if __name__ == "__main__":
    print(write())
