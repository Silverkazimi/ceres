"""Hermetic regression checks for the avatar, cards refresh and duplicate stage fixes."""

from pathlib import Path

from ceres.stage_plans import check_plans
from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_duplicate_cooling_card_is_dropped_on_the_later_plan():
    report = {
        "plots": [{"deed_uid": "plot-a"}, {"deed_uid": "plot-b"}],
        "workers": [], "bonus_assets": [],
        "cards": [{"asset_ref": "copy-1", "uid": "copy-1", "classification": {"bucket": "stake_expired_or_historical"},
                   "facts": {"uid": "copy-1", "name": "Card", "stake_end_date": "2099-01-01T00:00:00Z"}}],
    }
    plans = [{"key": key, "deed_uid": deed, "group_id": "worker", "slot_index": 0, "action": "stake", "asset_ref": "copy-1"}
             for key, deed in [("first", "plot-a"), ("second", "plot-b")]]
    result = check_plans(report, plans, account="owner")
    assert result[0]["verdict"] == "ok"
    assert result[1] == {"key": "second", "verdict": "drop", "reason": "duplicate_staged"}


def test_two_devices_merge_keeps_newest_ref_and_tombstones_other():
    run_in_module_context(r"""
const old = { deed_uid: 'a', group_id: 'worker', slot_index: 0, action: 'stake', asset_ref: 'uid-1', created_at: '2026-10-16T01:00:00Z' };
const newer = { ...old, deed_uid: 'b', created_at: '2026-10-16T02:00:00Z' };
const merged = mergeStaged({ plans: { 'a|worker|0': old } }, { plans: { 'b|worker|0': newer } });
if (Object.keys(merged.plans).join() !== 'b|worker|0' || !merged.removed['a|worker|0']) throw Error(JSON.stringify(merged));
const restored = mergeStaged({ plans: { 'a|worker|0': old, 'b|worker|0': newer } }, {});
if (Object.keys(restored.plans).join() !== 'b|worker|0') throw Error('single-device restore kept duplicate');
""", entry=STATIC / "account-space.js")


def test_staging_and_signature_gate_reject_duplicate_unsigned_ref():
    run_in_module_context(r"""
CERES_STAGE.push({ deed_uid: 'a', group_id: 'worker', slot_index: 0, action: 'stake', asset_ref: 'uid-1' });
if (stageForSlot('b', 'worker', 0, 'stake', 'uid-1') !== false) throw Error('duplicate stage accepted');
if (CERES_STAGE.length !== 1) throw Error('stage mutated');
CERES_STAGE.push({ deed_uid: 'b', group_id: 'worker', slot_index: 0, action: 'replace', asset_ref: 'uid-1' });
if (duplicateUnsignedStakeRef() !== 'uid-1') throw Error('signing gate missed duplicate');
CERES_STAGE.length = 0;
PLANS.pending.set('a|worker|0', { deed_uid: 'a', group_id: 'worker', slot_index: 0, action: 'stake', asset_ref: 'uid-2' });
if (stageForSlot('b', 'worker', 0, 'stake', 'uid-2') !== false) throw Error('pending ref was not reserved');
""", entry=STATIC / "app.js")


def test_avatar_retries_and_keeps_last_good_image():
    run_in_module_context(r"""
(async () => {
 const timers = [];
 setTimeout = fn => { timers.push(fn); return timers.length; };
 const box = { hidden: false, dataset: {}, children: [], querySelector(tag) { return tag === 'img' ? this.children.find(x => x.tag === 'img') : null; },
 replaceChildren(...children) { this.children = children; }, removeAttribute() {} };
 const status = { hidden: false, textContent: '', dataset: {} };
 document.getElementById = id => id === 'player-avatar' ? box : id === 'player-refresh-status' ? status : null;
 document.createElement = tag => ({ tag, className: '', setAttribute() {} });
 let reads = 0;
 playerAvatarLayers = async () => { reads++; if (reads < 3) throw Error('transient'); return ['ok', 'broken']; };
 Image = class { constructor() { this.tag = 'img'; } decode() { return this.src === 'ok' ? Promise.resolve() : Promise.reject(Error('layer')); } };
 syncPlayerAvatar('alice');
 for (let i = 0; i < 12; i++) { await Promise.resolve(); if (timers.length) timers.shift()(); }
 if (reads !== 3 || box.hidden || box.children.filter(x => x.tag === 'img').length !== 1) throw Error('avatar retry/partial failure');
 playerAvatarLayers = async () => { throw Error('failure'); };
 syncPlayerAvatar('bob');
 for (let i = 0; i < 12; i++) { await Promise.resolve(); if (timers.length) timers.shift()(); }
 if (box.hidden || !box.querySelector('img')) throw Error('last good avatar hidden');
})().catch(e => { console.error(e); process.exit(1); });
""", entry=STATIC / "app.js")
