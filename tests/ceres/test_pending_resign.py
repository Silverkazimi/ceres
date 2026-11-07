""" (aletheia round 6 F1): signing an action again while its earlier attempt is still pending.

The server issues a begin token only on an attempt's first contact, so a second begin for the same (pending) attempt
fails. Before the fix the gate then marked the earlier attempt never-sent. It must refuse instead: the earlier row stays
pending, the wallet is not asked a second time and nothing is sent twice. Runs the REAL gate and confirmer in the replay
suite's fake world (no network, no browser)."""
from __future__ import annotations

from tests.ceres.test_round1_flows import OPS, run

QUEUE_STUBS = """
walletCanSignTransaction = () => true;
let signCalls = 0;
prepareQueueBatch = async (account, batches, authority) => batches.map((operations) => ({ account, authority,
  transaction: { operations, expiration: '2099-01-01T00:00:00' } }));
signQueuedTransaction = () => { signCalls += 1; return new Promise(() => {}); };   // the wallet never answers: pending
"""


def test_second_sign_of_a_pending_attempt_does_not_mark_the_first_never_sent_and_sends_nothing_more():
    out = run(f"""
{QUEUE_STUBS}
const ops = {OPS('pend', 1)};
requestSignatureThroughGate('alice', ops, null, 'Posting').catch(() => {{}});
await __advance(2000);
const first = confirmationState('alice', ops);
let refused = null;
try {{ await requestSignatureThroughGate('alice', ops, null, 'Posting'); }} catch (e) {{ refused = {{ message: e.message, broadcasted: e.broadcasted }}; }}
await __advance(2000);
const after = confirmationState('alice', ops);
__mark({{ firstState: first?.state, firstToken: !!first?.beginToken, afterState: after?.state, afterBy: after?.neverSentBy || null,
  sameAttempt: after?.attemptId === first?.attemptId, tokenKept: after?.beginToken === first?.beginToken,
  refused, signCalls, wallet: __world.walletCalls }});
""")
    assert out["firstState"] == "pending" and out["firstToken"], out
    assert out["afterState"] == "pending" and out["afterBy"] is None and out["sameAttempt"] and out["tokenKept"], out
    assert out["refused"] and out["refused"]["broadcasted"] is False, out
    assert out["signCalls"] == 1 and out["wallet"] == 0, out


def test_second_sign_of_a_pending_attempt_respects_holds_server_claim():
    out = run(f"""
{QUEUE_STUBS}
const ops = {OPS('claim', 1)};
requestSignatureThroughGate('alice', ops, null, 'Posting', null, {{ holdsServerClaim: true }}).catch(() => {{}});
await __advance(2000);
try {{ await requestSignatureThroughGate('alice', ops, null, 'Posting', null, {{ holdsServerClaim: true }}); }} catch (e) {{}}
await __advance(2000);
const row = confirmationState('alice', ops);
__mark({{ state: row?.state, by: row?.neverSentBy || null, silent: !!row?.silent, signCalls }});
""")
    assert out["state"] == "pending" and out["by"] is None and out["signCalls"] == 1, out
