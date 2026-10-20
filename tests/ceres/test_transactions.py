import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from ceres import routes_harvest as routes
from ceres import harvest_evidence as evidence
from tests.ceres.vm_context import run_in_module_context, read_entry_source

ROOT = Path(__file__).resolve().parents[2]


def member(deed):
    return {"op": "tax_collection", "region_uid": "PR-LOP-61", "deed_uids": [deed], "nonce": deed}


def preview():
    return {"scope": {"kind": "taxes", "deed_uids": ["deed-one", "deed-two"]},
            "chunks": [{"members": [member(deed)], "operations": []} for deed in ["deed-one", "deed-two"]],
            "regions": [], "issues": [], "blocked": False}


def request(body, tmp_path):
    async def json():
        return body
    return SimpleNamespace(json=json, app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(root=tmp_path))))


@pytest.mark.parametrize("index", [0, 1])
def test_preflight_only_checks_current_chunk_and_ignores_accrual(monkeypatch, tmp_path, index):
    stored = preview()
    monkeypatch.setattr(routes, "_require_same_origin", lambda request: None)
    monkeypatch.setattr(routes, "player_for_request", lambda request, player: "alice")
    monkeypatch.setattr(routes, "_signing_or_403", lambda *args: None)
    monkeypatch.setattr(evidence, "load_preview", lambda *args: stored)
    def fresh(request, player, scope, fresh):
        assert scope == {"kind": "taxes", "deed_uids": [stored["scope"]["deed_uids"][index]]}
        result = deepcopy(stored)
        result["chunks"] = [deepcopy(stored["chunks"][index])]
        result["regions"] = [{"net_by_token": {"GRAIN": 999999}, "deeds": []}]
        result["chunks"][0]["members"][0]["nonce"] = "fresh-nonce"
        return result
    monkeypatch.setattr(routes, "_build_preview", fresh)
    result = asyncio.run(routes.harvest_preflight(request({"chunk_index": index}, tmp_path)))
    assert result["outcome"] == "PERMIT"
    assert result["chunk"] is stored["chunks"][index]


def test_preflight_still_rejects_changed_identity(monkeypatch, tmp_path):
    stored = preview()
    monkeypatch.setattr(routes, "_require_same_origin", lambda request: None)
    monkeypatch.setattr(routes, "player_for_request", lambda request, player: "alice")
    monkeypatch.setattr(routes, "_signing_or_403", lambda *args: None)
    monkeypatch.setattr(evidence, "load_preview", lambda *args: stored)
    changed = deepcopy(stored)
    changed["chunks"] = [{"members": [member("different-deed")]}]
    monkeypatch.setattr(routes, "_build_preview", lambda *args, **kwargs: changed)
    assert asyncio.run(routes.harvest_preflight(request({"chunk_index": 1}, tmp_path)))["outcome"] == "CHANGED"


def test_prepare_persists_all_attempts_before_wallet(monkeypatch, tmp_path):
    stored = evidence.save_preview("alice", preview())
    monkeypatch.setattr(routes, "_require_same_origin", lambda request: None)
    monkeypatch.setattr(routes, "player_for_request", lambda request, player: "alice")
    monkeypatch.setattr(routes, "_signing_or_403", lambda *args: None)
    monkeypatch.setattr(routes, "_config", lambda request: SimpleNamespace(root=tmp_path))
    body = {"preview_id": stored["preview_id"], "preview_digest": stored["preview_digest"], "attempts": [
        {"chunk_index": 0, "attempt_id": "attempt-0001"}, {"chunk_index": 1, "attempt_id": "attempt-0002"}]}
    result = asyncio.run(routes.harvest_prepare(request(body, tmp_path)))
    assert len(result["attempts"]) == 2
    assert all(evidence.load_attempt(tmp_path, "alice", item["attempt_id"])["state"] == "awaiting_keychain" for item in body["attempts"])
    assert evidence.load_attempt(tmp_path, "bob", "attempt-0001") is None
    body["attempts"][1]["chunk_index"] = 0
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.harvest_prepare(request(body, tmp_path)))
    assert exc.value.status_code == 422


