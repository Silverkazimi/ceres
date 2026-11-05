"""First-review identity survives changed economic terms and remains action-wide."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from ceres import routes_confirm as confirm
from tests.ceres.vm_context import run_in_module_context


class Request:
    def __init__(self, root, body):
        self.app = SimpleNamespace(state=SimpleNamespace(ceres_config=SimpleNamespace(root=root)))
        self.body = body

    async def json(self):
        return self.body


def ops(nonce, price):
    return [["custom_json", {"id": "sm_land_operation", "json": json.dumps({"op": "buy", "n": nonce, "price": price}),
                              "required_posting_auths": ["alice"], "required_auths": []}]]


def review_ops(price):
    return [["custom_json", {"id": "sm_land_operation", "json": json.dumps({"op": "buy", "price": price}),
                              "required_posting_auths": ["alice"], "required_auths": []}]]


def test_review_revision_repeat_begin_and_old_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(confirm, "_require_same_origin", lambda request: None)
    monkeypatch.setattr(confirm, "player_for_request", lambda request, player: player)
    monkeypatch.setattr(confirm.client_limits, "limit", lambda *args: None)
    def register(price, action_id=None, repeat=False):
        body = {"operations": review_ops(price), "repeat": repeat}
        if action_id:
            body["action_id"] = action_id
        return asyncio.run(confirm.register_review(Request(tmp_path, body), "alice"))
    first = register(1)
    assert first["revision"] == 1 and len(first["action_id"]) == 32
    revised = register(2, first["action_id"])
    assert revised == {"action_id": first["action_id"], "revision": 2}
    assert register(2, repeat=True)["action_id"] != first["action_id"]
    old = ops("abcdefghij", 1)
    new = ops("klmnopqrst", 2)
    a, b = "a" * 16, "b" * 16
    request = Request(tmp_path, {})
    begun = confirm.ledger_update(request, "alice", old,
        lambda row: confirm._apply_event(row, {"operations": old}, 200, "begin", a),
        action_id=first["action_id"], register_attempt=a, attempt=a)
    assert begun["action_id"] == first["action_id"] and begun["revision"] == 1
    assert register(3, first["action_id"])["revision"] == 3
    begun = confirm.ledger_update(request, "alice", new,
        lambda row: confirm._apply_event(row, {"operations": new}, 201, "begin", b),
        action_id=first["action_id"], register_attempt=b, attempt=b)
    assert begun["revision"] == 2
    from datetime import datetime, timezone
    row = [1, {"timestamp": datetime.fromtimestamp(202, timezone.utc).isoformat(),
               "trx_id": "1" * 40, "op": old[0]}]
    found = confirm.retain_candidates(request, "alice", new, b, [row], action_id=first["action_id"])
    assert found["found"] == "1" * 40
    assert found["matches"][0]["exact"] == [1]
    assert confirm.ledger_attempt(request, "alice", b)["action_match"] is True
    ledger = json.loads((tmp_path / "confirm_ledger.json").read_text())
    action = ledger[f"alice:action:{first['action_id']}"]
    assert action["revision"] == 3 and json.loads(action["revisions"][0]["review_operations"][0][1]["json"]) == {"op": "buy", "price": 1}
    assert not action["attempts"].get("c" * 16)


def test_real_operations_preview_requote_keeps_registered_id():
    static = Path(__file__).parents[2] / "ceres" / "static"
    run_in_module_context(r"""
