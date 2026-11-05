"""IN-0860 planning edits repaint the affected row and totals without a table render."""
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

ROOT = Path(__file__).resolve().parents[2]


def test_planning_stage_uses_one_mounted_row_and_totals():
    script = r"""
    (async () => {
      document.body.dataset = { boardMode: 'planning' };
      BOARD = { player: 'test-player', generated_at: 'stamp', plots: [
        { deed_uid: 'changed', plot: '1-1-1' }, { deed_uid: 'open-elsewhere', plot: '1-1-2' },
        { deed_uid: 'other', plot: '1-1-3' }], totals: {} };
      const touched = [];
      const totals = { innerHTML: '' };
      document.getElementById = (id) => id === 'plot-tbody'
        ? { querySelector(selector) { touched.push(selector); return null; } }
        : id === 'totals-strip' ? totals : null;
      window.dispatchEvent = (event) => {
        for (const listener of __ceresListeners[event.type] || []) listener(event);
        return true;
      };
      await boardPlanningStore().setPlacement({ deed_uid: 'changed', group_id: 'worker',
        slot_index: 0, asset_ref: 'worker-1', asset: { name: 'Worker' } });
      if (touched.length !== 1 || !touched[0].includes('changed'))
        throw new Error(`stage touched other rows or missed its row: ${JSON.stringify(touched)}`);
      if (touched.some((selector) => selector.includes('open-elsewhere') || selector.includes('other')))
        throw new Error('an unrelated row was repainted');
      if (!totals.innerHTML.includes('Total PP:') || !totals.innerHTML.includes('Unknown'))
        throw new Error('planning totals were not refreshed from the changed row');
      process.exit(0);
    })().catch((error) => { console.error(error); process.exit(1); });
    """
    result = run_in_module_context(script, extra_context_js="context.__ceresListeners = __ceresListeners;", check=False)
    assert result.returncode == 0, result.stderr[-4000:] + result.stdout[-1000:]


def test_planning_detail_completion_guards_full_table_render():
    rows = (ROOT / 'ceres/static/rows.js').read_text()
    assert "document.getElementById('badge-filter')?.value && !boardPlanningActive()" in rows
    assert "for (const deedUid of event?.detail?.deeds || []) updateMountedRow(deedUid);" in rows
