// IN-0267 phase 1: local virtual-board data. This module is deliberately separate
// from stage-plans.js: hypothetical assignments never enter CERES_STAGE, and the
// Changes/Checkout surfaces never read this store. Plain module with no imports, so
// tests can load it alone; every top-level name carries a boardPlan prefix because the
// test VM evaluates the board's whole module graph in one scope.
export const BOARD_PLAN_PREFIX = 'ceres:board-plans:v1:';
export const BOARD_PLAN_GROUPS = ['worker', 'runi', 'power_core', 'totem', 'title'];

export function boardPlanAccount(account) {
  const value = String(account || '').trim().toLowerCase();
  return /^[a-z][a-z0-9.-]{2,15}$/.test(value) ? value : null;
}

export function boardPlanStoreKey(account) {
  const owner = boardPlanAccount(account);
  return owner ? `${BOARD_PLAN_PREFIX}${owner}` : null;
}

export function emptyBoardPlan(account, now = new Date().toISOString()) {
  const owner = boardPlanAccount(account);
  if (!owner) throw new Error('A valid account is required to create a board plan.');
  return { id: `plan-${Date.parse(now)}-${Math.random().toString(36).slice(2, 8)}`, name: 'Plan 1', account: owner,
    schema_version: 1, revision: 0, created_at: now, updated_at: now, origin: 'human',
    base_capture_generated_at: null, rules_fingerprint: null, base_fingerprints: {},
    placements: [], explicit_removals: [], apply_policy: 'placements_only', parent_revision: null };
}

// Display-only copy of the chosen asset (name and art). Game state -- cooldown, location
// readiness, PP -- is never saved; it is read from the current capture every time.
const BOARD_PLAN_ASSET_FIELDS = ['asset_ref', 'name', 'image', 'image_url', 'card_detail_id', 'level', 'edition', 'rarity', 'foil',
  'color', 'secondary_color', 'splinter', 'boost', 'item_detail_id', 'type', 'skin', 'bcx', 'base_pp', 'land_base_pp'];
export function boardPlanAssetCopy(asset) {
  const out = {};
  for (const key of BOARD_PLAN_ASSET_FIELDS) if (asset && asset[key] !== undefined && asset[key] !== null) out[key] = asset[key];
  return out;
}

// Copy only occupants already present in the loaded board. The caller supplies live rows
// this function performs no detail fetch and does not stage a change.
export function boardPlanPlacementsFromLive(rows) {
  const placements = [];
  for (const row of rows || []) {
    if (!row?.deed_uid) continue;
    const workerCount = Number(row.capacity?.worker_count);
    if (Number.isInteger(workerCount) && workerCount > (row.workers || []).length)
      throw new Error("Some live cards are still loading, so this setup can't be copied yet. Nothing was changed; try again in a moment.");
    for (const worker of row.workers || []) {
      const slot = Number(worker.slot);
      const ref = worker.asset_ref || worker.uid || worker.card_uid;
      if (!ref || !Number.isInteger(slot) || slot < 0)
        throw new Error("Some live cards are still loading, so this setup can't be copied yet. Nothing was changed; try again in a moment.");
      placements.push(boardPlanCleanPlacement({ deed_uid: row.deed_uid,
        group_id: slot === 0 ? 'runi' : 'worker', slot_index: slot === 0 ? 0 : slot - 1,
        asset_ref: ref, asset: worker }));
    }
    const bonus = row._live_bonus || row.bonus || {};
    for (const group of ['power_core', 'totem', 'title']) {
      const asset = bonus[group];
      const ref = asset?.asset_ref || asset?.uid || asset?.card_uid;
      if (asset && !ref) throw new Error("Some live cards are still loading, so this item can't be copied into the setup yet. Nothing was changed; try again in a moment.");
      if (ref) placements.push(boardPlanCleanPlacement({ deed_uid: row.deed_uid, group_id: group,
        slot_index: 0, asset_ref: ref, asset }));
    }
  }
  return placements;
}

function boardPlanCleanPlacement(placement) {
  return { deed_uid: String(placement.deed_uid), group_id: placement.group_id, slot_index: placement.slot_index,
    asset_ref: String(placement.asset_ref), name: placement.name || placement.asset?.name || null,
    asset: boardPlanAssetCopy(placement.asset || { asset_ref: placement.asset_ref, name: placement.name }) };
}

