import { esc } from './format.js';
import { outcomeBoxHtml, poolBoxHtml, routeListColumnHtml, routeNameHtml, routeScrollCueHtml } from './routing-route-list.js';
import { tokenIcon, tokenLabel, fmt2 } from './harvest-state.js';
import { territorySlug } from './icons.js';
import { walletName, walletReady, walletControlsHtml, walletLabelKind } from './wallet.js';

const TERRITORIES = { PNW: 'Pristine Northwest', BRL: 'Broken Lands', LOP: 'Land of Prosperity', CEF: 'Central Fire', WNE: 'Wild Northeast', GLL: 'Great Lowlands', SHC: 'Shimmering Coasts' };
const FINAL = new Set(['confirmed', 'refused', 'skipped', 'stopped']);

export function routingApprovalCounts(manifest) {
  const bundles = manifest?.bundles || [];
  const fixed = bundles.filter(bundle => bundle.wave === 0).length;
  const maximum = Math.max(Number(manifest?.approval_counts?.routing) || 0, bundles.length,
    ...bundles.map(bundle => Number(bundle.approval) || 0));
  return { fixed, conditional: Math.max(0, maximum - fixed), maximum,
    harvest: manifest?.approval_counts?.harvest || 0 };
}

function regionTitle(uid) {
  const parts = String(uid || '').split('-');
  const code = parts.at(-1)?.padStart(3, '0') || String(uid || '');
  const territory = TERRITORIES[parts[1]];
  return `Region ${code}${territory ? ` · ${territory}` : ''}`;
}

function plaque(uid) {
  const parts = String(uid || '').split('-');
  const slug = territorySlug(TERRITORIES[parts[1]]);
  const code = parts.pop().padStart(3, '0');
  return `<span class="ch-plot-badge${slug ? ` territory-${slug}` : ''}" aria-label="${esc(regionTitle(uid))}">${esc(code)}</span>`;
}

function art(symbol, large = false) {
  const icon = tokenIcon(symbol);
  return icon ? `<img class="r3-art${large ? ' r3-art--large' : ''}" src="${esc(icon)}" alt="">` : '';
}

function stateLabel(state) {
  return ({ planned: 'Waiting', claimed: 'Awaiting wallet', attached: 'Checking Hive', checking: 'Checking game',
    outcome_unknown: 'Outcome unknown', confirmed: 'Confirmed by game', refused: 'Refused by game',
    skipped: 'Skipped', stopped: 'Stopped' })[state] || 'Waiting';
}

function chipStateLabel(state) {
  return ({ planned: 'Waiting', claimed: 'Waiting', attached: 'Checking', checking: 'Checking',
    outcome_unknown: 'Unknown', confirmed: 'Confirmed', refused: 'Refused',
    skipped: 'Skipped', stopped: 'Stopped' })[state] || 'Waiting';
}

function tone(state) {
  return ({ confirmed: 'ok', refused: 'bad', outcome_unknown: 'warn', attached: 'info', checking: 'info' })[state] || 'quiet';
}

function wholePercent(value) {
  return `${Math.round(Number(value) || 0)}%`;
}

export function routingBundleView(record, viewBundleId = null) {
  const bundles = record.manifest?.bundles || [];
  const current = bundles.find(bundle => !FINAL.has(record.bundles?.[bundle.id]?.state));
  // a route click shows the approval it belongs to; otherwise the current one, or the last when finished.
  const display = bundles.find(bundle => bundle.id === viewBundleId) || current || bundles.at(-1) || null;
  const members = display ? display.operations.flatMap(operation => operation.members.map(member => ({ operation, member }))) : [];
  const routes = [];
  const seen = new Set();
  for (const bundle of bundles) for (const operation of bundle.operations) for (const member of operation.members) {
    if (seen.has(member.route_id)) continue;
    seen.add(member.route_id);
    routes.push({ operation, member, bundle });
  }
  return { current, display, members, routes };
}

