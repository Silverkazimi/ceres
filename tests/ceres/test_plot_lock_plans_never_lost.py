""" (audit A09/A10): a staged plot-lock plan is removed only by the player's Undo or by the chain's own proof."""
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

PLOT_LOCK = Path(__file__).resolve().parents[2] / "ceres" / "static" / "plot-lock.js"


def test_unreadable_entries_unknown_facts_and_another_tabs_plans_survive():
    run_in_module_context(r"""
const KEY = 'ceres:plot-lock-plan:v1:alice';
const good = { deed_uid: 'I-1-aaaaaaaa', action: 'lock', baselineDays: null, baselineUnlockDate: null, lockDays: 3 };
const odd = { deed_uid: 'I-9-zzzzzzzz', action: 'future-action', extra: 1 };
localStorage.setItem(KEY, JSON.stringify([good, odd]));
const row = { deed_uid: 'I-1-aaaaaaaa', plot: '1-1-1', lock_days: null, unlock_date: null };
reconcilePlotLockPlans({ player: 'alice', plots: [row] });
if (!STAGED_PLOT_LOCKS.has(good.deed_uid)) throw Error('the plan was not loaded');
let saved = JSON.parse(localStorage.getItem(KEY));
if (!saved.some(entry => entry.deed_uid === odd.deed_uid && entry.extra === 1)) throw Error('an entry this version does not understand was dropped: ' + JSON.stringify(saved));
// the lock facts of the plot are not known yet: the plan stays (blocked), it is never deleted
reconcilePlotLockPlans({ player: 'alice', plots: [{ deed_uid: 'I-1-aaaaaaaa', plot: '1-1-1' }] });
if (!STAGED_PLOT_LOCKS.has(good.deed_uid)) throw Error('unknown lock facts deleted the plan');
// another tab added a plan: this tab's next save keeps it
const other = { deed_uid: 'I-2-bbbbbbbb', action: 'unlock', baselineDays: 3, baselineUnlockDate: null, lockDays: 3 };
localStorage.setItem(KEY, JSON.stringify([...JSON.parse(localStorage.getItem(KEY)), other]));
const second = { deed_uid: 'I-3-cccccccc', plot: '3', lock_days: null, unlock_date: null };
reconcilePlotLockPlans({ player: 'alice', plots: [row, second, { deed_uid: 'I-2-bbbbbbbb', plot: '2', lock_days: 3, unlock_date: null }] });
togglePlotLock(second);
saved = JSON.parse(localStorage.getItem(KEY)).map(entry => entry.deed_uid).sort().join(',');
if (saved !== 'I-1-aaaaaaaa,I-2-bbbbbbbb,I-3-cccccccc,I-9-zzzzzzzz') throw Error('a save lost a plan: ' + saved);
// the player's own Undo is final: it is not brought back by the other tab's copy
togglePlotLock(second);
if (JSON.parse(localStorage.getItem(KEY)).some(entry => entry.deed_uid === 'I-3-cccccccc')) throw Error('Undo did not remove it');
// an unreadable record is moved aside, not overwritten
localStorage.setItem('ceres:plot-lock-plan:v1:bob', '{not json');
const written = [];
const realSet = localStorage.setItem.bind(localStorage);
localStorage.setItem = (k, v) => { written.push([k, v]); realSet(k, v); };
reconcilePlotLockPlans({ player: 'bob', plots: [] });
if (!written.some(([k, v]) => k.startsWith('ceres:plot-lock-plan:v1:bob:corrupt:') && v === '{not json')) throw Error('the unreadable record was not kept');
""", entry=PLOT_LOCK)