// Phase 2a keeps the v1 bytes until a verified v2 write has completed.
export const BOARD_PLAN_V2_PREFIX = 'ceres:board-plans:v2:';
export function boardPlanV2Key(account) {
  const owner = boardPlanAccount(account);
  return owner ? `${BOARD_PLAN_V2_PREFIX}${owner}` : null;
}
function boardPlanClone(value) { return JSON.parse(JSON.stringify(value)); }
function boardPlanId() { return globalThis.crypto?.randomUUID?.() || `plan-${Date.now()}-${Math.random().toString(36).slice(2)}`; }
function boardPlanValid(data, owner) {
  return data && data.schema_version === 2 && data.account === owner && Number.isSafeInteger(data.revision)
    && data.revision >= 1 && data.plans && !Array.isArray(data.plans) && typeof data.plans === 'object'
    && data.tombstones && !Array.isArray(data.tombstones) && typeof data.tombstones === 'object'
    && (data.selected_plan_id === null || Object.hasOwn(data.plans, data.selected_plan_id))
    && Object.entries(data.plans).every(([id, plan]) => plan && plan.id === id && plan.account === owner
      && typeof plan.name === 'string' && Array.isArray(plan.placements));
}
function boardPlanLegacy(data, owner) {
  return data && data.version === 1 && data.account === owner && Array.isArray(data.plans);
}
function boardPlanName(name) {
  const trimmed = String(name || '').trim();
  if (!trimmed || trimmed.length > 80) throw new Error('Setup name must be 1–80 characters.');
  return trimmed;
}
function boardPlanChanged(a, b) { return JSON.stringify(a) !== JSON.stringify(b); }
function boardPlanConflictCopy(plan, now) {
  return { ...boardPlanClone(plan), id: boardPlanId(), name: `${plan.name.slice(0, 64)} (conflict copy)`,
    created_at: now, updated_at: now, parent_revision: plan.revision, revision: 1, run_state: undefined };
}

