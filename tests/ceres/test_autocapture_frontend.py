"""Frontend race coverage for confirmed-plot refreshes.

The former auto-capture tests were removed with ``maybeAutoCapture``,
``pollAutoCapture``, and ``capture-age``. The page-load and stale-board read
properties now live in ``test_board_r111_guards.py``.
"""

from __future__ import annotations

from .vm_context import run_in_module_context


def test_confirmed_plot_refresh_rejects_stale_response_but_updates_current_row() -> None:
    """A late response must not overwrite a newer row for the same deed.

 Two concurrent calls race: the first resolves while current and updates the
 row; the second resolves after a newer row has replaced it and must be dropped.
    """
    body_js = """
BOARD_GENERATION = 1;
CURRENT_PLAYER = 'player0005';
const mountedUpdates = [];
playerQueryAppend = (url) => url;
updateMountedRow = (deedUid) => { mountedUpdates.push(deedUid); };
refreshBoardTotals = () => {};

(async () => {
 const deedUid = 'I-294-5ee4a78c89c096';
 const initial = { deed_uid: deedUid, workers: [{ uid: 'old-card' }] };
 const confirmed = { deed_uid: deedUid, workers: [] };
 BOARD = { plots: [initial] };
 CURRENT_ROWS = [initial];
 let resolveCurrent;
 fetch = () => new Promise((resolve) => { resolveCurrent = resolve; });

 const currentRefresh = refreshConfirmedPlot(deedUid);
 resolveCurrent({ ok: true, json: async () => confirmed });
 await currentRefresh;
 if (BOARD.plots[0] !== confirmed || CURRENT_ROWS[0] !== confirmed) throw new Error('the current (non-stale) refresh did not land');

 const newer = { deed_uid: deedUid, workers: [] };
 let resolveStale;
 fetch = () => new Promise((resolve) => { resolveStale = resolve; });
 const staleRefresh = refreshConfirmedPlot(deedUid);
 const evenOlder = { deed_uid: deedUid, workers: [{ uid: 'C3-82-6KHURMQ2VK' }] };
 BOARD.plots[0] = newer;
 CURRENT_ROWS[0] = newer;
 resolveStale({ ok: true, json: async () => evenOlder });
 await staleRefresh;
 if (BOARD.plots[0] !== newer || CURRENT_ROWS[0] !== newer) throw new Error('the stale response overwrote the newer row');
 if (BOARD.plots[0].workers.some((worker) => worker.uid === 'C3-82-6KHURMQ2VK')) throw new Error('the stale response merged its own worker list into the newer row');
 if (mountedUpdates.length !== 1) throw new Error('updateMountedRow must fire once, for the current refresh only -- got ' + mountedUpdates.length);
})();
"""
    run_in_module_context(body_js)
