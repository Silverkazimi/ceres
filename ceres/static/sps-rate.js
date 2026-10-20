// the current SPS per PP per hour (GET /api/sps-rate), fetched lazily after the
// board and kept for a few minutes -- the shared pool's rate moves during the day. Display
// only: a failed or empty fetch leaves spsRateNow() null and every SPS figure falls back
// to its pool-share wording.
import { playerQueryAppend } from './board.js';

const MAX_AGE_MS = 5 * 60 * 1000;
let CACHE = { url: null, at: 0, value: null, pending: null };

export function spsRateNow() {
  return CACHE.url === playerQueryAppend('/api/sps-rate') ? CACHE.value : null;
}

export function loadSpsRate() {
  const url = playerQueryAppend('/api/sps-rate');
  if (CACHE.url === url && CACHE.pending) return CACHE.pending;
  if (CACHE.url === url && Date.now() - CACHE.at < MAX_AGE_MS) return Promise.resolve(CACHE.value);
  const entry = { url, at: Date.now(), value: CACHE.url === url ? CACHE.value : null, pending: null };
  entry.pending = fetch(url, { cache: 'no-store' })
    .then((resp) => (resp.ok ? resp.json() : null))
    .then((body) => (body && Number(body.rate) > 0 ? { rate: Number(body.rate), plots: body.plots, source: body.source } : null))
    .catch(() => null)
    .then((value) => { entry.value = value; entry.pending = null; return value; });
  CACHE = entry;
  return entry.pending;
}