// Storage is injected for isolated tests. Mutations are staged in memory, then committed under
// an account Web Lock. A failed write leaves the last durable revision intact and blocks switches.
export function createBoardPlanStore(storage, account, { onConflict = () => {}, lock = null } = {}) {
  const owner = boardPlanAccount(account);
  const key = boardPlanV2Key(owner);
  const backupKey = `${key}:backup`;
  if (!storage || !key) throw new Error('Board plan storage requires an account and storage adapter.');
  let state = null;
  let base = null;
  let dirty = false;
  let readOnly = false;
  let readOnlyReason = null;
  let writeSerial = Promise.resolve();
  const webLock = lock || ((task) => {
    if (typeof window === 'undefined') return task(); // isolated Node tests inject a lock when testing lock use
    if (!globalThis.navigator?.locks?.request) throw new Error('Safe setup locking is unavailable.');
    return navigator.locks.request(`ceres:board-plans:${owner}`, task);
  });
  const decode = (raw) => {
    let data;
    try { data = JSON.parse(raw); } catch (_) { throw new Error('Saved setup data is corrupt; the last good revision is retained.'); }
    if (data?.schema_version > 2 || (data?.version > 1 && !data?.schema_version)) {
      readOnly = true;
      readOnlyReason = 'Saved setups use a newer version. They are read-only in this Ceres version.';
      return { schema_version: data.schema_version, account: owner, revision: Number(data.revision) || 0,
        selected_plan_id: data.selected_plan_id || null,
        plans: data.account === owner && data.plans && !Array.isArray(data.plans) ? data.plans : {},
        tombstones: data.tombstones || {} };
    }
    if (!boardPlanValid(data, owner)) throw new Error('Saved setup data is incompatible; the last good revision is retained.');
    return data;
  };
  const writeVerified = (next) => {
    const bytes = JSON.stringify(next);
    const previous = storage.getItem(key);
    storage.setItem(key, bytes);
    if (storage.getItem(key) !== bytes) throw new Error('Setup save could not be verified.');
    if (previous) storage.setItem(backupKey, previous);
  };
  function load() {
    const raw = storage.getItem(key);
    if (raw) {
      try { state = decode(raw); }
      catch (error) {
        if (readOnly) throw error;
        const backup = storage.getItem(backupKey);
        if (!backup) throw error;
        state = decode(backup); readOnly = true;
        readOnlyReason = 'Saved setup data is corrupt. The previous good revision is open read-only.';
        base = boardPlanClone(state);
        return snapshot();
      }
      base = boardPlanClone(state); return snapshot();
    }
    const old = storage.getItem(boardPlanStoreKey(owner));
    if (old) {
      let legacy;
      try { legacy = JSON.parse(old); } catch (_) { throw new Error('Earlier setup data is corrupt; it has been kept.'); }
      if (!boardPlanLegacy(legacy, owner)) throw new Error('Earlier setup data is incompatible; it has been kept.');
      const plans = Object.fromEntries(legacy.plans.map((plan) => [plan.id,
        { schema_version: 1, origin: 'human', base_capture_generated_at: null,
          rules_fingerprint: null, base_fingerprints: {}, explicit_removals: [],
          apply_policy: 'placements_only', parent_revision: null, ...boardPlanClone(plan) }]));
      state = { schema_version: 2, account: owner, revision: 1,
        selected_plan_id: legacy.selected_id || legacy.plans[0]?.id || null, plans, tombstones: {} };
    } else {
      const plan = emptyBoardPlan(owner);
      state = { schema_version: 2, account: owner, revision: 1,
        selected_plan_id: plan.id, plans: { [plan.id]: plan }, tombstones: {} };
    }
    writeVerified(state); // old key intentionally remains as a recovery copy
    base = boardPlanClone(state);
    return snapshot();
  }
  function snapshot() {
    if (!state) throw new Error('Board plan store is not loaded.');
    return { account: owner, revision: state.revision, selectedId: state.selected_plan_id,
      plans: Object.values(state.plans).map(boardPlanClone), tombstones: boardPlanClone(state.tombstones),
      dirty, readOnly, readOnlyReason };
  }
  function refresh() {
    if (!state || dirty || readOnly) return snapshot();
    const raw = storage.getItem(key);
    if (!raw) throw new Error('Saved setups are no longer available. Nothing was changed.');
    const remote = decode(raw);
    if (remote.revision !== state.revision) { state = remote; base = boardPlanClone(remote); }
    return snapshot();
  }
  function selected() { return state?.plans[state.selected_plan_id] || null; }
  function ensureWritable() {
    if (!state) throw new Error('Board plan store is not loaded.');
    if (readOnly) throw new Error('Saved setups are read-only in this Ceres version.');
    if (dirty) throw new Error('A setup has unsaved changes. Switching is blocked until it is saved.');
  }
  function merge(remote, conflicts) {
    const merged = boardPlanClone(remote);
    const now = new Date().toISOString();
    for (const [id, deletedAt] of Object.entries(state.tombstones)) {
      if (!base.tombstones[id] || base.tombstones[id] !== deletedAt) {
        if (remote.plans[id] && boardPlanChanged(remote.plans[id], base.plans[id])) {
          const copy = boardPlanConflictCopy(remote.plans[id], now);
          merged.plans[copy.id] = copy; conflicts.push({ id, copyId: copy.id });
        }
        delete merged.plans[id]; merged.tombstones[id] = deletedAt;
      }
    }
    for (const [id, deletedAt] of Object.entries(base.tombstones)) {
      if (state.tombstones[id]) continue;
      if (remote.tombstones[id] === deletedAt) delete merged.tombstones[id];
      else if (remote.tombstones[id] && state.plans[id]) {
        const copy = boardPlanConflictCopy(state.plans[id], now);
        merged.plans[copy.id] = copy; merged.selected_plan_id = copy.id;
        conflicts.push({ id, copyId: copy.id });
      }
    }
    for (const [id, local] of Object.entries(state.plans)) {
      if (!boardPlanChanged(local, base.plans[id])) continue;
      if (merged.tombstones[id] && !base.tombstones[id]) {
        const copy = boardPlanConflictCopy(local, now); merged.plans[copy.id] = copy; conflicts.push({ id, copyId: copy.id });
      } else if (remote.plans[id] && boardPlanChanged(remote.plans[id], base.plans[id])) {
        // Keep the latest durable edit at its original ID and preserve this tab's edit as a copy.
        const copy = boardPlanConflictCopy(local, now); merged.plans[copy.id] = copy;
        if (state.selected_plan_id === id) merged.selected_plan_id = copy.id;
        conflicts.push({ id, copyId: copy.id });
      } else if (!merged.tombstones[id]) merged.plans[id] = boardPlanClone(local);
    }
    if (!conflicts.some((conflict) => conflict.id === state.selected_plan_id) && merged.plans[state.selected_plan_id])
      merged.selected_plan_id = state.selected_plan_id;
    if (!merged.plans[merged.selected_plan_id]) merged.selected_plan_id = Object.keys(merged.plans)[0] || null;
    return merged;
  }
  function persist() {
    if (!state || readOnly) return Promise.reject(new Error('Saved setups are read-only.'));
    const operation = writeSerial.catch(() => {}).then(() => webLock(() => {
      const raw = storage.getItem(key);
      const remote = raw ? decode(raw) : null;
      if (readOnly) throw new Error(readOnlyReason || 'Saved setups are read-only.');
      if (!remote) throw new Error('Saved setups disappeared; no change was written.');
      const conflicts = [];
      const next = remote.revision === base.revision ? boardPlanClone(state) : merge(remote, conflicts);
      next.revision = remote.revision + 1;
      const cutoff = Date.now() - 30 * 24 * 60 * 60 * 1000;
      for (const [id, deletedAt] of Object.entries(next.tombstones)) if (Date.parse(deletedAt) < cutoff) delete next.tombstones[id];
      writeVerified(next);
      state = next; base = boardPlanClone(next); dirty = false;
      conflicts.forEach((conflict) => { try { onConflict(conflict); } catch (_) { /* notice cannot undo a durable save */ } });
      return snapshot();
    }));
    writeSerial = operation;
    return operation;
  }
  function touch(plan) { plan.revision += 1; plan.updated_at = new Date().toISOString(); dirty = true; }
  function beforeMutation() { ensureWritable(); refresh(); ensureWritable(); }
  function validPlacement(placement) {
    return placement && placement.asset_ref && placement.deed_uid && BOARD_PLAN_GROUPS.includes(placement.group_id)
      && Number.isInteger(placement.slot_index) && placement.slot_index >= 0;
  }
  function validRoster(placements) {
    const assets = new Set(); const slots = new Set();
    for (const placement of placements) {
      const asset = String(placement.asset_ref);
      const slot = `${placement.deed_uid}|${placement.group_id}|${placement.slot_index}`;
      if (!validPlacement(placement) || assets.has(asset) || slots.has(slot))
        throw new Error('Live occupants contain a duplicate or invalid slot. No setup was created.');
      assets.add(asset); slots.add(slot);
    }
  }
  return { load, refresh, snapshot, selected, persist,
    async setPlacement(placement) {
      beforeMutation();
      if (!validPlacement(placement)) throw new Error('Invalid placement.');
      const plan = selected();
      const sameSlot = (p) => String(p.deed_uid) === String(placement.deed_uid) && p.group_id === placement.group_id && p.slot_index === placement.slot_index;
      if (plan.placements.some((p) => String(p.asset_ref) === String(placement.asset_ref) && !sameSlot(p))) throw new Error('This asset is already planned in this setup.');
      plan.placements = plan.placements.filter((p) => !sameSlot(p));
      plan.placements.push(boardPlanCleanPlacement(placement)); touch(plan);
      return persist();
    },
    async removePlacement({ deed_uid: deedUid, group_id: groupId, slot_index: slotIndex }) {
      beforeMutation();
      const plan = selected();
      const before = plan.placements.length;
      plan.placements = plan.placements.filter((p) => !(String(p.deed_uid) === String(deedUid) && p.group_id === groupId && p.slot_index === slotIndex));
      if (plan.placements.length === before) return false;
      touch(plan); await persist(); return true;
    },
    clear() { beforeMutation(); const plan = selected(); plan.placements = []; touch(plan); return persist(); },
    create(name = 'New setup', placements = [], { base_capture_generated_at = null } = {}) {
      beforeMutation();
      validRoster(placements);
      const now = new Date().toISOString();
      const plan = { ...emptyBoardPlan(owner, now), id: boardPlanId(), name: boardPlanName(name),
        base_capture_generated_at, placements: boardPlanClone(placements) };
      state.plans[plan.id] = plan; state.selected_plan_id = plan.id; dirty = true;
      return persist();
    },
    rename(id, name) { const prior = state?.selected_plan_id; beforeMutation(); const plan = state.plans[id === prior ? state.selected_plan_id : id]; if (!plan) throw new Error('Setup is unavailable.'); plan.name = boardPlanName(name); touch(plan); return persist(); },
    duplicate(id) {
      const prior = state?.selected_plan_id; beforeMutation(); const source = state.plans[id === prior ? state.selected_plan_id : id]; if (!source) throw new Error('Setup is unavailable.');
      const now = new Date().toISOString(); const copy = { ...boardPlanClone(source), id: boardPlanId(), name: boardPlanName(`${source.name.slice(0, 75)} copy`),
        revision: 0, created_at: now, updated_at: now, run_state: undefined };
      state.plans[copy.id] = copy; state.selected_plan_id = copy.id; dirty = true; return persist();
    },
    delete(id) {
      const prior = state?.selected_plan_id; beforeMutation(); const target = state.plans[id === prior ? state.selected_plan_id : id]; if (!target) throw new Error('Setup is unavailable.');
      const deleted = boardPlanClone(target); delete state.plans[target.id]; state.tombstones[target.id] = new Date().toISOString();
      state.selected_plan_id = Object.keys(state.plans)[0] || null;
      let replacementId = null;
      if (!state.selected_plan_id) {
        const replacement = emptyBoardPlan(owner);
        state.plans[replacement.id] = replacement; state.selected_plan_id = replacement.id;
        replacementId = replacement.id;
      }
      dirty = true;
      deleted.replacementId = replacementId;
      return persist().then((result) => ({ ...result, deleted, replacementId }));
    },
    undoDelete(plan) {
      beforeMutation(); if (!state.tombstones[plan?.id]) throw new Error('This deletion cannot be undone.');
      const replacementId = plan?.replacementId;
      const replacement = replacementId && state.plans[replacementId];
      if (replacement && state.selected_plan_id === replacementId && replacement.name === 'Plan 1' && !replacement.placements.length && replacement.revision === 0) delete state.plans[replacementId];
      delete state.tombstones[plan.id]; state.plans[plan.id] = boardPlanClone(plan); state.selected_plan_id = plan.id; dirty = true; return persist();
    },
    select(id) { beforeMutation(); if (!state.plans[id]) throw new Error('Setup is unavailable.'); if (id === state.selected_plan_id) return Promise.resolve(snapshot()); state.selected_plan_id = id; dirty = true; return persist(); },
    restoreBackup() {
      if (!readOnly || !readOnlyReason?.includes('previous good revision')) throw new Error('No recoverable backup is available.');
      const backup = storage.getItem(backupKey);
      if (!backup) throw new Error('No saved setup backup is available.');
      const recovered = JSON.parse(backup);
      if (!boardPlanValid(recovered, owner)) throw new Error('The saved setup backup is incompatible.');
      storage.setItem(key, backup);
      if (storage.getItem(key) !== backup) throw new Error('Setup restore could not be verified.');
      state = recovered; base = boardPlanClone(recovered); readOnly = false; readOnlyReason = null;
      return snapshot();
    },
  };
}

