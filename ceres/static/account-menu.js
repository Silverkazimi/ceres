// one small account panel for optional saved-space sign-in. Transaction signing keeps its own wallet drawer.
import { esc } from './format.js';
import { tipAttrs } from './tooltip.js';
import { accountWalletChoices, accountWalletRequest, cancelAccountWalletRequest, pairAccountWalletAgain, selectAccountWallet, walletAccount, walletKind, walletLogoHtml } from './wallet.js';
import { connect, deleteSaved, disconnect, hasSession, sessionWallet, spaceAvailable } from './account-space.js';

const state = { open: false, busy: false, note: '', confirm: null, done: '', manage: false, attempt: 0, loginKind: '' };
let button = null;
let panel = null;

const NOTES = {
  declined: 'Sign-in cancelled. Nothing was changed.',
  refused: 'The signature does not match this account. Nothing was changed.',
  busy: 'Too many attempts; try again in a minute.',
  unavailable: 'Could not sign in right now. Your routes are safe on this device.',
};

function selectedWallet(account) {
  const choices = accountWalletChoices();
  const kind = walletKind(account);
  return choices[kind] ? kind : choices.keychain ? 'keychain' : 'hiveauth';
}

export function accountMenuHtml(account = walletAccount()) {
  if (!account) return '<p class="acct-note">Enter a player name first.</p>';
  const signedIn = hasSession(account);
  const choices = accountWalletChoices();
  const kind = selectedWallet(account);
  const request = state.busy && state.loginKind === 'hiveauth' ? accountWalletRequest(account) : null;
  // (, work laptop without the Keychain extension): both
  // wallets are always shown while signed out; one this browser cannot use is greyed out and says why.
  const choiceBtn = (name, label, missing) => `<button type="button" class="ceres-btn ceres-btn--choice" data-control-density="comfortable" data-account-wallet="${name}" aria-pressed="${kind === name}"${missing ? ` disabled${tipAttrs({ title: `${label} not available`, body: missing })}` : ''}>${walletLogoHtml(name)}${label}</button>`;
  const walletChoice = !signedIn && (choices.keychain || choices.hiveauth)
    ? `<div class="acct-wallet-choice" role="group" aria-label="Wallet for sign in">${choiceBtn('keychain', 'Keychain', choices.keychain ? '' : 'The Hive Keychain browser extension was not found in this browser. Use HiveAuth with the Keychain app on your phone.')}${choiceBtn('hiveauth', 'HiveAuth', choices.hiveauth ? '' : 'HiveAuth needs a secure (https) page.')}</div>` : '';
  const waiting = !signedIn && state.busy
    ? `<div class="acct-wait" role="status"><span class="spinner" aria-hidden="true"></span><span>${state.loginKind === 'keychain' ? 'Confirm in the Keychain extension' : request?.link ? 'Scan with the Hive Keychain app and confirm' : request?.action === 'challenge' ? 'Confirm in the Hive Keychain app' : 'Preparing HiveAuth sign-in…'}</span></div>${request?.link ? `<div class="acct-pairing"><div class="wallet-qr" role="img" aria-label="HiveAuth connection QR code">${request.svg}</div><a class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" href="${esc(request.link)}" rel="noreferrer">Open Keychain app</a></div>` : ''}${request?.action === 'challenge' ? '<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-account="pair-again">Pair again with a QR code</button>' : ''}${state.loginKind === 'hiveauth' ? '<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-account="cancel">Stop waiting</button><p class="acct-note">If the Keychain app stays open, close its request there too.</p>' : ''}` : '';   // the Keychain extension popup cannot be withdrawn from the page, so only a HiveAuth wait offers this
  const manageBtn = spaceAvailable(account) ? `<button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-account="manage" aria-expanded="${state.manage || Boolean(state.confirm)}">Saved data options</button>` : '';   // next to Sign in / Sign out, not under it
  const main = signedIn
    ? `<p class="acct-note acct-state" role="status">Signed in as ${esc(account)}. Your routing setup and staged changes are saved across devices.</p><div class="acct-actions"><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-account="signout"${state.busy ? ' disabled' : ''}>${walletLogoHtml(sessionWallet(account) || kind)}Sign out</button>${manageBtn}</div>`
    : `<p class="acct-note">Save your routing setup and staged changes across devices. One confirmation in your wallet, no transaction, no cost.</p>${walletChoice}<div class="acct-actions"><button type="button" class="ceres-btn ceres-btn--primary" data-control-density="comfortable" data-account="player0014"${state.busy ? ' disabled' : ''}>Sign in</button>${manageBtn}</div>${waiting}`;
  const deleteControls = state.confirm
    ? `<p class="acct-note acct-warn" role="alert">${state.confirm === 'space' ? 'This permanently deletes your saved routing setup, staged changes and their older versions from the Ceres server. Copies on your own devices stay.' : 'This deletes the game data Ceres cached for this account. It is fetched again the next time you open the board. Your saved setup stays.'} Your wallet will ask you to sign a short message.</p><div class="acct-row"><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-case="destructive" data-account="delete-confirm"${state.busy ? ' disabled' : ''}>${state.confirm === 'space' ? 'Delete saved data' : 'Delete cached data'}</button><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-account="delete-cancel">Cancel</button></div>`
    : `<div class="acct-delete-actions"><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-case="destructive" data-account="delete-space">Delete saved data…</button><button type="button" class="ceres-btn ceres-btn--secondary" data-control-density="comfortable" data-control-case="destructive" data-account="delete-cache">Delete cached data…</button></div>`;
  const manage = state.manage || state.confirm ? `<div class="acct-manage">${deleteControls}</div>` : '';
  return `${main}${!signedIn && state.note ? `<p class="acct-note acct-warn" role="status">${esc(state.note)}</p>` : ''}${state.done ? `<p class="acct-note acct-state" role="status">${esc(state.done)}</p>` : ''}${manage}<p class="acct-note acct-privacy"><a href="/static/privacy.html" target="_blank" rel="noopener">What Ceres keeps about you</a></p>`;
}

