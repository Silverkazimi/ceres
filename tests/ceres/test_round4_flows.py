""" fix round 4, client behaviour (astra-review-r4.md , , , ; aletheia-verdict-r4.md 1-6).

Each test runs the REAL module(s) -- broadcast gate, shared confirmer, wallet.js or the notice surface -- in the replay
suite's deterministic fake world (virtual clock, fake Keychain/chain/game, fake server following the round-4 contract:
the find answer carries the server-clock `unanswered_for_s`). No network, no browser. Each failed on the round-3 tree
(snapshot before-r4) unless marked as a control.
"""
from __future__ import annotations

import json
import re

from tests.ceres.test_replay import STATIC
from tests.ceres.test_round1_flows import OPS, run
from tests.ceres.test_round3_flows import EXPIRED_HIVEAUTH, NEVER_ASKED
from tests.ceres.vm_context import run_in_module_context

HELD = "Your wallet never asked you. This step stays reserved because Ceres cannot prove it was not sent; nothing is sent again."
NOTICES = STATIC / "confirm-notices.js"
CONFIRMER = STATIC / "shared-confirmer.js"


def _module(body, entry, extra=""):
    result = run_in_module_context("(async () => {\n" + body + "\n})().catch(e => console.log('IR9545-RESULT ' + JSON.stringify({ error: String(e && e.stack || e) })));",
                                   entry=entry, check=False, extra_context_js=extra)
    line = next((l for l in result.stdout.splitlines() if l.startswith("IR9545-RESULT ")), None)
    assert line, (result.stdout[-1500:], result.stderr[-3000:])
    return json.loads(line.split(" ", 1)[1])


# Aletheia 1: the IN-0888 lifetime ending survives a reload / closed tab
def test_aletheia1_a_row_restored_after_a_reload_with_no_wallet_answer_still_ends():
    """The gate asked the wallet (begin recorded) and the tab closed before any answer or watchdog: the restored row
    has no `silentAt`. After the transaction's lifetime by the server's clock it ends with the maintainer's exact text.
    Before: walletNeverAnswered needed the in-page `silentAt`, so the row stayed pending for good."""
    out = run(f"""
const ops = {OPS('rlod', 1)};
beginAttempt('alice', ops);
await announceBegin('alice', ops);
const before = confirmationState('alice', ops);
__resetConfirmer();                      // the tab is gone: only the stored row is left
resumeConfirmations({{ before: __now() + 1 }});
await __advance(2 * 3600 * 1000);
const row = confirmationState('alice', ops);
__mark({{ silent: Boolean(before?.silentAt), state: row?.state, error: row?.error, by: row?.neverSentBy }});
""")
    assert out["silent"] is False, out
    assert out["state"] == "never-sent" and out["error"] == NEVER_ASKED and out["by"] == "wallet-expired", out


# Aletheia 2: the lifetime clock is the server's, never the browser's
def test_aletheia2_a_fast_browser_clock_never_ends_the_attempt_early():
    """The server's first record of the attempt is only 10 s old in its own clock (`unanswered_for_s` stays small) while
    the browser's clock passes the lifetime: the attempt stays pending (claim held). Before: the browser clock alone
    (row.since) ended it after 3,900 s."""
    out = run(f"""
const ops = {OPS('fast', 1)};
__world.route = async (path, body) => path === '/api/confirm/find'
  ? __world.reply({{ status: 'wait', next_in_s: 60, unanswered_for_s: 10, server_now: __now() }}) : null;
__world.scenario = {{ wallet: 'silent', neverSent: true }};
BroadcastGate.broadcast('alice', ops).catch(() => {{}});
await __advance(2 * 3600 * 1000);
const claims = JSON.parse(localStorage.getItem('ceres:broadcast-claims:v1:alice') || '[]');
__mark({{ state: confirmationState('alice', ops)?.state, claims: claims.length }});
""")
    assert out["state"] == "pending" and out["claims"] >= 1, out


