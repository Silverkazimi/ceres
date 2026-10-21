import json
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context
from ceres import routes_op_preview


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
GATE = STATIC / "broadcast-gate.js"
CHECKOUT = STATIC / "checkout.js"


def vm_context() -> str:
    return r"""
context.navigator.locks = { request(_name, _options, callback) { return callback(); } };
context.AbortController = class { constructor() { this.signal = {}; } abort() {} };
window.dispatchEvent = event => {
  (__ceresListeners[event.type] || []).forEach(listener => listener(event));
  return true;
};
context.CURRENT_PLAYER = 'alice';
context.BOARD = { player: 'alice', generated_at: '2026-09-21T00:00:00Z', plots: [{ deed_uid: 'D-1' }] };
"""


def operation(nonce: str) -> list:
    return [
        [
            "custom_json",
            {
                "id": "sm_land_operation",
                "required_auths": [],
                "required_posting_auths": ["alice"],
                "json": f'{{"n":"{nonce}","deed_uid":"D-1"}}',
            },
        ],
    ]


# A10 (broadcast-gate.js probeAdmission, routes_op_preview._request_identity_inclusion): a wallet
# request that came back WITHOUT a transaction id is never judged -- no history discovery, no
# fetch, it stays unresolved. Only a request with a known transaction id can be proved absent
# (or included). The tests below therefore prove release with a transaction-id row and assert
# that a no-id row is never probed, never released and keeps its replay claim.
OLD_TX_ROW_AGE_MS = 3900 * 1000 + 60000  # past TX_ABSENCE_GRACE_SECONDS, when the server may answer "not included"


def test_ambiguous_review_returns_sign_control_after_request_release() -> None:
    op = operation("ir7100-release")
    run_in_module_context(
        f"""
(async () => {{
  const operations = {op!r};
  const identity = JSON.stringify(['alice', 'custom_json', 'sm_land_operation', 'ir7100-release']);
  localStorage.setItem('ceres:broadcast-claims:v1:alice', JSON.stringify([identity]));
  localStorage.setItem('ceres:pending-custom-ops:v1:alice', JSON.stringify([{{
    id: identity, claimIds: [identity], count: 1, txId: null, createdAt: new Date().toISOString(),
  }}]));
  const review = {{
    payload: {{ account: 'alice', chunks: [{{ operations }}] }}, stagedSnapshot: [],
    broadcastPending: true, pendingBroadcastIds: [identity],
  }};
  CERES_PREVIEWS.set('D-1', review);
  window.hive_keychain = {{ requestBroadcast() {{}} }};
  let fetches = 0;
  fetch = async () => {{ fetches += 1; return {{ ok: true, json: async () => ({{ included: false, block_num: null }}) }}; }};
  // A10: without a transaction id nothing can prove the request absent, so it is not released.
  if (await releasePendingBroadcastRequests('alice', [operations])) throw Error('a no-id request was released without proof');
  if (fetches !== 0) throw Error('a no-id request was probed');
  if (!review.broadcastPending) throw Error('review stopped being pending without proof');
  if (JSON.stringify(JSON.parse(localStorage.getItem('ceres:broadcast-claims:v1:alice'))) !== JSON.stringify([identity])) throw Error('unproved release dropped the replay claim');
  // The wallet's transaction id arrives; the chain can now answer, and a definitive "not included" releases it.
  const [row] = JSON.parse(localStorage.getItem('ceres:pending-custom-ops:v1:alice'));
  localStorage.setItem('ceres:pending-custom-ops:v1:alice', JSON.stringify([{{
    ...row, txId: 'b'.repeat(40), createdAt: new Date(Date.now() - {OLD_TX_ROW_AGE_MS}).toISOString(),
  }}]));
  if (!await releasePendingBroadcastRequests('alice', [operations])) throw Error('release was refused');
  if (fetches !== 1) throw Error('the transaction-id request was not probed exactly once');
  if (review.broadcastPending || review.pendingBroadcastIds) throw Error('review stayed pending after release');
  const html = operationSignButtonHtml({{ deed_uid: 'D-1' }}, review);
  if (html.includes(' disabled')) throw Error('Sign stayed disabled after release');
}})().then(() => process.exit(0), error => {{ console.error(error); process.exit(1); }});
""",
        entry=CHECKOUT,
        extra_context_js=vm_context(),
    )


