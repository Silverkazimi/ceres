""" stuck states (; : "This is a failure and must never be like this").

Every signed action ends green or red, or says clearly "not found" with a way out -- never pending forever:
1. regional DEC: an unknown transaction ends as "Transaction not found" with Dismiss once the claims window has passed;
2. deed lock: a signed lock is looked up by its transaction id -- refused clears it, accepted waits for the deed state,
   unknown ends as "not found" with Dismiss, no answer offers Check again;
3. wallet-LP: a swap paid outside the game (SPS withdrawal / SPS pool swap) waits for the pool instead of "done", the
   pool settlement is read only on the maintainer's Check again, and a quote is never shown as a received amount.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ceres import routes_dec, routes_plot_lock, routes_wallet_lp as lp_routes
from ceres.app import create_app
from ceres.config import CeresConfig
from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"
TX = "a" * 40


class _Transport:
    def __init__(self, data):
        self._data = data

    def __call__(self, *args, **kwargs):
        return self

    def get_json(self, url):
        return SimpleNamespace(data=self._data)


# 1. regional DEC

def test_dec_verify_reports_an_unknown_transaction_as_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(routes_dec, "FixedGetTransport", _Transport({"trx_info": None}))
    app = create_app(CeresConfig(root=tmp_path, player="player0005", card_art_cache_enabled=False))
    with TestClient(app) as client:
        response = client.post("/api/dec/verify", json={"region_uid": "PR-BRL-41", "action": "stake", "amount": "1",
                                                        "tx_id": TX, "nonce": "b" * 16})
    assert response.status_code == 200 and response.json() == {"status": "not_found"}


class _GameSaysNotFound:
    """The game's real answer for an unknown id (measured 2026-09-29): HTTP 200 {"error": "Transaction [..] not found."},
    which the Praetoria transport raises as an UpstreamError."""
    def __call__(self, *args, **kwargs):
        return self

    def get_json(self, url):
        from praetoria.errors import UpstreamError
        raise UpstreamError(f"Splinterlands API body error: Transaction [{TX}] not found.")


def test_the_games_not_found_answer_is_not_found_not_an_outage(monkeypatch, tmp_path):
    from ceres import routes_wallet
    monkeypatch.setattr(routes_dec, "FixedGetTransport", _GameSaysNotFound())
    monkeypatch.setattr(routes_wallet, "FixedGetTransport", _GameSaysNotFound())
    monkeypatch.setattr(routes_plot_lock, "FixedGetTransport", _GameSaysNotFound())
    app = create_app(CeresConfig(root=tmp_path, player="player0005", card_art_cache_enabled=False))
    with TestClient(app) as client:
        dec = client.post("/api/dec/verify", json={"region_uid": "PR-BRL-41", "action": "stake", "amount": "1", "tx_id": TX, "nonce": "b" * 16})
        claim = client.post("/api/wallet/claim/verify", json={"tx_id": TX, "nonce": "b" * 16})
        lock = client.post("/api/plot-lock/verify", json={"tx_id": TX, "deed_uid": "I-1-abc", "action": "lock"})
    assert (dec.status_code, dec.json()) == (200, {"status": "not_found"})
    assert (claim.status_code, claim.json()) == (200, {"status": "not_found"})
    assert (lock.status_code, lock.json()) == (200, {"status": "not_found"})


def test_any_other_game_error_is_still_an_outage():
    from praetoria.errors import UpstreamError
    from ceres.game_lookup import lookup_trx_info

    def broken(url):
        raise UpstreamError("HTTP 503 from Splinterlands API")
    with pytest.raises(UpstreamError):
        lookup_trx_info(broken, TX)


_REGIONAL_CONTEXT = r"""
window.location.search = '?player=alice';
window.location.href = 'http://127.0.0.1/?player=alice';
document.documentElement.classList = { contains: className => className === 'ceres-lw-le-1279' };
window.matchMedia = () => ({ matches: true, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
const __regionalData = { player: 'alice', balance: 100, regions: [{ region_uid: '1-PNW-1', staked: 0, required: 10, shortfall: 10, surplus: 0 }] };
context.CURRENT_PLAYER = 'alice';
context.BOARD = { player: 'alice', plots: [] };
context.__verifyCalls = 0;
context.fetch = url => {
  if (String(url).includes('/verify')) { context.__verifyCalls += 1; return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'not_found' }) }); }
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(__regionalData) });
};
context.setTimeout = (fn) => { fn(); return 0; };
const __store = new Map();
context.localStorage = { getItem: k => (__store.has(k) ? __store.get(k) : null), setItem: (k, v) => __store.set(k, String(v)), removeItem: k => __store.delete(k) };
context.window.localStorage = context.localStorage;
context.__store = __store;
const __regionalTrigger = { innerHTML: '', addEventListener() {}, focus() {} };
const __regionalPanel = { innerHTML: '', querySelector() { return null; } };
document.body = { append(node) { context.__regionalDrawer = node; } };
document.getElementById = id => id === 'regional-dec-button' ? __regionalTrigger : null;
document.createElement = tag => {
  const node = {
    innerHTML: '', open: false, id: '', className: '', dataset: {}, style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute() {}, addEventListener() {}, removeEventListener() {}, append() {}, appendChild() {},
    contains() { return false; }, querySelector() { return null; }, querySelectorAll() { return []; },
    showModal() { this.open = true; }, show() { this.open = true; }, close() { this.open = false; },
    animate() { return { finished: Promise.resolve() }; }, focus() {},
  };
  if (tag === 'dialog') {
    node.__panel = __regionalPanel;
    node.querySelector = selector => selector === '.dec-drawer-panel' ? node.__panel : null;
  }
  return node;
};
context.__regionalDrawer = null;
"""


def test_dec_not_found_is_no_longer_a_25_second_end():
    """ replaces this  rule: a lagging game read as "never broadcast" after 25 s; the shared confirmer now decides from the game and the chain."""
    dec = (STATIC / "regional-dec.js").read_text(encoding="utf-8")
    assert "decNotFoundFinal" not in dec and "DEC_NOT_FOUND_AFTER_MS" not in dec
    assert "confirmAction({ account: player, operations: intent.ops" in dec and "if (D.pending) check();" in dec
    assert "'discard'" not in dec and "dec-transaction" not in dec and "Check again" not in dec
    # continuation 4 (manager, IN-0706): the Dismiss exit is gone; an unprovable entry blocks nothing instead.
    assert "'dismiss'" not in dec and "'Dismiss'" not in dec and "export function decUnprovable" in dec


def test_dec_unknown_transaction_keeps_confirming():
    """ replaces this  rule: an unknown DEC transaction ended as "not found"; it is now confirmed until the game answers or Hive proves it absent."""
    dec = (STATIC / "regional-dec.js").read_text(encoding="utf-8")
    assert "decNotFoundFinal" not in dec and "DEC_NOT_FOUND_AFTER_MS" not in dec
    assert "confirmAction({ account: player, operations: intent.ops" in dec and "if (D.pending) check();" in dec
    assert "'discard'" not in dec and "dec-transaction" not in dec and "Check again" not in dec
    # continuation 4 (manager, IN-0706): the Dismiss exit is gone; an unprovable entry blocks nothing instead.
    assert "'dismiss'" not in dec and "'Dismiss'" not in dec and "export function decUnprovable" in dec


def test_a_fresh_unknown_dec_transaction_is_not_ended_early():
    run_in_module_context(r"""