def test_aletheia2_hiveauth_expiry_waits_for_the_browsers_lead_over_the_server_clock():
    """HiveAuth reports its own expiry at 60 s, but this browser's clock is 60 s ahead of the server's (measured from the
    server's answers). The ending is applied only once that lead (plus a margin) has passed: still pending at 100 s,
    ended by 140 s. Before: ended at 60 s."""
    out = run(f"""
{EXPIRED_HIVEAUTH}
const ops = {OPS('skew', 1)};
__world.route = async (path, body) => path === '/api/confirm/find'
  ? __world.reply({{ status: 'wait', next_in_s: 1, server_now: __now() - 60000 }}) : null;
__world.scenario = {{ hiveAuthExpires: true, neverSent: true }};
const start = __now();
BroadcastGate.broadcast('alice', ops).catch(() => {{}});
await __advance(100000);
const at100 = confirmationState('alice', ops)?.state;
await __advance(40000);
const row = confirmationState('alice', ops);
__mark({{ at100, state: row?.state, error: row?.error }});
""")
    assert out["at100"] == "pending", out
    assert out["state"] == "never-sent" and out["error"] == NEVER_ASKED, out


# HiveAuth's own expiry after the watchdog reaches the confirmer
def test_r2_the_gate_ends_the_attempt_on_a_late_hiveauth_expiry():
    """Control for  (the gate side): once wallet.js forwards the late expiry (test above), the gate ends the attempt
    "never asked" within seconds, also after the 120 s watchdog, not after the hour. Passed before too: the round-3
    gap was only in wallet.js, which dropped the message."""
    out = run(f"""
const ops = {OPS('late', 1)};
window.hive_keychain.requestBroadcast = (account, operations, key, callback) => {{
  __world.walletCalls += 1;
  setTimeout(() => callback({{ success: false, late: true, ambiguousBroadcast: true, stillChecking: true, walletExpired: true, relayConfirmed: true, error: 'expired' }}), 130000);
}};
__world.scenario = {{ neverSent: true }};
BroadcastGate.broadcast('alice', ops).catch(() => {{}});
await __advance(125000);
const at125 = confirmationState('alice', ops)?.state;
await __advance(20000);
const row = confirmationState('alice', ops);
__mark({{ at125, state: row?.state, error: row?.error }});
""")
    assert out["at125"] == "pending", out
    assert out["state"] == "never-sent" and out["error"] == NEVER_ASKED, out


# signing again keeps the old attempt's watch
def test_r3_a_retry_keeps_watching_the_old_never_asked_attempt():
    """Astra r4 : A ends "never asked" (HiveAuth expiry); the maintainer signs again (B, wallet returns its id). A's
    transaction then shows up on Hive. Both are followed: B confirmed with its id, A's watched row found with A's id
    and confirmed. Before: only B was queried after the retry; A was lost."""
    out = run(f"""
{EXPIRED_HIVEAUTH}
const ops = {OPS('rtry', 1)};
const txA = 'cd'.repeat(20);
let attemptA = null, aVisible = false;
const asked = [];
__world.route = async (path, body) => {{
  if (path === '/api/confirm/find') {{
    asked.push(body.attempt);
    if (body.attempt === attemptA && aVisible && !(body.exclude_tx_ids || []).includes(txA)) return __world.reply({{ status: 'found', tx_id: txA, server_now: __now() }});
    if (body.attempt === attemptA) return __world.reply({{ status: 'wait', next_in_s: 30, server_now: __now() }});
  }}
  if (path === '/api/confirm/verdict' && body.tx_id === txA)
    return __world.reply({{ tx_id: txA, operations: body.operations.map((_, index) => ({{ index, state: 'confirmed' }})) }});
  return null;
}};
__world.scenario = {{ hiveAuthExpires: true, neverSent: true }};
BroadcastGate.broadcast('alice', ops).catch(() => {{}});
await __advance(70000);
attemptA = confirmationState('alice', ops)?.attemptId;
const first = confirmationState('alice', ops)?.state;
__world.scenario = {{ lag: 0 }};
BroadcastGate.broadcast('alice', ops).catch(() => {{}});
confirmAction({{ account: 'alice', operations: ops }}).catch(() => {{}});   // B's screen confirms B, as a flow does
await __advance(20000);
asked.length = 0;
aVisible = true;
await __advance(10 * 60 * 1000);
const rows = allConfirmations().filter(row => row.operations && JSON.stringify(row.operations) === JSON.stringify(ops));
__mark({{ first, rows: rows.map(row => ({{ attempt: row.attempt, txId: row.txId, state: row.state, detached: Boolean(row.detachedFrom) }})),
  askedA: asked.filter(a => a === attemptA).length }});
""")
    assert out["first"] == "never-sent", out
    rows = {row["attempt"]: row for row in out["rows"]}
    assert rows.get(2, {}).get("state") == "confirmed" and rows[2]["txId"] == "ab" * 20 and not rows[2]["detached"], out
    assert rows.get(1, {}).get("detached") and rows[1]["txId"] == "cd" * 20 and rows[1]["state"] == "confirmed", out
    assert out["askedA"] >= 1, out