function chip({ operation, member, bundle }, selected, record, excluded, busy = false) {
  // A share's own state speaks once it is final; until then the chip follows its bundle
  // (a claimed share waits for the wallet, it is not "unknown").
  const own = record.operations?.[operation.id]?.members?.[member.nonce]?.state;
  const bundleState = record.bundles?.[bundle.id]?.state;
  const raw = FINAL.has(own) ? own : bundleState || own || 'planned';
  const state = busy && raw === 'outcome_unknown' ? 'checking' : raw;
  const symbol = operation.source_symbol;
  const destination = operation.kind === 'liquidity' ? 'LP' : tokenLabel(operation.destination_symbol || 'DEC');
  const amount = member.source_percent == null ? fmt2(member.source_amount) : wholePercent(member.source_percent);
  const allocation = member.source_percent == null ? amount : `${amount} · ≈ ${fmt2(member.source_amount)} ${tokenLabel(symbol)}`;
  const omitted = excluded.has(member.route_id) && state === 'planned';
  return `<div class="r3-route-chip${selected ? ' is-selected' : ''}${omitted ? ' is-excluded' : ''}${state === 'confirmed' ? ' is-confirmed' : ''}" data-r3="view-bundle" data-r3-bundle-id="${esc(bundle.id)}" data-r3-operation-ref="${esc(operation.id)}" data-r3-route="${esc(member.route_id)}" role="button" tabindex="0" data-tip="${esc(`${regionTitle(operation.source_region)}: ${allocation} to ${destination}. ${omitted ? 'Excluded from this approval' : stateLabel(state)}.`)}">${plaque(operation.source_region)}${routeNameHtml({ resourceIconHtml: art(symbol), from: tokenLabel(symbol), destination, destinationIconHtml: operation.kind === 'liquidity' ? '' : art(operation.destination_symbol || 'DEC'),
    stateHtml: `<span class="r3-state r3-state--${tone(state)}">· ${omitted ? 'Excluded' : chipStateLabel(state)}</span>` })}</div>`;
}

function selection(member, operation, editable, excluded) {
  if (!editable || member.kind !== 'sale_dec') return '';
  return `<input class="r3-flow-select" type="checkbox" data-r3="toggle-member" data-r3-route="${esc(member.route_id)}" aria-label="Include ${esc(tokenLabel(operation.source_symbol))} sale from ${esc(regionTitle(operation.source_region))}"${excluded.has(member.route_id) ? '' : ' checked'}>`;
}

function memberCell({ operation, member }, editable, excluded, record = {}) {
  const done = record.operations?.[operation.id]?.state === 'confirmed';
  // A confirmed single-share deposit shows the DEC actually added, the same figure as its row above.
  const added = done && operation.members.length === 1 ? record.operations?.[operation.id]?.deposit?.dec_amount : null;
  const needed = ['lp_wallet', 'lp_sell_half'].includes(member.kind) ? `<span>${art('DEC')} ${fmt2(added ?? member.required_wallet_dec ?? member.required_dec ?? member.reserved_dec)} DEC ${done ? 'added' : 'needed'}</span>` : '';
  const share = member.source_percent == null ? '' : `${wholePercent(member.source_percent)} · `;
  return `<div class="r3-member${excluded.has(member.route_id) ? ' is-excluded' : ''}${done ? ' is-confirmed' : ''}" data-r3-member="${esc(member.route_id)}">${selection(member, operation, editable, excluded)}${plaque(operation.source_region)}${art(operation.source_symbol)}<span>${share}${fmt2(member.source_amount)} ${esc(tokenLabel(operation.source_symbol))}</span>${needed}</div>`;
}

function operationFlow(operation, record, editable, excluded) {
  const symbol = operation.source_symbol;
  const lp = operation.kind === 'liquidity';
  const member = operation.members[0];
  const deposit = record.operations?.[operation.id]?.deposit;
  const target = lp ? 'LP shares' : tokenLabel(operation.destination_symbol || 'DEC');
  const minimum = operation.protected_minimum || operation.members.reduce((sum, item) => sum + Number(item.protected_minimum || 0), 0);
  const done = record.operations?.[operation.id]?.state === 'confirmed';
  const needed = lp ? `<span class="r3-dec-needed">${art('DEC')} ${fmt2(deposit?.dec_amount || member.required_wallet_dec || member.required_dec || member.reserved_dec)} DEC ${done ? 'added' : 'needed'}</span>` : '';
  const amount = lp && deposit?.resource_amount ? deposit.resource_amount
    : operation.members.reduce((sum, item) => sum + Number(item.source_amount || 0), 0);
  const single = operation.members.length === 1 ? member : null;
  const sources = `${single ? selection(single, operation, editable, excluded) : ''}<div class="r3-flow-source">${plaque(operation.source_region)}${art(symbol, true)}<b>${esc(tokenLabel(symbol))}</b><span>${fmt2(amount)}${single && single.source_percent != null ? ` · ${wholePercent(single.source_percent)}` : ''}</span></div>`;
  return `<div class="r3-flowline${lp ? ' r3-flowline--lp' : ''}${done ? ' is-confirmed' : ''}" data-r3-operation="${esc(operation.id)}"><div class="r3-flow-sources">${sources}</div><span class="r3-arrow" aria-hidden="true">⟶</span>${poolBoxHtml({ title: lp ? 'Add liquidity' : 'Sell harvested', neededHtml: needed })}<span class="r3-arrow" aria-hidden="true">⟶</span>${outcomeBoxHtml({ iconHtml: art(lp ? symbol : operation.destination_symbol || 'DEC', true), label: lp ? 'LP shares · minimum' : 'Minimum', valueHtml: `${fmt2(minimum)} ${esc(lp ? tokenLabel(symbol) : target)}` })}</div>`;
  // (maintainer): the outcome box is two rows -- a label over a large amount -- the height of the Pool box beside it.
}