def test_worksite_undo_releases_budget_but_same_nonce_remains_replay_blocked() -> None:
    op = operation("ir7100-undo")
    run_in_module_context(
        f"""
(async () => {{
  const operations = {op!r};
  const identity = JSON.stringify(['alice', 'custom_json', 'sm_land_operation', 'ir7100-undo']);
  stageWorksiteBuild('D-1', {{ target: 'grain_farm', targetLabel: 'Grain Farm', firstClear: true }});
  CERES_PREVIEWS.set('D-1', {{ payload: {{ account: 'alice', chunks: [{{ operations }}] }} }});
  localStorage.setItem('ceres:broadcast-claims:v1:alice', JSON.stringify([identity]));
  localStorage.setItem('ceres:pending-custom-ops:v1:alice', JSON.stringify([{{
    id: identity, claimIds: [identity], count: 1, txId: null, createdAt: new Date().toISOString(),
  }}]));
  let fetches = 0;
  fetch = async () => {{ fetches += 1; return {{ ok: true, json: async () => ({{ included: false, block_num: null }}) }}; }};
  // A10: a no-id request cannot be proved absent, so Undo does not free it and nothing is probed.
  if (await unstageWorksiteBuild('D-1')) throw Error('Undo freed a request that was never proved absent');
  if (fetches !== 0) throw Error('a no-id request was probed');
  if (JSON.parse(localStorage.getItem('ceres:broadcast-claims:v1:alice')).length !== 1) throw Error('Undo released the exact replay claim');
  const retained = JSON.parse(localStorage.getItem('ceres:pending-custom-ops:v1:alice'));
  if (retained.length !== 1 || retained[0].budgetActive !== true || !retained[0].instanceId) throw Error('Undo released the unproved request from the budget');
  let walletCalls = 0;
  window.hive_keychain = {{ requestBroadcast(_account, _operations, _key, callback) {{
    walletCalls += 1;
    callback({{ success: true, result: {{ id: 'fresh-tx' }}, data: {{ key: 'Posting' }} }});
  }} }};
  const duplicate = await BroadcastGate.broadcast('alice', operations).catch(error => error);
  if (!duplicate.duplicateBroadcast || walletCalls !== 0) throw Error('Undo allowed the still-actionable exact request to reach the wallet again');
  if (fetches !== 0) throw Error('the replay check probed a no-id request');
  // Once the request carries a transaction id and the chain definitively says "not included", Undo frees it.
  const [row] = JSON.parse(localStorage.getItem('ceres:pending-custom-ops:v1:alice'));
  localStorage.setItem('ceres:pending-custom-ops:v1:alice', JSON.stringify([{{
    ...row, txId: 'b'.repeat(40), createdAt: new Date(Date.now() - {OLD_TX_ROW_AGE_MS}).toISOString(),
  }}]));
  if (!await unstageWorksiteBuild('D-1')) throw Error('Undo refused a request proved absent');
  if (fetches !== 1) throw Error('the proved-absent request was not probed exactly once');
  if (JSON.parse(localStorage.getItem('ceres:pending-custom-ops:v1:alice')).length !== 0) throw Error('Undo kept a proved-absent request in the budget');
  if (walletCalls !== 0) throw Error('Undo reached the wallet');
}})().then(() => process.exit(0), error => {{ console.error(error); process.exit(1); }});
""",
        entry=CHECKOUT,
        extra_context_js=vm_context(),
    )