(async () => {
  initRegionalDec();
  const fresh = { region_uid: '1-PNW-1', action: 'stake', amount: '1', nonce: 'c'.repeat(16), status: 'pending', tx_id: 'a'.repeat(40), signed_at: Date.now() };
  __store.set('ceres:regional-dec:pending:alice', JSON.stringify(fresh));
  await openWalletDrawer('DEC');
  if (D.pending?.status !== 'pending') throw Error('inside the window it keeps its Check again state, got ' + D.pending?.status);
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=STATIC / "regional-dec.js", extra_context_js=_REGIONAL_CONTEXT)


def test_dec_change_without_id_is_found_not_discarded():
    """ replaces this  rule: a change without id could be discarded at once; the own-history finder finds it, and the one expiry read (CONFIRM_EXPIRY_RECHECK) ends it confirmed or not sent."""
    dec = (STATIC / "regional-dec.js").read_text(encoding="utf-8")
    assert "decNotFoundFinal" not in dec and "DEC_NOT_FOUND_AFTER_MS" not in dec
    assert "confirmAction({ account: player, operations: intent.ops" in dec and "if (D.pending) check();" in dec
    assert "'discard'" not in dec and "dec-transaction" not in dec and "Check again" not in dec
    # continuation 4 (manager, IN-0706): the Dismiss exit is gone; an unprovable entry blocks nothing instead.
    assert "'dismiss'" not in dec and "'Dismiss'" not in dec and "export function decUnprovable" in dec


def test_dec_unprovable_entry_has_no_button_and_blocks_nothing():
    """IN-0706 (continuation 4): an entry saved before  with neither id nor operations cannot be read at all. It used
    to wait for a Dismiss press; now its receipt keeps an honest line, shows no button, and Stake stays available."""
    result = run_in_module_context(r"""
(async () => {
  initRegionalDec();
  const legacy = { region_uid: '1-PNW-1', action: 'stake', amount: '1', nonce: 'd'.repeat(16), status: 'pending', asked_at: Date.now() - 3 * 3600 * 1000 };
  __store.set('ceres:regional-dec:pending:alice', JSON.stringify(legacy));
  await openWalletDrawer('DEC');
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
  const html = globalThis.__regionalDrawer.__panel.innerHTML;
  if (/data-dec="dismiss"|>Dismiss</.test(html)) throw Error('a Dismiss control is still rendered');
  if (!/cannot prove whether this change was sent/.test(html) || !/dec-unprovable/.test(html)) throw Error('no honest unprovable line: ' + html.slice(0, 400));
  const stake = html.match(/<button[^>]*data-dec="stake"[^>]*>/);
  if (!stake || / disabled/.test(stake[0])) throw Error('Stake is blocked by an unprovable entry: ' + (stake && stake[0]));
  console.log('IR9545_DEC_UNPROVABLE_OK');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=STATIC / "regional-dec.js", extra_context_js=_REGIONAL_CONTEXT)
    assert "IR9545_DEC_UNPROVABLE_OK" in result.stdout   # a pending promise exits 0 too; the marker proves the checks ran


# 2. deed lock

_LOCK = {"id": TX, "player": "alice", "type": "lock_assets", "data": '{"type":"item","asset_ids":["I-1-abc"],"lock_days":3}'}


def test_lock_result_reads_the_game_verdict():
    assert routes_plot_lock.lock_result(None, "alice", TX, "I-1-abc", "lock") == {"status": "not_found"}
    assert routes_plot_lock.lock_result({**_LOCK, "success": True}, "alice", TX, "I-1-abc", "lock") == {"status": "accepted"}
    refused = routes_plot_lock.lock_result({**_LOCK, "success": False, "error": "Deed is staked"}, "alice", TX, "I-1-abc", "lock")
    assert refused == {"status": "failed", "message": "Deed is staked"}
    with pytest.raises(ValueError):
        routes_plot_lock.lock_result({**_LOCK, "success": True}, "alice", TX, "I-1-other", "lock")
    with pytest.raises(ValueError):
        routes_plot_lock.lock_result({**_LOCK, "success": True}, "alice", TX, "I-1-abc", "unlock")


def test_lock_verify_route_answers_by_transaction_id(monkeypatch, tmp_path):
    monkeypatch.setattr(routes_plot_lock, "FixedGetTransport", _Transport({"trx_info": {**_LOCK, "player": "player0005", "success": False, "error": "Deed is staked"}}))
    app = create_app(CeresConfig(root=tmp_path, player="player0005", card_art_cache_enabled=False))
    with TestClient(app) as client:
        response = client.post("/api/plot-lock/verify", json={"tx_id": TX, "deed_uid": "I-1-abc", "action": "lock"})
        assert response.status_code == 200 and response.json() == {"status": "failed", "message": "Deed is staked"}
        assert client.post("/api/plot-lock/verify", json={"tx_id": "x", "deed_uid": "I-1-abc", "action": "lock"}).status_code == 422


_LOCK_JS = r"""
const __events = [];
window.dispatchEvent = (event) => { __events.push(event.detail || null); return true; };
context.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init?.detail; } };
const __store = new Map();
context.localStorage = { getItem: k => (__store.has(k) ? __store.get(k) : null), setItem: (k, v) => __store.set(k, String(v)), removeItem: k => __store.delete(k) };
context.__events = __events;
"""


def _lock_run(body: str) -> None:
    run_in_module_context(r"""
