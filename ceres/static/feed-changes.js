// (maintainer: "Worksite changes (24h building time) and clearing plots ... aka 'Feed the workers' must appear in
// the Changes menu and should be able to be signed there, in batched processes if possible ... If they are not
// finished yet, they should still appear with time and as waiting, never be a blocker for other changes to be signed").
// Every plot with a running construction becomes one Changes row: a building one waits (gold, never signed), a
// finished one offers "Feed the workers". Its operation is built by the worksite window's own request
// (worksite.js `prepareWorksiteCompletion`); a feed that is not ready or whose preview fails is set aside, never a veto.
import { BOARD } from './board.js';
import { esc, humanizeInstant, num } from './format.js';
import { prepareWorksiteCompletion } from './worksite.js';

// deedUid -> { loading, error, grainCost, txId, sentAt }. Session memory only; the board decides what exists.
export const FEED_STATE = new Map();
const SENT_HOLD_MS = 10 * 60 * 1000; // a sent feed stays out of the next signature until the row refreshes, at most this long
let FEED_CHECK_RUNNING = false;

// One item per plot with a construction that has something to wait for or to sign: 'building' (ready later) or 'feed' (finished).
// (maintainer): a STALLED build (no PP, no timer, nothing to sign) is a state of the plot with its own Stalled badge, not a change: it never appears in Changes.
export function constructionItems(plots = BOARD?.plots || [], now = Date.now()) {
  const out = [];
  for (const row of plots) {
    const change = row?.pending_change;
    const completion = row?.worksite_completion || null;
    if (!row?.deed_uid || (!completion && change?.kind !== 'worksite_change')) continue;
    if (!completion && change.stalled) continue;
    const readyAt = completion?.ready_at || change?.ready_at || null;
    const readyMs = readyAt ? Date.parse(readyAt) : NaN;
    const kind = completion || (Number.isFinite(readyMs) && readyMs <= now) ? 'feed' : 'building';
    out.push({ deedUid: row.deed_uid, row, kind, toLabel: completion?.to_label || change?.to_label || change?.to || 'the worksite', readyAt, grainCost: completion?.grain_cost ?? null });
  }
  const feeds = new Set(out.filter((item) => item.kind === 'feed').map((item) => item.deedUid));
  for (const [deedUid, state] of [...FEED_STATE]) {
    if (!feeds.has(deedUid) || (state.txId && now - (state.sentAt || 0) > SENT_HOLD_MS)) FEED_STATE.delete(deedUid);
  }
  return out;
}

// Why a finished feed is not in this signature, or null when it is signable.
export function feedSetAside(item) {
  if (item.kind !== 'feed') return 'waiting';
  const state = FEED_STATE.get(item.deedUid);
  if (state?.txId) return 'working';
  if (state?.error) return 'waiting';
  return null;
}

export function constructionModel(plots = BOARD?.plots || []) {
  const items = constructionItems(plots);
  const signable = items.filter((item) => !feedSetAside(item));
  const waiting = items.filter((item) => feedSetAside(item) === 'waiting');
  return { items, signable, waiting, byDeed: new Map(items.map((item) => [item.deedUid, item])) };
}

function feedErrorText(error) {
  const text = String(error || '').replace(/^Error:\s*/, '');
  return /not ready/i.test(text) ? 'the game has not finished this build yet' : `could not prepare it: ${text}`;
}

export function constructionRowHtml(item) {
  if (!item) return '';
  const label = esc(item.toLabel);
  const row = (title, status, tone) => `<div class="ch-worksite-build-row" data-construction-kind="${item.kind}"><span><strong>${title}</strong> · <span class="checkout-move-status ch-status ${tone}">${status}</span></span></div>`;
  if (item.kind === 'building') {
    const when = humanizeInstant(item.readyAt);
    return row(`Building ${label}`, when ? `Waiting · ready ${esc(when.absolute)} (${esc(when.relative)})` : 'Waiting · ready time unknown', 'is-waiting');
  }
  const state = FEED_STATE.get(item.deedUid) || {};
  const grain = state.grainCost ?? item.grainCost;
  const cost = grain !== null && grain !== undefined ? `${esc(num(grain))} Grain` : state.loading ? 'checking the Grain cost…' : 'Grain cost checked when signing';
  if (state.txId) return row('Feed the workers', `Signed — waiting for the game to finish ${label}`, 'is-dim');
  if (state.error) return row('Feed the workers', `Waiting · ${esc(feedErrorText(state.error))}`, 'is-waiting');
  return row('Feed the workers', `Ready to sign · ${cost} · finishes ${label}`, 'is-ok');
}

// Called when the Changes window opens: each finished feed is previewed once (Grain cost, readiness).
export async function checkFeedPreviews(onChange) {
  if (FEED_CHECK_RUNNING) return;
  FEED_CHECK_RUNNING = true;
  try {
    for (const item of constructionItems().filter((entry) => entry.kind === 'feed')) {
      const current = FEED_STATE.get(item.deedUid);
      if (current?.txId || current?.loading || (current && !current.error)) continue;
      FEED_STATE.set(item.deedUid, { loading: true });
      onChange?.();
      try {
        const plan = await prepareWorksiteCompletion(item.row);
        FEED_STATE.set(item.deedUid, { grainCost: plan.grainCost });
      } catch (error) {
        FEED_STATE.set(item.deedUid, { error: String(error) });
      }
      onChange?.();
    }
  } finally {
    FEED_CHECK_RUNNING = false;
  }
}

export function markFeedSent(deedUid, txId) { FEED_STATE.set(deedUid, { ...(FEED_STATE.get(deedUid) || {}), loading: false, error: null, txId, sentAt: Date.now() }); }
export function markFeedAside(deedUid, error) { FEED_STATE.set(deedUid, { ...(FEED_STATE.get(deedUid) || {}), loading: false, error: String(error) }); }
export function forgetFeedErrors() { for (const [deedUid, state] of [...FEED_STATE]) if (state.error) FEED_STATE.delete(deedUid); }
