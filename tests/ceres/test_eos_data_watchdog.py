from __future__ import annotations

from pathlib import Path

from .vm_context import read_entry_source, run_in_module_context


EOS = Path(__file__).parents[2] / "ceres" / "static" / "eos.js"
WATCHDOG = """  if (!session.dataReady) {
    if (session.dataWatchdogRevision !== revision) {
      session.dataWatchdogRevision = revision;
      timer(session, () => {
        if (session.artRevision === revision && session.mountedFrame && !session.error && !session.dataReady) hooks.redraw(session.uid);
      }, CARD_SERVICE_BUDGET_MS);
    }
    return;
  }
"""


def _phase_machine_body() -> str:
    return r"""
(async () => {
  const row = {deed_uid: 'watchdog-plot', capacity: {potential_card_capacity: 0}};
  const status = {textContent: '', title: ''};
  const frame = {
    dataset: {},
    style: {setProperty() {}, getPropertyValue() { return ''; }},
    querySelector(selector) { return selector === '.eos-status-text' ? status : null; },
    querySelectorAll() { return []; },
    setAttribute() {},
    hasAttribute() { return false; },
  };
  let redraws = 0;
  configureEos({
    generation: () => 1,
    geometry: () => ({width: 1920, font: 16}),
    row: () => row,
    redraw: () => { redraws++; beforePlotContentReplace(row.deed_uid, {readiness: true}); mountPlotPresentation(row, frame, {dataReady: true}); },
    acquireLease: () => null,
    cancelLease() {},
    close() {},
    leaseIsCurrent: () => false,
    releaseLease() {},
  });
  const session = beginPlotOpening(row, 1920, 16);
  session.animateAllowed = false;
  mountPlotPresentation(row, frame, {dataReady: false});
  await new Promise(resolve => setTimeout(resolve, 5));
  if (session.phase !== 'scene-visible') throw new Error(`scene did not release: ${session.phase}`);
  mountPlotPresentation(row, frame, {dataReady: false});
  await new Promise(resolve => setTimeout(resolve, 150));
  if (!['ready', 'degraded'].includes(frame.dataset.plotState)) throw new Error(`not terminal: ${frame.dataset.plotState}`);
  if (redraws !== 1) throw new Error(`expected one data redraw, got ${redraws}`);
})().catch(error => { console.error(error); throw error; });
"""


def test_data_phase_watchdog_keeps_empty_worker_mount_live_and_is_causal() -> None:
    source = read_entry_source(EOS)
    source = source.replace("const CARD_SERVICE_BUDGET_MS = 10000;", "const CARD_SERVICE_BUDGET_MS = 20;")
    source = source.replace("const SCENE_RELEASE_MS = 1200;", "const SCENE_RELEASE_MS = 0;")
    assert source.count("const CARD_RELEASE_MS = 1800;") == 1
    source = source.replace("const CARD_RELEASE_MS = 1800;", "const CARD_RELEASE_MS = 0;")
    run_in_module_context(_phase_machine_body(), entry=EOS, source=source)

    assert source.count(WATCHDOG) == 1
    removed = source.replace(WATCHDOG, "  if (!session.dataReady) return;\n", 1)
    result = run_in_module_context(_phase_machine_body(), entry=EOS, source=removed, check=False)
    assert result.returncode != 0