(async () => {
  reconcilePlotLockPlans({ player: 'alice', plots: [] });
  const t0 = 1_000_000; let clock = t0;
  const sleep = async (ms) => { clock += ms; };
  const now = () => clock;
  STAGED_PLOT_LOCKS.set('I-1-abc', { deed_uid: 'I-1-abc', action: 'lock', baselineDays: null, baselineUnlockDate: null, lockDays: 3, txId: 'a'.repeat(40), signedAt: t0 });
  const answer = (status, extra = {}) => async () => ({ ok: true, json: async () => ({ status, ...extra }) });
""" + body + r"""
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=STATIC / "plot-lock.js", extra_context_js=_LOCK_JS)


def test_a_refused_lock_is_cleared_and_says_why():
    _lock_run(r"""
  const result = await verifySignedPlotLock('I-1-abc', { fetchImpl: answer('failed', { message: 'Deed is staked' }), sleep, now });
  if (result !== 'failed') throw Error('expected failed, got ' + result);
  if (STAGED_PLOT_LOCKS.has('I-1-abc')) throw Error('a refused lock must be cleared so the button works again');
  if (!__events.some(e => e?.status === 'failed' && e.message === 'Deed is staked')) throw Error('the reason must be announced');
""")


def test_an_accepted_lock_waits_for_the_live_deed_state():
    _lock_run(r"""
  const result = await verifySignedPlotLock('I-1-abc', { fetchImpl: answer('accepted'), sleep, now });
  if (result !== 'accepted' || STAGED_PLOT_LOCKS.get('I-1-abc')?.accepted !== true) throw Error('accepted must be kept, waiting');
  if (signedPlotLockText(STAGED_PLOT_LOCKS.get('I-1-abc')) !== 'Accepted — waiting for the live deed state') throw Error('wording');
""")


def test_an_unknown_lock_ends_as_not_found_and_can_be_dismissed():
    _lock_run(r"""
  const result = await verifySignedPlotLock('I-1-abc', { fetchImpl: answer('not_found'), sleep, now });
  if (result !== 'not_found' || STAGED_PLOT_LOCKS.get('I-1-abc')?.notFound !== true) throw Error('unknown must end as not found, got ' + result);
  if (!dismissPlotLock('I-1-abc') || STAGED_PLOT_LOCKS.has('I-1-abc')) throw Error('Dismiss must clear it');
""")


def test_a_lock_without_any_answer_offers_check_again():
    _lock_run(r"""
  const result = await verifySignedPlotLock('I-1-abc', { fetchImpl: async () => { throw new Error('offline'); }, sleep, now });
  if (result !== 'paused' || STAGED_PLOT_LOCKS.get('I-1-abc')?.checkPaused !== true) throw Error('no answer must pause with Check again, got ' + result);
  if (dismissPlotLock('I-1-abc')) throw Error('only a not-found lock is dismissed');
""")


# 3. wallet-LP settlement

def test_settlement_view_and_step():
    step = lp_routes.settlement_step(TX, "swap_sell", "SPS", {"external_swap_required": True}, True, None)
    assert step["funding"] == {"state": "funded"} and step["review"]["step"]["quote"]["destination_symbol"] == "DEC"
    assert lp_routes.settlement_step(TX, "swap_buy", "SPS", {}, False)["review"]["step"]["quote"]["destination_symbol"] == "SPS"
    assert lp_routes.settlement_view({"state": "settling", "bridge_next_start": 41})["status"] == "settling"
    view = lp_routes.settlement_view({"state": "confirmed", "bridge_transaction_id": "b" * 40,
                                      "receipt": {"received": [{"symbol": "DEC", "amount": "8.1"}]}})
    assert view == {"status": "settled", "bridge_tx_id": "b" * 40, "received": [{"symbol": "DEC", "amount": "8.1"}], "deposited": []}


def _lp_verify(answer: str, body: str) -> None:
    extra = r"""
const __verifyAnswer = """ + answer + r""";
context.__urls = [];
context.fetch = (url) => { context.__urls.push(String(url)); return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(__verifyAnswer) }); };
context.window.fetch = context.fetch;
context.setTimeout = (fn) => { fn(); return 0; };
"""
    run_in_module_context(r"""
(async () => {
  const st = swapState('alice', 'SPS');
  Object.assign(st, { stage: 'confirming', receipt: { kind: 'swap_buy', pay: 'DEC', receive: 'SPS', input: 10, output: 1.234, nonce: 'n'.repeat(16), tx_id: 'a'.repeat(40), payload: {}, twoStep: false } });
  const ctx = { player: 'alice', render() {}, reload: async () => {}, drawer: () => null, wallet: () => null, setSigning() {}, account: () => 'alice' };
  // no Resume control; the automatic check (as after a reload) is the path under test.
  resumeSwapChecks(ctx);
  for (let i = 0; i < 5000 && st.checking; i++) await Promise.resolve();
""" + body + r"""
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=STATIC / "wallet-lp.js", extra_context_js=extra)


def test_an_external_withdrawal_waits_for_the_pool_and_shows_no_quote():
    _lp_verify("{ status: 'confirmed', game_result: { external_withdrawal_required: true, external_withdrawal_amount: 1.2 } }", r"""
  if (st.stage !== 'settling') throw Error('an SPS withdrawal must wait for the pool, got ' + st.stage);
 if (st.receipt.output !== null) throw Error('the quote must not be shown as received');
 const html = swapHtml('SPS', {}, 'alice');
 if (!html.includes('Waiting for the pool to settle') || !html.includes('Amount confirmed when the pool settles')) throw Error('settling wording');
 // the settlement is read by itself through the shared scheduler; no Stop / Check again / Look further back.
 for (let i = 0; i < 5000 && !__urls.some(u => u.includes('/settle')); i++) await Promise.resolve();
 if (!__urls.some(u => u.includes('/settle'))) throw Error('the waiting swap must check the pool settlement by itself');
 if (/lp:settle|lp:stop|Check again|Stop checking|further back/.test(swapHtml('SPS', {}, 'alice'))) throw Error('a manual settle control is shown');
""")


def test_a_confirmed_swap_without_an_amount_never_shows_the_quote():
    _lp_verify("{ status: 'confirmed', game_result: {} }", r"""
  if (st.stage !== 'done') throw Error('expected done, got ' + st.stage);
  if (st.receipt.output !== null) throw Error('a missing amount must stay missing, not the quote: ' + st.receipt.output);
""")