// Read-only access for render paths: one JSON parse per stored revision, not per row.
let boardPlanReadCache = { key: null, raw: null, plan: null };
export function readSelectedBoardPlan(storage, account) {
  const key = boardPlanV2Key(account);
  if (!key || !storage) return null;
  try {
    const raw = storage.getItem(key);
    if (boardPlanReadCache.key === key && boardPlanReadCache.raw === raw) return boardPlanReadCache.plan;
    const data = JSON.parse(raw || 'null');
    const plan = boardPlanValid(data, boardPlanAccount(account)) ? data.plans[data.selected_plan_id] || null : null;
    boardPlanReadCache = { key, raw, plan };
    return plan;
  } catch (_) { return null; }
}

export function boardPlanRowPlacements(plan, deedUid) {
  return (plan?.placements || []).filter((p) => String(p.deed_uid) === String(deedUid));
}

// The evaluator's answer belongs to one exact question: the capture, whether the plot's
// assets were loaded, and the planned roster. Any change asks again.
export function boardPlanRowSignature(placements, captureStamp, detailLoaded = false) {
  const roster = placements.map((p) => `${p.group_id}:${p.slot_index}:${p.asset_ref}`).sort().join(',');
  return `${captureStamp || ''}|${detailLoaded ? 1 : 0}|${roster}`;
}

