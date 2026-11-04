// IN-0267 phase 1: the board's planning state (Q1: a state of the existing board, chosen in
// the Menu). Glue between the pure plan module (board-plans.js) and the board: which plan
// is selected, the one row projection every render path uses, and the debounced local
// evaluator. Entering planning reads and writes only browser storage; the evaluator asks
// only the local Ceres server, only for changed rows, and only when such a row renders.
import { BOARD, CURRENT_PLAYER, adoptLatestCapture, playerQueryAppend } from './board.js';
import { boardPlanAssetStatus, boardPlanRowPlacements, boardPlanRowSignature, boardPlanTotals, createBoardPlanEvaluator,
  createBoardPlanStore, projectBoardRow, readSelectedBoardPlan } from './board-plans.js';

export const BOARD_PLANNING_EVENT = 'ceres:board-planning-mode';
export const BOARD_PLANNING_EVALUATED_EVENT = 'ceres:board-plan-evaluated';
// IN-0315: a saved plan edit; the board repaints its plan totals.
export const BOARD_PLANNING_CHANGED_EVENT = 'ceres:board-plan-changed';

function boardPlanningAccountName() { return CURRENT_PLAYER || BOARD?.player || ''; }
function boardPlanningStorage() { try { return window.localStorage; } catch (_) { return null; } }
let boardPlanningStoreAccount = null;
const boardPlanningStores = new Map();
let boardPlanningRestoreSerial = 0;
if (typeof window !== 'undefined') window.addEventListener('ceres:player-changed', () => { boardPlanningRestoreSerial += 1; });
if (typeof window !== 'undefined') window.addEventListener('storage', (event) => {
  const account = boardPlanningAccountName().toLowerCase();
  if (!account || event.key !== `ceres:board-plans:v2:${account}`) return;
  const store = boardPlanningStores.get(account);
  if (!store || store.snapshot().dirty || store.snapshot().readOnly) return;
  try {
    const snapshot = store.refresh();
    if (boardPlanningActive()) {
      document.body.dataset.boardPlanName = snapshot.plans.find((plan) => plan.id === snapshot.selectedId)?.name || 'No setup';
      window.dispatchEvent(new Event(BOARD_PLANNING_EVENT));
      window.dispatchEvent(new CustomEvent(BOARD_PLANNING_CHANGED_EVENT, { detail: { deeds: [] } }));
    }
  } catch (_) { /* a storage event cannot make the current board unusable */ }
});

export function boardPlanningActive() {
  return typeof document !== 'undefined' && document.body?.dataset?.boardMode === 'planning';
}

export function boardPlanningPlan() {
  if (!boardPlanningActive()) return null;
  const account = boardPlanningAccountName().toLowerCase();
  const store = boardPlanningStores.get(account);
  if (store && (store.snapshot().dirty || store.snapshot().readOnly)) return store.selected();
  return readSelectedBoardPlan(boardPlanningStorage(), boardPlanningAccountName());
}

export function boardPlanningStore() {
  const account = boardPlanningAccountName().toLowerCase();
  if (boardPlanningStores.has(account)) return boardPlanningStores.get(account);
  if (boardPlanningStoreAccount !== account) boardPlanningRestoreSerial += 1;
  const store = createBoardPlanStore(boardPlanningStorage(), account, { onConflict: ({ id, copyId }) =>
    window.dispatchEvent(new CustomEvent('ceres:board-plan-conflict', { detail: { id, copyId } })) });
  store.load();
  boardPlanningStoreAccount = account;
  // IN-0315: every saved edit tells the board, so the plan totals follow the row that changed.
  const announce = (deedUid) => (result) => {
    window.dispatchEvent(new CustomEvent(BOARD_PLANNING_CHANGED_EVENT, { detail: { deeds: [String(deedUid)] } }));
    return result;
  };
  const announceFailure = (deedUid) => (error) => { announce(deedUid)(); throw error; };
  const wrapped = { ...store,
    setPlacement: (placement) => store.setPlacement(placement).then(announce(placement.deed_uid), announceFailure(placement.deed_uid)),
    removePlacement: (target) => store.removePlacement(target).then(announce(target.deed_uid), announceFailure(target.deed_uid)),
    clear: () => {
      const deeds = [...new Set((store.selected()?.placements || []).map((placement) => String(placement.deed_uid)))];
      return store.clear().then((result) => {
        window.dispatchEvent(new CustomEvent(BOARD_PLANNING_CHANGED_EVENT, { detail: { deeds } }));
        return result;
      }, (error) => { window.dispatchEvent(new CustomEvent(BOARD_PLANNING_CHANGED_EVENT, { detail: { deeds } })); throw error; });
    } };
  boardPlanningStores.set(account, wrapped);
  return wrapped;
}

