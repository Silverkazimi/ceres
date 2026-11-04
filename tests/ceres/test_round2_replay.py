""" fix round 2 (astra-review-r2 P2, "Missing worksite/deed-lock replays"): real-flow replays for the worksite
modal and the deed lock, plus their receipt races.

Worksite: the real `confirmWorksiteBroadcast` -> real gate -> fake Keychain -> real shared confirmer -> the worksite's own
/api/op-verify read, in the replay world (virtual clock, fake chain/game). Deed lock: the real `verifySignedPlotLock`
of a signed entry (the lock's confirm adapter) through the real confirmer. Only the screen painters are stubbed. History
shape numbers as in test_replay.SHAPES. No network, no browser.
"""
from __future__ import annotations

import json

import pytest

from tests.ceres.test_replay import STATIC, _exposed, _run

WORKSITE = r"""
(async () => {
const s = SCENARIO; __world.scenario = s;
const op = ['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ op: 'start_worksite', n: 'wsit000001' }) }];
CURRENT_PLAYER = 'alice'; BOARD = { player: 'alice', plots: [{ deed_uid: '', plot: '1-1-1', worksite_type: 'Grain Farm' }] };
S.WORKSITE_MODAL_PREVIEW = { payload: { account: 'alice', kind: 'worksite', chunks: [{ operations: [op] }] } };
keychainReady = () => true; worksiteWhatText = () => 'worksite'; worksiteModalStillOpenFor = () => false;
refreshConfirmedPlot = async () => {};
const verdicts = [];
reportWorksiteVerdict = (deed, chunk, tx, verdict) => verdicts.push({ ...verdict, tx });
let verifies = 0;
__world.gameFetch = async (path, body) => {
  if (path !== '/api/op-verify') return null;
  verifies += 1;
  if (s.failVerifyOnce && verifies === 1) return __world.reply({ detail: 'down' }, 503);
  const indexed = body.tx_id === __world.txId && __world.sent != null && __now() >= __world.sent + 3000 + (s.lag || 0) * 1000;
  if (!indexed) return __world.reply({ state: 'chain_absent', chain: { evidence: { reason_kind: 'not_found' } } });
  if (s.refuse === 0) return __world.reply({ state: 'game-refused', game: { message: 'Game refused op 0' } });
  return __world.reply({ state: 'domain-confirmed', refreshed_deeds: [''], refresh_errors: [] });
};
if (s.downFor) __world.scenario.downFrom = __now() + 8000;
confirmWorksiteBroadcast({ deed_uid: '', plot: '1-1-1' }, null);
if (s.hideFor) { await __advance(5000); await __hide(s.hideFor); __fire('pageshow'); __fire('visibilitychange'); await __advance(2000); }
const final = () => { const v = verdicts.filter((x) => x.phase !== 'pending' && x.phase !== 'accepted').pop(); if (!v) return null;
  return /not sent/i.test(v.title || '') ? 'never-sent' : v.state === 'is-failed' || v.tone === 'danger' ? 'refused' : /confirm|complete|active/i.test(v.title || '') ? 'confirmed' : null; };
for (let step = 0; step < LIMIT / 5000 && !final(); step++) await __advance(5000);
console.log('IR9545-RESULT ' + JSON.stringify({ state: final() || ('stuck:' + JSON.stringify(verdicts.at(-1) || null).slice(0, 200)), wallet: __world.walletCalls, verifies }));
process.exit(0);
})().catch((error) => { console.log('IR9545-RESULT ' + JSON.stringify({ state: 'error:' + (error && (error.stack || error.message)) })); process.exit(0); });
"""

WORKSITE_SHAPES = {
    "lost_ack_after_approval": ("12, 16", {"wallet": "expired", "lag": 10}, "confirmed"),
    "wallet_returns_no_tx_id": ("12", {"wallet": "no-id", "lag": 10}, "confirmed"),
    "late_broadcast_after_timeout": ("12, 22", {"wallet": "silent", "lateAnswerAt": 150000, "lag": 10}, "confirmed"),
    "game_lagging_hive": ("11", {"lag": 240}, "confirmed"),
    "one_failed_lookup_then_recovery": ("4, 26", {"lag": 10, "failVerifyOnce": True}, "confirmed"),
    "service_restart_mid_wait": ("26", {"lag": 10, "downFor": 200000}, "confirmed"),
    "hidden_tab_pageshow": ("17, 30", {"lag": 10, "hideFor": 900000}, "confirmed"),
    "refused_vs_pending": ("6, 13", {"lag": 10, "refuse": 0}, "refused"),
    "never_sent_closed_request": ("12, 13", {"wallet": "lost", "neverSent": True}, "never-sent"),
}


@pytest.mark.parametrize("shape", list(WORKSITE_SHAPES), ids=lambda s: f"{s}[history {WORKSITE_SHAPES[s][0]}]")
def test_e2e_worksite(shape):
    _, knobs, expected = WORKSITE_SHAPES[shape]
    entry, source = _exposed("worksite.js", ["keychainReady", "worksiteWhatText", "worksiteModalStillOpenFor", "refreshConfirmedPlot", "reportWorksiteVerdict"])
    limit = 4300000 if expected == "never-sent" else 1200000
    outcome = _run(WORKSITE.replace("SCENARIO", json.dumps(knobs)).replace("LIMIT", str(limit)), entry, source)
    assert outcome["state"] == expected, outcome
    assert outcome["wallet"] == 1, outcome