const id = 'a'.repeat(32);
const calls = [];
const begins = [];
fetch = async (url, init) => {
  if (url.includes('/api/confirm/review')) {
    const body = JSON.parse(init.body); calls.push(body);
    return { ok: true, status: 200, json: async () => ({ action_id: id, revision: calls.length }) };
  }
  if (url.includes('/api/confirm/intent')) {
    const body = JSON.parse(init.body); begins.push(body);
    return { ok: true, status: 200, json: async () => ({ action_id: body.action_id, revision: 2, begin_token: 'c'.repeat(32), server_now: Date.now() }) };
  }
  if (url.includes('/api/op-preview')) {
    const price = calls.length ? 2 : 1;
    return { ok: true, status: 200, json: async () => ({ account: 'alice', chunks: [{ operations: [
      ['custom_json', { id: 'sm_land_operation', json: JSON.stringify({ op: 'buy', price, n: price ? 'abcdefghij' : 'klmnopqrst' }), required_auths: [], required_posting_auths: ['alice'] }]
    ] }] }) };
  }
  return { ok: true, status: 200, json: async () => ({}) };
};
(async () => {
  await requestOperationPreview({ scope: 'portfolio' });
  const first = CERES_PREVIEWS.get(CERES_PORTFOLIO_KEY).actionIds[0];
  await requestOperationPreview({ scope: 'portfolio' }, [first]);
  const second = CERES_PREVIEWS.get(CERES_PORTFOLIO_KEY);
  if (calls.length !== 2 || calls[1].action_id !== id || second.actionIds[0] !== id)
    throw Error('re-quote lost the first review action id');
  beginAttempt('alice', second.payload.chunks[0].operations, { actionId: second.actionIds[0] });
  if (!await announceBegin('alice', second.payload.chunks[0].operations) || begins[0].action_id !== id)
    throw Error('the real preview adapter lost its action id at begin');
})().then(() => process.exit(0), e => { console.error(e); process.exit(1); });
""", entry=static / "operations.js")


def test_review_has_no_attempt_or_scan_and_rejects_foreign_account(tmp_path, monkeypatch):
    import pytest
    from fastapi import HTTPException
    monkeypatch.setattr(confirm, "_require_same_origin", lambda request: None)
    monkeypatch.setattr(confirm, "player_for_request", lambda request, player: player)
    monkeypatch.setattr(confirm.client_limits, "limit", lambda *args: None)
    first = asyncio.run(confirm.register_review(Request(tmp_path, {"operations": review_ops(4)}), "alice"))
    ledger = json.loads((tmp_path / "confirm_ledger.json").read_text())
    row = ledger[f"alice:action:{first['action_id']}"]
    assert row["attempts"] == {} and row["windows"] == 0 and row["reads"] == 0
    with pytest.raises(HTTPException) as foreign:
        asyncio.run(confirm.register_review(Request(tmp_path, {"operations": [["custom_json", {
            **review_ops(5)[0][1], "required_posting_auths": ["bob"]}]], "action_id": first["action_id"]}), "bob"))
    assert foreign.value.status_code == 409
    with pytest.raises(HTTPException) as nonce:
        asyncio.run(confirm.register_review(Request(tmp_path, {"operations": ops("abcdefghij", 4)}), "alice"))
    assert nonce.value.status_code == 422


def test_client_review_storage_repeat_and_failure_fallback():
    static = Path(__file__).parents[2] / "ceres" / "static"
    run_in_module_context(r"""
