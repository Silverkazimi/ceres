""" round 3: C01 a queued post-combine capture is not a finished one and a failed board load is never reported as success; C02 the mismatch text
shows the three decimals that cause the hold."""
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def run_settle(statuses, loads):
    return run_in_module_context(
        r"""
(async () => {
CURRENT_PLAYER = 'player0005'; let polls = 0, loadCalls = 0, after = 0; const timers = []; const notices = [];
const statuses = __STATUSES; const results = __LOADS;
fetch = async () => ({ json: async () => ({ state: statuses[Math.min(polls++, statuses.length - 1)] }) });
loadBoard = async (opts) => { if (!opts.fromCompletedCapture) throw Error('extra capture'); return results[Math.min(loadCalls++, results.length - 1)]; };
stageToast = (message, bad) => notices.push([message, Boolean(bad)]);
setTimeout = (fn) => { timers.push(fn); };
await settleCombineRefresh({ triggered: true }, { player: 'player0005', after: () => after++ });
let guard = 0; while (timers.length && guard++ < 20) await timers.shift()();
console.log(JSON.stringify({ polls, loadCalls, after, notices }));
})().catch(e => { console.error(e); process.exitCode = 1; });
""".replace("__STATUSES", statuses).replace("__LOADS", loads),
        entry=STATIC / "combine.js",
    )


def test_a_queued_capture_is_polled_again_before_the_board_is_adopted():
    r = run_settle("['queued', 'queued', 'complete']", "[true]")
    import json
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["polls"] == 3 and out["loadCalls"] == 1 and out["after"] == 1
    assert out["notices"][-1][0] == "Board updated with the combined card."


def test_a_failed_board_load_is_retried_and_never_reported_as_success():
    import json
    r = run_settle("['complete']", "[false, false, true]")
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["loadCalls"] == 3 and out["after"] == 1
    r = run_settle("['complete']", "[false]")
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["after"] == 0 and out["loadCalls"] == 5
    assert not any(text == "Board updated with the combined card." for text, _ in out["notices"])
    assert out["notices"][-1][1] is True and "could not be refreshed" in out["notices"][-1][0]


def test_the_mismatch_text_shows_the_decimals_that_cause_the_hold():
    source = (STATIC / "combine-chain-ui.js").read_text()
    assert "base PP ${num(plan.expected.base_pp, 3)}" in source and "base PP ${num(plan.result?.base_pp, 3)}" in source
