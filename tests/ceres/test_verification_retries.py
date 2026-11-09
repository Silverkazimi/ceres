""" / : a verification check that could not run keeps checking by itself.

Owner, 2026-09-29: "The feedback pill hangs on orange, zero changes. No green, no red. This is a failure and must
never be like this." A HiveAuth unstake (accepted by the game) was signed while Ceres restarted; the first
/api/op-verify request failed, and the catch branch of verifyBroadcast() reported "Could not check the game state"
and scheduled nothing, so the pill never left orange. Also: "the orange feedback pill ... is missing its spinner".
"""
from __future__ import annotations

from pathlib import Path

from .vm_context import run_in_module_context

OPERATIONS = Path(__file__).parents[2] / "ceres" / "static" / "operations.js"

# the operations poll schedule hook is gone; the shared confirmer's clock seam is frozen instead.
_FREEZE = "__setConfirmerClock(Object.fromEntries([['sleep', async () => undefined]]));\n"


def _run(body: str, marker: str | None = None) -> None:
    result = run_in_module_context(_FREEZE + body, entry=OPERATIONS)
    if marker:
        assert marker in result.stdout   # a pending promise exits 0 too; the marker proves the checks ran


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
  // the re-check is the shared confirmer's wait (its clock frozen here), not an operations.js timer.
  const poller = OPERATION_POLLERS.get(chunk.entryId);
  if (!poller || !poller.handle) throw Error('no automatic re-check was scheduled ()');
  for (let i = 0; i < 200 && calls < 3; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
  if (calls < 3) throw Error('the scheduled re-checks did not keep running: ' + calls);
  console.log('IR8482_RECHECK_RAN');
})().then(() => process.exit(0), (error) => { console.error(error); process.exit(1); });
""", marker='IR8482_RECHECK_RAN')


def test_the_automatic_schedule_is_never_used_up():
    """ replaces this  rule (the spinner stopped and "Check again" waited once the schedule ran out)."""
    ops = (Path(__file__).parents[2] / "ceres" / "static" / "operations.js").read_text(encoding="utf-8")
    assert "pollingPaused" not in ops and "Automatic checks have paused" not in ops
    assert "waitThrough({ key: `operation:${receipt.entryId}`" in ops
