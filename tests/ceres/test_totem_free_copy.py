""": a tier choice must use a free item uid, never move another plan."""

from pathlib import Path

from tests.ceres.vm_context import run_in_module_context


STATIC = Path(__file__).parents[2] / "ceres" / "static"
ACTIONS = (STATIC / "picker-actions.js").read_text(encoding="utf-8")
CHOOSE = ACTIONS[ACTIONS.index("export async function handleSlotEditorClick"):ACTIONS.index("export function handlePickerFilter")].replace(
    "export async function handleSlotEditorClick", "async function handleSlotEditorClick"
)


def test_stale_totem_tier_uses_free_copy_and_never_moves_earlier_stage():
    run_in_module_context(
        r"""
(async () => {
  const first = { asset_ref: 'I-318-a', name: 'Common Totem', rarity: 'common' };
  const second = { ...first, asset_ref: 'I-318-b' };
  const row = { deed_uid: 'plot-b', plot: '1-1-2' };
  const picker = { deedUid: row.deed_uid, row, groupId: 'totem', slotIndex: 0,
    mode: 'stake', currentOccupant: null, candidates: [first, second],
    rarityGroups: { common: { count: 2, candidate: first } }, filters: {} };
  S.CERES_PICKER = picker;
  CERES_STAGE.push({ deed_uid: 'plot-a', group_id: 'totem', slot_index: 0,
    action: 'stake', asset_ref: first.asset_ref });
  const action = { dataset: { pickerAction: 'choose', pickerRarity: 'common' }, closest: () => null };
  const event = { target: { closest: selector => selector === '[data-picker-action]' ? action : null }, preventDefault() {}, stopPropagation() {} };
  await handleSlotEditorClick(event);
  if (removed.length) throw Error('choosing a tier removed the earlier plot stage');
  if (CERES_STAGE.length !== 2 || CERES_STAGE[1].asset_ref !== second.asset_ref)
    throw Error('the free copy was not staged: ' + JSON.stringify({ stage: CERES_STAGE, removed, toasts }));
  if (toasts.some(([text, error]) => error)) throw Error('choosing a free tier showed an error toast');
  S.CERES_PICKER = picker;
  await handleSlotEditorClick(event);
  if (CERES_STAGE.length !== 2 || removed.length || toasts.some(([text, error]) => error))
    throw Error('exhausted tier changed stages or showed an error toast');
  picker.rarityGroups.common.count = 3;
  S.CERES_PICKER = picker;
  await handleSlotEditorClick(event);
  if (fetches !== 1 || CERES_STAGE.length !== 3 || CERES_STAGE[2].asset_ref !== 'I-318-c' || removed.length)
    throw Error('free copy beyond loaded page was not selected');
})().catch(error => { console.error(error); process.exit(1); });
""",
        entry=STATIC / "picker-actions.js",
        source=CHOOSE,
        extra_context_js=r"""
context.S = { CERES_PICKER: null };
context.BOARD = { plots: [{ deed_uid: 'plot-b', plot: '1-1-2' }] };
context.CERES_STAGE = [];
context.removed = [];
context.toasts = [];
context.fetches = 0;
context.candidateQuery = () => '/api/plots/plot-b/candidates?group_id=totem';
context.playerQueryAppend = url => url;
context.fetch = async () => { context.fetches++; return { ok: true, json: async () => ({
  candidates: [{ asset_ref: 'I-318-c', name: 'Common Totem', rarity: 'common' }],
}) }; };
context.editorGroups = () => [{ id: 'totem' }];
context.editorOccupant = candidate => ({ asset_ref: candidate.asset_ref, name: candidate.name });
context.candidateDelta = () => 0;
context.plannedPlacementReason = () => '';
context.stageForSlot = (deed, group, slot, action, ref) => {
  if (context.CERES_STAGE.some(entry => entry.asset_ref === ref)) {
    context.toasts.push(['already staged', true]); return false;
  }
  context.CERES_STAGE.push({ deed_uid: deed, group_id: group, slot_index: slot, action, asset_ref: ref });
  return true;
};
context.removeCheckoutStage = async entry => { context.removed.push(entry.asset_ref); context.CERES_STAGE.splice(context.CERES_STAGE.indexOf(entry), 1); };
context.mutateEditorSlot = () => {};
context.stageToast = (...args) => context.toasts.push(args);
context.editorSlotLabel = () => 'Totem';
context.stageDeltaToastText = () => '0';
context.handleUpgradeAction = async () => false;
context.releaseFillRunHeight = () => {};
context.unbindCountdowns = () => {};
context.abortWorkerUpgradeOptions = () => {};
context.updateMountedRow = () => {};
context.renderCheckoutSurface = () => {};
context.closeGridPickerAfterChoice = () => {};
context.formatPlotLabel = () => '1-1-2';
""",
    )


def test_exhausted_totem_tier_is_disabled_in_open_picker():
    run_in_module_context(
        r"""
CERES_STAGE.push({ deed_uid: 'plot-a', group_id: 'totem', slot_index: 0,
  action: 'stake', asset_ref: 'I-318-a' });
const picker = { deedUid: 'plot-b', groupId: 'totem', candidates: [{ asset_ref: 'I-318-a', rarity: 'common' }],
  rarityGroups: { common: { count: 1, candidate: { asset_ref: 'I-318-a', rarity: 'common' } } } };
const html = renderItemRarityGridCards(picker, false);
const common = html.match(/<button[^>]*data-picker-rarity="common"[^>]*>/)?.[0];
if (!common || !common.includes('disabled') || !common.includes('aria-disabled="true"'))
  throw Error('exhausted tier is still choosable');
CERES_STAGE.length = 0;
PLANS.pending.set('plot-a|totem|0', { deed_uid: 'plot-a', group_id: 'totem',
  slot_index: 0, action: 'stake', asset_ref: 'I-318-a' });
const pendingHtml = renderItemRarityGridCards(picker, false);
const pendingCommon = pendingHtml.match(/<button[^>]*data-picker-rarity="common"[^>]*>/)?.[0];
if (!pendingCommon || !pendingCommon.includes('disabled'))
  throw Error('pending restored plan did not reserve the tier');
""",
        entry=STATIC / "app.js",
    )