// Real state of one planned asset from the evaluator's capture-bound answer. Never "Ready"
// available needs a positive server verdict (Survival included); otherwise it waits.
export function boardPlanAssetStatus(state) {
  if (!state || state.known !== true || !state.cooldown) return { status: 'unknown', label: 'Unknown' };
  const cooldown = state.cooldown;
  const survivalDead = cooldown.state === 'survival_locked';
  if (survivalDead) return { status: 'waiting', label: 'Dead in Survival', until: cooldown.ready_at || null };
  if (state.staked) return { status: 'staked', label: 'Staked', locationDeedUid: state.location_deed_uid || null };
  if (cooldown.state === 'cooling') return { status: 'waiting', label: 'Cooling', until: cooldown.ready_at || null };
  if (cooldown.ready === true && cooldown.state === 'ready') return { status: 'available', label: 'Available' };
  return { status: 'waiting', label: cooldown.state === 'unverified' ? 'Cooldown unverified' : 'Cooldown unknown' };
}

const BOARD_PLAN_VALUE_FIELDS = ['harvest_pp', 'construction_pp', 'base_pp', 'base_pp_after_cap', 'base_pp_cap', 'base_pp_cap_reached',
  'work_per_hour', 'multiplier', 'grain_cost_per_hour', 'wood_cost_per_hour', 'stone_cost_per_hour', 'iron_cost_per_hour',
  // the evaluator's power answer, read by the open plot's power warning (never rendered as a cell).
  'powered'];