# a later result replaces "Starting again is safe"
def test_r4_a_later_confirmation_replaces_the_never_asked_notice():
    """Astra r4  probe: the "never asked" line is shown; the same attempt reopens and is confirmed. The line is
    withdrawn on reopening and the confirmation is shown in its place. Before: the confirmed row returned null and the
    visible HTML kept "Not sent ... Starting again is safe."."""
    out = _module(r"""
const ops = [['custom_json', { id: 'sm_claim_rewards', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ n: 'aaaaaaaaaa' }) }]];
const base = { id: 'act', account: 'alice', attempt: 1, operations: ops, since: new Date().toISOString(), updatedAt: new Date().toISOString() };
const first = confirmNoticeFor({ ...base, state: 'never-sent', neverSentBy: 'wallet-expired', error: WALLET_NEVER_ASKED });
confirmNoticeFor({ ...base, state: 'pending', reopens: 1 });
const reopened = confirmNoticesHtml();
const later = confirmNoticeFor({ ...base, state: 'confirmed', reopens: 1, perOp: [{ index: 0, state: 'confirmed' }] });
console.log('IR9545-RESULT ' + JSON.stringify({ first: Boolean(first), reopened, later: later?.text || null, html: confirmNoticesHtml() }));
""", NOTICES)
    assert out["first"] and "Starting again is safe" not in out["reopened"], out
    assert out["later"] and "confirmed by the game" in out["later"] and "Starting again is safe" not in out["html"], out


# L3: old results are never replayed, however many there are
OLD_ROWS = r"""
function __hashId(text) {
  let a = 0x811c9dc5, b = 0x9e3779b1;
  for (let i = 0; i < text.length; i += 1) { const c = text.charCodeAt(i); a = Math.imul(a ^ c, 16777619); b = Math.imul(b ^ c, 2246822519); }
  return (a >>> 0).toString(16).padStart(8, '0') + (b >>> 0).toString(16).padStart(8, '0');
}
const __ids = [];
for (let i = 0; i < 201; i += 1) {
  const operations = [['custom_json', { id: 'sm_claim_rewards', n: i }]];
  const row = { id: JSON.stringify(['alice', operations]), account: 'alice', attempt: 1, attemptId: 'a1a1a1a1a1a1a1a1', operations, orphan: true,
    state: 'confirmed', txId: 'ab'.repeat(20), rev: 3, since: new Date(Date.now() - 3 * 3600000).toISOString(),
    updatedAt: new Date(Date.now() - 2 * 3600000 + i * 1000).toISOString(), perOp: [{ index: 0, state: 'confirmed' }] };
  __ids.push(row.id);
  __ceresLocalStorage.setItem('ceres:confirm:v2:' + __hashId(row.id), JSON.stringify(row));
}
__ceresLocalStorage.setItem('ceres:confirm:v2-index', JSON.stringify(__ids));
"""


def test_r7_l3_two_hundred_and_one_old_results_show_no_notice_on_the_first_load():
    """Astra r4  (L3): 201 recent orphan results that were final before the first upgrade load; none is shown, also
    the one beyond the old 200-key list. A result final from now on still shows (control). Before: one old notice."""
    out = _module(r"""
const before = confirmNoticeLines.length;
const fresh = confirmNoticeFor({ id: 'fresh', attempt: 1, orphan: true, state: 'confirmed', updatedAt: new Date(Date.now() + 1000).toISOString(),
  operations: [['custom_json', { id: 'sm_claim_rewards' }]] });
console.log('IR9545-RESULT ' + JSON.stringify({ before, fresh: Boolean(fresh) }));
""", NOTICES, OLD_ROWS)
    assert out["before"] == 0 and out["fresh"], out