def test_e2e_worksite_silent_wallet_is_never_reported_not_sent():
    """Round 2 P0-1 on the worksite, as amended by the maintainer's IN-0888 (fix round 3): a wallet that never answers leaves
    the worksite pill honestly pending for the transaction's whole lifetime; only after it is it 'not sent'."""
    entry, source = _exposed("worksite.js", ["keychainReady", "worksiteWhatText", "worksiteModalStillOpenFor", "refreshConfirmedPlot", "reportWorksiteVerdict"])
    early = _run(WORKSITE.replace("SCENARIO", json.dumps({"wallet": "silent", "neverSent": True})).replace("LIMIT", "3600000"), entry, source)
    assert early["state"].startswith("stuck:") and early["wallet"] == 1, early
    outcome = _run(WORKSITE.replace("SCENARIO", json.dumps({"wallet": "silent", "neverSent": True})).replace("LIMIT", "7200000"), entry, source)
    assert outcome["state"] == "never-sent" and outcome["wallet"] == 1, outcome


PLOT_LOCK = r"""
(async () => {
const s = SCENARIO; __world.scenario = s;
const op = ['custom_json', { id: 'sm_lock_assets', required_auths: ['alice'], required_posting_auths: [], json: JSON.stringify({ type: 'item', lock_days: 3, asset_ids: ['I-1-aaaaaaaa'], app: 'ceres/1.2.0', n: '0f0e0d0c-0b0a-4908-8706-050403020100' }) }];
localStorage.setItem('ceres:plot-lock-plan:v1:alice', JSON.stringify([{ deed_uid: 'I-1-aaaaaaaa', action: 'lock', baselineDays: null, baselineUnlockDate: null, lockDays: 3, txId: __world.txId, operations: [op], signedAt: __now() }]));
__world.sent = s.neverSent ? null : __now();
if (s.preFinal) { trackSigning('alice', [op]); attachTransaction('alice', [op], __world.txId); markConfirmation('alice', [op], 'confirmed', {}); }
// Fix round 3 (N1/N3): the signing (queue branch of the gate) recorded its begin before the wallet and the wallet closed
// the request; only then can a known id ever be proven never sent.
if (s.neverSent && typeof announceBegin === 'function') { trackSigning('alice', [op]); await announceBegin('alice', [op]); markAnswered('alice', [op], { closed: true }); }
let verifies = 0;
const verdicts = [];
window.addEventListener('ceres:plot-lock-verdict', (event) => verdicts.push(event.detail));
const fetchImpl = async (url, init) => {
  verifies += 1;
  if (s.failVerifyOnce && verifies === 1) return __world.reply({ detail: 'down' }, 503);
  const indexed = __world.sent != null && __now() >= __world.sent + 3000 + (s.lag || 0) * 1000;
  if (!indexed) return __world.reply({ status: 'pending' });
  if (s.refuse === 0) return __world.reply({ status: 'failed', message: 'Game refused op 0' });
  return __world.reply({ status: 'accepted' });
};
reconcilePlotLockPlans({ player: 'alice', plots: s.boardShowsLocked ? [{ deed_uid: 'I-1-aaaaaaaa', plot: '1-1-1', lock_days: 3, unlock_date: null }] : [] });
let result = null;
verifySignedPlotLock('I-1-aaaaaaaa', { fetchImpl }).then((r) => { result = r; });
if (s.hideFor) { await __advance(5000); await __hide(s.hideFor); __fire('pageshow'); __fire('visibilitychange'); await __advance(2000); }
for (let step = 0; step < LIMIT / 5000 && !result; step++) await __advance(5000);
const state = result === 'accepted' ? 'confirmed' : result === 'failed' ? 'refused' : result === 'not_found' ? 'never-sent' : 'stuck:' + result;
console.log('IR9545-RESULT ' + JSON.stringify({ state, verifies, kept: STAGED_PLOT_LOCKS.has('I-1-aaaaaaaa'), verdicts }));
process.exit(0);
})().catch((error) => { console.log('IR9545-RESULT ' + JSON.stringify({ state: 'error:' + (error && (error.stack || error.message)) })); process.exit(0); });
"""

LOCK_SHAPES = {
    "game_lagging_hive": ("11", {"lag": 240}, "confirmed"),
    "one_failed_lookup_then_recovery": ("4, 26", {"lag": 10, "failVerifyOnce": True}, "confirmed"),
    "hidden_tab_pageshow": ("17, 30", {"lag": 10, "hideFor": 900000}, "confirmed"),
    "refused_vs_pending": ("6, 13", {"lag": 10, "refuse": 0}, "refused"),
    "board_shows_locked_before_the_verdict": ("5", {"lag": 120, "boardShowsLocked": True}, "confirmed"),
    "final_before_the_screen_receipt_race": ("24, 25", {"lag": 0, "preFinal": True}, "confirmed"),
    "never_sent_known_id_past_lifetime": ("12, 13", {"neverSent": True}, "never-sent"),
}


@pytest.mark.parametrize("shape", list(LOCK_SHAPES), ids=lambda s: f"{s}[history {LOCK_SHAPES[s][0]}]")
def test_e2e_deed_lock(shape):
    _, knobs, expected = LOCK_SHAPES[shape]
    limit = 5400000 if expected == "never-sent" else 1200000
    outcome = _run(PLOT_LOCK.replace("SCENARIO", json.dumps(knobs)).replace("LIMIT", str(limit)), STATIC / "plot-lock.js")
    assert outcome["state"] == expected, outcome
    assert outcome["verifies"] >= 1, outcome          # the verdict always comes from the operation's own read (shape 5)