// IN-0315: in planning the plan is the only truth. A plot
// with nothing planned is a clean sheet: every stat the board derives from what is staked reads 0 (or the
// board's existing "—" where a value does not apply), never the live figure. Plot facts (label, rarity
// terrain, worksite, capacity limit, lock) stay; nothing staked, nothing produced, no boost, no DEC.
// A shallow copy with the stat fields replaced -- the live row is never mutated.
// round 3 (manager): badges that describe the PLOT itself, which a plan does not change, stay in planning
// with their own notices (tooltips). Unstable totem is one of them: board.py reads it from deed_stats, a deed
// property like Building in a Box, not the staked totem.
const BOARD_PLAN_PLOT_FACT_BADGES = new Set(['no_worksite', 'worksite_incomplete', 'building_in_box', 'unstable_totem']);
function boardPlanPlotFacts(live) {
  return {
    badges: (live.badges || []).filter((code) => BOARD_PLAN_PLOT_FACT_BADGES.has(code)),
    notices: (live.notices || []).filter((notice) => BOARD_PLAN_PLOT_FACT_BADGES.has(notice?.code)),
  };
}

export function boardPlanEmptyRow(liveRow) {
  const live = liveRow || {};
  const cap = live.capacity || {};
  const cardCapacity = Number.isFinite(Number(cap.card_capacity)) ? Number(cap.card_capacity) : null;
  const zeroIfApplies = (value) => (value === null || value === undefined ? null : 0);
  return {
    ...live,
    harvest_pp: 0, construction_pp: 0, base_pp: 0, base_pp_after_cap: 0, base_pp_cap_reached: false,
    work_per_hour: 0, work_per_hour_unscaled: 0, tax_rate: null, multiplier: null, multiplier_parts: null,
    grain_cost_per_hour: zeroIfApplies(live.grain_cost_per_hour), food_discount: null, food_discount_cards: [],
    wood_cost_per_hour: zeroIfApplies(live.wood_cost_per_hour), stone_cost_per_hour: zeroIfApplies(live.stone_cost_per_hour),
    iron_cost_per_hour: zeroIfApplies(live.iron_cost_per_hour),
    workers: [], worker_names: [], pp_tooltip: { workers: [] },
    bonus: { totem: null, title: null, power_core: null }, _live_bonus: undefined,
    dec: { ...(live.dec || {}), needed: 0, needed_discount: null, committed: 0, shortfall: 0 }, dec_discount_cards: [],
    plot_dec_efficiency: null,
    capacity: { ...cap, worker_count: 0, powered_worker_count: 0, runi_staked: false,
      open_slots: cardCapacity ?? cap.open_slots ?? null, display: cardCapacity === null ? '—' : `0/${cardCapacity}` },
    staking_facts: live.staking_facts
      ? { ...live.staking_facts, is_runi_staked: false, is_power_core_staked: false, is_energized: false } : live.staking_facts,
    // nothing the badges read survives from Live. A plot with nothing staked is Idle (the server's own
    // rule, the same count as the totals' "Idle plots"); no live notice, region DEC or ability holder.
    // `planning_view` tells the badge code to ignore the live stage and the live worksite build too (Changes
    // never sees a plan); `pending_change` itself stays, because the Worksite cell shows that plot fact.
    powered: null, badges: ['idle', ...boardPlanPlotFacts(live).badges], notices: boardPlanPlotFacts(live).notices,
    region_efficiency: null, ability_holders: [],
    planning_view: true,
  };
}

// the badge codes a changed row carries, from the plan values only -- the server's rules
// (ceres/board.py) applied to the evaluator's answer. Pending or unknown: no plan badge (plot facts stay).
function boardPlanRowBadges(row, status) {
  if (status !== 'planned') return [];
  const cap = row.capacity || {};
  const workers = Number(cap.worker_count);
  const regular = Number.isFinite(workers) ? workers - (cap.runi_staked === true ? 1 : 0) : 0;
  const badges = [];
  if (workers === 0) badges.push('idle');
  if (regular > 0 && row.powered === false) badges.push('unpowered');
  if (row.base_pp_cap_reached === true) badges.push('over_cap');
  return badges;
}

