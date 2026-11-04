// the board's saved, unsigned Land plans (stage-plans.js, `ceres:stage-plan:v1:<player>`), read so the
// one-copy guard answers "Already staged for plot …" on the Cards page and at the board handoff too. Only the
// four fields the server's reservation check reads are sent; none of them is ever shown on screen.
export function boardStagedPlans(player) {
  if (!player) return [];
  try {
    const store = JSON.parse(localStorage.getItem(`ceres:stage-plan:v1:${player}`) || 'null');
    return Object.values(store?.plans || {}).filter((plan) => plan && typeof plan === 'object')
      .map((plan) => ({ deed_uid: plan.deed_uid, action: plan.action, asset_ref: plan.asset_ref, evict_asset_ref: plan.evict_asset_ref }))
      .slice(0, 200);
  } catch (_) { return []; }
}