# Aletheia 3: routing never says "Starting again is safe" when its claim stays with the server
def test_aletheia3_a_held_routing_claim_is_never_told_starting_again_is_safe():
    """Routing v2 signs with `holdsServerClaim` (its server releases the bundle only on its own proof). A silent wallet
    past the lifetime: the attempt stays pending with the held-claim line, the browser claim is kept, nothing says
    "Starting again is safe". Before: never-sent with WALLET_NEVER_ASKED and the claim released."""
    out = run(f"""
const ops = {OPS('rout', 1)};
__world.scenario = {{ wallet: 'silent', neverSent: true }};
BroadcastGate.broadcast('alice', ops, null, 'Posting', {{ holdsServerClaim: true }}).catch(() => {{}});
await __advance(2 * 3600 * 1000);
const row = confirmationState('alice', ops);
const claims = JSON.parse(localStorage.getItem('ceres:broadcast-claims:v1:alice') || '[]');
__mark({{ state: row?.state, message: row?.message, error: row?.error || '', claims: claims.length }});
""")
    assert out["state"] == "pending" and out["message"] == HELD and out["claims"] >= 1, out
    assert "Starting again is safe" not in out["error"] + out["message"], out


def test_aletheia3_routing_run_signs_with_the_held_claim_flag():
    source = (STATIC / "routing-run.js").read_text(encoding="utf-8")
    assert "await sign(player, handoff.operations, null, 'Posting', prepared, { runId: `routing-v2-${S.runId}`, runKind: 'harvest', holdsServerClaim: true })" in source, "routing must hold its claim"


# Pending maintainer question: one named source for the Keychain payment ending
def test_payment_flows_take_their_ending_from_one_named_function():
    """SPS pool funding and market purchase: the lifetime ending text is walletNeverAskedText -> PAYMENT_PROMPT_OPEN_ENDING
    (now OR-1087's payment text); HiveAuth's expiry and other flows keep WALLET_NEVER_ASKED. Both flows render the
    confirmer's text, never a literal."""
    out = _module(r"""
const purchase = [['custom_json', { id: 'sm_market_purchase', required_auths: ['alice'], required_posting_auths: [], json: JSON.stringify({ currency: 'DEC', items: ['a'], price: 1, app: 'ceres/1', n: 'ab'.repeat(8) }) }]];
const funding = [['custom_json', { id: 'sm_sps', required_auths: ['alice'], required_posting_auths: [], json: JSON.stringify({ action: 'token_transfer', params: { token: 'SPS', qty: 1, to: 'x', memo: 'lpsid:1', n: 'ab'.repeat(8) } }) }]];
const harvest = [['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ n: 'abcdefghij' }) }]];
console.log('IR9545-RESULT ' + JSON.stringify({ paymentText: PAYMENT_PROMPT_OPEN_ENDING === 'Not sent: your wallet never asked you. Close any open Keychain window before starting again.',
  purchase: walletNeverAskedText(purchase, 'lifetime') === PAYMENT_PROMPT_OPEN_ENDING,
  funding: walletNeverAskedText(funding, 'lifetime') === PAYMENT_PROMPT_OPEN_ENDING,
  hiveauth: walletNeverAskedText(purchase, 'hiveauth') === WALLET_NEVER_ASKED,
  harvest: walletNeverAskedText(harvest, 'lifetime') === WALLET_NEVER_ASKED }));
""", CONFIRMER)
    assert all(out.values()), out
    lp = (STATIC / "wallet-lp.js").read_text(encoding="utf-8")
    checkout = (STATIC / "card-checkout.js").read_text(encoding="utf-8")
    assert "walletNeverAskedText(ops, row.neverAskedCause)" in lp and "WALLET_NEVER_ASKED" not in lp
    assert "walletNeverAskedText(tx.operations, 'lifetime')" in checkout and "${esc(tx.neverAsked)}" in checkout


# Aletheia 4: the swap waits present their attempt's begin token
def test_aletheia4_wallet_lp_waits_carry_the_begin_token():
    source = (STATIC / "wallet-lp.js").read_text(encoding="utf-8")
    calls = [source[m.start():source.index("})", m.start())] for m in re.finditer(r"waitThrough\(\{", source)]
    assert len(calls) == 2 and all("beginToken:" in call for call in calls), calls


# Aletheia 6: the begin starts while the request is prepared, and is awaited before the wallet
def test_aletheia6_the_begin_overlaps_the_preparation_and_precedes_the_wallet():
    gate = (STATIC / "broadcast-gate.js").read_text(encoding="utf-8")
    queued = gate[gate.index("export async function requestSignatureThroughGate("):]
    assert queued.index("startBegin(account, operations)") < queued.index("prepareQueueBatch(account")
    assert queued.index("await (begun || startBegin(account, operations))") < queued.index("signQueuedTransaction(envelope)")