// One projection for every collapsed row while planning. A row the plan does not touch is the clean
// sheet above (status 'empty', no marker: it renders through the ordinary cells). A changed row starts
// from that clean sheet too: Totem/Title come from the plan; PP, production, boost, food, materials and
// capacity come only from a complete evaluator answer for this exact roster. Without one the row says
// pending or unknown -- never 0 and never the live figure under a planned label.
export function projectBoardRow(liveRow, plan, { evaluation = null, coverageComplete = true } = {}) {
  const entries = boardPlanRowPlacements(plan, liveRow?.deed_uid);
  if (!entries.length) return { row: boardPlanEmptyRow(liveRow), changed: false, status: 'empty', placements: [] };
  const row = boardPlanEmptyRow(liveRow);
  const item = (group) => {
    const placement = entries.find((p) => p.group_id === group);
    return placement ? { ...(placement.asset || {}), asset_ref: placement.asset_ref, name: placement.asset?.name || placement.name || null } : null;
  };
  delete row._live_bonus;
  row.bonus = { totem: item('totem'), title: item('title'), power_core: item('power_core') };
  let status = 'pending';
  let reason = null;
  if (!coverageComplete) { status = 'unknown'; reason = 'detail_failed'; }
  else if (evaluation?.coverage === 'complete') {
    status = 'planned';
    for (const field of BOARD_PLAN_VALUE_FIELDS) row[field] = evaluation[field] ?? null;
    row.capacity = { ...(row.capacity || {}), ...(evaluation.capacity || {}) };
    // open slots follow the planned roster, not the clean sheet's empty count.
    const cardCapacity = Number(row.capacity.card_capacity);
    const regular = Number(row.capacity.worker_count) - (row.capacity.runi_staked === true ? 1 : 0);
    row.capacity.open_slots = Number.isFinite(cardCapacity) && Number.isFinite(regular) ? Math.max(0, cardCapacity - regular) : null;
    row.multiplier_parts = null;
  } else if (evaluation?.coverage === 'unknown') { status = 'unknown'; reason = evaluation.reason || 'unknown'; }
  if (status !== 'planned') {
    // No live figure survives on a changed row under a planned label.
    for (const field of BOARD_PLAN_VALUE_FIELDS) row[field] = null;
    // the workers cell says Unknown, so the counts the sort reads are no value either.
    row.capacity = { ...(row.capacity || {}), display: null, worker_count: null, open_slots: null };
    row.multiplier_parts = null;
  }
  // the DEC cell of a changed row is Unknown (no planned-DEC model yet), so its DEC is no value.
  row.dec = { ...(row.dec || {}), needed: null, committed: null, shortfall: null };
  row.badges = [...boardPlanPlotFacts(liveRow || {}).badges, ...boardPlanRowBadges(row, status)];
  // Round 3: the text filter finds a plot by its PLANNED worker names (Live finds it by its live ones).
  row.worker_names = entries.filter((p) => p.group_id === 'worker' || p.group_id === 'runi')
    .map((p) => p.asset?.name || p.name).filter(Boolean);
  row.planning = { status, reason };
  return { row, changed: true, status, placements: entries };
}

// IN-0315: the board totals while planning, from the plan only. Plots with nothing planned add 0 and
// count as idle. A changed plot adds its planned figures only from a complete answer; a pending or
// unknown one makes that total null ("Unknown"), never a live figure. DEC for a planned roster is not
// worked out yet (the row cell says Unknown), so any changed plot makes the DEC total Unknown.
// `projectRow(liveRow)` is the board's own row projection, so totals and cells ask the same question.
export function boardPlanTotals(liveRows, plan, projectRow) {
  const rows = Array.isArray(liveRows) ? liveRows : [];
  const byDeed = new Map();
  for (const placement of plan?.placements || []) {
    const deed = String(placement.deed_uid);
    if (!byDeed.has(deed)) byDeed.set(deed, []);
    byDeed.get(deed).push(placement);
  }
  const totals = { plots: rows.length, plots_idle: 0, workers_staked: 0, base_pp: 0, construction_pp: 0, harvest_pp: 0,
    dec_needed: 0, planning: true, unknown_plots: 0 };
  for (const live of rows) {
    const placements = byDeed.get(String(live?.deed_uid)) || [];
    const workers = placements.filter((p) => p.group_id === 'worker' || p.group_id === 'runi').length;
    if (!workers) totals.plots_idle += 1;
    totals.workers_staked += workers;
    if (!placements.length) continue;
    totals.dec_needed = null;
    const projected = projectRow(live);
    if (!projected || projected.status !== 'planned') {
      totals.unknown_plots += 1;
      totals.base_pp = null; totals.construction_pp = null; totals.harvest_pp = null;
      continue;
    }
    for (const field of ['base_pp', 'construction_pp', 'harvest_pp']) {
      const value = Number(projected.row[field]);
      if (totals[field] !== null) totals[field] = Number.isFinite(value) ? totals[field] + value : null;
    }
  }
  return totals;
}