export function boardPlanningSetups() {
  if (!boardPlanningActive()) return null;
  try { return boardPlanningStore().refresh(); } catch (_) { return null; }
}

async function boardPlanningLivePlacements(account, restoreSerial) {
  const stamp = BOARD?.generated_at;
  if (!stamp) throw new Error("Some live cards are still loading, so this setup can't be copied yet. Nothing was changed; try again in a moment.");
  const response = await fetch(playerQueryAppend('/api/board-plans/from-live'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ capture_generated_at: stamp }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload?.detail?.message || "Some live cards are still loading, so this setup can't be copied yet. Nothing was changed; try again in a moment.");
  if (account !== boardPlanningAccountName().toLowerCase() || restoreSerial !== boardPlanningRestoreSerial
      || stamp !== BOARD?.generated_at || payload.account !== account || payload.generated_at !== stamp
      || !Array.isArray(payload.placements)) throw new Error('The board changed while copying live occupants. Nothing was saved.');
  return { placements: payload.placements, stamp };
}

export async function boardPlanningSetupAction(action, value) {
  const account = boardPlanningAccountName().toLowerCase();
  const store = boardPlanningStore();
  store.refresh();
  const restoreSerial = boardPlanningRestoreSerial;
  const before = store.snapshot();
  let result;
  try {
    if (action === 'new') result = await store.create(value || 'New setup');
    else if (action === 'retry') result = await store.persist();
    else if (action === 'restore') result = store.restoreBackup();
    else if (action === 'start-live') {
      const live = await boardPlanningLivePlacements(account, restoreSerial);
      result = await store.create(value || 'From live', live.placements, { base_capture_generated_at: live.stamp });
    } else if (action === 'rename') result = await store.rename(value.id || before.selectedId, value.name || value);
    else if (action === 'duplicate') result = await store.duplicate(before.selectedId);
    else if (action === 'delete') result = await store.delete(value || before.selectedId);
    else if (action === 'undo-delete') result = await store.undoDelete(value);
    else if (action === 'select') result = await store.select(value);
    else throw new Error('Unknown setup action.');
  } catch (error) {
    if (store.snapshot().dirty && account === boardPlanningAccountName().toLowerCase()) {
      document.body.dataset.boardPlanName = store.selected()?.name || 'Unsaved setup';
      window.dispatchEvent(new Event(BOARD_PLANNING_EVENT));
    }
    throw error;
  }
  if (account !== boardPlanningAccountName().toLowerCase() || restoreSerial !== boardPlanningRestoreSerial)
    throw new Error('The account changed while saving. Try again for the current account.');
  const after = store.snapshot();
  document.body.dataset.boardPlanName = after.plans.find((plan) => plan.id === after.selectedId)?.name || 'No setup';
  window.dispatchEvent(new Event(BOARD_PLANNING_EVENT));
  return result;
}

