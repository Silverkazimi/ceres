""" speed rule (brief item 8, IN-0708 "confirming swaps ... works super well and super fast"): no flow may confirm
slower than the pre-fix swap.

Deterministic fake-clock harness (the replay suite's fake world, stepped every 250 ms): the wallet answers 3 s after the
request and the transaction is in a Hive block at the same moment; the game indexes it `lag` seconds later. Six
scenarios: ack present / ack lost (the wallet answers without an id) x game lag 0 / 10 / 30 s. Measured: the virtual
time from the wallet request until the flow shows the confirmed result.

  pre-fix swap   = ceres/static of 15c6a6da5 (IR9545_BASELINE=<dir>/ceres/static), wallet swap card
  new, per flow  = this checkout: wallet swap card, harvest modal, and the gate + shared confirmer path that every other
                   flow uses (Changes, worksite, claim, DEC, deed lock, checkout, combine)
No live wallet measurement was possible (no real wallet, no signing in this queue).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.ceres import test_replay as replay
from tests.ceres.vm_context import read_entry_source

HERE = Path(__file__).resolve().parents[2] / "ceres" / "static"
BASE = Path(os.environ["IR9545_BASELINE"]) if os.environ.get("IR9545_BASELINE") else None
SCENARIOS = [(ack, lag) for ack in ("present", "lost") for lag in (0, 10, 30)]


def _knobs(ack, lag):
    return {"lag": lag, "ackMs": 3000, **({"wallet": "no-id"} if ack == "lost" else {})}


REAL_ADAPTERS = {
    "dec": ("regional-dec.js", ["render", "D", "drawer"], replay.DEC_PATCHES, replay.DEC),
    "checkout": ("card-checkout.js", ["render", "walletAccount", "co"], (), replay.CHECKOUT),
    "claim": ("wallet-claim.js", [], (("!walletReady(D.player)", "false"),), replay.CLAIM),
    "combine": ("combine-dialog.js", ["COMBINE_PREVIEW", "COMBINE_SELECTION", "COMBINE_DIALOG", "COMBINE_ARRIVAL", "COMBINE_VERIFY", "COMBINE_SIGN_STEP",
                                      "rebuildCombineForSigning", "combineReviewMatches", "renderCombineDialog", "combineLedgerData"], (), replay.COMBINE),
}


def _measure(flow, static, ack, lag):
    knobs = _knobs(ack, lag)
    if flow == "swap":
        entry = static / "wallet-lp.js"
        source = read_entry_source(entry).replace(*replay.SWAP_SOURCE_PATCH)
        body = replay.SWAP
    elif flow == "harvest":
        entry = static / "harvest-modal.js"
        source = read_entry_source(entry)
        source = source.rsplit("}", 1)[0] + """
Object.defineProperties(globalThis, { M: {get: () => M}, render: {get: () => render, set: value => {render = value;}},
  postJson: {get: () => postJson, set: value => {postJson = value;}} });
}
"""
        body = replay.HARVEST
    elif flow in REAL_ADAPTERS:
        # Fix round 1 (astra "Speed test measures swap, harvest, and generic gate ... not every signing path"): the real
        # flow adapters of the replay suite, under the same scenarios.
        name, exposed, patches, body = REAL_ADAPTERS[flow]
        replay_static, replay.STATIC = replay.STATIC, static
        try:
            entry, source = replay._exposed(name, exposed, patches)
        finally:
            replay.STATIC = replay_static
    else:
        entry, source, body = static / "broadcast-gate.js", None, replay.GATE
    body = body.replace("SCENARIO", json.dumps(knobs)).replace("LIMIT", "300000").replace("STEP", "250")
    outcome = replay._run(body, entry, source)
    assert outcome["state"] == "confirmed", (flow, ack, lag, outcome)
    return outcome["at"] / 1000


@pytest.mark.skipif(BASE is None, reason="set IR9545_BASELINE to the pre-fix ceres/static directory")
def test_no_flow_is_slower_than_the_pre_fix_swap():
    table = []
    for ack, lag in SCENARIOS:
        before = _measure("swap", BASE, ack, lag)
        after = {flow: _measure(flow, HERE, ack, lag) for flow in ("swap", "harvest", "gate", *REAL_ADAPTERS)}
        table.append({"ack": ack, "lag_s": lag, "pre_fix_swap_s": before, **{f"new_{k}_s": v for k, v in after.items()}})
    print("IR9545-SPEED " + json.dumps(table))
    out = os.environ.get("IR9545_SPEED_OUT")
    if out:
        Path(out).write_text(json.dumps(table, indent=1), encoding="utf-8")
    slower = [(row, k) for row in table for k in row if k.startswith("new_") and row[k] > row["pre_fix_swap_s"]]
    assert not slower, slower