// open question (maintainer: routing footer has no wallet chooser): when no wallet is ready to sign, the chooser sits
// left of the sign button, as on every other signing surface, so an expired HiveAuth session reconnects right here.
// the wallet chooser is ALWAYS shown next to the Sign button (it states which wallet is selected and lets the player switch); every signature, the
// optional sign-in included, uses the wallet selected here.
export function withWalletChooser(player, signButton) {
  return player ? `<span class="wallet-sign-pair">${walletControlsHtml(player, 'routing-window')}${signButton}</span>` : signButton;
}

// a web page cannot switch apps for the player (HiveAuth has a deep link only for connecting, none for a signing request), so once
// the request is with the wallet the window says plainly that it now waits in the wallet app.
function walletWaitHtml(state, player, error = '', busy = false) {
  if (state !== 'claimed' || !player || walletLabelKind(player) !== 'hiveauth') return '';
  // the wallet request ran out of time here. The wallet may still have signed, so the player can have Ceres find the transaction.
  if (/still checking/i.test(error)) {
    return `<div class="r3-wallet-wait" role="alert"><strong>Did you approve it in the Keychain app?</strong><span>Ceres lost the answer while you were away. If you approved, Ceres can find your transaction in your wallet history and carry on. If you did not approve, stop here and start again.</span><button type="button" class="ceres-btn ceres-btn--primary" data-control-density="comfortable" data-r3="find"${busy ? ' disabled' : ''}>I approved it: find my transaction</button></div>`;
  }
  return '<div class="r3-wallet-wait" role="alert"><strong>Switch to the Hive Keychain app and confirm the transaction.</strong><span>It is waiting in Hive Keychain or the HiveAuth app on your phone. Approve it there, then come back to this window.</span></div>';
}