// Debounced, bounded evaluation requests for changed rows only. Transport and timers are
// injected (the browser passes fetch/setTimeout; tests pass fakes), so nothing here runs
// at module load and no handle outlives a test.
export function createBoardPlanEvaluator({ request, adoptLatest = null, onUpdate = () => {}, setTimer = setTimeout,
  clearTimer = clearTimeout, debounceMs = 250, batchSize = 50 } = {}) {
  if (typeof request !== 'function') throw new Error('createBoardPlanEvaluator needs a request function.');
  const results = new Map(); // deed -> { signature, row }
  const assets = new Map(); // asset_ref -> state (from the latest answer that named it)
  const queued = new Map(); // deed -> { signature, placements, stamp }
  const inFlight = new Map(); // deed -> signature
  let timer = null;
  let running = false;
  let epoch = 0;
  const adoptedStamps = new Set();
  function arm() {
    if (timer !== null || running || !queued.size) return;
    timer = setTimer(() => { timer = null; flush(); }, debounceMs);
  }
  function settle(rows, signatureByDeed, rowsByDeed, assetStates) {
    const touched = [];
    for (const [deed, signature] of signatureByDeed) {
      if (inFlight.get(deed) === signature) inFlight.delete(deed);
      results.set(deed, { signature, row: rowsByDeed.get(deed) || { deed_uid: deed, coverage: 'unknown', reason: 'missing_in_answer' } });
      touched.push(deed);
    }
    for (const [ref, state] of Object.entries(assetStates || {})) assets.set(ref, state);
    if (touched.length) onUpdate(touched);
  }
  async function flush() {
    if (running) return;
    running = true;
    const startEpoch = epoch;
    try {
      while (queued.size && startEpoch === epoch) {
        const batch = [...queued.entries()].slice(0, batchSize);
        batch.forEach(([deed]) => queued.delete(deed));
        const stamp = batch[0][1].stamp;
        const sameStamp = batch.filter(([, item]) => item.stamp === stamp);
        batch.filter(([, item]) => item.stamp !== stamp).forEach(([deed, item]) => queued.set(deed, item));
        const signatureByDeed = new Map(sameStamp.map(([deed, item]) => [deed, item.signature]));
        sameStamp.forEach(([deed, item]) => inFlight.set(deed, item.signature));
        const body = { capture_generated_at: stamp, rows: sameStamp.map(([deed, item]) => ({ deed_uid: deed,
          placements: item.placements.map((p) => ({ group_id: p.group_id, slot_index: p.slot_index, asset_ref: String(p.asset_ref) })) })) };
        let answer = null;
        let failure = 'request_failed';
        try {
          answer = await request(body);
        } catch (error) {
          failure = error?.code || 'request_failed';
          if (failure === 'capture_superseded' && adoptLatest && !adoptedStamps.has(stamp)) {
            adoptedStamps.add(stamp);
            sameStamp.forEach(([deed]) => inFlight.delete(deed));
            // The board adopts the newer capture and re-renders; the changed rows ask again
            // with the new stamp. Their old question is dropped, not answered with a guess.
            try { await adoptLatest(); } catch (_) { /* rows stay pending; next render asks again */ }
            continue;
          }
        }
        if (startEpoch !== epoch) break;
        const rowsByDeed = new Map((answer?.rows || []).filter((row) => row && row.deed_uid).map((row) => [String(row.deed_uid), row]));
        if (!answer) for (const [deed] of sameStamp) rowsByDeed.set(deed, { deed_uid: deed, coverage: 'unknown', reason: failure });
        settle(sameStamp, signatureByDeed, rowsByDeed, answer?.assets);
      }
    } finally {
      running = false;
      arm();
    }
  }
  return {
    // Ask for one changed row. A cached or in-flight answer for the same question is reused.
    want(deedUid, placements, stamp, signature) {
      const deed = String(deedUid);
      if (results.get(deed)?.signature === signature || inFlight.get(deed) === signature) return;
      if (queued.get(deed)?.signature === signature) return;
      queued.set(deed, { signature, placements: placements.slice(), stamp });
      arm();
    },
    result(deedUid, signature) {
      const hit = results.get(String(deedUid));
      return hit && hit.signature === signature ? hit.row : null;
    },
    asset(ref) { return assets.get(String(ref)) || null; },
    reset() {
      epoch += 1;
      results.clear(); assets.clear(); queued.clear(); inFlight.clear(); adoptedStamps.clear();
      if (timer !== null) { clearTimer(timer); timer = null; }
    },
    pendingCount() { return queued.size + inFlight.size; },
    flushNow() { if (timer !== null) { clearTimer(timer); timer = null; } return flush(); },
  };
}
