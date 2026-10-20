""": opt-in space validates chain shape and merges two devices safely."""
from copy import deepcopy
from pathlib import Path

import pytest

from ceres.player_space import SpaceError, validate_payload
from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def plan(state="planned"):
    return {"version": 2, "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-02T00:00:00Z",
            "state": state, "origin": {"kind": "picker", "asset_uid": "C-A"}, "card": {"name": "Card"},
            "expected": {"bcx": 2}, "inputs": [{"uid": "C-A", "cc": 1, "kind": "staked", "status": "active", "unstake": None}]}


def test_server_accepts_shape_and_rejects_oversize_and_duplicate_inputs():
    valid = {"plans": {}, "chains": {"cc-one": plan()}}
    validate_payload("staged", valid)
    with pytest.raises(SpaceError):
        validate_payload("staged", {"chains": {f"cc-{i}": plan() for i in range(201)}})
    broken = deepcopy(valid)
    broken["chains"]["cc-one"]["inputs"].append(deepcopy(broken["chains"]["cc-one"]["inputs"][0]))
    with pytest.raises(SpaceError):
        validate_payload("staged", broken)


def test_client_merges_devices_tombstones_and_signed_unstake():
    run_in_module_context(r"""
const input = (status, unstake = null) => ({uid:'C-A', cc:1, kind:'staked', status, unstake});
const plan = (state, at, inputs) => ({version:2, created_at:'2026-10-01T00:00:00Z', updated_at:at, state, inputs});
const signed = {tx_id:'tx-1', signed_at:'2026-10-02T00:00:00Z', confirmed_at:null};
const remote = {chains:{'cc-one':plan('cooling','2026-10-03T00:00:00Z',[input('active',signed)]),'cc-two':plan('planned','2026-10-02T00:00:00Z',[])}};
const local = {chains:{'cc-one':plan('ready','2026-10-04T00:00:00Z',[input('left_out')]),'cc-three':plan('planned','2026-10-02T00:00:00Z',[])}};
const merged = mergeStaged(local,remote);
if (Object.keys(merged.chains).length !== 3) throw Error('device plan lost');
if (merged.chains['cc-one'].state !== 'ready' || merged.chains['cc-one'].inputs[0].status !== 'left_out') throw Error('rank lost');
if (merged.chains['cc-one'].inputs[0].unstake?.tx_id !== 'tx-1') throw Error('signed unstake lost');
const cancelled = {chains:{'cc-one':plan('cancelled','2026-10-05T00:00:00Z',[])}};
const tombstone = mergeStaged(merged,cancelled).chains['cc-one'];
if (tombstone.state !== 'cancelled' || tombstone.inputs[0].unstake?.tx_id !== 'tx-1') throw Error('tombstone or signed record lost');
const revived = mergeStaged({chains:{'cc-one':plan('planned','2026-10-06T00:00:00Z',[input('active')])}},{chains:{'cc-one':tombstone}}).chains['cc-one'];
if (revived.state !== 'cancelled' || revived.inputs[0].unstake?.tx_id !== 'tx-1') throw Error(' A09: cancelled id resurrected');
const equalRank = mergeStaged({chains:{'cc-one':plan('planned','2026-10-06T00:00:00Z',[input('active')])}},
  {chains:{'cc-one':plan('planned','2026-10-05T00:00:00Z',[{...input('active'), cc:2}])}}).chains['cc-one'];
if (equalRank.inputs[0].cc !== 1) throw Error('input edit timestamp lost');
if (stagedDigest(merged) === stagedDigest({...merged,chains:{}})) throw Error('chains absent from digest');
""", entry=STATIC / "account-space.js")
