"""Lock sorting uses current or staged target state and leaves unknowns last."""

import json

from .vm_context import run_in_module_context


def test_lock_sort_direction_staged_targets_unknown_last_and_stable_ties():
    result = run_in_module_context(r"""
    const rows = [
 {deed_uid: 'u1', plot: '1-1-1', lock_days: 0, unlock_date: null},
 {deed_uid: 'l1', plot: '1-1-2', lock_days: 10, unlock_date: null},
 {deed_uid: 'unknown', plot: '1-1-3'},
 {deed_uid: 'u2', plot: '1-1-4', lock_days: 0, unlock_date: null},
 {deed_uid: 'l2', plot: '1-1-5', lock_days: 0, unlock_date: null},
 ];
 STAGED_PLOT_LOCKS.set('l2', {action: 'lock'});
 STAGED_PLOT_LOCKS.set('l1', {action: 'unlock'});
 S.SORT_KEY = 'lock_state';
 S.SORT_DIR = 1;
 const ascending = sortRows(rows).map(row => row.deed_uid);
 S.SORT_DIR = -1;
 const descending = sortRows(rows).map(row => row.deed_uid);
 console.log('__LOCK_SORT__' + JSON.stringify({ascending, descending}));
    """)
    output = result.stdout.split("__LOCK_SORT__", 1)
    assert len(output) == 2, result.stdout
    records = json.loads(output[1].splitlines()[0])
    assert records["ascending"] == ["u1", "l1", "u2", "l2", "unknown"]
    assert records["descending"] == ["l2", "u1", "l1", "u2", "unknown"]


def test_signed_lock_change_awaiting_confirmation_sorts_by_its_shown_current_state():
    # The icon shows the current state once a change is signed (txId); sorting must agree with it.
    result = run_in_module_context(r"""
    const rows = [
 {deed_uid: 'l1', plot: '1-1-1', lock_days: 10, unlock_date: null},
 {deed_uid: 'u1', plot: '1-1-2', lock_days: 0, unlock_date: null},
 ];
 STAGED_PLOT_LOCKS.set('l1', {action: 'unlock', txId: 'a'.repeat(40)});
 S.SORT_KEY = 'lock_state';
 S.SORT_DIR = 1;
 console.log('__LOCK_SORT__' + JSON.stringify(sortRows(rows).map(row => row.deed_uid)));
    """)
    output = result.stdout.split("__LOCK_SORT__", 1)
    assert len(output) == 2, result.stdout
    assert json.loads(output[1].splitlines()[0]) == ["u1", "l1"]
