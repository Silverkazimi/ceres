// the wallet's pool swap card (option B, the always-visible pair card) on the Grain, Wood, Stone
// and Iron tabs. It replaces the two LP cards the maintainer called "unusable and 100% noise".
// Both ways: the ⇅ flip swaps You pay / You receive, resource -> DEC
// (sell, spent from a region) and DEC -> resource (buy, received into a region). G2 was proven on
// 2026-09-28 by a game transaction <tx id>; a direction the server does not list
// (`wallet.swap.directions`) stays hidden.
// Every figure is live: one `/api/wallet/lp/quote` per settled amount (700 ms idle, one in flight, newer
// input wins). (maintainer: "Completely remove the edit button ... Add a swap button. It opens keychain
// i sign. Done"): one "Swap X -> Y" button, no Review/Edit. The click makes one fresh 3-read build of what the
// card shows (refused if it would give less than the shown figure minus 2.5 %) and opens the wallet.
// S10: only an UNSETTLED swap is stored locally (localStorage, `ceres:wallet-lp-intent:v1`), so a reload or a second tab cannot
// offer a second signature for the same swap; everything else is page memory ().
// No error states (): a failed quote shows no figure; a moved quote or a declined wallet leaves the
// card with its button ready again (a fresh "You receive" is the only change); a game refusal returns to the
// card with live balances. No response text from the server is ever rendered.
// Voucher and SPS too. They live in the account
// wallet, so they swap with no region (region_uid ""). (maintainer: "In the game, it is one click, one
// step"): an SPS sale is still two transactions, but the second -- the Active `sm_sps` transfer of the SPS to the
// pool (memo lpsid:<pending_swap_id>) -- is requested by itself as soon as the swap confirms, as the game does.
// A declined transfer leaves one "Send SPS to the pool" button; an interrupted or unclear transfer is never offered again (settle-only).
import { balancesChanged } from './balances-bus.js';
import { esc, regionLabel } from './format.js';
import { tokenIcon, tokenLabel } from './harvest-state.js';
import { plaque } from './wallet-resources.js';
import { walletControlsHtml, walletMethodHtml, walletReady, walletSignLabelHtml, walletWaitHtml } from './wallet.js';
import { requestBroadcastThroughGate } from './broadcast-gate.js';
import { initCustomSelect } from './customSelect.js';

export const QUOTE_IDLE_MS = 700;
export const VERIFY_SCHEDULE_MS = [0, 2000, 4000, 8000, 12000, 18000, 26000, 36000, 50000, 70000];  // the game can take ~30 s to index
// after the schedule Ceres keeps checking on its own every 15 s.
// a swap that is in neither the game
// nor the account's own history 5 min after the wallet was asked was not sent, and the card says so. A wallet can still broadcast
// a signed request up to an hour after it was built (Hive's expiry; today's HiveAuth approval came 8 min late), so the intent is
// retained as a pending intent while a wallet response can still be delayed; absence by history alone does not release it.
export const RECHECK_MS = 15000;
export const NEVER_SENT_AFTER_MS = 3900 * 1000; // Hive expiry plus the existing inclusion grace
export const LATE_WATCH_MS = 65 * 60 * 1000;
// with 's clock: the game confirms a
// transaction in seconds (: up to ~30 s to index), the pool answered today's swap in 51 s. Three minutes for the game
// five for the pool; past that the card turns red and says so. Checking can always be resumed by hand.
export const CONFIRM_TTL_MS = 3 * 60 * 1000;
export const SETTLE_TTL_MS = 5 * 60 * 1000;
export const SETTLE_SCHEDULE_MS = [10000];  // every 10 s
// A hard cap on rounds, well past both limits in real time; it only ends a loop whose clock never moves (a page without timers).
const MAX_ROUNDS = 400;
// The game's own fee wording (Trade Hub pool swap): 10 % on the swap value, half to shareholders, half burnt.
export const FEE_LABEL = '10% fee on swap value (5% to shareholders, 5% burnt)';
const states = new Map();  // `${player}:${symbol}` -> swap state; page memory only ()
const fixed = value => Number(value).toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const plain = value => String(Number(value));  // a figure placed in an input, no grouping commas
const art = symbol => tokenIcon(symbol) ? `<img src="${esc(tokenIcon(symbol))}" alt="">` : '';
const label = symbol => symbol === 'DEC' ? 'DEC' : tokenLabel(symbol);
const btn = (action, text, kind = 'secondary', density = 'compact', disabled = false, extra = '') =>
  `<button type="button" class="ceres-btn ceres-btn--${kind}" data-control-density="${density}" data-dec="lp:${action}"${extra}${disabled ? ' disabled' : ''}>${text}</button>`;

// the Transfer tab's own page memory (recipient, regions, amount, quote, review); never stored.
const blankTransfer = () => ({ to: '', recipient: null, looking: false, toRegion: null, fromRegion: null, amount: '', quote: null, quoted: '', review: null, reviewing: false, note: '', noteTone: '', timer: null, generation: 0, inFlight: false, queued: false });
const blankPool = () => ({ mode: null, amount: '', dec: '', decTyped: false, pct: '', region: null, locked: null, loading: false });
function blank() {
  return { pool: blankPool(), tab: 'swap', xfer: blankTransfer(), direction: null, amount: '', target: '', mode: 'pay', region: null, quote: null, quoted: '', stage: 'edit', receipt: null, generation: 0, timer: null, inFlight: false, queued: false };
}
// S10 / L2-02: an unresolved swap (signed or possibly signed) is kept in THIS browser's storage, so a reload or a second tab restores
// its receipt instead of offering a fresh swap button. Local only; no keys, no signatures; dropped when the swap is settled or provably failed.
const INTENT_KEY = (player, symbol) => `ceres:wallet-lp-intent:v1:${String(player).toLowerCase()}:${symbol}`;
// intents the wallet never answered, kept (with their receipt) until Hive's expiry so a late broadcast is adopted, never doubled.
const WATCH_KEY = (player, symbol) => `ceres:wallet-lp-watch:v1:${String(player).toLowerCase()}:${symbol}`;
function readWatch(player, symbol) {
  try { const list = JSON.parse(localStorage.getItem(WATCH_KEY(player, symbol)) || '[]'); return Array.isArray(list) ? list.filter(item => item?.receipt?.nonce) : []; }
  catch (_) { return []; }
}
function watchAskedAt(item) { for (const raw of [item?.receipt?.asked_at, item?.receipt?.signed_at, item?.at]) { const ms = raw == null ? NaN : Date.parse(raw); if (Number.isFinite(ms)) return ms; } return null; }
function writeWatch(player, symbol, list) { try { if (list.length) localStorage.setItem(WATCH_KEY(player, symbol), JSON.stringify(list)); else localStorage.removeItem(WATCH_KEY(player, symbol)); } catch (_) { /* page memory only */ } }
// ( S3 / L2-01: an unknown wallet outcome is never permission to sign again): the line shown while the account history
// cannot be read. A failed read is never evidence that a swap was not sent.
export const HISTORY_UNREADABLE_NOTE = 'Ceres has not confirmed what happened to this request yet. It stays pending; do not swap again.';
// find reads only the newest 100 operations; `since` (when the wallet was asked) lets it say whether that window covers the swap.
const findBody = receipt => ({ nonce: receipt.nonce, ...(receipt.asked_at || receipt.signed_at ? { since: receipt.asked_at || receipt.signed_at } : {}) });
// One lookup per watched intent (the account's own recent history by nonce). Returns 'adopted' (a hit, restored as confirming)
// 'clear' (every watched nonce was read successfully and is not there) or 'unknown' (a read failed: no new swap may start).
export async function adoptLateBroadcast(ctx, symbol, st) {
  const now = Date.now();
  const list = readWatch(ctx.player, symbol).filter(item => { const askedAt = watchAskedAt(item); return askedAt !== null && now - askedAt < NEVER_SENT_AFTER_MS; });
  writeWatch(ctx.player, symbol, list);
  let unknown = false;
  for (const item of list) {
    let found = null;
    try { found = await post('find', ctx.player, findBody(item.receipt)); } catch (_) { unknown = true; continue; }
    if (found?.status === 'not_found' && found.covered === true) { unknown = true; continue; } // before Hive expiry, absence is not proof the wallet cannot still broadcast
    if (found?.status === 'not_found') { unknown = true; continue; }
    if (found?.status !== 'found') { unknown = true; continue; }
    writeWatch(ctx.player, symbol, list.filter(other => other !== item));
    Object.assign(st, { stage: 'confirming', receipt: { ...item.receipt, tx_id: found.tx_id, unknownOutcome: false, signed_at: new Date().toISOString() }, lpNote: '', lpNoteTone: '', stopped: false, failed: null, busy: false });
    syncStoredIntent(ctx.player, symbol, st);
    verify(ctx, symbol, st);
    return 'adopted';
  }
  return unknown ? 'unknown' : 'clear';
}
// The edit card's history line follows the latest watch lookup; returns true when the card must be redrawn.
function noteWatchResult(st, outcome) {
  if (outcome === 'unknown' && st.lpNote !== HISTORY_UNREADABLE_NOTE) { Object.assign(st, { lpNote: HISTORY_UNREADABLE_NOTE, lpNoteTone: 'warn' }); return true; }
  if (outcome === 'clear' && st.lpNote === HISTORY_UNREADABLE_NOTE) { Object.assign(st, { lpNote: '', lpNoteTone: '' }); return true; }
  return false;
}
// S10 / L2-03: NO expiry. Elapsed time never proves an unresolved swap did not happen; only the maintainer can say so ("I checked my wallet: not sent").
const OPEN_STAGES = ['signing', 'confirming', 'funding', 'funding-signing', 'settling'];
// S10 / A-02: returns false when the record could not be written; every wallet handoff checks it and does NOT open the wallet then.
export function syncStoredIntent(player, symbol, st) {
  try {
    // S10 / A-01: a record that another tab's swap owns (a different ownership token) is never written over or removed by a late result of this one.
    const current = readStored(player, symbol);
    if (intentIsOpen(current) && current.token && current.token !== st.token) return false;
    if (st.receipt && OPEN_STAGES.includes(st.stage)) {
      localStorage.setItem(INTENT_KEY(player, symbol), JSON.stringify({ stage: st.stage, receipt: st.receipt, token: st.token || null, savedAt: Date.now() }));
    } else { localStorage.removeItem(INTENT_KEY(player, symbol)); st.token = null; }
    return true;
  } catch (_) { return false; }
}
const isPoolKind = kind => kind === 'add' || kind === 'remove';
const poolSymbol = intent => intent.symbol;
const intentSymbol = intent => isPoolKind(intent.kind) ? intent.symbol : intent.kind === 'swap_buy' ? intent.receive : intent.pay;
function readStored(player, symbol) {
  try { return JSON.parse(localStorage.getItem(INTENT_KEY(player, symbol)) || 'null'); } catch (_) { return null; }
}
const intentIsOpen = stored => !!stored && typeof stored === 'object' && OPEN_STAGES.includes(stored.stage);
// S10 / A-01: read-and-reserve is one step per account and symbol across the tabs of this browser (Web Lock), with a read-back of the
// reservation's own token as the check where Web Locks do not exist.
async function withIntentLock(player, symbol, work) {
  const locks = globalThis.navigator?.locks;
  if (locks?.request) return locks.request(`ceres:wallet-lp:${String(player).toLowerCase()}:${symbol}`, async () => work());
  return work();
}
function restoreIntent(player, symbol, st) {
  try {
    const stored = JSON.parse(localStorage.getItem(INTENT_KEY(player, symbol)) || 'null');
    if (!stored || typeof stored !== 'object' || !stored.receipt || typeof stored.receipt !== 'object' || !OPEN_STAGES.includes(stored.stage)) return;
    // A swap that was still waiting for the wallet when the page ended has an unknown outcome: it is restored as one to be checked.
    const wasSigning = stored.stage === 'signing';
    const wasFunding = stored.stage === 'funding-signing';   // the Active transfer was at the wallet prompt: its outcome is unknown, never "send it again"
    st.token = stored.token || null;
    st.receipt = { ...stored.receipt, restored: true, ...(wasSigning ? { unknownOutcome: true } : {}), ...(wasFunding ? { fundingUnknown: true } : {}) };
    st.stage = wasSigning ? 'confirming' : wasFunding ? 'settling' : stored.stage;
    if (wasFunding) st.settleNote = 'Your wallet did not say whether the SPS transfer was sent. Check the pool settlement before sending anything again.';
  } catch (_) { /* a damaged record is ignored */ }
}
export function swapState(player, symbol) {
  const id = `${player}:${symbol}`;
  if (!states.has(id)) { const fresh = blank(); restoreIntent(player, symbol, fresh); states.set(id, fresh); }
  return states.get(id);
}
// Tabs and drawer close stay locked from the wallet approval until the game has answered (or the page ends).
// only an open wallet prompt locks the tabs; confirming is a background check and never freezes the wallet.
export function swapLocked(player) {
  for (const [id, st] of states) if (id.startsWith(`${player}:`) && (st.stage === 'signing' || st.stage === 'funding-signing')) return id.slice(player.length + 1);
  return null;
}
export function parseAmount(text) {
  const clean = String(text ?? '').replace(/,/g, '').trim();
  return /^[0-9]{1,15}(?:\.[0-9]{1,3})?$/.test(clean) && Number(clean) > 0 ? clean : null;
}
// The input can never hold more than the live balance or more than three decimals (no "insufficient" state).
export function capAmount(text, available) {
  let clean = String(text ?? '').replace(/[^0-9.]/g, '');
  const dot = clean.indexOf('.');
  if (dot >= 0) clean = clean.slice(0, dot + 1) + clean.slice(dot + 1).replace(/\./g, '').slice(0, 3);
  if (clean && Number(clean) > Number(available)) clean = String(Math.floor(Number(available) * 1000) / 1000);
  return clean;
}

