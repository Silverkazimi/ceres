""" receipt arithmetic and terminal harvest verification guards."""
from pathlib import Path

import pytest

from ceres.receipt_model import amounts
from tests.ceres.vm_context import read_entry_source, run_in_module_context


MODAL = Path(__file__).parents[2] / "ceres" / "static" / "harvest-modal.js"


def _source_with_render_bindings():
    source = read_entry_source(MODAL)
    return source.rsplit("}", 1)[0] + """
Object.defineProperties(globalThis, {
  ir8369Render: {get: () => render},
  ir8369M: {get: () => M},
  ir8369Close: {get: () => closeHarvestModal},
  ir8369RenderInner: {set: value => {renderInner = value;}}
});
}
"""


def _grain_row(**changes):
    row = {
        "resource_symbol": "GRAIN",
        "resource_amount": 741.951,
        "tax_amount": 74.195,
        "grain_rewards_eaten": 60.039,
        "received_amount": 607.717,
        "grain_eaten": 12.0,
        "dec_spent": 0,
        "inputs_consumed": [],
    }
    row.update(changes)
    return row


def test_grain_reward_deduction_reconciles_and_is_counted_once():
    result = amounts([_grain_row()])
    assert result["before_tax"]["GRAIN"] == 741.951
    assert result["received"]["GRAIN"] == 607.717
    assert result["costs"]["GRAIN"] == 12.0
    assert result["net"]["GRAIN"] == 595.717


def test_row_without_grain_rewards_eaten_uses_original_equation():
    row = _grain_row(received_amount=667.756)
    del row["grain_rewards_eaten"]
    result = amounts([row])
    assert result["received"]["GRAIN"] == 667.756
    assert result["net"]["GRAIN"] == 655.756


def test_genuine_mismatch_still_fails_reconciliation():
    with pytest.raises(ValueError, match="game harvest amounts do not reconcile"):
        amounts([_grain_row(received_amount=607.0)])


def test_non_grain_rewards_eaten_remains_a_grain_cost():
    result = amounts([_grain_row(resource_symbol="WOOD", received_amount=667.756)])
    assert result["received"]["WOOD"] == 667.756
    assert result["costs"]["GRAIN"] == 72.039
    assert result["net"]["GRAIN"] == -72.039


def test_unreconcilable_amounts_stay_pending_through_the_shared_confirmer():
    """ replaces the 150 s deadline: unreadable game amounts keep an honest pending line while the shared
    confirmer reads on; nothing ends in a manual "Check now" state."""
    source = MODAL.read_text()
    assert "VERIFY_SCHEDULE_S" not in source and "VERIFY_MAX_WAIT_MS" not in source and "deadlineTimer" not in source
    assert "harvestAmountsUnreconcilable(result?.members) ? VERIFY_AMOUNTS_TEXT" in source
    assert "verification_incomplete" not in source and "pollNow" not in source
    assert "confirmAction({ account, operations, txId, check, onUpdate })" in source


def test_render_throw_falls_back_to_closable_receipt_with_link_and_no_manual_check():
    run_in_module_context(r"""
(async () => {
  const M = ir8369M;
  const closeHarvestModal = ir8369Close;
  const panel = {innerHTML: ''};
  const overlay = {hidden: false, querySelector: () => panel};
  document.getElementById = id => id === 'harvest-modal-overlay' ? overlay : null;
  Object.assign(M, {open:true, player:'alice', routing:null, routingRunId:null,
    routingTerminal:true, returnFocus:null, unsubscribe:null,
    run:{phase:'success', busy:false, txId:'a'.repeat(40), members:[], verifyIds:new Set(['attempt']), pollNow:() => {}}});
  ir8369RenderInner = () => { throw Error('receipt renderer failed'); };
  ir8369Render();
  if (M.run.busy || !M.run.renderFailed) throw Error('fallback did not become closable');
  if (!panel.innerHTML.includes('Harvest sent') ||
      !panel.innerHTML.includes('https://hivehub.dev/tx/' + 'a'.repeat(40)) ||
      panel.innerHTML.includes('check-now') || /Check (now|again)/.test(panel.innerHTML) ||
      !panel.innerHTML.includes('data-hm="close"')) throw Error(panel.innerHTML);
  await closeHarvestModal();
  if (M.open || !overlay.hidden) throw Error('Close did not dismiss fallback');
})().then(() => process.exit(0), error => {console.error(error);process.exit(1);});
""", entry=MODAL, source=_source_with_render_bindings())