def test_gate_opens_next_wallet_without_inclusion_requests():
    run_in_module_context(r"""
(async () => {
const handlers = [];
let reads = 0;
window.hive_keychain = { requestBroadcast(account, operations, key, callback) { handlers.push(callback); } };
fetch = async () => { reads++; return new Promise(() => {}); };
const ops = (n) => [['custom_json', {id:'sm_land_operation', json:JSON.stringify({n})}]];
const first = BroadcastGate.broadcast('alice', ops('one'));
await Promise.resolve();
if (handlers.length !== 1) throw Error('first wallet missing');
handlers[0]({success:true, result:{id:'tx-one'}});
const accepted = await first;
if (accepted.blockNum !== null) throw Error('unobserved inclusion was asserted');
const second = BroadcastGate.broadcast('alice', ops('two'));
await Promise.resolve();
if (handlers.length !== 2 || reads !== 0) throw Error('validation delayed next wallet');
handlers[1]({success:true, result:{id:'tx-two'}});
await second;
const duplicate = await BroadcastGate.broadcast('alice', ops('one')).catch(error => error);
if (!duplicate.duplicateBroadcast || handlers.length !== 2) throw Error('duplicate reached wallet');
})().then(() => process.exit(0), error => {console.error(error);process.exit(1);});
""", entry=ROOT / 'ceres/static/broadcast-gate.js', extra_context_js="context.navigator.locks = {request(_name, _options, callback) {return callback();}};")


def test_checkout_signs_every_chunk_before_verification():
    run_in_module_context(r"""
(async () => {
const events = [];
const target = {scope:'portfolio'};
const review = {payload:{account:'alice', chunks:[{operations:[]},{operations:[]},{operations:[]}]}, stagedSnapshot:[]};
CERES_PREVIEWS.set(previewKey(target), review);
walletReady = () => true;
freshReviewForSigning = async () => review;
renderConfirmationBanner = () => {};
setVerificationNotice = () => {};
prepareSigningBatch = async () => null;
requestSignatureThroughGate = async () => { events.push('wallet'); return {txId:'tx-'+events.length, blockNum:null, submitted:Promise.resolve()}; };
verifyBroadcast = async () => { events.push('verify'); throw Error('receipt read unavailable'); };
await confirmOperationBroadcast(target);
if (events.join(',') !== 'wallet,wallet,wallet,verify,verify,verify') throw Error(events.join(','));
if (review.broadcasting || CERES_BROADCAST_ACTIVE) throw Error('busy state leaked');
})().then(() => process.exit(0), error => {console.error(error);process.exit(1);});
""", entry=ROOT / 'ceres/static/operations.js')


@pytest.mark.parametrize("reject_second", [False, True])
@pytest.mark.parametrize("saved_routes", [False, True])
def test_harvest_prepares_then_signs_without_intervening_reads(reject_second, saved_routes):
    entry = ROOT / 'ceres/static/harvest-modal.js'
    source = read_entry_source(entry)
    source = source.rsplit('}', 1)[0] + """
Object.defineProperties(globalThis, {
 M: {get: () => M},
 openRouting: {get: () => openRouting, set: value => {openRouting = value;}},
 render: {get: () => render, set: value => {render = value;}},
 postJson: {get: () => postJson, set: value => {postJson = value;}}
});
}
"""
    run_in_module_context(r"""
(async () => {
const events = [];
const chunk = (n) => ({operations:[], members:[{nonce:n, deed_uids:[], op:'harvest_all', region_uid:'PR-LOP-'+n}]});
const pv = {signing_enabled:true, chunks:[chunk('1'),chunk('2'),chunk('3')], preview_id:'preview', preview_digest:'digest'};
Object.assign(M, {open:true, openId:1, gen:1, player:'alice', mode:'plot', scopeKind:'deeds', preview:pv, run:null, reviewing:null, deedUids:[], routingRunId:null});
if (SAVED_ROUTES) Object.assign(M, {mode:'batch', scopeKind:'all', selectedRegions:new Set()});
hasSavedHarvestRouting = () => SAVED_ROUTES;
openRouting = () => {events.push('routes');};
CURRENT_PLAYER = 'alice'; BOARD = {player:'alice', plots:[]};
walletReady = () => true; walletKind = () => 'keychain';
render = () => {};
let signed = 0;
let uuid = 0;
globalThis.crypto = {randomUUID:() => 'attempt000000000'+(++uuid)};
postJson = async (url, body) => {
 if (url.endsWith('/preflight')) {events.push('preflight');return {outcome:'PERMIT', chunk:pv.chunks[body.chunk_index]};}
 if (url.endsWith('/prepare')) {events.push('prepare');if(body.attempts.length !== 3) throw Error('incomplete preparation');return {};}
 if (url.endsWith('/attempt')) {events.push('receipt');return {};}
 throw Error('unexpected request '+url);
};
prepareSigningBatch = async () => null;
requestSignatureThroughGate = async () => {
 events.push('wallet');signed++;
 if (REJECT_SECOND && signed === 2) {const e=Error('User declined');e.broadcasted=false;throw e;}
 return {txId:'tx-'+signed, blockNum:null, submitted:Promise.resolve()};
};
verifyUntilFinal = async () => {events.push('verify');return 'success';};
await confirmHarvest();
const expected = REJECT_SECOND ? 'preflight,preflight,preflight,prepare,wallet,wallet' : 'preflight,preflight,preflight,prepare,wallet,wallet,wallet';
if (events.slice(0, expected.split(',').length).join(',') !== expected) throw Error(events.join(','));
if (M.run.busy) throw Error('busy state leaked');
if (REJECT_SECOND) {
 if (M.run.phase !== 'failed' || M.run.members[0].outcome === 'not_sent' || M.run.members[2].outcome !== 'not_sent') throw Error('partial receipt misclassified');
} else if (M.run.phase !== 'success') throw Error('batch not successful');
if (events.includes('routes')) throw Error('routing must wait for the results action');
})().then(() => process.exit(0), error => {console.error(error);process.exit(1);});
""".replace('REJECT_SECOND', str(reject_second).lower()).replace('SAVED_ROUTES', str(saved_routes).lower()), entry=entry, source=source)