const byHolding = symbol => (a, b) => Number(b.balances?.[symbol] || 0) - Number(a.balances?.[symbol] || 0) || String(a.region_uid).localeCompare(String(b.region_uid), undefined, { numeric: true });
// Sell spends from a region that holds the resource; buy can land the resource in any of the player's regions.
function regionsFor(symbol, wallet, direction) {
  const rows = (wallet.regions || []).filter(r => r.balances?.[symbol] != null);
  return (direction === 'sell' ? rows.filter(r => Number(r.balances[symbol]) > 0) : rows).sort(byHolding(symbol));
}
const isWalletToken = (symbol, wallet) => !!wallet?.swap?.account_symbols?.includes(symbol);
function walletContext(symbol, wallet, player) {
  // Voucher / SPS: the balance is the account's own; there is no region to spend from or receive into.
  const allowed = { sell: wallet.swap.directions?.includes('swap_sell'), buy: wallet.swap.directions?.includes('swap_buy') };
  const dec = Number(wallet.account.DEC);
  const held = Number(wallet.account[symbol]);
  const possible = { sell: allowed.sell && held > 0, buy: allowed.buy };
  if (!possible.sell && !possible.buy) return null;
  const st = swapState(player, symbol);
  if (!possible[st.direction]) st.direction = possible.sell ? 'sell' : 'buy';
  const buy = st.direction === 'buy';
  st.region = '';
  return {
    st, regions: [], region: null, walletToken: true, dec, held, swap: wallet.swap, buy, canFlip: possible.sell && possible.buy,
    kind: buy ? 'swap_buy' : 'swap_sell', pay: buy ? 'DEC' : symbol, receive: buy ? symbol : 'DEC',
    available: buy ? dec : held, rates: wallet.swap.rates?.[symbol]?.[buy ? 'swap_buy' : 'swap_sell'] || null,
    fee: wallet.swap.fees?.[symbol] || '10', twoStep: !buy && !!wallet.swap.two_step_sell?.includes(symbol),
  };
}
function context(symbol, wallet, player) {
  if (!wallet?.swap?.symbols?.includes(symbol) || !wallet.account) return null;
  if (isWalletToken(symbol, wallet)) return walletContext(symbol, wallet, player);
  if (!wallet.regions) return null;
  const allowed = { sell: wallet.swap.directions?.includes('swap_sell'), buy: wallet.swap.directions?.includes('swap_buy') };
  const possible = { sell: allowed.sell && regionsFor(symbol, wallet, 'sell').length > 0, buy: allowed.buy && regionsFor(symbol, wallet, 'buy').length > 0 };
  if (!possible.sell && !possible.buy) return null;
  const st = swapState(player, symbol);
  if (!possible[st.direction]) st.direction = possible.sell ? 'sell' : 'buy';
  const buy = st.direction === 'buy';
  const regions = regionsFor(symbol, wallet, st.direction);
  if (!regions.some(r => r.region_uid === st.region)) st.region = regions[0].region_uid;
  const region = regions.find(r => r.region_uid === st.region);
  const dec = Number(wallet.account.DEC);
  const held = Number(region.balances[symbol]);
  return {
    st, regions, region, dec, held, swap: wallet.swap, buy, canFlip: possible.sell && possible.buy,
    kind: buy ? 'swap_buy' : 'swap_sell', pay: buy ? 'DEC' : symbol, receive: buy ? symbol : 'DEC',
    available: buy ? dec : held, rates: wallet.swap.rates?.[symbol]?.[buy ? 'swap_buy' : 'swap_sell'] || null,
    fee: wallet.swap.fees?.[symbol] || '10', twoStep: false,
  };
}
// Where the token sits: a region plaque for land resources, the account wallet for Voucher / SPS.
const afterLabel = (c, symbol, region) => c.walletToken ? `Wallet ${esc(label(symbol))} after` : `${esc(regionLabel(region))} ${esc(label(symbol))} after`;
const line = (name, value) => `<div class="wallet-swap-line"><span>${name}</span><b>${value}</b></div>`;

// design B, simplified.
// One focus -- the big "You receive" figure; one small line under each box; everything else behind "Details".
const unitLine = (c, rates) => rates ? `1 ${esc(label(c.pay))} = ${fixed(rates.unit)} ${esc(label(c.receive))}` : '';
const detailsHtml = (c, q, fresh, extraRows) => {
  const open = !!c.st.moreOpen;
  const rows = q ? `${line(`Pool fee · ${esc(q.fee_percent || c.fee)}%`, `${fixed(q.pool_cost)} ${esc(label(c.pay))}`)}${line('You get at least', `${fixed(q.minimum)} ${esc(label(c.receive))}`)}${extraRows}` : '';
  return `<div class="wallet-swap-details${fresh ? '' : ' is-stale'}">${btn('details', `Details <span class="ceres-chevron${open ? ' is-open' : ''}" aria-hidden="true"></span>`, 'secondary', 'compact', !q, ` aria-expanded="${open}"`)}${open ? `<div class="wallet-swap-grid">${rows}</div>` : ''}</div>`;
};
function editHtml(symbol, c, player) {
  const { st, regions, region, available, dec, held, buy, pay, receive } = c;
  const q = st.quote && st.quote.kind === c.kind ? st.quote : null;
  const fresh = q && st.quoted === st.amount && parseAmount(st.amount) !== null;
  const flip = c.canFlip
    ? btn('flip', '⇅', 'secondary', 'compact', false, ` data-lp-flip aria-label="Reverse swap direction: ${esc(label(receive))} to ${esc(label(pay))}"`)
    : '<span class="wallet-swap-arrow" aria-hidden="true">↓</span>';
  const choices = regions.length > 1
    ? `<select data-lp-region aria-label="Change region">${regions.map(r => `<option value="${esc(r.region_uid)}"${r.region_uid === region.region_uid ? ' selected' : ''}>${esc(regionLabel(r.region_uid))} · ${esc(fixed(r.balances[symbol]))}</option>`).join('')}</select>` : '';
  const where = c.walletToken ? '' : `${plaque(region.region_uid)}${choices}`;
  const decAfter = q ? (buy ? dec - Number(q.input) : dec + Number(q.output)) : 0;
  const heldAfter = q ? (buy ? held + Number(q.output) : Math.max(0, held - Number(q.input))) : 0;
  const after = q ? `${line('Wallet DEC after', fixed(Math.max(0, decAfter)))}${line(afterLabel(c, symbol, region?.region_uid), fixed(heldAfter))}` : '';
  const poolOpen = isPoolKind(st.receipt?.kind);  // a pool add/withdraw is in progress below; the swap waits for it
  const busy = st.stage === 'signing' && !poolOpen;
  const action = busy ? `<span class="wallet-sign-label">Waiting for ${walletMethodHtml(player)}…</span>` : fresh ? `Swap ${fixed(q.input)} ${esc(label(pay))} → ${fixed(q.output)} ${esc(label(receive))}` : 'Enter an amount';
  // one button, no Review/Edit. The wallet
  // chooser appears beside it only while no wallet is ready to sign (OM:6100: chooser left of the sign button).
  const ready = walletReady(player);
  const swapBtn = btn('swap', action, 'primary', ready ? 'compact' : 'touch', !fresh || busy || poolOpen || !ready || !c.swap.signing_enabled, ' data-lp-primary');
  const actionRow = (ready ? swapBtn : `<span class="wallet-sign-pair wallet-swap-sign">${walletControlsHtml(player)}${swapBtn}</span>`) + (busy ? walletWaitHtml(player) : '');
  return `<div class="wallet-swap-head"><span class="wallet-swap-title"><span>${art(pay)}${esc(label(pay))}</span>→<span>${art(receive)}${esc(label(receive))}</span></span></div>
    <div class="wallet-swap-box"><div class="wallet-swap-line"><span>You pay</span><span class="wallet-swap-pcts">${[25, 50, 75, 100].map(pct => btn(`pct:${pct}`, `${pct}%`, 'secondary', 'compact', st.stage !== 'edit')).join('')}</span></div>
      <div class="wallet-swap-asset"><span class="wallet-swap-name">${art(pay)}<strong>${esc(label(pay))}</strong></span><input type="text" class="ceres-wide-input wallet-swap-amount" data-control-density="compact" data-lp-amount="${esc(symbol)}" inputmode="decimal" autocomplete="off" spellcheck="false" placeholder="0" aria-label="You pay, ${esc(label(pay))}" value="${esc(st.amount)}"></div>
      <div class="wallet-swap-sub"><span>of ${fixed(available)}${buy || c.walletToken ? ' in your wallet' : ''}</span>${buy || !where ? '' : `<span class="wallet-swap-where">${where}</span>`}</div></div>
    ${flip}
    <div class="wallet-swap-box wallet-swap-receive"><div class="wallet-swap-line"><span>You receive</span><span aria-hidden="true"></span></div><div class="wallet-swap-asset"><span class="wallet-swap-name">${art(receive)}<strong>${esc(label(receive))}</strong></span><input type="text" class="ceres-wide-input wallet-swap-amount wallet-swap-out${fresh || st.mode === 'receive' ? '' : ' is-stale'}" data-control-density="compact" data-lp-receive="${esc(symbol)}" inputmode="decimal" autocomplete="off" spellcheck="false" placeholder="0" aria-label="You receive, ${esc(label(receive))}" value="${esc(st.mode === 'receive' ? st.target : q && fresh ? plain(q.output) : '')}"></div>
      <div class="wallet-swap-sub"><span>${unitLine(c, (fresh && q.rates) || c.rates)}</span>${buy && where ? `<span class="wallet-swap-where">${where}</span>` : ''}</div></div>
    ${detailsHtml(c, q, fresh, after)}
    ${c.twoStep ? `<p class="wallet-swap-note" data-lp-two-step>Selling ${esc(label(pay))} takes two wallet approvals: this swap (Posting key), then sending the ${esc(label(pay))} to the pool with your <b>Active</b> key (a Hive transfer).</p>` : ''}
    ${actionRow}`;
}

