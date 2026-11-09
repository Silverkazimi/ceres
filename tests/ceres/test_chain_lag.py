""" (game lag, scaled verdict wait, stall rule) and  (find_transaction probe, node failover).

No browser and no network: the JS runs in the shared VM sandbox with fake RPC and fetch; the Python side gets fake
connections. Nothing here signs or broadcasts.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.ceres.vm_context import run_in_module_context

ROOT = Path(__file__).resolve().parents[2]
LAG = ROOT / "ceres/static/chain-lag.js"
QUEUE = ROOT / "ceres/static/transaction-queue.js"
MODAL = ROOT / "ceres/static/harvest-modal.js"

SETUP = r"""
context.crypto = require('node:crypto').webcrypto;
context.TextEncoder = TextEncoder;
context.AbortController = AbortController;
const locks = new Map();
context.navigator.locks = { request(name, options, callback) {
  if (options.ifAvailable && locks.has(name)) return Promise.resolve(callback(null));
  const before = locks.get(name) || Promise.resolve();
  const next = before.catch(()=>{}).then(()=>callback({name}));
  locks.set(name,next);
  next.finally(()=>{if(locks.get(name)===next) locks.delete(name);}).catch(()=>{});
  return next;
}};
"""
PRELUDE = r"""
const assert = (value, message) => { if (!value) throw Error(message); };
const eq = (a, b, message) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw Error(`${message}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); };
const iso = ms => new Date(ms).toISOString().slice(0, 19);
const tx = (expiresAt) => ({ ref_block_num: 1, ref_block_prefix: 2, expiration: iso(expiresAt), extensions: [],
  operations: [['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: '{"n":"x"}' }]] });
"""


def run(body: str) -> None:
    script = PRELUDE + "\n(async()=>{\n" + body + "\n})().then(()=>process.exit(0),e=>{console.error(e);process.exit(1)});"
    result = run_in_module_context(script, entry=LAG, extra_context_js=SETUP, check=False)
    assert result.returncode == 0, result.stderr + result.stdout


# lag math, wording, timeout scaling, stall rule

def test_lag_math_and_plain_words():
    run(r"""
eq(gameLag(100, 113), { gameBlock: 100, hiveHead: 113, blocks: 13, seconds: 39 }, 'lag');
eq(gameLag(120, 113).seconds, 0, 'game ahead of a lagging head reads as no lag');
assert(gameLag(null, 113) === null && gameLag(100, 0) === null && gameLag('100', 113) === null, 'missing read must give null');
eq(gameLagText(null), '', 'unknown lag shows no line');
eq(gameLagText({ seconds: 3 }), 'Game is keeping up with Hive', 'small');
eq(gameLagText({ seconds: 39 }), 'Game is about 40 s behind Hive', 'seconds');
eq(gameLagText({ seconds: 9 }), 'Game is about 10 s behind Hive', 'rounded');
eq(gameLagText({ seconds: 300 }), 'Game is about 5 min behind Hive', 'minutes');
eq(gameLagText({ seconds: 3 }, { stalled: true }), 'The game has not read a new Hive block for 3 min', 'stalled');
""")


def test_chain_lag_owns_no_wait_and_harvest_owns_no_schedule():
    """ replaces 's lag-scaled deadline: the shared confirmer never gives up, so no deadline is scaled."""
    lag = LAG.read_text(encoding="utf-8")
    modal = MODAL.read_text(encoding="utf-8")
    for name in ("nextVerifyDelayMs", "scaledVerifyWaitMs", "VERIFY_FLOOR_MS", "VERIFY_CEILING_MS", "VERIFY_TAIL_STEP_MS", "waitMs("):
        assert name not in lag, name
    for name in ("VERIFY_SCHEDULE_S", "VERIFY_MAX_WAIT_MS", "VERIFY_REQUEST_MS", "lag.waitMs"):
        assert name not in modal, name
    assert "confirmAction" in modal


def test_stall_rule_counts_only_successful_reads():
    run(r"""
const watch = createStallWatch(180000);
assert(!watch.observe(100, 0), 'first read');
assert(!watch.observe(100, 179999), 'just under 3 min');
assert(watch.observe(100, 180000), 'frozen for 3 min is a stall');
assert(!watch.observe(101, 190000), 'advancing clears the stall');
const quiet = createStallWatch(180000);
quiet.observe(100, 0);
assert(!quiet.observe(null, 500000), 'failed reads never prove a stall');
assert(!quiet.observe(99, 1000) && quiet.lastBlock === 100, 'a lower block never counts as movement');
""")


def test_lag_monitor_reads_two_values_throttles_and_detects_stall():
    run(r"""
let clock = 1000000, gameCalls = 0, rpcCalls = 0, game = 500, head = 520;
const fetchImpl = async (url) => { gameCalls++; assert(url === 'https://api.splinterlands.com/last_block', 'game url ' + url);
  return { ok: true, json: async () => ({ last_block: game }) }; };
const rpc = async (method) => { rpcCalls++; assert(method === 'condenser_api.get_dynamic_global_properties', 'method ' + method); return { head_block_number: head }; };
const monitor = createLagMonitor({ fetchImpl, rpc, now: () => clock });
await monitor.refresh();
eq([gameCalls, rpcCalls], [1, 1], 'one read each');
eq(monitor.lag.seconds, 60, 'lag seconds');
eq(monitor.text(), 'Game is about 60 s behind Hive', 'text');
clock += 5000; await monitor.refresh();
eq([gameCalls, rpcCalls], [1, 1], 'throttled within 15 s');
clock += 180000; head = 580; await monitor.refresh();
assert(monitor.stalled, 'game block frozen for 3 min');
eq(monitor.text(), 'The game has not read a new Hive block for 3 min', 'stall text');
game = 501; clock += 20000; await monitor.refresh();
assert(!monitor.stalled, 'moved again');
const broken = createLagMonitor({ fetchImpl: async () => { throw Error('down'); }, rpc: async () => { throw Error('down'); }, now: () => clock });
await broken.refresh();
eq([broken.lag, broken.text()], [null, ''], 'unreadable lag shows nothing');
""")


# find_transaction mapping, fallback, failover

def test_find_transaction_status_mapping():
    run(r"""
const expected = { within_mempool: 'pending', within_reversible_block: 'in_block', within_irreversible_block: 'in_block',
  expired_reversible: 'expiring', expired_irreversible: 'dropped', too_old: 'unknown', unknown: 'unknown', something_new: 'unknown' };
for (const [status, chain] of Object.entries(expected)) eq(findTransactionChainStatus(status), chain, status);
eq(findTransactionChainStatus(undefined), 'unknown', 'missing status');
""")


def test_probe_uses_find_transaction_and_falls_back_to_get_transaction():
    run(r"""
const live = tx(Date.now() + 300000), old = tx(Date.now() - 60000);
const liveRow = { txId: await queueTransactionId(live), transaction: live };
const oldRow = { txId: await queueTransactionId(old), transaction: old };
const fake = (status, found) => { const calls = []; const rpc = async (method, params) => { calls.push(method);
  if (method === 'transaction_status_api.find_transaction') { if (status instanceof Error) throw status; return { status }; }
  if (method === 'condenser_api.get_transaction') return found ? { ...found, block_num: 77 } : null;
  throw Error('unexpected ' + method); }; rpc.calls = calls; return rpc; };
let rpc = fake('within_mempool', live);
eq(await probeQueueInclusion(liveRow, rpc), { blockNum: null, chain: 'pending' }, 'mempool');
eq(rpc.calls, ['transaction_status_api.find_transaction'], 'mempool needs no second read');
rpc = fake('within_irreversible_block', live);
eq(await probeQueueInclusion(liveRow, rpc), { blockNum: 77, chain: 'in_block' }, 'in block, hash proven');
rpc = fake('within_reversible_block', null);
eq(await probeQueueInclusion(liveRow, rpc), { blockNum: null, chain: 'unknown' }, 'in block without proof stays unresolved');
rpc = fake('within_reversible_block', old);
eq(await probeQueueInclusion(liveRow, rpc), { blockNum: null, chain: 'unknown' }, 'a different transaction never counts');
const unsupported = Object.assign(Error('Hive rejected the request.'), { rpcError: { code: -32003, message: 'Could not find API transaction_status_api' } });
rpc = fake(unsupported, live);
eq(await probeQueueInclusion(liveRow, rpc), { blockNum: 77, chain: 'in_block' }, 'node without find_transaction: get_transaction fallback');
rpc = fake(unsupported, null);
eq(await probeQueueInclusion(liveRow, rpc), { blockNum: null, chain: 'unsupported' }, 'fallback without a record');
rpc = fake('expired_irreversible', null);
eq(await probeQueueInclusion(oldRow, rpc), { blockNum: null, chain: 'dropped' }, 'final expiry');
eq(rpc.calls, ['transaction_status_api.find_transaction', 'condenser_api.get_transaction'], 'expired is double-checked');
rpc = fake('expired_irreversible', old);
eq(await probeQueueInclusion(oldRow, rpc), { blockNum: 77, chain: 'in_block' }, 'a found transaction always wins');
rpc = fake('expired_irreversible', null);
eq(await probeQueueInclusion(liveRow, rpc), { blockNum: null, chain: 'unknown' }, 'expired before our own expiry is not believed');
rpc = fake('expired_reversible', null);
eq(await probeQueueInclusion(oldRow, rpc), { blockNum: null, chain: 'expiring' }, 'reversible expiry is not final');
""")


def test_read_failover_skips_unreachable_nodes_but_not_rpc_answers():
    run(r"""
eq(HIVE_RPC_NODES, ['https://api.hive.blog', 'https://api.openhive.network', 'https://api.deathwing.me'], 'node list');
let tried = [];
queueRpc = async (method, params, node) => { tried.push(node); if (node === HIVE_RPC_NODES[0]) throw Error('Hive connection unavailable.'); return { node }; };
eq(await hiveReadRpc('condenser_api.get_transaction', ['ab']), { node: HIVE_RPC_NODES[1] }, 'second node answers');
eq(tried, HIVE_RPC_NODES.slice(0, 2), 'tried in order');
tried = [];
queueRpc = async (method, params, node) => { tried.push(node); throw Object.assign(Error('Hive rejected the request.'), { rpcError: { code: 1 } }); };
const error = await hiveReadRpc('transaction_status_api.find_transaction', {}).catch(e => e);
assert(error.rpcError && tried.length === 1, 'an RPC answer is final, no failover');
tried = [];
queueRpc = async (method, params, node) => { tried.push(node); throw Error('down'); };
const down = await hiveReadRpc('x', []).catch(e => e);
assert(down.message === 'down' && tried.length === 3, 'all nodes tried, last error kept');
""")


def test_expired_submission_shows_dropped_status_and_is_never_resent():
    run(r"""
const old = tx(Date.now() - 30000);
const txId = await queueTransactionId(old);
const signed = { ...old, signatures: ['ab'.repeat(65)] };
localStorage.setItem('ceres:signed-queue:v1:alice', JSON.stringify([{ account: 'alice', txId, transaction: old, authority: 'Posting', runKind: 'harvest',
  state: 'unknown', signedTransaction: signed, submissionAttempted: true, updatedAt: new Date().toISOString() }]));
const methods = [];
queueRpc = async (method, params, node) => { methods.push(method);
  if (method === 'transaction_status_api.find_transaction') return { status: 'expired_irreversible' };
  if (method === 'condenser_api.get_transaction') return null;
  throw Error('unexpected ' + method); };
await resumeSignedQueue('alice');
const row = signedQueueRows('alice')[0];
eq([row.state, row.chainStatus, row.message], ['unknown', 'dropped', QUEUE_DROPPED_MESSAGE], 'status only');
assert(row.signedTransaction && !methods.some(m => m.includes('broadcast')), 'never resent');
eq(QUEUE_DROPPED_MESSAGE, 'Not included: Hive reports this transaction expired. Start the harvest again to send a new one; Ceres never resends it.', 'wording');
assert(hasSignedQueueClaim('alice', old.operations), 'the claim is kept: the same operations cannot be signed twice by accident');
""")


def test_queue_failure_still_blocks_the_rest_of_the_batch():
    # ruling "Keep stopping, safer": unchanged; a terminal earlier row still blocks its dependant.
    source = QUEUE.read_text(encoding="utf-8")
    assert "Not sent: an earlier transaction in this batch did not complete." in source
    assert "if (!dependency || TERMINAL_QUEUE_STATES.has(dependency.state))" in source


def test_broadcast_never_uses_failover():
    source = QUEUE.read_text(encoding="utf-8")
    assert "await queueRpc('condenser_api.broadcast_transaction', [row.signedTransaction]);" in source
    assert not re.search(r"hiveReadRpc\([^)]*broadcast", source)
    # signing-head reads stay on the first node, as before
    assert source.count("queueRpc('condenser_api.get_dynamic_global_properties')") == 3


# consistency between the two node lists, the CSP and the old wait

def test_node_lists_match_and_are_allowed_by_csp():
    from ceres import hive_rpc
    from ceres.security_headers import CONNECT_SRC
    js = re.search(r"HIVE_RPC_NODES = (\[[^\]]+\])", QUEUE.read_text(encoding="utf-8")).group(1)
    nodes = json.loads(js.replace("'", '"'))
    assert nodes == ["https://" + host for host in hive_rpc.HIVE_RPC_NODES]
    assert set(nodes) <= set(CONNECT_SRC)
    assert "https://api.splinterlands.com" in CONNECT_SRC   # the game's /last_block read


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status, self._body = status, body

    def read(self, _limit: int) -> bytes:
        return self._body


class _Connection:
    def __init__(self, host: str, behaviour) -> None:
        self.host, self.behaviour = host, behaviour

    def request(self, *_args) -> None:
        if isinstance(self.behaviour, Exception):
            raise self.behaviour

    def getresponse(self) -> _Response:
        return self.behaviour

    def close(self) -> None:
        pass


def _fake_nodes(monkeypatch, behaviours: dict) -> list[str]:
    from ceres import hive_rpc
    tried: list[str] = []

    def connect(host: str):
        tried.append(host)
        return _Connection(host, behaviours[host])
    monkeypatch.setattr(hive_rpc, "_connect", connect)
    return tried


def _ok(result: dict) -> _Response:
    return _Response(200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode())


def test_server_rpc_fails_over_to_the_next_node(monkeypatch):
    from ceres import hive_rpc
    tried = _fake_nodes(monkeypatch, {"api.hive.blog": OSError("refused"), "api.openhive.network": _Response(502, b""),
                                      "api.deathwing.me": _ok({"head_block_number": 9})})
    assert hive_rpc._rpc("condenser_api.get_dynamic_global_properties", {}) == {"head_block_number": 9}
    assert tried == list(hive_rpc.HIVE_RPC_NODES)


def test_server_rpc_answer_with_error_is_final(monkeypatch):
    from ceres import hive_rpc
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000}}).encode()
    tried = _fake_nodes(monkeypatch, {"api.hive.blog": _Response(200, body), "api.openhive.network": _ok({}),
                                      "api.deathwing.me": _ok({})})
    with pytest.raises(hive_rpc.ActivityReadError):
        hive_rpc._rpc("condenser_api.get_account_history", {"account": "alice"})
    assert tried == ["api.hive.blog"]


def test_server_rpc_all_nodes_down(monkeypatch):
    from ceres import hive_rpc
    tried = _fake_nodes(monkeypatch, {host: OSError("down") for host in hive_rpc.HIVE_RPC_NODES})
    with pytest.raises(hive_rpc.ActivityReadError, match="could not be read"):
        hive_rpc._rpc("condenser_api.get_dynamic_global_properties", {})
    assert len(tried) == 3
    with pytest.raises(hive_rpc.ActivityReadError, match="retired"):
        hive_rpc._rpc("block_api.get_block_range", {})


def test_fresh_review_after_a_dropped_row_is_accepted_but_the_dropped_operations_stay_refused():
    """A fresh harvest review mints new nonces, so its identities differ from the dropped row's: the redo signs,
    the dropped row's own operations are still refused and nothing is broadcast twice."""
    from fides_land.harvest import harvest_all_op, harvest_resources_op, tax_collection_op
    region, deed = "PR-AB-1", "I-1-aaaaaaaa"
    for build in (lambda: harvest_all_op("alice", region, app="ceres/1.0.0"),
                  lambda: harvest_resources_op("alice", region, deed, app="ceres/1.0.0"),
                  lambda: tax_collection_op("alice", region, deed, app="ceres/1.0.0")):
        first, second = build(), build()
        assert first.json_payload["n"] != second.json_payload["n"]
    run(r"""
CURRENT_PLAYER = 'alice'; BOARD = { player: 'alice', plots: [] };
fetch = async url => {
  if (String(url).includes('/api/confirm/intent')) return {ok:true,json:async()=>({begin_token:'c'.repeat(32),action_id:'a'.repeat(32),revision:1,server_now:Date.now()})};
  if (String(url).includes('/api/op-inclusion')) return {ok:true,json:async()=>({included:true,block_num:100})};
  throw Error('unexpected fetch '+url);
};
const op = n => ['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'],
  json: JSON.stringify({ op: 'harvest_all', region_uid: 'LAND-1-1', deed_uids: [], app: 'ceres/1.0.0', n }) }];
const old = { ...tx(Date.now() - 30000), operations: [op('oldNonce01')] };
const txId = await queueTransactionId(old);
localStorage.setItem('ceres:signed-queue:v1:alice', JSON.stringify([{ account: 'alice', txId, transaction: old, authority: 'Posting', runKind: 'harvest',
  state: 'unknown', chainStatus: 'dropped', signedTransaction: { ...old, signatures: ['ab'.repeat(65)] }, submissionAttempted: true,
  updatedAt: new Date().toISOString() }]));
let broadcasts = 0, signatures = 0;
window.hive_keychain = { requestBroadcast() { throw Error('combined wallet API must not be used'); },
  requestSignTx(a, t, k, cb) { signatures++; cb({ success: true, result: { ...t, signatures: ['ab'.repeat(65)] } }); } };
queueRpc = async (method, params) => {
  if (method.endsWith('get_dynamic_global_properties')) return { head_block_number: 100, head_block_id: 'd6908f068242802d3791ff650ddcaefbb54782a5', time: iso(Date.now()) };
  if (method.endsWith('broadcast_transaction')) { broadcasts++; return null; }
  if (method.endsWith('find_transaction')) return { status: 'within_mempool' };
  if (method.endsWith('get_transaction')) return null;
  throw Error('unexpected ' + method); };
assert(hasSignedQueueClaim('alice', [op('oldNonce01')]), 'dropped operations keep their claim');
assert(!hasSignedQueueClaim('alice', [op('newNonce02')]), 'a fresh review is not claimed');
const [oldEnvelope] = await prepareQueueBatch('alice', [[op('oldNonce01')]]);
const refused = await signQueuedTransaction(oldEnvelope).catch(error => error);
assert(refused instanceof Error && /do not sign it again/.test(refused.message), 'dropped operations stay refused');
const [fresh] = await prepareQueueBatch('alice', [[op('newNonce02')]]);
const job = await signQueuedTransaction(fresh);
assert(job.txId === fresh.txId && signatures === 1, 'fresh review signed');
for (let i = 0; i < 200 && broadcasts < 1; i++) await new Promise(r => setTimeout(r, 5));
const rows = signedQueueRows('alice');
assert(broadcasts === 1, 'only the fresh transaction is broadcast');
assert(rows.find(r => r.txId === txId).state === 'unknown', 'the dropped row is untouched');
""")
