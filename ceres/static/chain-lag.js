// how far the game is behind Hive (shown to the maintainer). : no wait or deadline lives here any more
// every confirmation wait is the shared confirmer's (shared-confirmer.js).
// Two single reads of public state per refresh -- the game's last processed block (GET /last_block) and the Hive head
// (condenser_api.get_dynamic_global_properties) -- never a loop over blocks or any history read ().
// Everything here is read/verify/status only: nothing in this file signs, sends or decides what is sent.
import { hiveReadRpc } from './transaction-queue.js';

export const GAME_LAST_BLOCK_URL = 'https://api.splinterlands.com/last_block';
export const HIVE_BLOCK_SECONDS = 3;
// Stall rule: the game's last block has not moved for 3 minutes (60 Hive blocks). The game normally follows the head
// within seconds and the verdict appears ~20-22 s after inclusion (api-reference 7.9), so three minutes without a single
// processed block is an outage, not lag.
export const GAME_STALL_MS = 180000;
// Lag reads are throttled: at most one pair of reads per 15 s, however many waits ask for it.
export const LAG_REFRESH_MS = 15000;

const blockNumber = value => (Number.isSafeInteger(value) && value > 0 ? value : null);

// Blocks and seconds the game is behind the Hive head; null when either read is missing.
export function gameLag(gameBlock, hiveHead) {
  const game = blockNumber(gameBlock);
  const head = blockNumber(hiveHead);
  if (!game || !head) return null;
  const blocks = Math.max(0, head - game);
  return { gameBlock: game, hiveHead: head, blocks, seconds: blocks * HIVE_BLOCK_SECONDS };
}

// One line of plain words for the dialog; '' when the lag is unknown (the line is then simply absent).
export function gameLagText(lag, { stalled = false } = {}) {
  if (stalled) return 'The game has not read a new Hive block for 3 min';
  if (!lag || !Number.isFinite(lag.seconds)) return '';
  if (lag.seconds <= 6) return 'Game is keeping up with Hive';
  if (lag.seconds < 90) return `Game is about ${Math.max(5, Math.round(lag.seconds / 5) * 5)} s behind Hive`;
  return `Game is about ${Math.round(lag.seconds / 60)} min behind Hive`;
}

// Watches the game's last block across refreshes. `stalled` turns true once successful reads have shown the same block
// for stallMs. A failed read proves nothing: it neither moves the block nor counts towards a stall.
export function createStallWatch(stallMs = GAME_STALL_MS) {
  let lastBlock = null;
  let movedAt = null;
  let seenAt = null;
  return {
    observe(block, at = Date.now()) {
      const value = blockNumber(block);
      if (value == null) return this.stalled();
      if (lastBlock == null || value > lastBlock) { lastBlock = value; movedAt = at; }
      seenAt = at;
      return this.stalled();
    },
    stalled() { return movedAt != null && seenAt - movedAt >= stallMs; },
    get lastBlock() { return lastBlock; },
  };
}

export async function readGameLastBlock(fetchImpl = globalThis.fetch) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetchImpl(GAME_LAST_BLOCK_URL, { credentials: 'omit', signal: controller.signal });
    if (!response?.ok) return null;
    const body = await response.json();
    return blockNumber(body?.last_block);
  } catch (_) { return null; } finally { clearTimeout(timer); }
}

export async function readHiveHeadBlock(rpc = hiveReadRpc) {
  try { return blockNumber((await rpc('condenser_api.get_dynamic_global_properties', []))?.head_block_number); }
  catch (_) { return null; }
}

// A throttled lag reader for one dialog: refresh() reads at most once per LAG_REFRESH_MS and feeds the stall watch.
export function createLagMonitor({ fetchImpl, rpc, now = () => Date.now(), stallMs = GAME_STALL_MS,
  refreshMs = LAG_REFRESH_MS } = {}) {
  const watch = createStallWatch(stallMs);
  const state = { lag: null, readAt: 0, inflight: null };
  async function read() {
    const [gameBlock, hiveHead] = await Promise.all([readGameLastBlock(fetchImpl), readHiveHeadBlock(rpc)]);
    const at = now();
    watch.observe(gameBlock, at);
    state.lag = gameLag(gameBlock, hiveHead) || state.lag;
    state.readAt = at;
    return state.lag;
  }
  return {
    refresh(force = false) {
      if (state.inflight) return state.inflight;
      if (!force && state.readAt && now() - state.readAt < refreshMs) return Promise.resolve(state.lag);
      state.inflight = read().finally(() => { state.inflight = null; });
      return state.inflight;
    },
    get lag() { return state.lag; },
    get stalled() { return watch.stalled(); },
    text() { return gameLagText(state.lag, { stalled: watch.stalled() }); },
  };
}