// After signing: the receipt. An SPS sale continues by itself with the Active transfer of the SPS to the pool
// (: one click, as in the game); a declined transfer leaves one "Send SPS to the pool" button, no error text.
// while the game has not answered, the card says what Ceres is doing by itself; the only control is Stop (and Resume after it).
function waitingHtml(st) {
  if (st.stage !== 'confirming') return '';
  const r = st.receipt || {};
  const since = r.signed_at ? new Date(r.signed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
  if (st.failed === 'confirm') return `<p class="wallet-swap-note is-warn" role="status">Still pending: the game has not confirmed this ${isPoolKind(r.kind) ? 'change' : 'swap'} yet. Nothing was signed again. Resume checking asks the game once more.</p>`;
  if (st.stopped) return `<p class="wallet-swap-note is-warn" role="status">Checking is stopped. This ${isPoolKind(r.kind) ? 'change' : 'swap'} stays open until the game confirms it — do not send it again.</p>`;
  if (!r.tx_id && st.historyUnreadable) {  // a failed read never releases the swap; the card says so and keeps trying
    const late = r.signed_at && Date.now() - Date.parse(r.signed_at) > NEVER_SENT_AFTER_MS;
    return `<p class="wallet-swap-note ${late ? 'is-danger' : 'is-warn'}" role="${late ? 'alert' : 'status'}">${HISTORY_UNREADABLE_NOTE}</p>`;
  }
  const what = r.tx_id ? 'The game has not confirmed it yet.' : 'Your wallet did not return the transaction. Ceres is looking for it in your account history.';
  return st.checking ? `<p class="wallet-swap-note is-warn" role="status">${what} Ceres keeps checking by itself${since ? ` (sent ${esc(since)})` : ''}.</p>` : '';
}
function confirmActions(st) {
  if (st.stage !== 'confirming') return '';
  return st.stopped || st.failed === 'confirm' ? btn('verify', 'Resume checking', 'primary', 'touch') : st.checking ? btn('stop', 'Stop checking', 'secondary', 'touch') : '';
}
// an open confirmation (also one restored after a reload) is checked without a click, unless the maintainer stopped it.
export function resumeSwapChecks(ctx) {
  for (const [id, st] of states) {
    const [player, symbol] = [id.slice(0, id.indexOf(':')), id.slice(id.indexOf(':') + 1)];
    // a failed confirmation is terminal for the automatic check (rendering never restarts it); only Resume does.
    if (player === ctx.player && st.stage === 'confirming' && st.receipt && !st.checking && !st.stopped && !st.failed) verify(ctx, symbol, st);
    else if (player === ctx.player && st.stage === 'settling' && st.receipt && !st.settleLooping && !st.settleStopped && !st.failed) settleLoop(ctx, st);
    else if (player === ctx.player && st.stage === 'edit' && !st.busy && !st.watchLooking && readWatch(ctx.player, symbol).length) {
      st.watchLooking = true;
      adoptLateBroadcast(ctx, symbol, st).then(outcome => { st.watchLooking = false; if (outcome === 'adopted' || noteWatchResult(st, outcome)) ctx.render(); }, () => { st.watchLooking = false; });
    }
    // a sale whose swap the game accepted but whose SPS transfer was never sent continues by itself once per page load
    // (the same automatic second step as right after the swap); a decline leaves the button and says so.
    else if (player === ctx.player && st.stage === 'funding' && st.receipt?.tx_id && !st.busy && !st.autoFunded && walletReady(ctx.player)) { st.autoFunded = true; fund(ctx, st); }
  }
}

function receiptHtml(symbol, st, player) {
  const r = st.receipt;
  if (isPoolKind(r.kind)) return poolReceiptHtml(symbol, st, player);
  if (r.kind === 'transfer') return transferReceiptHtml(symbol, st, player);
  // (stuck states): a swap whose value moves outside the game (an SPS withdrawal, or an SPS sale's pool swap)
  // is only "done" once the pool has settled it; until then it says so, and never shows a quote as a received amount.
  if (st.stage === 'settling') return settlingHtml(symbol, st, player);
  const done = st.stage === 'done';
  const sending = st.stage === 'funding-signing' || (st.stage === 'funding' && st.busy);
  const retry = st.stage === 'funding' && !st.busy;
  const verb = r.kind === 'swap_buy' ? (done ? 'Bought' : 'Buying') : (done ? 'Sold' : 'Selling');
  const status = st.checking ? 'Confirming with the game…' : sending ? 'Sending SPS to the pool…' : '';
  const retryBtn = retry ? `<span class="wallet-sign-pair">${walletControlsHtml(player)}${btn('fund', 'Send SPS to the pool', 'primary', 'touch', !walletReady(player))}</span>` : '';
  return `<section class="wallet-card wallet-swap dec-review dec-receipt${done ? ' is-confirmed' : ' is-waiting'}" data-lp-symbol="${esc(symbol)}" data-lp-stage="${st.stage}" aria-live="polite">
    <header><h3>${done ? 'Swap confirmed' : st.failed === 'confirm' ? 'Still pending' : 'Swapping'}</h3>${r.region ? plaque(r.region) : ''}</header>
    <div class="dec-receipt-amount">${art(r.pay)}<div><span>${verb}</span><strong>${fixed(r.input)} <small>${esc(label(r.pay))}</small> → ${r.output == null ? '—' : fixed(r.output)} <small>${esc(label(r.receive))}</small></strong></div>${art(r.receive)}</div>
    ${done && r.output == null ? '<p class="wallet-swap-note">The game did not report the amount received; your wallet balance shows it.</p>' : ''}
    ${st.stage === 'funding-signing' ? walletWaitHtml(player, 'Active') + `<div class="wallet-fund-controls">${walletControlsHtml(player, null, { compactRequest: true })}</div>` : status ? `<p class="dec-status" role="status"><span class="spinner" aria-hidden="true"></span>${status}</p>` : ''}
    ${st.stage === 'funding' && !st.busy ? `<p class="wallet-swap-note is-warn" role="status">${esc(st.fundError || `The game accepted the swap and is waiting for your ${label(r.kind === 'add' ? r.symbol : r.pay)}. Send it to the pool to finish (Active key).`)}</p>` : ''}
    ${waitingHtml(st)}
    ${done && r.bridge_tx ? `<p class="wallet-swap-note">Settled by the pool · <a href="https://hivehub.dev/tx/${encodeURIComponent(r.bridge_tx)}" target="_blank" rel="noopener noreferrer">settlement ↗</a></p>` : ''}
    <div class="dec-receipt-actions">${r.tx_id ? `<a href="https://hivehub.dev/tx/${encodeURIComponent(r.tx_id)}" target="_blank" rel="noopener noreferrer">View transaction ↗</a>` : ''}${r.funding_tx ? `<a href="https://hivehub.dev/tx/${encodeURIComponent(r.funding_tx)}" target="_blank" rel="noopener noreferrer">SPS transfer ↗</a>` : ''}${done ? btn('done', 'Done', 'primary', 'touch') : retryBtn || confirmActions(st)}</div></section>`;
}

function settlingHtml(symbol, st, player) {
  const r = st.receipt;
  const sell = r.kind !== 'swap_buy';
  const note = st.settleNote ? `<p class="wallet-swap-note" role="status">${esc(st.settleNote)}</p>` : '';
  const failed = st.failed === 'settle';
  return `<section class="wallet-card wallet-swap dec-review dec-receipt ${failed ? 'is-failed' : 'is-waiting'}" data-lp-symbol="${esc(symbol)}" data-lp-stage="settling" aria-live="polite">
    <header><h3>${failed ? 'Not settled within 5 minutes' : 'Waiting for the pool to settle'}</h3>${r.region ? plaque(r.region) : ''}</header>
    <div class="dec-receipt-amount">${art(r.pay)}<div><span>${sell ? 'Selling' : 'Buying'}</span><strong>${fixed(r.input)} <small>${esc(label(r.pay))}</small> → — <small>${esc(label(r.receive))}</small></strong></div>${art(r.receive)}</div>
    <p>The game accepted the swap${sell && r.funding_tx ? ' and your SPS went to the pool' : ''}. Amount confirmed when the pool settles; nothing is signed again.</p>
    ${settleStatusHtml(st)}
    ${note}
    ${r.fundingUnknown && !r.funding_tx ? btn('funding-not-sent', 'I checked my wallet: the SPS transfer was not sent', 'secondary', 'compact') : ''}
    <div class="dec-receipt-actions">${r.tx_id ? `<a href="https://hivehub.dev/tx/${encodeURIComponent(r.tx_id)}" target="_blank" rel="noopener noreferrer">View transaction ↗</a>` : ''}${r.funding_tx ? `<a href="https://hivehub.dev/tx/${encodeURIComponent(r.funding_tx)}" target="_blank" rel="noopener noreferrer">SPS transfer ↗</a>` : ''}${settleActions(st)}${st.settleCursor != null ? btn('settle-older', 'Look further back', 'secondary', 'touch', !!st.busy) : ''}${btn('done', 'Close', 'secondary', 'touch', !!st.busy)}</div></section>`;
}
// what the settlement loop is doing, and the buttons that fit it.
function settleStatusHtml(st) {
  if (st.failed === 'settle') return `<p class="wallet-swap-note is-danger" role="alert">The pool has not paid this swap within 5 minutes. Ceres stopped checking; nothing was signed again. Check again reads the pool once more, or look in the game.</p>`;
  if (st.settleStopped) return `<p class="wallet-swap-note is-warn" role="status">Checking is stopped. The swap stays open until the pool has paid it.</p>`;
  return `<p class="wallet-swap-note is-warn" role="status"><span class="spinner" aria-hidden="true"></span> Ceres reads the pool by itself until it has paid this swap.</p>`;
}
function settleActions(st) {
  if (st.failed === 'settle' || st.settleStopped) return btn('settle', st.busy ? 'Checking…' : st.failed ? 'Check again' : 'Resume checking', 'primary', 'touch', !!st.busy);
  return btn('settle-stop', 'Stop checking', 'secondary', 'touch', !!st.busy);
}

// Pure over (symbol, wallet, player) plus this module's page-memory state.
export function swapHtml(symbol, wallet, player) {
  // (; "100% wallet functions from desktop must work on mobile"): same swap on every tier.
  if (!player) return '';
  const st0 = states.get(`${player}:${symbol}`);
  // a pool add/withdraw shows its progress and result in the pool box itself; the swap card stays where it is, so nothing moves.
  if (st0?.receipt && !isPoolKind(st0.receipt.kind) && ['confirming', 'funding', 'funding-signing', 'settling', 'done', 'refused'].includes(st0.stage)) return receiptHtml(symbol, st0, player);
  const t = transferContext(symbol, wallet, player);  // Grain, Wood, Stone and Iron get a Transfer tab beside Swap
  if (t && t.st.tab === 'transfer') return `<section class="wallet-card wallet-swap wallet-transfer" data-lp-symbol="${esc(symbol)}" data-lp-tab="transfer" data-lp-stage="${t.st.stage === 'signing' ? 'signing' : t.x.review ? 'review' : 'edit'}">${t.st.storageBlocked ? '<p class="wallet-swap-note" role="status">This browser would not save the transfer note, so the wallet was not opened. Allow site storage for Ceres and try again.</p>' : ''}${t.st.lpNote ? `<p class="wallet-swap-note${t.st.lpNoteTone === 'danger' ? ' is-danger' : t.st.lpNoteTone === 'warn' ? ' is-warn' : ''}" role="${t.st.lpNoteTone === 'danger' ? 'alert' : 'status'}">${esc(t.st.lpNote)}</p>` : ''}${tabsHtml(t.st, 'transfer')}${transferHtml(symbol, t, player)}</section>`;
  const c = context(symbol, wallet, player);
  if (!c) return '';
  const st = c.st;
  return `<section class="wallet-card wallet-swap" data-lp-symbol="${esc(symbol)}" data-lp-direction="${c.buy ? 'buy' : 'sell'}" data-lp-stage="${st.stage === 'signing' ? 'signing' : 'edit'}">${t ? tabsHtml(st, 'swap') : ''}${st.storageBlocked ? '<p class="wallet-swap-note" role="status">This browser would not save the swap note, so the wallet was not opened. Allow site storage for Ceres and try again.</p>' : ''}${st.lpNote ? `<p class="wallet-swap-note${st.lpNoteTone === 'danger' ? ' is-danger' : st.lpNoteTone === 'warn' ? ' is-warn' : ''}" role="${st.lpNoteTone === 'danger' ? 'alert' : 'status'}">${esc(st.lpNote)}</p>` : ''}${editHtml(symbol, c, player)}</section>`;
}

// The shared dropdown for "Change region": the trigger reads "Change region", the list names region + balance.
export function enhanceSwap(root) {
  root?.querySelectorAll?.('select[data-lp-region], select[data-lp-pool-region], select[data-lp-transfer-from], select[data-lp-transfer-region]').forEach(select => initCustomSelect(select, {
    ariaLabel: 'Change region', decorateTrigger: () => ({ label: select.hasAttribute('data-lp-transfer-region') ? 'Change their region' : 'Change region' }),
    decorateOption: value => ({ icon: plaque(value) }),
  }));
}

async function post(path, player, body) {
  const base = path.startsWith('/') ? path : `/api/wallet/lp/${path}`;  // the transfer routes live under /api/wallet/transfer
  const response = await fetch(`${base}?player=${encodeURIComponent(player)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  let result = null;
  try { result = await response.json(); } catch (_) { result = null; }
  if (!response.ok || !result) { const failure = new Error('wallet-lp'); failure.status = response.status; throw failure; }
  return result;
}

// The server returns included:false only after its 3,900-second known-id absence grace.
async function expiredAndAbsent(player, txId, askedAt) {
  if (!txId || !askedAt) return false;
  try {
    const result = await post('/api/op-inclusion', player, { tx_id: txId, created_at: askedAt });
    return result?.included === false;
  } catch (_) { return false; }
}

// Re-render only this card, keeping the caret where the user is typing.
function patch(drawer, symbol, wallet, player) {
  const card = drawer?.querySelector(`.wallet-swap[data-lp-symbol="${symbol}"]`);
  if (!card) return;
  const typed = ['data-lp-amount', 'data-lp-receive', 'data-lp-transfer-to', 'data-lp-transfer-amount'];  // the Transfer fields too
  const input = [...card.querySelectorAll(typed.map(attr => `[${attr}]`).join(', '))].find(el => el === document.activeElement);
  const focused = !!input;
  const which = `[${typed.find(attr => input?.hasAttribute(attr)) || 'data-lp-amount'}]`;
  const caret = focused ? [input.selectionStart, input.selectionEnd] : null;
  const holder = document.createElement('div');
  holder.innerHTML = swapHtml(symbol, wallet, player, true);
  const next = holder.firstElementChild;
  if (!next) { card.remove(); return; }
  card.replaceWith(next);
  enhanceSwap(next);
  const again = next.querySelector(which);
  if (focused && again) { again.focus({ preventScroll: true }); try { again.setSelectionRange(...caret); } catch (_) { /* non-text input */ } }
}

function scheduleQuote(ctx, symbol) {
  const st = swapState(ctx.player, symbol);
  clearTimeout(st.timer);
  st.generation += 1;
  if (parseAmount(st.mode === 'receive' ? st.target : st.amount) === null) { st.quote = null; st.quoted = ''; return; }
  st.timer = setTimeout(() => runQuote(ctx, symbol), QUOTE_IDLE_MS);
}
async function runQuote(ctx, symbol) {
  const st = swapState(ctx.player, symbol);
  if (st.inFlight) { st.queued = true; return; }
  const amount = st.amount, target = st.target, backwards = st.mode === 'receive', generation = st.generation;
  if (parseAmount(backwards ? target : amount) === null) return;
  st.inFlight = true;
  try {
    const kind = st.direction === 'buy' ? 'swap_buy' : 'swap_sell';
    // "You receive" typed -> the server works out "You pay", never above the balance the card shows.
    const available = context(symbol, ctx.wallet(), ctx.player)?.available;
    const body = backwards
      ? { kind, resource_symbol: symbol, target_output: parseAmount(target), max_input: String(Math.floor(Number(available) * 1000) / 1000) }
      : { kind, resource_symbol: symbol, amount: parseAmount(amount) };
    const q = await post('quote', ctx.player, body);
    const current = generation === st.generation && q.resource_symbol === symbol && q.kind === kind
      && (backwards ? st.mode === 'receive' && st.target === target : st.amount === amount);
    if (current && backwards) {
      st.amount = plain(q.input); st.quote = q; st.quoted = st.amount;
      if (q.capped) Object.assign(st, { mode: 'pay', target: '' });  // more than you hold: the card shows 100 % instead
    } else if (current) { st.quote = q; st.quoted = amount; }
  } catch (_) {
    if (generation === st.generation) { st.quote = null; st.quoted = ''; }  // no figure, no text ()
  } finally {
    st.inFlight = false;
    if (st.queued) { st.queued = false; if (generation !== st.generation) runQuote(ctx, symbol); }
    if (st.stage === 'edit') patch(ctx.drawer(), symbol, ctx.wallet(), ctx.player);
  }
}

// Typing: keep the typed text (capped to the live balance), and ask for a quote once it settles.
export function swapInput(ctx, input) {
  const symbol = input.dataset.lpAmount;
  const c = context(symbol, ctx.wallet(), ctx.player);
  if (!c || c.st.stage !== 'edit') return;
  const capped = capAmount(input.value, c.available);
  if (capped !== input.value) input.value = capped;
  Object.assign(c.st, { amount: capped, mode: 'pay', target: '' });
  scheduleQuote(ctx, symbol);
  patch(ctx.drawer(), symbol, ctx.wallet(), ctx.player);
}
// typing in "You receive" asks for the amount to pay; "You pay" fills in when the quote arrives.
export function swapReceive(ctx, input) {
  const symbol = input.dataset.lpReceive;
  const c = context(symbol, ctx.wallet(), ctx.player);
  if (!c || c.st.stage !== 'edit') return;
  const typed = capAmount(input.value, Number.MAX_SAFE_INTEGER);
  if (typed !== input.value) input.value = typed;
  Object.assign(c.st, { target: typed, mode: 'receive', amount: '', quote: null, quoted: '' });
  scheduleQuote(ctx, symbol);
  patch(ctx.drawer(), symbol, ctx.wallet(), ctx.player);
}
export function swapRegion(ctx, select) {
  const symbol = select.closest('[data-lp-symbol]')?.dataset.lpSymbol;
  const c = symbol && context(symbol, ctx.wallet(), ctx.player);
  if (!c || c.st.stage !== 'edit') return;
  c.st.region = select.value;
  const next = context(symbol, ctx.wallet(), ctx.player);
  if (parseAmount(c.st.amount) !== null && Number(parseAmount(c.st.amount)) > next.available) c.st.amount = capAmount(c.st.amount, next.available);
  scheduleQuote(ctx, symbol);
  patch(ctx.drawer(), symbol, ctx.wallet(), ctx.player);
}

// The preview body for either direction: sell spends `resource_amount` from the region, buy spends `dec_amount`
// and lands the resource in the region (the maintainer's proven shapes, tx <tx id> and <tx id>).
export function previewBody(symbol, direction, region, amount) {
  return direction === 'buy'
    ? { kind: 'swap_buy', resource_symbol: symbol, region_uid: region, dec_amount: amount }
    : { kind: 'swap_sell', resource_symbol: symbol, region_uid: region, resource_amount: amount };
}
async function verify(ctx, symbol, st, resumed = false) {
  if (st.checking || !st.receipt) return;
  const nonce = st.receipt.nonce;
  const current = () => st.stage === 'confirming' && st.receipt?.nonce === nonce && !st.stopped;
  if (!st.receipt.signed_at) st.receipt.signed_at = new Date().toISOString();
  // Resume gives the game a fresh 3-minute window from the press; signed_at (the 5-minute never-sent clock) is kept as it was.
  if (resumed) st.receipt.checkFrom = new Date().toISOString();
  const confirmStart = () => Math.max(Date.parse(st.receipt.signed_at) || 0, Date.parse(st.receipt.checkFrom || '') || 0);
  st.checking = true; st.stopped = false; st.failed = null; ctx.render();
  const pause = ms => new Promise(resolve => { const timer = setTimeout(resolve, ms); st.wake = () => { clearTimeout(timer); resolve(); }; });
  // no transaction id from the wallet -> the account's own recent history finds it by the intent nonce.
  // 'found' | 'not_found' (the read SUCCEEDED and the nonce is not there) | 'unknown' (the read failed: proves nothing).
  const lookup = async () => {
    if (!nonce) return 'not_found';  // the nonce is stored before the wallet is asked: without one the wallet never saw this swap
    let found;
    try { found = await post('find', ctx.player, findBody(st.receipt)); }
    catch (_) { if (current() && !st.historyUnreadable) { st.historyUnreadable = true; ctx.render(); } return 'unknown'; }  // read again on the next round
    // a not_found whose window does not reach back to the request is 'unknown', never evidence of "not sent"
    if (found?.status !== 'found' && !(found?.status === 'not_found' && found.covered === true)) { if (current() && !st.historyUnreadable) { st.historyUnreadable = true; ctx.render(); } return 'unknown'; }
    if (st.historyUnreadable) { st.historyUnreadable = false; ctx.render(); }
    if (found.status === 'found' && current()) {
      st.receipt = { ...st.receipt, tx_id: found.tx_id, unknownOutcome: false };
      syncStoredIntent(ctx.player, symbol, st); ctx.render();
      return 'found';
    }
    return found.status === 'found' ? 'found' : 'not_found';
  };
  try {
    for (let i = 0; ; i++) {
      if (i >= MAX_ROUNDS) { st.stopped = true; return; }
      // every run (a reload, a Resume) reads the game at least once before the 3-minute limit can end it
      if (i > 0 && st.receipt.tx_id && Date.now() - confirmStart() > CONFIRM_TTL_MS) { st.failed = 'confirm'; return; }
      const wait = i < VERIFY_SCHEDULE_MS.length ? VERIFY_SCHEDULE_MS[i] - (VERIFY_SCHEDULE_MS[i - 1] || 0) : RECHECK_MS;
      if (wait) await pause(wait);
      if (!current()) return;
      // A no-ID intent is released only after the Hive expiry grace and a successful covering history miss.
      if (!st.receipt.tx_id) {
        const seen = await lookup();
        if (seen === 'not_found' && Date.now() - Date.parse(st.receipt.asked_at || st.receipt.signed_at || '') >= NEVER_SENT_AFTER_MS && current()) {
          Object.assign(st, { stage: 'edit', receipt: null, quote: null, quoted: '', lpNoteTone: 'warn',
            lpNote: 'This wallet request expired without being sent. Nothing was spent.' });
          syncStoredIntent(ctx.player, symbol, st); await ctx.reload(); return;
        }
        if (seen !== 'found') continue;
      }
      if (!current()) return;
      const intent = { ...st.receipt };
      let result;
      try { result = await post('verify', ctx.player, { tx_id: intent.tx_id, nonce: intent.nonce, kind: intent.kind, expected_payload: intent.payload }); }
      catch (_) { continue; }
      if (!current()) return;
      if (result.status === 'not_found') {
        if (await expiredAndAbsent(ctx.player, intent.tx_id, st.receipt.asked_at || st.receipt.signed_at) && current()) {
          Object.assign(st, { stage: 'edit', receipt: null, quote: null, quoted: '', lpNoteTone: 'warn',
            lpNote: 'This wallet transaction expired without being included. Nothing was spent.' });
          await ctx.reload(); return;
        }
        continue;
      }
      const deposit = intent.kind === 'add' && !!result.game_result?.pending_deposit_id;  // an SPS deposit is paid by a second transfer
      if (result.status === 'confirmed' && (intent.twoStep || deposit)) {
        balancesChanged({ player: ctx.player, assets: [intent.symbol, intent.pay, intent.receive].filter(Boolean), regions: [intent.region].filter(Boolean), id: `wallet-game:${intent.tx_id}` });  // SPS sale: the Active transfer follows by itself
        st.stage = 'funding'; st.checking = false;
        await fund(ctx, st);
        return;
      }
      if (result.status === 'confirmed' && isPoolKind(intent.kind)) {
        balancesChanged({ player: ctx.player, assets: [intent.symbol, 'DEC'].filter(Boolean), regions: [intent.region].filter(Boolean), id: `wallet-game:${intent.tx_id}` });
        const effects = result.game_result || {};
        const got = key => { const value = Number(effects[key]); return Number.isFinite(value) && value > 0 ? value : null; };
        // the SPS part comes through the pool bridge; : the DEC the game paid is kept as reported evidence
        if (effects.external_withdrawal_required === true) { st.receipt = { ...st.receipt, gameDec: got('dec_amount') }; st.stage = 'settling'; return; }
        st.receipt = { ...st.receipt, resource: got('resource_amount'), dec: got('dec_amount'), reported: true };  // the game's figures, never the estimate
        st.stage = 'done';
        return;
      }
      if (result.status === 'confirmed') {
        balancesChanged({ player: ctx.player, assets: [intent.symbol, intent.pay, intent.receive].filter(Boolean), regions: [intent.region, intent.destination_region].filter(Boolean), id: `wallet-game:${intent.tx_id}` });
        const effects = result.game_result || {};
        // an external withdrawal / swap is paid outside the game -- wait for the pool, show no amount yet.
        if (effects.external_withdrawal_required === true || effects.external_swap_required === true) {
          st.receipt = { ...st.receipt, output: null }; st.stage = 'settling';
          return;
        }
        const paid = Number(intent.kind === 'swap_buy' || intent.kind === 'transfer' ? effects.resource_amount : effects.dec_amount);  // what the recipient got
        st.receipt = { ...st.receipt, output: Number.isFinite(paid) && paid > 0 ? paid : null };  // never the quote
        st.stage = 'done';
        return;
      }
      if (result.status === 'failed' && intent.kind === 'transfer') {  // a refused transfer is red and gives the game's reason verbatim
        Object.assign(st, { stage: 'refused', refusal: String(result.message || '') });
        return;
      }
      if (result.status === 'failed') {  // game refusal: back to the card with live balances, silently
        Object.assign(st, { stage: 'edit', receipt: null, quote: null, quoted: '' });
        await ctx.reload();
        return;
      }
    }
  } finally { st.checking = false; st.wake = null; syncStoredIntent(ctx.player, symbol, st); ctx.render(); if (st.stage === 'settling') settleLoop(ctx, st); }  // the confirmed event refreshes wallet balances
}

// the settlement is read by itself (the pool's public history, by the swap's own id), on a schedule, until the pool has
// paid or an hour has passed; then the card is red. A press of Check again still reads at once.
async function settleLoop(ctx, st) {
  if (st.settleLooping || st.stage !== 'settling' || !st.receipt) return;
  const intent = st.receipt;
  const startedAt = Date.parse(intent.funding_at || intent.signed_at || '') || Date.now();
  st.settleLooping = true; st.failed = null; st.settleStopped = false;
  const pause = ms => new Promise(resolve => { const timer = setTimeout(resolve, ms); st.settleWake = () => { clearTimeout(timer); resolve(); }; });
  try {
    for (let i = 0; st.stage === 'settling' && st.receipt === intent && !st.settleStopped; i++) {
      if (i >= MAX_ROUNDS) { st.settleStopped = true; return; }
      if (Date.now() - startedAt > SETTLE_TTL_MS) { st.failed = 'settle'; return; }
      if (!st.busy) await settleCheck(ctx, st, false);
      if (st.stage !== 'settling' || st.receipt !== intent || st.settleStopped) return;
      await pause(SETTLE_SCHEDULE_MS[Math.min(i, SETTLE_SCHEDULE_MS.length - 1)]);
    }
  } finally { st.settleLooping = false; st.settleWake = null; ctx.render(); }
}

// one settlement read. Settled -> done with the amount the pool paid; otherwise it stays waiting.
async function settleCheck(ctx, st, older) {
  const intent = st.receipt;
  st.busy = true; st.settleNote = ''; ctx.render();
  try {
    const result = await post('settle', ctx.player, { tx_id: intent.tx_id, nonce: intent.nonce, kind: intent.kind, expected_payload: intent.payload,
      funding_tx: intent.funding_tx || null, cursor: older ? st.settleCursor : null });
    if (st.receipt !== intent) return;
    // (audit #4): the SPS transfer whose wallet answer was lost was found in the account's own history -- attach it to the intent.
    if (typeof result.funding_tx === 'string' && /^[a-f0-9]{40}$/i.test(result.funding_tx) && !intent.funding_tx) {
      const { fundingUnknown, ...rest } = intent;
      st.receipt = { ...rest, funding_tx: result.funding_tx };
      return await settleCheckResult(ctx, st, st.receipt, result, older);
    }
    return await settleCheckResult(ctx, st, intent, result, older);
  } catch (_) {
    st.settleNote = 'The pool history could not be read right now. Check again later — nothing was signed.';
  } finally { st.busy = false; syncStoredIntent(ctx.player, intentSymbol(intent), st); ctx.render(); }  // the confirmed event refreshes wallet balances
}
async function settleCheckResult(ctx, st, intent, result, older) {
  if (result.status === 'settled' && isPoolKind(intent.kind)) {
    balancesChanged({ player: ctx.player, assets: [intent.symbol, 'DEC'].filter(Boolean), regions: [intent.region].filter(Boolean), id: `wallet-settled:${intent.tx_id}` });
    // (audit #3): only figures the settlement reported; a quote is never shown as a reported amount.
    const rows = [...(result.received || []), ...(result.deposited || [])];
    const sum = token => rows.filter(row => row.symbol === token).reduce((total, row) => total + Number(row.amount || 0), 0) || null;
    const resource = sum(poolSymbol(intent)), dec = sum('DEC') ?? intent.gameDec ?? null;
    st.receipt = { ...intent, resource: resource ?? intent.resource, dec: dec ?? intent.dec, reported: resource != null && dec != null, bridge_tx: result.bridge_tx_id || null };
    Object.assign(st, { stage: 'done', settleNote: '', settleCursor: null });
    return;
  }
  if (result.status === 'settled') {
    balancesChanged({ player: ctx.player, assets: [intent.symbol, intent.receive, 'DEC', 'SPS'].filter(Boolean), regions: [intent.region].filter(Boolean), id: `wallet-settled:${intent.tx_id}` });
    const got = (result.received || []).filter(row => row.symbol === intent.receive).reduce((sum, row) => sum + Number(row.amount || 0), 0);
    st.receipt = { ...intent, output: got > 0 ? got : null, bridge_tx: result.bridge_tx_id || null };
    Object.assign(st, { stage: 'done', settleNote: '', settleCursor: null });
    return;
  }
  if (result.status === 'refunded') {  // the pool sent the SPS back -- the swap is over
    const back = (result.refunded || []).map(row => `${fixed(row.amount)} ${label(row.symbol)}`).join(' + ') || `your ${label(intent.pay || intent.symbol)}`;
    // (audit #5): "Nothing was lost" only when the refund paid the full pending amount.
    const full = result.full === true;
    const shortBy = !full && result.expected != null ? ` of the ${fixed(result.expected)} ${label(intent.pay || intent.symbol)} you sent` : '';
    Object.assign(st, { stage: 'edit', receipt: null, funding: null, quote: null, quoted: '', settleNote: '', settleCursor: null, pool: blankPool(),
      lpNote: full ? `The pool refunded ${back}: it did not execute this swap, most likely because the price moved past your slippage since the quote. Nothing was lost; swap again at the current price.`
        : `The pool refunded ${back}${shortBy}: it did not execute this swap. Check your wallet balance before swapping again.`, lpNoteTone: 'danger' });
    await ctx.reload(); return;
  }
  st.settleCursor = Number.isInteger(result.next_cursor) ? result.next_cursor : null;
  st.settleNote = result.funding === 'not_found' ? 'Your SPS transfer to the pool is not in your account history yet. Ceres keeps looking; nothing is signed again.'
    : result.refund_pending ? 'The pool did not execute this swap; its refund has not arrived yet. Ceres keeps checking.'
    : older ? 'Not found further back either.' : 'Not settled yet — the pool has not paid this swap in its latest history.';
}

// the SPS sale's second half, requested right after the swap confirms, from the same wallet, no click.
// The server reads the game's answer (pending_swap_id, amount, pool account) and builds the Active `sm_sps`
// transfer. Declined, not sent, or not yet readable: the card keeps one "Send SPS to the pool" button (page memory
// only, ); nothing is shown as an error.
async function fund(ctx, st) {
  const intent = st.receipt;
  st.busy = true; st.fundError = ''; ctx.render();
  try {
    if (!st.funding?.operations) {
      const response = await post('sps-funding', ctx.player, { tx_id: intent.tx_id, nonce: intent.nonce, kind: intent.kind, expected_payload: intent.payload });
      if (response.status === 'failed') {  // the game refused the swap itself: back to the card, silently
        Object.assign(st, { stage: 'edit', receipt: null, funding: null, quote: null, quoted: '' });
        await ctx.reload();
        return;
      }
      if (response.status !== 'ready' || !Array.isArray(response.operations)) return;
      st.funding = response;
    }
    if (!walletReady(ctx.player)) return;
    if (ctx.account() !== ctx.player) { st.stage = 'funding'; return; }  // S10 / L4: another account is selected now; no prompt for the old one (the button waits)
    // S10 / A-04, A-02: one funding prompt per sale across tabs -- reserve it under the lock, or no prompt.
    const symbol = intentSymbol(intent);
    const fundingTaken = await withIntentLock(ctx.player, symbol, () => {
      const open = readStored(ctx.player, symbol);
      // S10 / A-01: funding needs THIS tab's own live record. A missing or foreign record (another tab settled or replaced the sale while this check
      // was waiting) means this tab's result is stale: it never authorises a transfer.
      const mine = intentIsOpen(open) && (open.token || null) === (st.token || null) && open.receipt?.nonce === intent.nonce;
      if (!mine || open.stage === 'funding-signing' || open.stage === 'settling' || open.receipt?.funding_tx || open.receipt?.fundingUnknown) {
        if (intentIsOpen(open)) restoreIntent(ctx.player, symbol, st);
        else Object.assign(st, { stage: 'edit', receipt: null, quote: null, quoted: '', token: null });
        st.funding = null; return 'taken';
      }
      st.stage = 'funding-signing';
      if (!syncStoredIntent(ctx.player, symbol, st)) { st.stage = 'funding'; return 'unwritable'; }
      return 'ok';
    });
    if (fundingTaken !== 'ok') return;
    ctx.setSigning(true); ctx.render();  // S10 / L2-01: on disk BEFORE the prompt
    let tx = null;
    let ambiguous = false;
    let answered = false;  // (audit #4): set once this handoff's outcome is recorded; a later wallet answer goes to the durable intent
    const lateAnswer = id => {
      if (!answered || st.receipt?.nonce !== intent.nonce || st.receipt.funding_tx || !['settling', 'funding'].includes(st.stage) || (st.stage === 'funding' && st.busy)) return;
      const { fundingUnknown, ...rest } = st.receipt;
      st.receipt = { ...rest, funding_tx: id, funding_at: new Date().toISOString(), output: null };
      Object.assign(st, { stage: 'settling', funding: null, settleNote: '', fundError: '' });
      syncStoredIntent(ctx.player, intentSymbol(intent), st); ctx.render(); settleLoop(ctx, st);
    };
    let fundFailure = '';  // a refused or impossible transfer says why instead of leaving a silent button // S10 round 2 / L4-02: an SPS transfer whose outcome is unknown must not be offered again
    try {
      const result = await requestBroadcastThroughGate(ctx.player, st.funding.operations, response => {
        const id = response?.result?.id || response?.result?.tx_id || response?.tx_id;
        if (typeof id === 'string' && /^[a-f0-9]{40}$/i.test(id)) { tx = id; lateAnswer(id); }
      }, 'Active', { direct: true });  // one Keychain "sign and send" prompt, like the game
      tx = result.txId || tx;
    } catch (failure) {
      if (typeof failure?.txId === 'string' && /^[a-f0-9]{40}$/i.test(failure.txId) && failure?.broadcasted !== false) tx = failure.txId;
      ambiguous = failure?.broadcasted !== false;
      fundFailure = failure?.declined ? 'The transfer was declined in the wallet. Send it when you are ready.' : 'The wallet did not send the transfer. Send it again when you are ready.';
    } finally { ctx.setSigning(false); answered = true; }
    if (tx) { st.receipt = { ...st.receipt, funding_tx: tx, funding_at: new Date().toISOString(), output: null }; st.stage = 'settling'; st.funding = null; }
    else if (ambiguous) { st.receipt = { ...st.receipt, fundingUnknown: true, output: null }; st.stage = 'settling'; st.funding = null; st.settleNote = 'Your wallet did not say whether the SPS transfer was sent. Check the pool settlement before sending anything again.'; }
    else { st.stage = 'funding'; st.fundError = fundFailure; }
  } catch (error) { st.stage = 'funding'; st.fundError = 'The pool transfer could not be prepared. Try again in a moment.'; /* keep the retry button */ }
  finally { st.busy = false; syncStoredIntent(ctx.player, intentSymbol(intent), st); ctx.render(); if (st.stage === 'settling') settleLoop(ctx, st); }
}

// One click: a fresh build of exactly what the card shows, then the wallet. `name` is the part after "lp:" in
// data-dec. ctx: { player, drawer(), wallet(), render(), reload(), setSigning(bool), account() }.
export async function swapAction(ctx, name, target) {
  const symbol = target?.closest('[data-lp-symbol]')?.dataset.lpSymbol;
  if (!symbol) return;
  const st = swapState(ctx.player, symbol);
  if (st.busy) return;
  // The receipt does not depend on the card's context (the balances it spent may be gone).
  if (name === 'done' && st.stage === 'settling' && st.receipt?.kind === 'remove') {  // a withdrawal needs nothing more from you; the bridge pays the SPS
    Object.assign(st, { stage: 'edit', receipt: null, funding: null, settleNote: '', settleCursor: null, pool: blankPool() });
    syncStoredIntent(ctx.player, symbol, st);
    await ctx.reload(); return;
  }
  if (name === 'done' && st.stage === 'settling') {  // S10 round 2 / L4-03: an unsettled swap is never discarded by a Close
    st.settleNote = 'This swap is still waiting for the pool. It stays here until it has settled.'; ctx.render(); return;
  }
  if (name === 'done' && (st.stage === 'refused' || (st.stage === 'done' && st.receipt?.kind === 'transfer'))) {  // back to the Transfer tab
    Object.assign(st, { stage: 'edit', receipt: null, refusal: '', tab: 'transfer', xfer: { ...blankTransfer(), to: st.xfer.to, recipient: st.xfer.recipient, toRegion: st.xfer.toRegion, fromRegion: st.xfer.fromRegion } });
    syncStoredIntent(ctx.player, symbol, st);
    await ctx.reload(); return;
  }
  if (name === 'tab:swap' || name === 'tab:transfer') {  // Swap | Transfer; never while a wallet prompt is open
    if (st.stage === 'edit' && !st.receipt) { st.tab = name.slice(4); ctx.render(); }
    return;
  }
  if (name.startsWith('transfer-')) { await transferAction(ctx, name.slice(9), symbol, st); return; }
  if (name === 'done' && st.stage === 'done') {
    const pool = isPoolKind(st.receipt?.kind);
    Object.assign(st, { stage: 'edit', receipt: null, funding: null, quote: null, quoted: '', amount: '', target: '', mode: 'pay', settleNote: '', settleCursor: null, pool: blankPool() });
    syncStoredIntent(ctx.player, symbol, st);
    await ctx.reload();
    if (pool) ctx.render('pool');  // Done after a pool add/withdraw leaves the refreshed pool box in view
    return;
  }
  // the pool settlement is read only when the maintainer presses Check again (it reads the pool's public history).
  if (name === 'settle-stop' && st.stage === 'settling') { st.settleStopped = true; st.settleWake?.(); ctx.render(); return; }
  if ((name === 'settle' || name === 'settle-older') && st.stage === 'settling') { if (name === 'settle') { st.failed = null; st.settleStopped = false; } await settleCheck(ctx, st, name === 'settle-older'); if (name === 'settle' && !st.settleLooping && st.stage === 'settling') settleLoop(ctx, st); return; }
  if (name === 'stop' && st.stage === 'confirming') { st.stopped = true; st.wake?.(); ctx.render(); return; }  // the maintainer can stop the automatic check
  if (name === 'funding-not-sent' && st.stage === 'settling' && st.receipt?.fundingUnknown && !st.receipt?.funding_tx) {
    const sure = typeof confirm === 'function' ? confirm('Only continue if your wallet history shows the SPS transfer to the pool was NOT sent. Send it now?') : true;
    if (!sure) return;
    const { fundingUnknown, ...rest } = st.receipt;
    Object.assign(st, { stage: 'funding', receipt: rest, funding: null, settleNote: '' });
    syncStoredIntent(ctx.player, symbol, st); ctx.render(); return;
  }
  if (name === 'verify' && st.stage === 'confirming') { st.stopped = false; st.failed = null; await verify(ctx, symbol, st, true); return; }  // Resume checking (: reads at once)
  if (name === 'fund' && st.stage === 'funding') { await fund(ctx, st); return; }
  if (name.startsWith('pool-')) { await poolAction(ctx, name.slice(5), symbol, st); return; }
  const c = context(symbol, ctx.wallet(), ctx.player);
  if (!c) return;
  // 25 / 50 / 75 / 100 % of what you can pay (100 % was "Max"), as in the game's pool.
  const pct = /^pct:(25|50|75|100)$/.exec(name);
  if ((pct || name === 'max') && st.stage === 'edit') {
    const share = pct ? Number(pct[1]) / 100 : 1;
    st.amount = capAmount(String(Math.floor(Number(c.available) * share * 1000) / 1000), c.available);
    Object.assign(st, { mode: 'pay', target: '' });
    scheduleQuote(ctx, symbol); clearTimeout(st.timer); runQuote(ctx, symbol);
    patch(ctx.drawer(), symbol, ctx.wallet(), ctx.player); return;
  }
  if (name === 'flip' && st.stage === 'edit' && c.canFlip) {  // ⇅: You pay and You receive trade places
    clearTimeout(st.timer);
    Object.assign(st, { direction: c.buy ? 'sell' : 'buy', amount: '', target: '', mode: 'pay', quote: null, quoted: '', generation: st.generation + 1 });
    ctx.render(); return;
  }
  if (name === 'details') { st.moreOpen = !st.moreOpen; ctx.render(); return; }
  if (name !== 'swap' || st.stage !== 'edit') return;
  const q = st.quote && st.quote.kind === c.kind && st.quoted === st.amount ? st.quote : null;
  if (!q || !c.swap.signing_enabled || !walletReady(ctx.player)) return;
  await signIntent(ctx, symbol, st, { kind: c.kind, pay: c.pay, receive: c.receive, region: st.region || null },
    { ...previewBody(symbol, st.direction, st.region, q.input), sign: true, seen_output: q.output },
    fresh => ({ twoStep: !!(fresh.quote?.two_step ?? c.twoStep), input: Number(fresh.quote?.input ?? q.input), output: Number(fresh.quote?.output ?? q.output) }),
    () => { scheduleQuote(ctx, symbol); clearTimeout(st.timer); runQuote(ctx, symbol); });
}

// One wallet handoff for a swap or a pool add/withdraw (): reserve the intent across tabs, one fresh server build of
// exactly what the card shows, the intent on disk BEFORE the wallet is asked, then the game's confirmation.
async function signIntent(ctx, symbol, st, base, body, fromFresh, onRefused, previewPath = 'preview') {
  // the earlier swap was sent late -> adopted, no second one. : a new swap starts only after a SUCCESSFUL read of every
  // watched nonce says not_found; a failed read refuses here and the wallet is never opened on an unknown.
  if (readWatch(ctx.player, symbol).length) {
    if (st.watchLooking) return;
    st.watchLooking = true;
    let outcome;
    try { outcome = await adoptLateBroadcast(ctx, symbol, st); } catch (_) { outcome = 'unknown'; } finally { st.watchLooking = false; }
    if (outcome !== 'clear') { if (outcome === 'unknown') noteWatchResult(st, outcome); ctx.render(); return; }
    noteWatchResult(st, outcome);
  }
  // S10 / L2-02, / A-01, A-02: another tab of this browser may already hold an open swap for this symbol. Read and reserve in one locked
  // step; if the reservation cannot be stored the wallet is NOT opened (a reload could not recover an unrecorded prompt).
  const taken = await withIntentLock(ctx.player, symbol, () => {
    const open = readStored(ctx.player, symbol);
    if (intentIsOpen(open)) { restoreIntent(ctx.player, symbol, st); return 'taken'; }
    st.token = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
    st.stage = 'signing'; st.busy = true; st.lpNote = ''; st.lpNoteTone = ''; st.historyUnreadable = false; st.stopped = false; st.failed = null; st.settleStopped = false;
    st.receipt = { ...base, reserved: true, unknownOutcome: true };
    if (!syncStoredIntent(ctx.player, symbol, st)) return 'unwritable';
    if (readStored(ctx.player, symbol)?.token !== st.token) { restoreIntent(ctx.player, symbol, st); return 'taken'; }
    return 'ok';
  });
  if (taken === 'taken') { st.busy = false; ctx.render(); return; }
  if (taken === 'unwritable') {
    Object.assign(st, { stage: 'edit', receipt: null, busy: false, token: null, storageBlocked: true });
    ctx.render(); return;
  }
  st.storageBlocked = false; ctx.setSigning(true);
  ctx.render();
  let fresh;
  try {
    fresh = await post(previewPath, ctx.player, body);
    if (ctx.account() !== ctx.player) throw new Error('wallet-lp');
  } catch (_) {
    // Moved beyond the tolerance (409) or unreadable: stay on the card; fresh figures replace the shown ones.
    st.stage = 'edit'; st.busy = false; st.receipt = null; ctx.setSigning(false); syncStoredIntent(ctx.player, symbol, st);   // the reservation is released: nothing was asked of the wallet
    onRefused();
    ctx.render(); return;
  }
  st.receipt = { ...base, ...fromFresh(fresh), nonce: fresh.nonce, payload: fresh.payload, tx_id: null, asked_at: new Date().toISOString() };
  if (!syncStoredIntent(ctx.player, symbol, st)) {  // S10 round 2 / L2-02, / A-02: the intent is on disk BEFORE the wallet is asked, or no prompt
    Object.assign(st, { stage: 'edit', receipt: null, busy: false, token: null, storageBlocked: true }); ctx.setSigning(false); syncStoredIntent(ctx.player, symbol, st); ctx.render(); return;
  }
  try {
    const result = await requestBroadcastThroughGate(ctx.player, fresh.operations, response => {
      const tx = response?.result?.id || response?.result?.tx_id || response?.tx_id;
      if (typeof tx === 'string' && /^[a-f0-9]{40}$/i.test(tx)) {
        st.receipt.tx_id = tx; syncStoredIntent(ctx.player, symbol, st); ctx.render();
      }
    }, 'Posting');
    st.receipt.tx_id = result.txId || st.receipt.tx_id;
  } catch (failure) {
    if (typeof failure?.txId === 'string' && /^[a-f0-9]{40}$/i.test(failure.txId)) st.receipt.tx_id = failure.txId;
    if (failure?.broadcasted === false) {  // wallet declined / provably nothing sent: the button is ready again
      st.receipt = null; st.stage = 'edit'; st.busy = false; ctx.setSigning(false); syncStoredIntent(ctx.player, symbol, st); ctx.render(); return;
    }
    // S3 / L2-01: no transaction id AND not provably unsent (timeout, late or empty wallet answer) is an UNKNOWN outcome. The
    // swap button must not come back: the card stays on its receipt and only "Check again" is offered, so a second swap is never one click away.
    if (!st.receipt.tx_id) st.receipt.unknownOutcome = true;
  }
  st.stage = 'confirming'; st.busy = false; ctx.setSigning(false);
  syncStoredIntent(ctx.player, symbol, st);
  verify(ctx, symbol, st);  // the click is done once the card shows the receipt; the check loop runs on by itself
}

// (maintainer: "add the users LP position to the bottom of each resource in our Ceres wallet, with add and withdrawal for all
// resources with pools"): the player's own position in the resource's DEC pool, closing every pool tab (Grain, Wood, Stone, Iron
// Voucher, SPS; never DEC, Research or Aura). Add pairs the resource with DEC at the pool's ratio; Withdraw takes a percentage of the
// position (the game's own `shares_out`, three decimals). One click each, like the swap, through the same intent/verify path.
const POOL_PCTS = [25, 50, 75, 100];
const decFor = (reserve, amount) => Math.round(Number(amount) * Number(reserve.dec) / Number(reserve.resource) * 1000) / 1000;
// DEC typed -> the resource it pairs with at the pool's ratio (three decimals, rounded down so the DEC never grows).
const resourceFor = (reserve, dec) => Math.floor(Number(dec) * Number(reserve.resource) / Number(reserve.dec) * 1000) / 1000;
function poolContext(symbol, wallet, player) {
  const pool = wallet?.pool;
  if (!pool?.symbols?.includes(symbol) || !wallet.account) return null;
  const st = swapState(player, symbol);
  const walletToken = !!pool.account_symbols?.includes(symbol);
  const reserve = pool.reserves?.[symbol];
  const position = pool.positions ? pool.positions[symbol] : null;
  const dec = Number(wallet.account.DEC);
  const p = st.pool;
  // Add spends from a region holding the resource (or the account wallet); Withdraw can land the resource in any region.
  const regions = walletToken || !wallet.regions ? [] : regionsFor(symbol, wallet, p.mode === 'add' ? 'sell' : 'buy');
  if (regions.length && !regions.some(r => r.region_uid === p.region)) p.region = regions[0].region_uid;
  if (walletToken) p.region = '';
  const region = regions.find(r => r.region_uid === p.region) || null;
  const held = walletToken ? Number(wallet.account[symbol] || 0) : Number(region?.balances?.[symbol] || 0);
  // The most you can add: what you hold, and what your DEC can pair with.
  const addMax = reserve ? Math.max(0, Math.min(held, Math.floor(dec * Number(reserve.resource) / Number(reserve.dec) * 1000) / 1000)) : 0;
  return { st, p, walletToken, reserve, position, dec, regions, region, held, addMax, external: !!pool.external?.includes(symbol),
           shares: Number(position?.shares || 0), signing: !!wallet.swap?.signing_enabled };
}
// The game's own withdrawal maths (Manage view): position share of both reserves, x 0.9 for shares still in their 30 days.
export function withdrawEstimate(reserve, shares, locked, pct) {
  const fraction = Math.round(Number(pct) * 10) / 1000;
  if (!(fraction > 0) || fraction > 1 || !(shares > 0) || !reserve) return null;
  const taken = shares * fraction;
  const unlocked = Math.max(shares - Number(locked || 0), 0);
  const factor = unlocked >= taken ? 1 : unlocked === 0 ? 0.9 : Math.max(0, 1 - (taken - unlocked) / taken * 0.1);
  const part = total => Math.floor(Math.round(taken * Number(total) / Number(reserve.shares) * 1000) / 1000 * factor * 1000) / 1000;
  return { fraction, resource: part(reserve.resource), dec: part(reserve.dec), fee: Math.round((1 - factor) * 10000) / 100 };
}
const parsePct = text => { const clean = String(text ?? '').trim(); return /^[0-9]{1,3}(?:\.[0-9])?$/.test(clean) && Number(clean) >= 0.1 && Number(clean) <= 100 ? clean : null; };

function poolRegionPicker(c, symbol) {
  if (c.walletToken || !c.region) return '';
  const choices = c.regions.length > 1
    ? `<select data-lp-pool-region aria-label="Change region">${c.regions.map(r => `<option value="${esc(r.region_uid)}"${r.region_uid === c.region.region_uid ? ' selected' : ''}>${esc(regionLabel(r.region_uid))} · ${esc(fixed(r.balances[symbol]))}</option>`).join('')}</select>` : '';
  return `<span class="wallet-swap-where">${plaque(c.region.region_uid)}${choices}</span>`;
}
function poolSignButton(c, player, text, ready) {
  const busy = c.st.stage === 'signing';
  const walletOk = walletReady(player);
  const button = btn('pool-sign', busy ? `<span class="wallet-sign-label">Waiting for ${walletMethodHtml(player)}…</span>` : text, 'primary', walletOk ? 'compact' : 'touch', !ready || busy || !walletOk || !c.signing, ' data-lp-primary');
  return (walletOk ? button : `<span class="wallet-sign-pair wallet-swap-sign">${walletControlsHtml(player)}${button}</span>`) + (busy ? walletWaitHtml(player) : '');
}
function poolAddHtml(symbol, c, player) {
  const amount = parseAmount(c.p.amount);
  const dec = amount && c.reserve ? decFor(c.reserve, amount) : null;
  const decShown = c.p.decTyped ? c.p.dec : dec ? plain(dec) : '';
  const decMax = c.reserve ? decFor(c.reserve, c.addMax) : 0;
  const ready = !!amount && dec > 0 && Number(amount) <= c.addMax && dec <= c.dec;
  return `<div class="wallet-swap-box"><div class="wallet-swap-line"><span>You add</span><span class="wallet-swap-pcts">${POOL_PCTS.map(pct => btn(`pool-pct:${pct}`, `${pct}%`)).join('')}</span></div>
      <div class="wallet-swap-asset"><span class="wallet-swap-name">${art(symbol)}<strong>${esc(label(symbol))}</strong></span><input type="text" class="ceres-wide-input wallet-swap-amount" data-control-density="compact" data-lp-pool-amount="${esc(symbol)}" inputmode="decimal" autocomplete="off" spellcheck="false" placeholder="0" aria-label="You add, ${esc(label(symbol))}" value="${esc(c.p.amount)}"></div>
      <div class="wallet-swap-sub"><span>up to ${fixed(c.addMax)}${c.walletToken ? ' from your wallet' : ''}</span>${poolRegionPicker(c, symbol)}</div></div>
    <div class="wallet-swap-box"><div class="wallet-swap-line"><span>With DEC</span><span aria-hidden="true"></span></div>
      <div class="wallet-swap-asset"><span class="wallet-swap-name">${art('DEC')}<strong>DEC</strong></span><input type="text" class="ceres-wide-input wallet-swap-amount${c.p.decTyped ? '' : ' is-stale'}" data-control-density="compact" data-lp-pool-dec="${esc(symbol)}" inputmode="decimal" autocomplete="off" spellcheck="false" placeholder="0" aria-label="With DEC" value="${esc(decShown)}"></div>
      <div class="wallet-swap-sub"><span>up to ${fixed(decMax)} of ${fixed(c.dec)} in your wallet · at the pool's ratio</span></div></div>
    ${c.external ? `<p class="wallet-swap-note">Adding ${esc(label(symbol))} takes two wallet approvals: this deposit (Posting key), then sending the ${esc(label(symbol))} to the pool with your <b>Active</b> key.</p>` : ''}
    ${poolSignButton(c, player, ready ? `Add ${fixed(amount)} ${esc(label(symbol))} + ${fixed(dec)} DEC` : 'Enter an amount', ready)}`;
}
function poolRemoveHtml(symbol, c, player) {
  const pct = parsePct(c.p.pct);
  const loaded = c.p.locked != null;
  const est = pct && loaded ? withdrawEstimate(c.reserve, c.shares, c.p.locked, pct) : null;
  const fee = est && est.fee > 0 ? `<p class="wallet-swap-note" data-lp-early-fee>Includes the game's early-withdrawal fee of ${esc(String(est.fee))}% — shares added in the last 30 days pay 10% (it goes to the other shareholders).</p>` : '';
  return `<div class="wallet-swap-box"><div class="wallet-swap-line"><span>You withdraw</span><span class="wallet-swap-pcts">${POOL_PCTS.map(n => btn(`pool-pct:${n}`, `${n}%`)).join('')}</span></div>
      <div class="wallet-swap-asset"><span class="wallet-swap-name"><strong>% of your position</strong></span><input type="text" class="ceres-wide-input wallet-swap-amount" data-control-density="compact" data-lp-pool-pct="${esc(symbol)}" inputmode="decimal" autocomplete="off" spellcheck="false" placeholder="0" aria-label="Percent of your position to withdraw" value="${esc(c.p.pct)}"></div>
      <div class="wallet-swap-sub"><span>${loaded ? '0.1 to 100' : c.p.loading ? 'Reading your position…' : 'Position unavailable — try again'}</span></div></div>
    <div class="wallet-swap-box wallet-swap-receive"><div class="wallet-swap-line"><span>You receive about</span><span aria-hidden="true"></span></div>
      <div class="wallet-swap-asset"><span class="wallet-swap-name">${art(symbol)}<strong>${esc(label(symbol))}</strong></span><b class="wallet-pool-figure">${est ? fixed(est.resource) : '—'}</b></div>
      <div class="wallet-swap-asset"><span class="wallet-swap-name">${art('DEC')}<strong>DEC</strong></span><b class="wallet-pool-figure">${est ? fixed(est.dec) : '—'}</b></div>
      <div class="wallet-swap-sub"><span>${c.walletToken ? `${esc(label(symbol))} and DEC to your wallet` : `DEC to your wallet, ${esc(label(symbol))} to`}</span>${poolRegionPicker(c, symbol)}</div></div>
    ${fee}
    ${poolSignButton(c, player, est ? `Withdraw ${esc(pct)}% → ${fixed(est.resource)} ${esc(label(symbol))} + ${fixed(est.dec)} DEC` : 'Enter a percentage', !!est)}`;
}
export function positionHtml(symbol, wallet, player) {
  if (!player) return '';
  const st0 = states.get(`${player}:${symbol}`);
  const poolKind = isPoolKind(st0?.receipt?.kind);
  if (poolKind && st0.stage !== 'signing') return poolReceiptHtml(symbol, st0, player);  // the result appears where the pool box is
  if (st0?.receipt && !poolKind) return '';  // a swap is open above: one operation per resource at a time
  const c = poolContext(symbol, wallet, player);
  if (!c) return '';
  const pos = c.position;
  const figures = !wallet.pool.positions ? '<p class="wallet-unavailable">Your position is unavailable right now.</p>'
    : c.shares > 0 ? `<dl class="dec-figures"><div><dt>Your share of the pool</dt><dd>${esc(pos.share_percent)}%</dd></div><div><dt>${esc(label(symbol))}</dt><dd>${fixed(pos.resource_amount)}</dd></div><div><dt>DEC</dt><dd>${fixed(pos.dec_amount)}</dd></div><div><dt>Pool shares</dt><dd>${fixed(pos.shares)}</dd></div></dl>`
    : '<p>You have no position in this pool yet.</p>';
  const mode = c.p.mode;
  const tabs = `<div class="wallet-pool-actions">${btn('pool-add', 'Add', mode === 'add' ? 'primary' : 'secondary', 'compact', c.st.stage === 'signing', ` aria-pressed="${mode === 'add'}"`)}${btn('pool-remove', 'Withdraw', mode === 'remove' ? 'primary' : 'secondary', 'compact', c.st.stage === 'signing' || !(c.shares > 0), ` aria-pressed="${mode === 'remove'}"`)}</div>`;
  const form = mode === 'add' ? poolAddHtml(symbol, c, player) : mode === 'remove' && c.shares > 0 ? poolRemoveHtml(symbol, c, player) : '';
  return `<section class="wallet-card wallet-swap wallet-pool" data-lp-symbol="${esc(symbol)}" data-lp-pool="${mode || ''}"${poolKind ? ' data-lp-active' : ''}>
    <div class="wallet-swap-head"><span class="wallet-swap-title"><span>${art(symbol)}${esc(label(symbol))}</span>·<span>${art('DEC')}DEC</span> pool</span></div>
    ${figures}${tabs}${form}</section>`;
}

function poolReceiptHtml(symbol, st, player) {
  const r = st.receipt;
  const add = r.kind === 'add';
  const done = st.stage === 'done';
  const settling = st.stage === 'settling';
  const sending = st.stage === 'funding-signing' || (st.stage === 'funding' && st.busy);
  const retry = st.stage === 'funding' && !st.busy;
  const title = st.failed === 'settle' ? 'Not settled within 5 minutes' : st.failed === 'confirm' ? 'Still pending' : add ? (done ? 'Liquidity added' : settling ? 'Waiting for the pool to settle' : 'Adding liquidity') : (done ? 'Liquidity withdrawn' : settling ? 'Waiting for the pool to pay the SPS' : 'Withdrawing liquidity');
  const approx = r.reported ? '' : '≈ ';
  const figure = (value, token) => `${value == null ? '—' : approx + fixed(value)} <small>${esc(label(token))}</small>`;
  const amounts = `${figure(r.resource, r.symbol)} + ${figure(r.dec, 'DEC')}`;
  const status = st.checking ? 'Confirming with the game…' : sending ? `Sending ${esc(label(r.symbol))} to the pool…` : '';
  const retryBtn = retry ? `<span class="wallet-sign-pair">${walletControlsHtml(player)}${btn('fund', `Send ${esc(label(r.symbol))} to the pool`, 'primary', 'touch', !walletReady(player))}</span>` : '';
  const note = st.settleNote ? `<p class="wallet-swap-note" role="status">${esc(st.settleNote)}</p>` : '';
  const settleText = settling ? `<p>${add ? `The game accepted the deposit${r.funding_tx ? ` and your ${esc(label(r.symbol))} went to the pool` : ''}. Your shares are added when the pool settles.` : 'The game accepted the withdrawal. The DEC is in your wallet; the SPS is paid by the pool bridge.'} Nothing is signed again.</p>${settleStatusHtml(st)}${note}` : '';
  const actions = done ? btn('done', 'Done', 'primary', 'touch')
    : settling ? `${settleActions(st)}${st.settleCursor != null ? btn('settle-older', 'Look further back', 'secondary', 'touch', !!st.busy) : ''}${add ? '' : btn('done', 'Close', 'secondary', 'touch', !!st.busy)}`
    : retryBtn || confirmActions(st);
  return `<section class="wallet-card wallet-swap wallet-pool dec-review dec-receipt${done ? ' is-confirmed' : st.failed === 'settle' ? ' is-failed' : ' is-waiting'}" data-lp-symbol="${esc(symbol)}" data-lp-stage="${st.stage}" data-lp-kind="${r.kind}" data-lp-active aria-live="polite">
    <header><h3>${title}</h3>${r.region ? plaque(r.region) : ''}</header>
    <div class="dec-receipt-amount">${art(r.symbol)}<div><span>${add ? (done ? 'Added' : 'Adding') : `${done ? 'Withdrew' : 'Withdrawing'} ${esc(String(r.pct))}%`}</span><strong>${amounts}</strong></div>${art('DEC')}</div>
    ${done && (r.resource == null || r.dec == null) ? '<p class="wallet-swap-note">The game did not report every amount; your wallet balance shows it.</p>' : ''}
    ${st.stage === 'funding-signing' ? walletWaitHtml(player, 'Active') + `<div class="wallet-fund-controls">${walletControlsHtml(player, null, { compactRequest: true })}</div>` : status ? `<p class="dec-status" role="status"><span class="spinner" aria-hidden="true"></span>${status}</p>` : ''}
    ${st.stage === 'funding' && !st.busy ? `<p class="wallet-swap-note is-warn" role="status">${esc(st.fundError || `The game accepted the swap and is waiting for your ${label(r.kind === 'add' ? r.symbol : r.pay)}. Send it to the pool to finish (Active key).`)}</p>` : ''}
    ${settleText}
    ${r.fundingUnknown && settling && !r.funding_tx ? btn('funding-not-sent', `I checked my wallet: the ${esc(label(r.symbol))} transfer was not sent`, 'secondary', 'compact') : ''}
    ${waitingHtml(st)}
    ${done && r.bridge_tx ? `<p class="wallet-swap-note">Settled by the pool · <a href="https://hivehub.dev/tx/${encodeURIComponent(r.bridge_tx)}" target="_blank" rel="noopener noreferrer">settlement ↗</a></p>` : ''}
    <div class="dec-receipt-actions">${r.tx_id ? `<a href="https://hivehub.dev/tx/${encodeURIComponent(r.tx_id)}" target="_blank" rel="noopener noreferrer">View transaction ↗</a>` : ''}${r.funding_tx ? `<a href="https://hivehub.dev/tx/${encodeURIComponent(r.funding_tx)}" target="_blank" rel="noopener noreferrer">${esc(label(r.symbol))} transfer ↗</a>` : ''}${actions}</div></section>`;
}

function poolPatch(drawer, symbol, wallet, player) {
  const card = drawer?.querySelector(`.wallet-pool[data-lp-symbol="${symbol}"]`);
  if (!card) return;
  const input = [...card.querySelectorAll('[data-lp-pool-amount], [data-lp-pool-pct], [data-lp-pool-dec]')].find(el => el === document.activeElement);
  const caret = input ? [input.selectionStart, input.selectionEnd] : null;
  const holder = document.createElement('div');
  holder.innerHTML = positionHtml(symbol, wallet, player);
  const next = holder.firstElementChild;
  if (!next) { card.remove(); return; }
  card.replaceWith(next);
  enhanceSwap(next);
  const again = input && next.querySelector(['data-lp-pool-pct', 'data-lp-pool-dec', 'data-lp-pool-amount'].map(a => input.hasAttribute(a) ? `[${a}]` : '').find(Boolean));
  if (again) { again.focus({ preventScroll: true }); try { again.setSelectionRange(...caret); } catch (_) { /* non-text input */ } }
}
export function poolInput(ctx, input) {
  const symbol = input.dataset.lpPoolAmount || input.dataset.lpPoolPct || input.dataset.lpPoolDec;
  const c = poolContext(symbol, ctx.wallet(), ctx.player);
  if (!c || c.st.stage !== 'edit' || c.st.receipt) return;
  if (input.hasAttribute('data-lp-pool-pct')) {
    let clean = String(input.value).replace(/[^0-9.]/g, '');
    const dot = clean.indexOf('.');
    if (dot >= 0) clean = clean.slice(0, dot + 1) + clean.slice(dot + 1).replace(/\./g, '').slice(0, 1);
    if (clean && Number(clean) > 100) clean = '100';
    if (clean !== input.value) input.value = clean;
    c.p.pct = clean;
  } else if (input.hasAttribute('data-lp-pool-dec')) {
    // typing the DEC works out the resource.
    const capped = capAmount(input.value, c.reserve ? decFor(c.reserve, c.addMax) : 0);
    if (capped !== input.value) input.value = capped;
    const resource = parseAmount(capped) !== null && c.reserve ? resourceFor(c.reserve, capped) : 0;
    Object.assign(c.p, { dec: capped, decTyped: true, amount: resource > 0 ? plain(resource) : '' });
  } else {
    const capped = capAmount(input.value, c.addMax);
    if (capped !== input.value) input.value = capped;
    Object.assign(c.p, { amount: capped, dec: '', decTyped: false });
  }
  poolPatch(ctx.drawer(), symbol, ctx.wallet(), ctx.player);
}
export function poolRegion(ctx, select) {
  const symbol = select.closest('[data-lp-symbol]')?.dataset.lpSymbol;
  const c = symbol && poolContext(symbol, ctx.wallet(), ctx.player);
  if (!c || c.st.stage !== 'edit') return;
  c.p.region = select.value;
  const next = poolContext(symbol, ctx.wallet(), ctx.player);
  if (c.p.mode === 'add' && parseAmount(c.p.amount) !== null && Number(c.p.amount) > next.addMax) c.p.amount = capAmount(c.p.amount, next.addMax);
  ctx.render('pool');
}
async function loadLocked(ctx, symbol, p) {
  p.loading = true; p.locked = null; ctx.render('pool-top');
  try {
    const result = await post('position', ctx.player, { resource_symbol: symbol });
    p.locked = Number(result.locked_shares);
  } catch (_) { p.locked = null; }
  finally { p.loading = false; ctx.render('pool-top'); }
}
async function poolAction(ctx, name, symbol, st) {
  if (st.stage !== 'edit' || st.receipt) return;
  const c = poolContext(symbol, ctx.wallet(), ctx.player);
  if (!c) return;
  const p = st.pool;
  if (name === 'add' || name === 'remove') {
    // opening (or closing) Add / Withdraw shows the top of the pool box, never somewhere in the middle of the wallet
    if (p.mode === name) { st.pool = blankPool(); ctx.render('pool-top'); return; }  // pressing the open one closes it
    st.pool = { ...blankPool(), mode: name };
    if (name === 'remove') await loadLocked(ctx, symbol, st.pool);
    else ctx.render('pool-top');
    return;
  }
  const pct = /^pct:(25|50|75|100)$/.exec(name);
  if (pct) {
    if (p.mode === 'add') Object.assign(p, { amount: capAmount(String(Math.floor(c.addMax * Number(pct[1]) / 100 * 1000) / 1000), c.addMax), dec: '', decTyped: false });
    else if (p.mode === 'remove') p.pct = pct[1];
    ctx.render('pool'); return;
  }
  if (name !== 'sign' || !c.signing || !walletReady(ctx.player)) return;
  const region = c.walletToken ? '' : c.region?.region_uid;
  if (region == null) return;
  if (p.mode === 'add') {
    const amount = parseAmount(p.amount);
    const dec = amount && decFor(c.reserve, amount);
    if (!amount || !(dec > 0)) return;
    await signIntent(ctx, symbol, st, { kind: 'add', symbol, region: region || null },
      { kind: 'add', resource_symbol: symbol, region_uid: region, resource_amount: amount, sign: true, seen_dec: String(dec) },
      fresh => ({ resource: Number(fresh.deposit?.resource_amount ?? amount), dec: Number(fresh.deposit?.dec_amount ?? dec) }),
      () => {});
    return;
  }
  if (p.mode === 'remove') {
    const chosen = parsePct(p.pct);
    const est = chosen && p.locked != null ? withdrawEstimate(c.reserve, c.shares, p.locked, chosen) : null;
    if (!est) return;
    await signIntent(ctx, symbol, st, { kind: 'remove', symbol, region: region || null, pct: chosen },
      { kind: 'remove', resource_symbol: symbol, region_uid: region, shares_out: est.fraction.toFixed(3), sign: true,
        seen: { resource_amount: est.resource.toFixed(3), dec_amount: est.dec.toFixed(3) } },
      fresh => ({ resource: Number(fresh.withdraw?.resource_amount ?? est.resource), dec: Number(fresh.withdraw?.dec_amount ?? est.dec) }),
      () => loadLocked(ctx, symbol, st.pool));
  }
}

// Grain, Wood, Stone and Iron to ANOTHER player's region. The game's own transfer
// (player0005 -> player0004, <tx id>, 2026-10-13) is the region-to-region pool swap with from_symbol = to_symbol and `to_player`
// two legs (resource -> DEC -> resource) at 5 % each, slippage 2.5 %. Reads happen on a press only (): Find reads the
// account (Hive, Splinterlands, their land regions: 3 calls), a settled amount one pools read, Review 3 fresh reads. Review then
// Sign; signing, the stored intent and the confirmation are the swap's own (signIntent, verify), so an unknown outcome never
// offers a second signature ( S3) and the clock applies.
export const TRANSFER_SYMBOLS = ['GRAIN', 'WOOD', 'STONE', 'IRON'];
const ACCOUNT_NAME = /^[a-z][a-z0-9.-]{2,15}$/;
const cleanName = text => String(text ?? '').trim().replace(/^@/, '').toLowerCase();
function transferContext(symbol, wallet, player) {
  if (!TRANSFER_SYMBOLS.includes(symbol) || !wallet?.regions || !wallet?.swap) return null;
  const regions = regionsFor(symbol, wallet, 'sell');
  if (!regions.length) return null;
  const st = swapState(player, symbol);
  const x = st.xfer || (st.xfer = blankTransfer());
  if (!regions.some(r => r.region_uid === x.fromRegion)) x.fromRegion = regions[0].region_uid;
  const region = regions.find(r => r.region_uid === x.fromRegion);
  return { st, x, regions, region, held: Number(region.balances[symbol]), signing: !!wallet.swap.signing_enabled };
}
function tabsHtml(st, open) {
  const locked = st.stage !== 'edit' || !!st.receipt;
  return `<div class="ceres-tabs wallet-swap-tabs" role="tablist" aria-label="Swap or transfer">${btn('tab:swap', 'Swap', 'secondary', 'compact', locked, ` role="tab" aria-selected="${open === 'swap'}"`)}${btn('tab:transfer', 'Transfer', 'secondary', 'compact', locked, ` role="tab" aria-selected="${open === 'transfer'}"`)}</div>`;
}
const regionName = row => `${regionLabel(row.region_uid)}${row.name ? ` · ${String(row.name).replace(/^\d+\s*-\s*/, '')}` : ''}`;
function recipientHtml(x) {
  const r = x.recipient;
  if (x.looking) return '<p class="dec-status wallet-transfer-status" role="status"><span class="spinner" aria-hidden="true"></span>Checking the account…</p>';
  if (!r) return '';
  if (r.status === 'error') return '<p class="wallet-swap-note is-warn" role="status">The account could not be checked right now. Press Find again.</p>';
  if (r.status === 'invalid') return '<p class="wallet-swap-note is-danger" role="alert">That account name cannot receive a transfer (not a valid Hive name, or your own account).</p>';
  if (r.status === 'no_hive_account') return `<p class="wallet-swap-note is-danger" role="alert">There is no Hive account named @${esc(r.account)}.</p>`;
  if (r.status === 'no_splinterlands_account') return `<p class="wallet-swap-note is-danger" role="alert">@${esc(r.account)} has no Splinterlands account.</p>`;
  const rows = r.regions || [];
  const chosen = rows.find(row => row.region_uid === x.toRegion) || rows[0];
  const choices = rows.length > 1 ? `<select data-lp-transfer-region aria-label="Change their region">${rows.map(row => `<option value="${esc(row.region_uid)}"${row.region_uid === chosen.region_uid ? ' selected' : ''}>${esc(regionName(row))}${row.plots_owned ? ` · ${esc(String(row.plots_owned))} plots` : ''}</option>`).join('')}</select>` : '';
  // a recipient with no land picks from every region, and the card says so.
  const noLand = r.no_land ? '<p class="wallet-swap-note is-warn" role="status" data-lp-transfer-no-land>They own no land in this region.</p>' : '';
  return `<div class="wallet-swap-sub wallet-transfer-found"><span class="is-ok">✓ @${esc(r.account)} receives in</span><span class="wallet-swap-where">${plaque(chosen.region_uid)}${choices}</span></div>${noLand}`;
}
function legsHtml(q, symbol) {
  const [one, two] = q.legs || [];
  return `${one ? line(`Fee ${esc(label(one.pay))} → DEC · ${esc(one.fee_percent)}%`, `${fixed(one.fee)} ${esc(label(one.fee_symbol))}`) : ''}${two ? line(`Fee DEC → ${esc(label(two.receive))} · ${esc(two.fee_percent)}%`, `${fixed(two.fee)} ${esc(label(two.fee_symbol))}`) : ''}${line('Total fee', `${esc(q.fee_percent)}%`)}${line(`At least · slippage ${esc(q.slippage)}%`, `${fixed(q.minimum)} ${esc(label(symbol))}`)}`;
}
function transferHtml(symbol, c, player) {
  const { x, region, regions, held } = c;
  if (x.review) return transferReviewHtml(symbol, c, player);
  const name = cleanName(x.to);
  const found = x.recipient?.status === 'ok' && x.recipient.account === name;
  const q = x.quote && x.quoted === x.amount && parseAmount(x.amount) !== null ? x.quote : null;
  const from = regions.length > 1 ? `<select data-lp-transfer-from aria-label="Change region">${regions.map(r => `<option value="${esc(r.region_uid)}"${r.region_uid === region.region_uid ? ' selected' : ''}>${esc(regionLabel(r.region_uid))} · ${esc(fixed(r.balances[symbol]))}</option>`).join('')}</select>` : '';
  const ready = found && !!x.toRegion && !!q && !x.reviewing;
  const action = x.reviewing ? 'Reading fresh figures…' : !found ? 'Find the player first' : q ? `Review transfer of ${fixed(q.input)} ${esc(label(symbol))}` : 'Enter an amount';
  return `<div class="wallet-swap-head"><span class="wallet-swap-title"><span>${art(symbol)}${esc(label(symbol))}</span>→<span>another player</span></span></div>
    <div class="wallet-swap-box wallet-transfer-flow"><div class="wallet-transfer-section"><div class="wallet-swap-line"><span>To player</span><span aria-hidden="true"></span></div>
      <div class="wallet-swap-asset wallet-transfer-to"><input type="text" class="ceres-wide-input" data-control-density="compact" data-lp-transfer-to="${esc(symbol)}" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="Hive account" aria-label="Recipient Hive account" value="${esc(x.to)}">${btn('transfer-find', 'Find', 'secondary', 'compact', !ACCOUNT_NAME.test(name) || x.looking)}</div>
      ${recipientHtml(x)}</div>
    <div class="wallet-transfer-section"><div class="wallet-swap-line"><span>You send</span><span class="wallet-swap-pcts">${[25, 50, 75, 100].map(pct => btn(`transfer-pct:${pct}`, `${pct}%`)).join('')}</span></div>
      <div class="wallet-swap-asset"><span class="wallet-swap-name">${art(symbol)}<strong>${esc(label(symbol))}</strong></span><input type="text" class="ceres-wide-input wallet-swap-amount" data-control-density="compact" data-lp-transfer-amount="${esc(symbol)}" inputmode="decimal" autocomplete="off" spellcheck="false" placeholder="0" aria-label="You send, ${esc(label(symbol))}" value="${esc(x.amount)}"></div>
      <div class="wallet-swap-sub"><span>of ${fixed(held)}</span><span class="wallet-swap-where">${plaque(region.region_uid)}${from}</span></div></div>
    <div class="wallet-transfer-section wallet-swap-receive"><div class="wallet-swap-line"><span>They receive about</span><span aria-hidden="true"></span></div>
      <div class="wallet-swap-asset"><span class="wallet-swap-name">${art(symbol)}<strong>${esc(label(symbol))}</strong></span><b class="wallet-pool-figure${q ? '' : ' is-stale'}" data-lp-transfer-out>${q ? fixed(q.output) : '—'}</b></div>
      ${q ? `<div class="wallet-swap-grid">${legsHtml(q, symbol)}</div>` : ''}</div>
    </div>
    ${x.note ? `<p class="wallet-swap-note${x.noteTone === 'warn' ? ' is-warn' : ''}" role="status">${esc(x.note)}</p>` : ''}
    ${btn('transfer-review', action, 'primary', 'compact', !ready, ' data-lp-primary')}`;
}
function transferReviewHtml(symbol, c, player) {
  const { st, x } = c;
  const v = x.review, q = v.quote;
  const busy = st.stage === 'signing';
  const ready = walletReady(player);
  const sign = btn('transfer-sign', busy ? `<span class="wallet-sign-label">Waiting for ${walletMethodHtml(player)}…</span>` : walletSignLabelHtml('Sign transfer with', player), 'primary', ready ? 'compact' : 'touch', busy || !ready || !c.signing || x.reviewing, ' data-lp-primary');
  const signRow = ready ? sign : `<span class="wallet-sign-pair wallet-swap-sign">${walletControlsHtml(player)}${sign}</span>`;
  return `<div class="wallet-swap-head"><span class="wallet-swap-title"><span>${art(symbol)}Review transfer</span></span></div>
    <div class="wallet-swap-box wallet-transfer-review" data-lp-transfer-review>
      <div class="wallet-swap-line"><span>You send</span><b>${fixed(q.input)} ${esc(label(symbol))}</b></div>
      <div class="wallet-swap-sub"><span>from</span><span class="wallet-swap-where">${plaque(v.payload.from_region_uid)}</span></div>
      <div class="wallet-swap-line"><span>To</span><b>@${esc(v.to_player)}</b></div>
      <div class="wallet-swap-sub"><span>into their</span><span class="wallet-swap-where">${plaque(v.payload.to_region_uid)}</span></div>
      ${x.recipient?.no_land ? '<p class="wallet-swap-note is-warn" role="status">They own no land in this region.</p>' : ''}
      <div class="wallet-swap-line"><span>They receive about</span><b class="wallet-pool-figure">${fixed(q.output)} ${esc(label(symbol))}</b></div>
      <div class="wallet-swap-grid">${legsHtml(q, symbol)}${line(`${esc(regionLabel(v.payload.from_region_uid))} ${esc(label(symbol))} after`, fixed(v.projected?.region_after ?? 0))}</div></div>
    <p class="wallet-swap-note is-warn" role="status">A transfer to another player cannot be undone. Check the account name before you sign.</p>
    ${x.note ? `<p class="wallet-swap-note${x.noteTone === 'warn' ? ' is-warn' : ''}" role="status">${esc(x.note)}</p>` : ''}
    ${!c.signing ? '<p class="wallet-swap-note" role="status">Signing is disabled on this server.</p>' : ''}
    <div class="wallet-transfer-actions">${signRow}${btn('transfer-edit', 'Edit', 'secondary', 'compact', busy)}</div>${busy ? walletWaitHtml(player) : ''}`;
}
function transferReceiptHtml(symbol, st, player) {
  const r = st.receipt;
  const done = st.stage === 'done', refused = st.stage === 'refused', failed = st.failed === 'confirm';
  const tone = done ? ' is-confirmed' : refused ? ' is-failed' : ' is-waiting';
  const title = done ? 'Transfer confirmed' : refused ? 'The game refused this transfer' : failed ? 'Still pending' : 'Sending';
  const got = done ? (r.output == null ? '—' : fixed(r.output)) : `≈ ${fixed(r.estimate ?? r.output ?? 0)}`;
  return `<section class="wallet-card wallet-swap wallet-transfer dec-review dec-receipt${tone}" data-lp-symbol="${esc(symbol)}" data-lp-stage="${st.stage}" data-lp-kind="transfer" aria-live="polite">
    <header><h3>${title}</h3>${r.region ? plaque(r.region) : ''}</header>
    <div class="dec-receipt-amount">${art(symbol)}<div><span>${done ? 'Sent' : refused ? 'Not sent' : 'Sending'} ${fixed(r.input)} ${esc(label(symbol))} to @${esc(r.to_player)}</span><strong>${got} <small>${esc(label(symbol))}</small> into their ${r.to_region ? esc(regionLabel(r.to_region)) : 'region'}</strong></div>${art(symbol)}</div>
    ${refused ? `<p class="wallet-swap-note is-danger" role="alert">${esc(st.refusal || 'The game gave no reason.')}</p><p class="wallet-swap-note">Nothing left your region.</p>` : ''}
    ${done && r.output == null ? '<p class="wallet-swap-note">The game did not report the amount received.</p>' : ''}
    ${st.checking ? '<p class="dec-status" role="status"><span class="spinner" aria-hidden="true"></span>Confirming with the game…</p>' : ''}
    ${waitingHtml(st)}
    <div class="dec-receipt-actions">${r.tx_id ? `<a href="https://hivehub.dev/tx/${encodeURIComponent(r.tx_id)}" target="_blank" rel="noopener noreferrer">View transaction ↗</a>` : ''}${done || refused ? btn('done', done ? 'Done' : 'Close', 'primary', 'touch') : confirmActions(st)}</div></section>`;
}
// Typing in the recipient or amount field (bound in regional-dec.js).
export function transferInput(ctx, input) {
  const symbol = input.dataset.lpTransferTo || input.dataset.lpTransferAmount;
  const c = transferContext(symbol, ctx.wallet(), ctx.player);
  if (!c || c.st.stage !== 'edit' || c.x.review) return;
  if (input.hasAttribute('data-lp-transfer-to')) {
    c.x.to = input.value;
    if (c.x.recipient && c.x.recipient.account !== cleanName(input.value)) Object.assign(c.x, { recipient: null, toRegion: null });  // a new name is checked again
  } else {
    const capped = capAmount(input.value, c.held);
    if (capped !== input.value) input.value = capped;
    c.x.amount = capped;
    scheduleTransferQuote(ctx, symbol, c.x);
  }
  patch(ctx.drawer(), symbol, ctx.wallet(), ctx.player);
}
export function transferSelect(ctx, select) {
  const symbol = select.closest('[data-lp-symbol]')?.dataset.lpSymbol;
  const c = symbol && transferContext(symbol, ctx.wallet(), ctx.player);
  if (!c || c.st.stage !== 'edit' || c.x.review) return;
  if (select.hasAttribute('data-lp-transfer-region')) c.x.toRegion = select.value;
  else {
    c.x.fromRegion = select.value;
    const next = transferContext(symbol, ctx.wallet(), ctx.player);
    if (parseAmount(c.x.amount) !== null && Number(c.x.amount) > next.held) { c.x.amount = capAmount(c.x.amount, next.held); scheduleTransferQuote(ctx, symbol, c.x); }
  }
  ctx.render();
}
function scheduleTransferQuote(ctx, symbol, x) {
  clearTimeout(x.timer);
  x.generation += 1;
  if (parseAmount(x.amount) === null) { x.quote = null; x.quoted = ''; return; }
  x.timer = setTimeout(() => runTransferQuote(ctx, symbol, x), QUOTE_IDLE_MS);
}
async function runTransferQuote(ctx, symbol, x) {
  if (x.inFlight) { x.queued = true; return; }
  const amount = x.amount, generation = x.generation;
  if (parseAmount(amount) === null) return;
  x.inFlight = true;
  try {
    const q = await post('/api/wallet/transfer/quote', ctx.player, { resource_symbol: symbol, amount: parseAmount(amount) });
    if (generation === x.generation && x.amount === amount && q.resource_symbol === symbol) { x.quote = q; x.quoted = amount; }
  } catch (_) {
    if (generation === x.generation) { x.quote = null; x.quoted = ''; }
  } finally {
    x.inFlight = false;
    if (x.queued) { x.queued = false; if (generation !== x.generation) runTransferQuote(ctx, symbol, x); }
    patch(ctx.drawer(), symbol, ctx.wallet(), ctx.player);
  }
}
async function runTransferReview(ctx, symbol, c, note = '') {
  const x = c.x;
  const q = x.quote;
  if (!q || x.recipient?.status !== 'ok' || !x.toRegion) return;
  x.reviewing = true; x.note = ''; ctx.render();
  try {
    x.review = await post('/api/wallet/transfer/preview', ctx.player, { resource_symbol: symbol, from_region_uid: x.fromRegion, to_player: x.recipient.account,
      to_region_uid: x.toRegion, in_amount: q.input, sign: false });
    x.note = note; x.noteTone = note ? 'warn' : '';
  } catch (_) {
    x.review = null; x.note = 'The figures could not be read again. Nothing was signed; try Review again.'; x.noteTone = 'warn';
  } finally { x.reviewing = false; ctx.render(); }
}
async function transferAction(ctx, name, symbol, st) {
  const c = transferContext(symbol, ctx.wallet(), ctx.player);
  if (!c || st.stage !== 'edit' || st.receipt) return;
  const x = c.x;
  if (name === 'find') {  // the maintainer's press: one check of the account and its land regions
    const account = cleanName(x.to);
    if (!ACCOUNT_NAME.test(account) || x.looking) return;
    x.looking = true; x.recipient = null; x.toRegion = null; ctx.render();
    try {
      const found = await fetch(`/api/wallet/transfer/recipient?player=${encodeURIComponent(ctx.player)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ to_player: account }) });
      let body = null;
      try { body = await found.json(); } catch (_) { body = null; }
      if (cleanName(x.to) !== account) return;  // the name changed while the check ran
      if (found.status === 422) x.recipient = { status: 'invalid', account };  // fixed text, never the server's
      else if (!found.ok || !body?.status) x.recipient = { status: 'error', account };
      else { x.recipient = body; x.toRegion = body.regions?.[0]?.region_uid || null; }
    } catch (_) { x.recipient = { status: 'error', account }; }
    finally { x.looking = false; ctx.render(); }
    return;
  }
  const pct = /^pct:(25|50|75|100)$/.exec(name);
  if (pct && !x.review) {
    x.amount = capAmount(String(Math.floor(c.held * Number(pct[1]) / 100 * 1000) / 1000), c.held);
    scheduleTransferQuote(ctx, symbol, x); clearTimeout(x.timer); runTransferQuote(ctx, symbol, x);
    patch(ctx.drawer(), symbol, ctx.wallet(), ctx.player); return;
  }
  if (name === 'review' && !x.review) { await runTransferReview(ctx, symbol, c); return; }
  if (name === 'edit') { Object.assign(x, { review: null, note: '', noteTone: '' }); ctx.render(); return; }
  if (name !== 'sign' || !x.review || !c.signing || !walletReady(ctx.player)) return;
  const v = x.review;
  await signIntent(ctx, symbol, st, { kind: 'transfer', pay: symbol, receive: symbol, region: v.payload.from_region_uid, to_player: v.to_player, to_region: v.payload.to_region_uid },
    { resource_symbol: symbol, from_region_uid: v.payload.from_region_uid, to_player: v.to_player, to_region_uid: v.payload.to_region_uid,
      in_amount: v.quote.input, sign: true, reviewed_payload: v.payload },
    fresh => ({ input: Number(fresh.quote?.input ?? v.quote.input), output: null, estimate: Number(fresh.quote?.output ?? v.quote.output) }),
    () => { x.review = null; runTransferReview(ctx, symbol, transferContext(symbol, ctx.wallet(), ctx.player) || c, 'The pool moved since your review, or the figures could not be read. These are the fresh figures; nothing was signed.'); },
    '/api/wallet/transfer/preview');
}

let intentListener = null;
export function onIntentChange(fn) { intentListener = fn; }   // S10 / A-03: the wallet drawer re-renders when another tab changes a swap
// A second tab of the same browser: progress and resolution of an unsettled swap reach this page's card too (no second swap, no stale receipt).
try {
  globalThis.addEventListener?.('storage', (event) => {
    const match = /^ceres:wallet-lp-intent:v1:([^:]+):(.+)$/.exec(String(event?.key || ''));
    if (!match) return;
    for (const [id, st] of states) {
      if (id.toLowerCase() !== `${match[1]}:${match[2]}`.toLowerCase()) continue;
      const working = st.busy || st.checking || st.stage === 'signing' || st.stage === 'funding-signing';
      if (!event.newValue) {  // the other tab settled or discarded it
        if (!working && st.receipt && st.stage !== 'done') Object.assign(st, { stage: 'edit', receipt: null, funding: null, quote: null, quoted: '', token: null });
        try { queueMicrotask(() => intentListener?.(match[1], match[2])); } catch (_) { /* no listener */ }
        continue;
      }
      let next = null;
      try { next = JSON.parse(event.newValue); } catch (_) { /* ignored */ }
      if (!intentIsOpen(next)) continue;
      try { queueMicrotask(() => intentListener?.(match[1], match[2])); } catch (_) { /* no listener */ }
      if (st.stage === 'edit' || (!working && (next.stage !== st.stage || next.receipt?.tx_id !== st.receipt?.tx_id || next.receipt?.funding_tx !== st.receipt?.funding_tx))) restoreIntent(match[1], match[2], st);
    }
  });
} catch (_) { /* no window in tests */ }