function place() {
  if (!panel || !button) return;
  const box = button.getBoundingClientRect();
  panel.style.insetBlockStart = `${Math.round(box.bottom + 6)}px`;
  const left = Math.max(8, Math.round(Math.min(box.left + box.width / 2 - panel.offsetWidth + 28, window.innerWidth - panel.offsetWidth - 8)));
  panel.style.insetInlineStart = `${left}px`;
  panel.style.setProperty('--acct-arrow-x', `${Math.round(box.left + box.width / 2 - left)}px`);
}

function render() {
  if (!panel) return;
  panel.hidden = !state.open;
  button?.setAttribute('aria-expanded', String(state.open));
  if (button) {
    if (state.open) { if (button.dataset.tip) { button.dataset.tipHeld = button.dataset.tip; button.dataset.tipBodyHeld = button.dataset.tipBody || ''; } button.dataset.tip = ''; button.removeAttribute('data-tip-body'); }
    else if (button.dataset.tipHeld) { button.dataset.tip = button.dataset.tipHeld; button.dataset.tipBody = button.dataset.tipBodyHeld; delete button.dataset.tipHeld; delete button.dataset.tipBodyHeld; }
  }
  button?.classList.toggle('is-signed-in', hasSession(walletAccount()));
  if (button) {
    const logo = walletLogoHtml(selectedWallet(walletAccount()));
    if (button.dataset.logo !== logo) { button.innerHTML = logo; button.dataset.logo = logo; }
  }
  if (!state.open) return;
  panel.innerHTML = accountMenuHtml();
  place();
}

function close() { if (state.open) { state.open = false; render(); button?.focus?.({ preventScroll: true }); } }

async function player0014() {
  const account = walletAccount();
  if (!account || state.busy) return;
  const kind = selectedWallet(account);
  if (walletKind(account) !== kind) selectAccountWallet(account, kind);
  if (!spaceAvailable(account)) { state.note = NOTES.unavailable; render(); return; }
  const attempt = ++state.attempt;
  state.busy = true; state.loginKind = kind; state.note = ''; state.done = ''; render();
  const result = await connect(account, { shouldContinue: () => state.attempt === attempt, wallet: kind });
  if (state.attempt !== attempt) return;
  state.busy = false;
  if (!result.ok) state.note = NOTES[result.reason] || NOTES.unavailable;
  render();
}

function cancelSignIn() {
  ++state.attempt;
  state.busy = false;
  state.note = NOTES.declined;
  cancelAccountWalletRequest(walletAccount());
  render();
}

async function deleteNow() {
  const account = walletAccount();
  const scope = state.confirm;
  if (!account || !scope || state.busy) return;
  state.busy = true; state.note = ''; state.done = ''; render();
  const result = await deleteSaved(account, scope);
  state.busy = false;
  if (result.ok) {
    state.confirm = null;
    state.done = scope === 'space'
      ? `Deleted ${result.documents} saved file${result.documents === 1 ? '' : 's'} from the server.${result.kept_safety_records ? ' A routing transaction with an unknown result is still remembered, so it cannot be signed twice.' : ''}`
      : `Deleted ${result.files} cached file${result.files === 1 ? '' : 's'}.`;
    window.dispatchEvent(new CustomEvent('ceres:space-changed', { detail: { account } }));
  } else state.note = NOTES[result.reason] || NOTES.unavailable;
  render();
}

async function signOutNow() {
  const account = walletAccount();
  if (!account) return;
  state.note = '';
  await disconnect(account);
  render();
}

export function mountAccountMenu() {
  button = document.getElementById('account-menu-btn');
  if (!button || panel) return;
  panel = document.createElement('div');
  panel.id = 'account-menu'; panel.className = 'account-menu'; panel.setAttribute('role', 'group'); panel.setAttribute('aria-label', 'Account'); panel.hidden = true;
  document.body.appendChild(panel);
  button.addEventListener('click', (event) => { event.stopPropagation(); state.open = !state.open; render(); });
  panel.addEventListener('click', (event) => {
    event.stopPropagation();   // rerendering detaches the clicked node before the document outside-click listener runs.
    const wallet = event.target.closest('[data-account-wallet]');
    if (wallet && !state.busy) { selectAccountWallet(walletAccount(), wallet.dataset.accountWallet); state.note = ''; render(); return; }
    const action = event.target.closest('[data-account]');
    if (!action || action.disabled) return;
    if (action.dataset.account === 'player0014') player0014();
    else if (action.dataset.account === 'pair-again') pairAccountWalletAgain(walletAccount());
    else if (action.dataset.account === 'cancel') cancelSignIn();
    else if (action.dataset.account === 'signout') signOutNow();
    else if (action.dataset.account === 'manage') { state.manage = !state.manage; state.confirm = null; render(); }
    else if (action.dataset.account === 'delete-space' || action.dataset.account === 'delete-cache') { state.confirm = action.dataset.account.slice(7); state.note = ''; state.done = ''; render(); }
    else if (action.dataset.account === 'delete-cancel') { state.confirm = null; render(); }
    else if (action.dataset.account === 'delete-confirm') deleteNow();
  });
  document.addEventListener('click', (event) => { if (state.open && !panel.contains(event.target) && !button.contains(event.target)) close(); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && state.open && !event.defaultPrevented) { event.preventDefault(); close(); } });
  window.addEventListener('ceres:wallet-changed', render);
  window.addEventListener('ceres:space-changed', render);
  window.addEventListener('resize', place);
  render();
}