export function routingWindowHtml(record, { player, busy = false, error = '', changedAmountReview = [], excludedRouteIds = new Set(), viewBundleId = null } = {}) {
  const { current, display, members, routes } = routingBundleView(record, viewBundleId);
  const counts = routingApprovalCounts(record.manifest);
  const bundles = record.manifest?.bundles || [];
  const confirmed = bundles.filter(bundle => record.bundles?.[bundle.id]?.state === 'confirmed').length;
  const refused = bundles.filter(bundle => record.bundles?.[bundle.id]?.state === 'refused').length;
  const stored = current ? record.bundles?.[current.id]?.state : null;
  // While the scheduled checks are still running an unanswered lookup is "checking", not an unknown outcome.
  const state = busy && stored === 'outcome_unknown' ? 'checking' : stored;
  const heading = current ? `Approval ${current.approval} of ${counts.maximum}` : 'Routing finished';
  const message = error || (changedAmountReview.length ? 'Review the confirmed sale proceeds and final LP deposit before wallet approval'
    : state === 'outcome_unknown' ? 'Reservation retained · dependents blocked'
    : state === 'checking' || state === 'attached' ? 'Hive included · checking game receipt'
    : state === 'claimed' ? busy ? 'Approve with Posting wallet' : 'Wallet outcome uncertain · check receipt history'
    : current ? `${confirmed ? `${confirmed} of ${bundles.length} confirmed · ` : ''}Ready to sign` : `${confirmed} confirmed${refused ? ` · ${refused} refused by game` : ''}`);
  const budget = record.call_budget || {};
  const ceiling = budget.ceiling || record.manifest?.call_budget?.run_ceiling || 120;
  const manualCeiling = record.manifest?.call_budget?.action_ceilings?.manual_retry || 6;
  const manualRemaining = Math.max(0, manualCeiling - (budget.actions?.manual_retry || 0));
  const canCheck = !!(record.bundles?.[current?.id]?.tx_id && ['attached', 'checking', 'outcome_unknown'].includes(stored)
    && !budget.stopped && (budget.total || 0) < ceiling && manualRemaining > 0);
  const canSign = (state === 'planned' || (state === 'claimed' && changedAmountReview.length)) && record.state === 'ready';
  const editable = state === 'planned' && !busy;
  // every route of the shown approval is
  // marked and points at it; a finished run with every approval confirmed turns the run green and says it is complete.
  // a single-share route needs no row of its own (its amount, DEC and percentage are in the flow box); only an operation that
  // merges several shares lists them.
  const sharedMembers = members.filter(item => item.operation.members.length > 1);
  const shownRouteIds = new Set(members.map(item => item.member.route_id));
  const complete = !current && bundles.length > 0 && confirmed === bundles.length;
  const displayState = display ? record.bundles?.[display.id]?.state : null;
  const needMemberGrid = sharedMembers.length > 0;
  const review = changedAmountReview.length ? `<div class="r3-changed-review"><strong>Review final LP amounts from the confirmed sale</strong>${changedAmountReview.map(item => {
    const operation = display?.operations.find(op => op.id === item.operation_id);
    return `<span>${art('DEC')}${fmt2(item.confirmed_sale_dec)} DEC received · deposit ${art(operation?.source_symbol)}${fmt2(item.resource_amount)} ${esc(tokenLabel(operation?.source_symbol || ''))} and ${art('DEC')}${fmt2(item.dec_amount)} DEC</span>`;
  }).join('')}</div>` : '';
  const terminal = state === 'outcome_unknown'
    ? `<div class="r3-terminal" role="status"><strong>Outcome unknown</strong><span>Reservation retained · dependents blocked</span><span>${manualRemaining} manual checks remain</span></div>`
    : complete ? `<div class="r3-terminal is-complete" role="status"><strong><span class="r3-complete-tick" aria-hidden="true">✓</span>Routing complete · all ${routes.length} route${routes.length === 1 ? '' : 's'} confirmed</strong><span>Nothing left to sign. You can close this window.</span></div>`
    : !current ? `<div class="r3-terminal" role="status"><strong>Routing finished · ${confirmed} of ${bundles.length} confirmed${refused ? ` · ${refused} refused` : ''}</strong>${refused ? `<span>${refused} refused by the game</span>` : ''}</div>` : '';
  // (maintainer: the status row hid behind the route list and "should be placed elsewhere and require no scrolling"): the
  // status now lives in the top summary row, which has the free space. Same content, same live-region semantics.
  const status = current ? `<span class="r3-summary-status" role="status" aria-live="polite">${['checking', 'attached'].includes(state) ? '' : `<span>${stateLabel(state)}</span>`}${['attached', 'checking', 'outcome_unknown'].includes(state) ? `<span>Metered routing checks ${budget.total || 0}/${ceiling}</span>` : ''}<span class="r3-status-message">${esc(message)}</span><span class="r3-status-spinner${busy ? ' is-busy' : ''}"><span class="spinner spinner-sm" aria-hidden="true"></span></span></span>` : '';
  return `<section class="r3-window${complete ? ' is-complete' : ''}" data-r3-bundle="${esc(display?.id || '')}" aria-label="Harvest routing run"><div class="r3-summary">${current ? `<strong>${counts.fixed} fixed${counts.conditional ? ` + up to ${counts.conditional} later` : ''}</strong><span>${routes.length} route${routes.length === 1 ? '' : 's'} · ${bundles.length} planned bundle${bundles.length === 1 ? '' : 's'}</span>` : `<strong>${routes.length} route${routes.length === 1 ? '' : 's'}</strong><span>${bundles.length} approval${bundles.length === 1 ? '' : 's'}</span>`}${routeScrollCueHtml()}${Number(record.protected_wallet_dec || 0) > 0 ? `<span>Protected wallet DEC ${fmt2(record.protected_wallet_dec)}</span>` : ''}${status}<span class="r3-wallet">${esc(walletName(player))} · ${esc(player)} · Posting</span></div>${walletWaitHtml(state, player, error, busy)}${terminal}<div class="r3-flow-layout">${routeListColumnHtml(routes.map(route => chip(route, shownRouteIds.has(route.member.route_id), record, excludedRouteIds, busy)).join(''))}<div class="r3-flow-stage${displayState === 'confirmed' ? ' is-confirmed' : ''}" tabindex="-1"><div class="r3-stage-head"><h3>${display ? `Approval ${display.approval} of ${counts.maximum}` : 'Approval'}${display ? ` <span class="r3-state r3-state--${tone(displayState)}">${chipStateLabel(displayState)}</span>` : ''}</h3><span>${display?.operations.some(operation => operation.kind === 'liquidity') ? 'LP deposits' : 'Sales'} · ${members.length} route${members.length === 1 ? '' : 's'}</span></div><div class="r3-operation-flows">${display?.operations.map(operation => operationFlow(operation, record, editable, excludedRouteIds)).join('') || ''}</div>${needMemberGrid ? `<div class="r3-members" aria-label="Current bundle shares">${sharedMembers.map(item => memberCell(item, editable, excludedRouteIds, record)).join('')}</div>` : ''}${review}<div class="r3-stage-status"></div></div></div><template class="r3-actions">${canCheck ? `<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-r3="check"${busy ? ' disabled' : ''}>Check again</button>` : ''}${current ? `<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-r3="stop"${busy ? ' disabled' : ''}>Stop remaining routes</button>` : ''}${canSign ? withWalletChooser(player, `<button type="button" class="ceres-btn ceres-btn--primary" data-control-density="comfortable" data-r3="sign"${busy ? ' disabled' : ''}>${members.every(item => excludedRouteIds.has(item.member.route_id)) ? 'Skip this bundle' : 'Sign'}</button>`) : ''}</template></section>`;
}

