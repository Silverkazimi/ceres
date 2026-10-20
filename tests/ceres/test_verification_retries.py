""" a verification check that could not run keeps checking by itself.

Owner, 2026-09-29: "The feedback pill hangs on orange, zero changes. No green, no red. This is a failure and must
never be like this." A HiveAuth unstake (accepted by the game) was signed while Ceres restarted; the first
/api/op-verify request failed, and the catch branch of verifyBroadcast() reported "Could not check the game state"
and scheduled nothing, so the pill never left orange. Also: "the orange feedback pill ... is missing its spinner".
"""
from __future__ import annotations

from pathlib import Path

from .vm_context import run_in_module_context

OPERATIONS = Path(__file__).parents[2] / "ceres" / "static" / "operations.js"

_SCHEDULE = "window.__ceresOperationPollSchedule = [0, 0];"


def _run(body: str) -> None:
    run_in_module_context(body, entry=OPERATIONS, extra_context_js=_SCHEDULE)


def test_failed_check_schedules_the_next_one_and_spins():
    _run(r"""
(async () => {
 let calls = 0;
 fetch = async (url) => {
 if (String(url).includes('/api/op-verify')) {
 calls += 1;
 throw new TypeError('Failed to fetch');
 }
 return { ok: true, status: 200, json: async () => ({}) };
 };
 const chunk = { operations: [['custom_json', { id: 'sm_stake_change', json: '{}' }]] };
 const review = { scope: 'portfolio' };
 await verifyBroadcast(chunk, null, 'a'.repeat(40), review).catch(() => {});
 if (calls !== 1) throw Error('first check did not run');
 const banner = CERES_CONFIRMATION_BANNER;
 if (!banner || banner.state !== 'is-unresolved') throw Error('no orange pill: ' + JSON.stringify(banner));
 if (banner.waiting !== true) throw Error('the orange pill must spin while Ceres keeps checking ()');
 if (!/checks again automatically/.test(banner.detail)) throw Error('pill must say it checks again: ' + banner.detail);
 const poller = OPERATION_POLLERS.get(chunk.entryId);
 if (!poller || poller.timer == null) throw Error('no automatic re-check was scheduled ()');
 await new Promise((resolve) => setTimeout(resolve, 20));
 if (calls < 2) throw Error('the scheduled re-check never ran');
})().then(() => process.exit(0), (error) => { console.error(error); process.exit(1); });
""")


def test_spinner_stops_once_the_automatic_schedule_is_used_up():
    _run(r"""
(async () => {
 fetch = async (url) => {
 if (String(url).includes('/api/op-verify')) throw new TypeError('Failed to fetch');
 return { ok: true, status: 200, json: async () => ({}) };
 };
 const chunk = { operations: [['custom_json', { id: 'sm_stake_change', json: '{}' }]] };
 await verifyBroadcast(chunk, null, 'b'.repeat(40), { scope: 'portfolio' }).catch(() => {});
 await new Promise((resolve) => setTimeout(resolve, 50));
 const banner = CERES_CONFIRMATION_BANNER;
 if (banner.waiting !== false) throw Error('a paused pill must not spin');
 if (banner.action?.label !== 'Check again') throw Error('a paused pill keeps Check again');
})().then(() => process.exit(0), (error) => { console.error(error); process.exit(1); });
""")