def test_four_operation_clear_releases_one_stale_two_op_txid_row_after_definitive_absence() -> None:
    run_in_module_context(
        r"""
(async () => {
  const op = nonce => ['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ n: nonce }) }];
  const claimIds = ['stale-a', 'stale-b'].map(nonce => JSON.stringify(['alice', 'custom_json', 'sm_land_operation', nonce]));
  const stale = { id: claimIds[0], claimIds, count: 2, txId: 'a'.repeat(40), createdAt: new Date().toISOString() };
  localStorage.setItem('ceres:broadcast-claims:v1:alice', JSON.stringify(claimIds));
  localStorage.setItem('ceres:pending-custom-ops:v1:alice', JSON.stringify([stale]));
  let probes = 0;
  fetch = async () => { probes += 1; return { ok: true, json: async () => ({ included: false, block_num: null }) }; };
  let walletCalls = 0;
  window.hive_keychain = { requestBroadcast(_account, _operations, _key, callback) {
    walletCalls += 1;
    callback({ success: true, result: { id: 'clear-tx' }, data: { key: 'Posting' } });
  } };
  await BroadcastGate.broadcast('alice', [op('clear-power'), op('clear-workers'), op('clear-totem'), op('clear-build')]);
  if (probes !== 1 || walletCalls !== 1) throw Error('four-op clear did not release the definitive absent two-op transaction');
  if (JSON.parse(localStorage.getItem('ceres:broadcast-claims:v1:alice')).some(id => claimIds.includes(id))) throw Error('definitively absent transaction kept its claims');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=GATE,
        extra_context_js=vm_context(),
    )


def test_inconclusive_uncertain_request_blocks_and_same_envelope_never_rebroadcasts() -> None:
    run_in_module_context(
        r"""
(async () => {
  const nativeSetTimeout = setTimeout;  // : the budget wait checks once a second; run those waits at once here
  setTimeout = (callback, delay) => delay === 1000 ? Promise.resolve().then(callback) : nativeSetTimeout(callback, delay);
  const op = nonce => ['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ n: nonce }) }];
  let walletCalls = 0;
  window.hive_keychain = { requestBroadcast(_account, _operations, _key, callback) {
    walletCalls += 1;
    callback({ success: false, ambiguousBroadcast: true, error: 'The wallet did not return a verified transaction id.' });
  } };
  const uncertain = [op('uncertain-a'), op('uncertain-b')];
  const first = await BroadcastGate.broadcast('alice', uncertain).catch(error => error);
  if (!first.ambiguousBroadcast || walletCalls !== 1) throw Error('request was not classified uncertain');
  const duplicate = await BroadcastGate.broadcast('alice', uncertain).catch(error => error);
  if (!duplicate.duplicateBroadcast || walletCalls !== 1) throw Error('uncertain envelope was rebroadcast');
  fetch = async () => ({ ok: false, json: async () => ({}) });
  const blocked = await BroadcastGate.broadcast('alice', [op('next-a'), op('next-b'), op('next-c'), op('next-d')]).catch(error => error);
  if (walletCalls !== 1 || !blocked.message.includes('still being checked')) throw Error('inconclusive request did not block in plain words');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=GATE,
        extra_context_js=vm_context(),
    )


def test_one_stale_operation_plus_four_operation_clear_does_not_block() -> None:
    run_in_module_context(
        r"""
(async () => {
  const op = nonce => ['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ n: nonce }) }];
  const id = JSON.stringify(['alice', 'custom_json', 'sm_land_operation', 'one-stale']);
  localStorage.setItem('ceres:broadcast-claims:v1:alice', JSON.stringify([id]));
  localStorage.setItem('ceres:pending-custom-ops:v1:alice', JSON.stringify([{ id, claimIds: [id], count: 1, txId: 'a'.repeat(40), createdAt: new Date().toISOString() }]));
  let probes = 0;
  fetch = async () => { probes += 1; return { ok: true, json: async () => ({ included: null, block_num: null }) }; };
  let walletCalls = 0;
  window.hive_keychain = { requestBroadcast(_account, _operations, _key, callback) {
    walletCalls += 1;
    callback({ success: true, result: { id: 'clear-tx' }, data: { key: 'Posting' } });
  } };
  await BroadcastGate.broadcast('alice', [op('clear-power'), op('clear-workers'), op('clear-totem'), op('clear-build')]);
  if (walletCalls !== 1 || probes !== 0) throw Error('one stale operation incorrectly blocked a four-operation transaction');  // : within budget, no inclusion read
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=GATE,
        extra_context_js=vm_context(),
    )


def test_txid_lookup_failure_stays_blocked_in_plain_words() -> None:
    run_in_module_context(
        r"""
(async () => {
  const nativeSetTimeout = setTimeout;
  setTimeout = (callback, delay) => delay === 1000 ? Promise.resolve().then(callback) : nativeSetTimeout(callback, delay);
  const op = nonce => ['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ n: nonce }) }];
  const claimIds = ['stale-a', 'stale-b'].map(nonce => JSON.stringify(['alice', 'custom_json', 'sm_land_operation', nonce]));
  localStorage.setItem('ceres:broadcast-claims:v1:alice', JSON.stringify(claimIds));
  localStorage.setItem('ceres:pending-custom-ops:v1:alice', JSON.stringify([{ id: claimIds[0], claimIds, count: 2, txId: 'a'.repeat(40), createdAt: new Date().toISOString() }]));
  let probes = 0;
  fetch = async () => { probes += 1; return { ok: true, json: async () => ({ included: null, block_num: null }) }; };
  let walletCalls = 0;
  window.hive_keychain = { requestBroadcast() { walletCalls += 1; } };
  const blocked = await BroadcastGate.broadcast('alice', [op('clear-power'), op('clear-workers'), op('clear-totem'), op('clear-build')]).catch(error => error);
  if (probes !== 31 ||  /* : 30 one-second checks + the release pass */ walletCalls !== 0 || !blocked.message.includes('still being checked') || !blocked.message.includes('not sent')) throw Error('lookup failure did not retain the row with a plain refusal');
  if (JSON.parse(localStorage.getItem('ceres:pending-custom-ops:v1:alice')).length !== 1
    || JSON.parse(localStorage.getItem('ceres:broadcast-claims:v1:alice')).length !== 2) throw Error('lookup failure released uncertain state');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=GATE,
        extra_context_js=vm_context(),
    )


def test_migrated_rows_without_timestamp_require_not_included_probe_for_release() -> None:
    run_in_module_context(
        r"""
(async () => {
  const op = nonce => ['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ n: nonce }) }];
  // Two legacy rows carry a transaction id (the chain can judge them); one has none (A10: never judged).
  const oldRows = [['old-a', 'a'.repeat(40)], ['old-b', 'b'.repeat(40)], ['old-no-id', null]].map(([nonce, txId]) => {
    const id = JSON.stringify(['alice', 'custom_json', 'sm_land_operation', nonce]);
    return { id, count: 1, txId };
  });
  const noIdClaim = oldRows[2].id;
  localStorage.setItem('ceres:broadcast-claims:v1:alice', JSON.stringify(oldRows.map(row => row.id)));
  localStorage.setItem('ceres:pending-custom-ops:v1:alice', JSON.stringify(oldRows));
  const migrated = pendingCustomOps('alice');
  if (migrated.length !== 3 || migrated.some(row => !Number.isFinite(Date.parse(row.createdAt))
    || Date.parse(row.createdAt) > Date.now() - 599000)) throw Error('timestamp-less rows were not migrated to their probe-eligible time');
  if (JSON.parse(localStorage.getItem('ceres:broadcast-claims:v1:alice')).length !== 3) throw Error('timestamp-less migration released claims by the clock alone');
  let probes = 0;
  fetch = async () => { probes += 1; return { ok: true, json: async () => ({ included: false, block_num: null }) }; };
  let walletCalls = 0;
  window.hive_keychain = { requestBroadcast(_account, _operations, _key, callback) {
    walletCalls += 1;
    callback({ success: true, result: { id: 'migrated-clear-tx' }, data: { key: 'Posting' } });
  } };
  await BroadcastGate.broadcast('alice', [op('clear-a'), op('clear-b'), op('clear-c'), op('clear-d')]);
  if (probes !== 2 || walletCalls !== 1) throw Error('definite not-included probes did not release migrated rows');
  const claims = JSON.parse(localStorage.getItem('ceres:broadcast-claims:v1:alice'));
  if (claims.includes(oldRows[0].id) || claims.includes(oldRows[1].id)) throw Error('proved-absent migrated rows kept their claims');
  if (!claims.includes(noIdClaim)) throw Error('a migrated no-id row lost its replay claim without a probe');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=GATE,
        extra_context_js=vm_context(),
    )


def test_null_id_probe_proves_absence_only_from_a_covered_settled_history(monkeypatch) -> None:
    # A10: no automatic history discovery, even after expiry. A covered, settled history that
    # lacks the nonce is still not proof of absence for a request without a transaction id,
    # and the probe makes no outbound read at all.
    created_at = "2026-09-20T10:02:00Z"
    claim = '["alice","custom_json","sm_land_operation","missing-nonce"]'
    rpc_calls = 0

    def history(*_args, **_kwargs):
        nonlocal rpc_calls
        rpc_calls += 1
        return {"history": [[1, {
            "timestamp": "2026-09-20T10:01:00Z",
            "trx_id": "a" * 40,
            "block": 123,
            "op_in_trx": 0,
            "op": ["custom_json", {
                "id": "sm_land_operation",
                "required_auths": [],
                "required_posting_auths": ["alice"],
                "json": '{"n":"different-nonce"}',
            }],
        }]]}

    monkeypatch.setattr(routes_op_preview, "_rpc", history)
    result = routes_op_preview._request_identity_inclusion(
        "alice", {"claim_ids": [claim], "created_at": created_at},
    )
    assert result == {"included": None, "block_num": None}
    assert rpc_calls == 0


def test_tx_id_probe_preserves_lookup_failure_as_inconclusive(monkeypatch) -> None:
    monkeypatch.setattr(routes_op_preview, "player_for_request", lambda _request, _player: "alice")
    outcomes = {
        "included": ({"block_num": 123}, None),
        "missing": (None, "not_found"),
        "failed": (None, "lookup_failed"),
    }
    monkeypatch.setattr(routes_op_preview, "get_transaction", lambda tx_id: outcomes[tx_id])

    assert json.loads(routes_op_preview.op_inclusion({"tx_id": "included"}, object()).body) == {
        "included": True, "block_num": 123,
    }
    # S3 / L2-02: the first "not found" is not proof; only a request past its lifetime is called absent.
    from datetime import datetime, timedelta, timezone
    young = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(seconds=routes_op_preview.TX_ABSENCE_GRACE_SECONDS + 5)).isoformat()
    assert json.loads(routes_op_preview.op_inclusion({"tx_id": "missing"}, object()).body) == {"included": None, "block_num": None}
    assert json.loads(routes_op_preview.op_inclusion({"tx_id": "missing", "created_at": young}, object()).body) == {"included": None, "block_num": None}
    assert json.loads(routes_op_preview.op_inclusion({"tx_id": "missing", "created_at": "garbage"}, object()).body) == {"included": None, "block_num": None}
    assert json.loads(routes_op_preview.op_inclusion({"tx_id": "missing", "created_at": old}, object()).body) == {"included": False, "block_num": None}
    assert json.loads(routes_op_preview.op_inclusion({"tx_id": "failed"}, object()).body) == {
        "included": None, "block_num": None,
    }


def test_null_id_probe_keeps_recent_malformed_and_uncovered_claims_inconclusive(monkeypatch) -> None:
    rpc_calls = 0

    def history(*_args, **_kwargs):
        nonlocal rpc_calls
        rpc_calls += 1
        return {"history": [[100, {
            "timestamp": "2000-01-01T00:01:00Z",
            "trx_id": "a" * 40,
            "block": 123,
            "op": ["custom_json", {
                "id": "sm_land_operation",
                "required_auths": [],
                "required_posting_auths": ["alice"],
                "json": '{"n":"different-nonce"}',
            }],
        }]]}

    monkeypatch.setattr(routes_op_preview, "_rpc", history)
    valid_claim = '["alice","custom_json","sm_land_operation","wanted-nonce"]'
    recent = routes_op_preview._request_identity_inclusion(
        "alice", {"claim_ids": [valid_claim], "created_at": "2999-01-01T00:00:00Z"},
    )
    malformed = routes_op_preview._request_identity_inclusion(
        "alice", {"claim_ids": ['["alice","custom_json"]'], "created_at": "2000-01-01T00:00:00Z"},
    )
    uncovered = routes_op_preview._request_identity_inclusion(
        "alice", {"claim_ids": [valid_claim], "created_at": "2000-01-01T00:00:00Z"},
    )

    # A10: a request without a transaction id is never judged, so every case stays inconclusive
    # and none promises a wait after which it would be judged (no retry_after); no history read.
    assert recent == malformed == uncovered == {"included": None, "block_num": None}
    assert rpc_calls == 0


def test_included_null_txid_row_completes_without_releasing_duplicate_claim() -> None:
    run_in_module_context(
        r"""
(async () => {
  // A10: the no-id row is never probed, so even an "included" answer cannot complete it; the
  // transaction-id row is the one an inclusion verdict completes, and it keeps its claim.
  const id = JSON.stringify(['alice', 'custom_json', 'sm_land_operation', 'landed']);
  const noId = JSON.stringify(['alice', 'custom_json', 'sm_land_operation', 'landed-no-id']);
  const createdAt = new Date(Date.now() - 600001).toISOString();
  localStorage.setItem('ceres:broadcast-claims:v1:alice', JSON.stringify([id, noId]));
  localStorage.setItem('ceres:pending-custom-ops:v1:alice', JSON.stringify([
    { id, claimIds: [id], count: 1, txId: 'd'.repeat(40), createdAt },
    { id: noId, claimIds: [noId], count: 1, txId: null, createdAt },
  ]));
  let fetches = 0;
  fetch = async () => { fetches += 1; return { ok: true, json: async () => ({ included: true, block_num: 456 }) }; };
  const recovered = await recoverPendingBroadcastRequests('alice', 4);
  if (fetches !== 1) throw Error('the no-id row was probed');
  if (recovered.included !== 1 || recovered.pending !== 1 || recovered.remaining !== 1 || recovered.blocked) throw Error('included transaction-id admission did not complete');
  const left = JSON.parse(localStorage.getItem('ceres:pending-custom-ops:v1:alice'));
  if (left.length !== 1 || left[0].id !== noId || left[0].budgetActive !== true) throw Error('included admission stayed pending or the no-id row was resolved');
  if (JSON.stringify(JSON.parse(localStorage.getItem('ceres:broadcast-claims:v1:alice'))) !== JSON.stringify([id, noId])) throw Error('included transaction lost duplicate-broadcast protection');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=GATE,
        extra_context_js=vm_context(),
    )


def test_stale_false_probe_cannot_release_row_after_late_txid_attachment() -> None:
    run_in_module_context(
        r"""
(async () => {
  const id = JSON.stringify(['alice', 'custom_json', 'sm_land_operation', 'late-before-probe']);
  const admissionKey = 'ceres:pending-custom-ops:v1:alice';
  localStorage.setItem('ceres:broadcast-claims:v1:alice', JSON.stringify([id]));
  localStorage.setItem(admissionKey, JSON.stringify([{ id, claimIds: [id], count: 2, txId: null, createdAt: new Date(Date.now() - 600001).toISOString() }]));
  let answerProbe;
  let fetches = 0;
  fetch = async () => { fetches += 1; return new Promise(resolve => { answerProbe = resolve; }); };
  // A10: no probe starts for the no-id row, so no verdict can be in flight when its tx id arrives.
  const first = await recoverPendingBroadcastRequests('alice', 4);
  if (fetches !== 0 || first.released !== 0 || first.pending !== 1) throw Error('a no-id row was probed or released');
  // The late tx id attaches; now a probe starts, and while it is in flight the row's tx id is
  // replaced. The stale "not included" verdict (for the old tx id) must not release the row.
  const [row] = JSON.parse(localStorage.getItem(admissionKey));
  localStorage.setItem(admissionKey, JSON.stringify([{ ...row, txId: 'c'.repeat(40) }]));
  const recovery = recoverPendingBroadcastRequests('alice', 4);
  for (let attempt = 0; attempt < 10 && typeof answerProbe !== 'function'; attempt += 1) await Promise.resolve();
  if (typeof answerProbe !== 'function') throw Error('probe did not start');
  localStorage.setItem(admissionKey, JSON.stringify([{ ...row, txId: 'e'.repeat(40) }]));
  answerProbe({ ok: true, json: async () => ({ included: false, block_num: null }) });
  const result = await recovery;
  const retained = JSON.parse(localStorage.getItem(admissionKey))[0];
  if (result.released !== 0 || result.pending !== 1 || retained?.txId !== 'e'.repeat(40) || retained.budgetActive !== true) throw Error('stale false verdict released a row whose tx id changed while the probe was in flight');
  if (JSON.stringify(JSON.parse(localStorage.getItem('ceres:broadcast-claims:v1:alice'))) !== JSON.stringify([id])) throw Error('stale false verdict released replay claim');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""",
        entry=GATE,
        extra_context_js=vm_context(),
    )