export function patchRoutingWindow(current, html) {
  if (!current || !html.startsWith('<section class="r3-window"')) return false;
  const template = document.createElement('template');
  template.innerHTML = html;
  const next = template.content.firstElementChild;
  const ids = section => [...section.querySelectorAll('[data-r3-operation]')].map(node => node.dataset.r3Operation).join('|');
  if (current.dataset.r3Bundle !== next.dataset.r3Bundle || ids(current) !== ids(next)) return false;
  if (current.classList.contains('is-complete') !== next.classList.contains('is-complete')) return false;
  if (!!current.querySelector('.r3-terminal') !== !!next.querySelector('.r3-terminal')
      || !!current.querySelector('.r3-summary-status') !== !!next.querySelector('.r3-summary-status')
      || !!current.querySelector('.r3-members') !== !!next.querySelector('.r3-members')
      || !!current.querySelector('.r3-changed-review') !== !!next.querySelector('.r3-changed-review')
      || (current.querySelector('.r3-wallet-wait')?.innerHTML ?? '') !== (next.querySelector('.r3-wallet-wait')?.innerHTML ?? '')) return false;   // the wallet notice appears and goes away with a full redraw
  for (const selector of ['.r3-summary', '.r3-terminal', '.r3-route-index', '.r3-stage-head', '.r3-operation-flows', '.r3-members', '.r3-changed-review', '.r3-stage-status', '.r3-actions']) {
    const target = current.querySelector(selector);
    if (target) target.innerHTML = next.querySelector(selector).innerHTML;
  }
  const stage = current.querySelector('.r3-flow-stage');
  if (stage) stage.className = next.querySelector('.r3-flow-stage').className;
  current.closest('.harvest-panel')?.querySelector('.r3-modal-actions')?.replaceChildren(...current.querySelector('.r3-actions').content.cloneNode(true).childNodes);
  return true;
}

// the route list
// scrolls on its own; whenever the shown approval changes, the list moves so the first route of that approval is at the top.
// Only on a change of approval, so scrolling the list by hand is never fought.
export function scrollRouteIndexToActive(root) {
  for (const section of root?.querySelectorAll?.('.r3-window') || []) {
    const list = section.querySelector('.r3-route-index');
    const first = list?.querySelector('.r3-route-chip.is-selected');
    if (!list || !first) continue;
    const bundle = section.dataset.r3Bundle || '';
    if (list.dataset.r3Scrolled === bundle) continue;
    const previous = list.dataset.r3Scrolled;
    list.dataset.r3Scrolled = bundle;
    const top = Math.max(0, first.offsetTop - list.offsetTop - 4);
    if (Math.abs(list.scrollTop - top) > 1) list.scrollTo({ top, behavior: previous ? 'smooth' : 'auto' });
  }
}

