// staged changes follow the player across devices once they are signed in (optional, like the routing setup). This device's staged store
// (`ceres:stage-plan:v1:<account>`) and the saved copy are merged in both directions, nothing is lost; an undone or signed change is remembered as removed so it
// does not come back from the other device. Without a sign-in nothing here runs and staged changes stay on this device.
import { hasSession, stagedDigest, stagedSyncedDigest, syncStaged } from './account-space.js';
import { CURRENT_PLAYER, BOARD } from './board.js';
import { onPlansChanged, persistStagePlans, restoreStagePlans } from './stage-plans.js';

const PUSH_DELAY_MS = 2000;
let timer = null;
let running = false;
let again = false;

const account = () => String(CURRENT_PLAYER || BOARD?.player || '').toLowerCase();
const storeKey = name => `ceres:stage-plan:v1:${name}`;

function readLocal(name) {
  try {
    const raw = localStorage.getItem(storeKey(name));
    if (raw === null) return { ok: true, data: {} };
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? { ok: true, data: parsed } : { ok: false, data: {} };
  } catch (_) { return { ok: false, data: {} }; }
}

export async function syncStagedNow() {
  const name = account();
  if (!name || !hasSession(name)) return { ok: false, reason: 'signed-out' };
  if (running) { again = true; return { ok: true, queued: true }; }
  running = true;
  try {
    await persistStagePlans();   // what is staged in this page is in the store before it is compared
    const local = readLocal(name);
    if (!local.ok) return { ok: false, reason: 'unreadable' };   // an unreadable local record is never overwritten
    const result = await syncStaged(name, {
      read: () => { const latest = readLocal(name); if (!latest.ok) throw new Error('Unreadable staged plans'); return latest.data; },
      write: merged => { localStorage.setItem(storeKey(name), JSON.stringify({ ...merged, revision: (Number(readLocal(name).data.revision) || 0) + 1, account: name, dropped: [] })); },
    });
    if (result.ok && result.changed && account() === name) await restoreStagePlans();   // adopt what the other device staged, checked against the live board like any kept plan
    return result;
  } catch (_) { return { ok: false, reason: 'failed' }; } finally {
    running = false;
    if (again) { again = false; if (!stagedSavedHere(name)) schedule(); }   // only a real change queued meanwhile; a sync's own echo is not one
  }
}

function schedule() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => { timer = null; syncStagedNow(); }, PUSH_DELAY_MS);
}

export function stagedSavedHere(name = account()) {
  const local = readLocal(name);
  return local.ok && stagedSyncedDigest(name) === stagedDigest(local.data);
}

export function mountStagedSync() {
  // Only a REAL change of this device's staged changes starts a sync (a write that equals the last synced state, such as the one a sync itself causes, does not): without this guard a sync re-triggered itself forever.
  onPlansChanged(() => { const name = account(); if (name && hasSession(name) && !running && !stagedSavedHere(name)) schedule(); });
  window.addEventListener('ceres:space-changed', () => { if (hasSession(account())) syncStagedNow(); });
}
