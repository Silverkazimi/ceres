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

export function boardPlanningActive() {
  return typeof document !== 'undefined' && document.body?.dataset?.boardMode === 'planning';
}

export function boardPlanningPlan() {
  if (!boardPlanningActive()) return null;
  return readSelectedBoardPlan(boardPlanningStorage(), boardPlanningAccountName());
}

export function boardPlanningStore() {
  const store = createBoardPlanStore(boardPlanningStorage(), boardPlanningAccountName());
  store.load();
  // IN-0315: every saved edit tells the board, so the plan totals follow the row that changed.
  const announce = (result) => { window.dispatchEvent(new CustomEvent(BOARD_PLANNING_CHANGED_EVENT)); return result; };
  return { ...store,
    setPlacement: (placement) => store.setPlacement(placement).then(announce),
    removePlacement: (target) => store.removePlacement(target).then(announce),
    clear: () => store.clear().then(announce) };
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
    document.body.dataset.boardPlanName = snapshot.plans.find((plan) => plan.id === snapshot.selectedId)?.name || 'Plan 1';
  }
  window.dispatchEvent(new Event(BOARD_PLANNING_EVENT));
}