// (maintainer, after the pills were rejected for costing space): the scroll cue of the route list. While the list is longer than its
// box, the summary row shows "showing 4-12 of 36" with two page buttons, and the list edge that has more routes fades (classes
// has-more-above / has-more-below, see ceres.css). Nothing is added to the list itself, so its size never changes. Serves the setup
// list and the window list.
export function updateRouteScrollCue(root) {
  for (const section of root?.querySelectorAll?.('.r3-window, .r3-setup') || []) {
    const list = section.querySelector('.r3-route-index');
    const cue = section.querySelector('.r3-scroll-cue');
    if (!list || !cue) continue;
    const overflow = list.scrollHeight > list.clientHeight + 1;
    cue.hidden = !overflow;
    list.classList.toggle('has-more-above', overflow && list.scrollTop > 1);
    list.classList.toggle('has-more-below', overflow && list.scrollTop + list.clientHeight < list.scrollHeight - 1);
    if (!overflow) { list.removeAttribute('tabindex'); list.removeAttribute('role'); list.removeAttribute('aria-label'); continue; }
    list.setAttribute('tabindex', '0'); list.setAttribute('role', 'region'); list.setAttribute('aria-label', 'Routes, scrollable');
    const box = list.getBoundingClientRect();
    const chips = [...list.querySelectorAll('.r3-route-chip')];
    let first = 0, last = 0;
    chips.forEach((chip, index) => {
      const rect = chip.getBoundingClientRect();
      const middle = rect.top + rect.height / 2;
      if (middle >= box.top && middle <= box.bottom) { if (!first) first = index + 1; last = index + 1; }
    });
    cue.querySelector('.r3-scroll-cue-text').textContent = `${first}\u2013${last} of ${chips.length}`;
    cue.setAttribute('aria-label', `Showing routes ${first} to ${last} of ${chips.length}`);
    const up = cue.querySelector('[data-r3-more="up"]'), down = cue.querySelector('[data-r3-more="down"]');
    up.disabled = !list.classList.contains('has-more-above');
    down.disabled = !list.classList.contains('has-more-below');
  }
}

export function scrollRouteIndexByPage(button) {
  const list = button?.closest?.('.r3-window, .r3-setup')?.querySelector('.r3-route-index');
  if (!list) return;
  const step = Math.max(40, list.clientHeight - 40);
  list.scrollBy({ top: button.dataset.r3More === 'up' ? -step : step, behavior: 'smooth' });
}

// (maintainer: "All routes of the bundle should use a pointer and point at their own respective part inside the bundle
// box"): one line per marked route, from the route to its own row in the approval box, drawn from the real positions.
export function drawRoutePointers(root) {
  for (const layout of root?.querySelectorAll?.('.r3-window .r3-flow-layout') || []) {
    layout.querySelector(':scope > svg.r3-pointers')?.remove();
    const box = layout.getBoundingClientRect();
    const list = layout.querySelector('.r3-route-index')?.getBoundingClientRect();
    const stage = layout.querySelector('.r3-flow-stage')?.getBoundingClientRect();
    if (!list || !stage || !box.width) continue;
    if (stage.left < list.right - 1) continue; // the phone stacks the list above the approval -- no side pointers
    const paths = [...layout.querySelectorAll('.r3-route-chip.is-selected')].map((chip) => {
      const flow = layout.querySelector(`.r3-flowline[data-r3-operation="${CSS.escape(chip.dataset.r3OperationRef || '')}"]`);
      const from = chip.getBoundingClientRect();
      if (!flow || from.bottom <= list.top || from.top >= list.bottom) return '';
      const to = flow.getBoundingClientRect();
      const x1 = from.right - box.left, y1 = from.top + from.height / 2 - box.top;
      const x2 = to.left - box.left, y2 = to.top + to.height / 2 - box.top;
      const bend = (x1 + stage.left - box.left) / 2;
      const r = Math.min(6, Math.abs(y2 - y1) / 2);
      const dy = Math.sign(y2 - y1);
      const d = r < 0.5 ? `M${x1} ${y1}H${x2}`
        : `M${x1} ${y1}H${bend - r}Q${bend} ${y1} ${bend} ${y1 + dy * r}V${y2 - dy * r}Q${bend} ${y2} ${bend + r} ${y2}H${x2}`;
      return `<path class="${chip.classList.contains('is-confirmed') ? 'is-confirmed' : ''}" d="${d}"/>`;
    }).join('');
    if (paths) layout.insertAdjacentHTML('beforeend', `<svg class="r3-pointers" aria-hidden="true" width="${box.width}" height="${box.height}">${paths}</svg>`);
  }
}