def test_attempt_id_cannot_be_rebound_to_another_chunk(tmp_path):
    stored = evidence.save_preview('alice', preview())
    evidence.begin_attempt(tmp_path, 'alice', stored, 0, 'attempt-0001')
    with pytest.raises(ValueError, match='different harvest'):
        evidence.begin_attempt(tmp_path, 'alice', stored, 1, 'attempt-0001')


def test_late_receipt_update_cannot_erase_transaction_or_terminal_state(tmp_path):
    stored = evidence.save_preview('alice', preview())
    evidence.begin_attempt(tmp_path, 'alice', stored, 0, 'attempt-0001')
    evidence.update_attempt(tmp_path, 'alice', 'attempt-0001', tx_id='a' * 40, state='reconciled', members=[{'outcome': 'success'}])
    result = evidence.update_attempt(tmp_path, 'alice', 'attempt-0001', tx_id=None, state='submitted')
    assert result['tx_id'] == 'a' * 40 and result['state'] == 'reconciled'
    result = evidence.update_attempt(tmp_path, 'alice', 'attempt-0001', tx_id=None, state='rejected_before_send')
    assert result['tx_id'] == 'a' * 40 and result['state'] == 'reconciled'
    with pytest.raises(ValueError, match='different transaction'):
        evidence.update_attempt(tmp_path, 'alice', 'attempt-0001', tx_id='b' * 40)


def test_routing_prepares_all_attempts_as_one_batch(monkeypatch, tmp_path):
    from contextlib import contextmanager
    from ceres import harvest_routing_runs as runs
    stored = evidence.save_preview('alice', dict(preview(), routing_run_id='routing-run'))
    record = {'phase':'harvest', 'state':'ready_harvest', 'harvest_preview':stored, 'harvest_attempts':[]}
    @contextmanager
    def locked(*args):
        yield tmp_path
    monkeypatch.setattr(runs, 'locked', locked)
    monkeypatch.setattr(runs, 'read', lambda *args: record)
    monkeypatch.setattr(runs, 'write', lambda *args: None)
    monkeypatch.setattr(runs, 'harvest_attempts', lambda *args: [])
    attempts = [{'chunk_index':0, 'attempt_id':'attempt-0001'}, {'chunk_index':1, 'attempt_id':'attempt-0002'}]
    prepared = runs.prepare_harvest_batch(tmp_path, 'alice', stored, attempts)
    assert len(prepared) == 2 and record['harvest_attempts'] == attempts
    assert all(item['state'] == 'awaiting_keychain' for item in prepared)
    record['state'] = 'cancelled'
    with pytest.raises(runs.RoutingError):
        runs.prepare_harvest_batch(tmp_path, 'alice', stored, attempts)


def test_harvest_verification_runs_off_the_event_loop(monkeypatch, tmp_path):
    import threading
    caller = threading.get_ident()
    monkeypatch.setattr(routes, '_require_same_origin', lambda request: None)
    monkeypatch.setattr(routes, 'player_for_request', lambda request, player: 'alice')
    def verify(request, player, body):
        assert threading.get_ident() != caller
        return {'outcome':'pending'}
    monkeypatch.setattr(routes, '_verify_harvest_attempt', verify)
    assert asyncio.run(routes.harvest_verify(request({'attempt_id':'attempt-0001'}, tmp_path))) == {'outcome':'pending'}