const requests = [];
let fail = false;
fetch = async (_url, init) => {
  const body = JSON.parse(init.body); requests.push(body);
  if (fail) throw Error('offline');
  return { ok: true, status: 200, json: async () => ({ action_id: body.repeat ? 'b'.repeat(32) : 'a'.repeat(32), revision: requests.length }) };
};
const op = price => [['custom_json', { id: 'sm_land_operation', json: JSON.stringify({ op: 'buy', price, n: 'abcdefghij' }), required_auths: [], required_posting_auths: ['alice'] }]];
(async () => {
  const first = await registerReview('alice', op(1), { reviewKey: 'market:cart1' });
  const revised = await registerReview('alice', op(2), { reviewKey: 'market:cart1' });
  if (first.actionId !== revised.actionId || requests[1].action_id !== first.actionId || requests[1].operations[0][1].json.includes('abcdefghij'))
    throw Error('stored lineage or nonce stripping failed');
  const repeat = await registerReview('alice', op(2), { reviewKey: 'market:cart1', repeat: true });
  if (repeat.actionId === first.actionId || !requests[2].repeat || requests[2].action_id) throw Error('repeat reused the old action');
  beginAttempt('alice', op(2), { actionId: repeat.actionId });
  markConfirmation('alice', op(2), 'confirmed');
  const next = await registerReview('alice', op(2), { reviewKey: 'market:cart1' });
  if (!requests[3].repeat || requests[3].action_id || next.actionId !== repeat.actionId) throw Error('conclusive repeat flag missing');
  fail = true;
  const fallback = await registerReview('alice', op(3), { reviewKey: 'market:cart1' });
  if (fallback.actionId !== null) throw Error('failed registration must use begin-time minting');
})().then(() => process.exit(0), e => { console.error(e); process.exit(1); });
""", entry=static / "shared-confirmer.js")


def test_two_tabs_join_review_key_and_repeat_breaks_lineage(tmp_path, monkeypatch):
    monkeypatch.setattr(confirm, "_require_same_origin", lambda request: None)
    monkeypatch.setattr(confirm, "player_for_request", lambda request, player: player)
    monkeypatch.setattr(confirm.client_limits, "limit", lambda *args: None)
    key = "d" * 16
    first = asyncio.run(confirm.register_review(Request(tmp_path, {"operations": review_ops(1), "review_key": key}), "alice"))
    other_tab = asyncio.run(confirm.register_review(Request(tmp_path, {"operations": review_ops(2), "review_key": key}), "alice"))
    assert other_tab == {"action_id": first["action_id"], "revision": 2}
    repeated = asyncio.run(confirm.register_review(Request(tmp_path, {"operations": review_ops(2), "review_key": key, "repeat": True}), "alice"))
    assert repeated["action_id"] != first["action_id"] and repeated["revision"] == 1


def test_market_first_review_terms_are_nonce_free_and_read_no_chain(tmp_path, monkeypatch):
    from ceres import market_purchase as market
    monkeypatch.setattr(confirm, "_require_same_origin", lambda request: None)
    monkeypatch.setattr(confirm, "player_for_request", lambda request, player: player)
    monkeypatch.setattr(confirm.client_limits, "limit", lambda *args: None)
    monkeypatch.setattr(market.QUOTES, "get", lambda quote_id, account: {"quote": quote_id, "account": account})
    monkeypatch.setattr(market, "build_transactions", lambda account, quote, currency: [
        {"lkeys": ["listing-1"], "operations": ops("abcdefghij", 7)}])
    result = asyncio.run(confirm.market_review_terms(Request(tmp_path, {"quote_id": "q", "currency": "DEC"}), "alice"))
    assert result["transactions"][0]["lkeys"] == ["listing-1"]
    assert json.loads(result["transactions"][0]["operations"][0][1]["json"]) == {"op": "buy", "price": 7}
    assert not (tmp_path / "confirm_ledger.json").exists()


def test_real_market_review_price_change_reaches_begin():
    static = Path(__file__).parents[2] / "ceres" / "static"
    run_in_module_context(r"""
const id = 'a'.repeat(32);
const reviews = [];
const begins = [];
let price = 1;
const signed = p => [['custom_json', { id: 'sm_market_purchase', json: JSON.stringify({ currency: 'DEC', items: ['market-id'], price: p, app: 'ceres/test', n: 'abcdefghij' }), required_auths: ['alice'], required_posting_auths: [] }]];
fetch = async (url, init) => {
  if (url.includes('/api/confirm/market-terms')) return { ok: true, status: 200, json: async () => ({ transactions: [{ lkeys: ['listing-1'], operations: signed(price) }] }) };
  const body = JSON.parse(init.body);
  if (url.includes('/api/confirm/review')) { reviews.push(body); return { ok: true, status: 200, json: async () => ({ action_id: id, revision: reviews.length }) }; }
  if (url.includes('/api/confirm/intent')) { begins.push(body); return { ok: true, status: 200, json: async () => ({ action_id: id, revision: 2, begin_token: 'c'.repeat(32), server_now: Date.now() }) }; }
  throw Error(url);
};
(async () => {
  co.player = 'alice'; co.items = [{ lkey: 'listing-1' }]; co.quote = { quote_id: 'q' }; co.currency = 'DEC';
  await registerMarketQuoteReview();
  price = 2;
  await registerMarketQuoteReview();
  if (reviews.length !== 2 || reviews[1].action_id !== id || co.reviewActionIds[0] !== id) throw Error('market price re-quote lost lineage');
  const operations = signed(2);
  beginAttempt('alice', operations, { actionId: co.reviewActionIds[0] });
  if (!await announceBegin('alice', operations) || begins[0].action_id !== id) throw Error('market handoff lost lineage');
})().then(() => process.exit(0), e => { console.error(e); process.exit(1); });
""", entry=static / "card-checkout.js")