async function boardPlanningRequest(body) {
  const response = await fetch(playerQueryAppend('/api/board-plans/evaluate'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(`HTTP ${response.status}`);
    error.code = payload?.detail?.code || `http_${response.status}`;
    throw error;
  }
  return payload;
}

let boardPlanningEvaluatorInstance = null;
let boardPlanningEvaluatorScope = null;
function boardPlanningEvaluator() {
  const scope = boardPlanningAccountName();
  if (!boardPlanningEvaluatorInstance) {
    boardPlanningEvaluatorInstance = createBoardPlanEvaluator({
      request: boardPlanningRequest,
      adoptLatest: () => adoptLatestCapture(),
      onUpdate: (deeds) => window.dispatchEvent(new CustomEvent(BOARD_PLANNING_EVALUATED_EVENT, { detail: { deeds } })),
    });
  }
  if (boardPlanningEvaluatorScope !== scope) { boardPlanningEvaluatorInstance.reset(); boardPlanningEvaluatorScope = scope; }
  return boardPlanningEvaluatorInstance;
}

// The single projection: collapsed cells call it on every render, remount, detail load
// and quiet refresh, because all of them pass through plotRowCellsHtml. IN-0315: in planning
// EVERY row is projected; a row with nothing planned is the clean sheet (all stats 0) and asks
// nothing, so an empty plan makes no evaluate request at all.
export function boardPlanningRow(row, { detailLoaded = false, coverageComplete = true } = {}) {
  // Planning without a readable plan is still a clean sheet, never live.
  if (!row || !boardPlanningActive()) return null;
  const plan = boardPlanningPlan();
  const placements = boardPlanRowPlacements(plan, row.deed_uid);
  if (!placements.length) return projectBoardRow(row, plan);
  const stamp = BOARD?.generated_at || null;
  const signature = boardPlanRowSignature(placements, stamp, detailLoaded);
  const evaluator = boardPlanningEvaluator();
  let evaluation = evaluator.result(row.deed_uid, signature);
  if (!evaluation && coverageComplete) evaluator.want(row.deed_uid, placements, stamp, signature);
  return projectBoardRow(row, plan, { evaluation, coverageComplete });
}

// the ONE projection every planning reader shares -- cells, totals, sort, badges and the open plot's
// power warning. The board (rows.js) registers what it knows about each row's detail load, so every caller
// asks the evaluator exactly the same question. Outside planning it answers null / the live row itself.
// The registry is a property of a hoisted function declaration, so rows.js can register while the module
// graph is still evaluating (an import cycle may run rows.js first; a `let` would still be uninitialised).
function boardPlanningRowState(row) {
  return typeof boardPlanningRowState.of === 'function' ? boardPlanningRowState.of(row) || {} : {};
}
export function setBoardPlanningRowState(stateOf) {
  boardPlanningRowState.of = typeof stateOf === 'function' ? stateOf : null;
}
export function boardPlanningProjection(row) {
  return row ? boardPlanningRow(row, boardPlanningRowState(row)) : null;
}
export function boardPlanningViewRow(row) {
  const projected = boardPlanningProjection(row);
  return projected ? projected.row : row;
}

// IN-0315: the board totals while planning, from the plan only (null outside planning).
// `projectRow` is the caller's row projection, so totals ask exactly what the cells ask.
export function boardPlanningTotals(rows, projectRow) {
  if (!boardPlanningActive()) return null;
  return boardPlanTotals(rows, boardPlanningPlan(), projectRow);
}

export function boardPlanningAssetStatus(assetRef) {
  if (!boardPlanningActive()) return null;
  return boardPlanAssetStatus(boardPlanningEvaluator().asset(assetRef));
}

// Menu toggle: Live <-> Planning. Entry only loads (or creates) the saved plan in browser
// storage; it touches no live stage, network or wallet. Leaving it removes the projection.
export function toggleBoardPlanning() {
  if (boardPlanningActive()) {
    delete document.body.dataset.boardMode;
    delete document.body.dataset.boardPlanName;
  } else {
    const store = boardPlanningStore();
    const snapshot = store.snapshot();
    document.body.dataset.boardMode = 'planning';
    document.body.dataset.boardPlanName = snapshot.plans.find((plan) => plan.id === snapshot.selectedId)?.name
      || (snapshot.readOnly ? 'Read-only setups' : 'Plan 1');
    if (snapshot.readOnlyReason) window.dispatchEvent(new CustomEvent('ceres:board-plan-notice', { detail: snapshot.readOnlyReason }));
  }
  window.dispatchEvent(new Event(BOARD_PLANNING_EVENT));
}
