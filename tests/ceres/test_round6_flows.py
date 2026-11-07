"""Focused round-6 client checks using the production confirmer module."""

from tests.ceres.test_round4_flows import CONFIRMER, NOTICES, _module


def test_answer_clears_a_pending_hiveauth_expiry():
    out = _module(r"""
const ops = [['custom_json', {id:'sm_claim_rewards', required_auths:[], required_posting_auths:['alice'],
  json:JSON.stringify({n:'a'.repeat(16)})}]];
beginAttempt('alice', ops);
markWalletNeverAsked('alice', ops);
const before = confirmationState('alice', ops);
markAnswered('alice', ops, {closed:true});
const after = confirmationState('alice', ops);
console.log('IR9545-RESULT ' + JSON.stringify({hadExpiry:Number.isFinite(before.hiveAuthExpiredAt),
  cleared:after.hiveAuthExpiredAt == null, answered:!!after.answeredAt, pending:after.state === 'pending'}));
""", CONFIRMER)
    assert all(out.values()), out


def test_second_never_asked_transition_shows_a_new_notice():
    out = _module(r"""
const ops = [['custom_json', {id:'sm_claim_rewards', required_auths:[], required_posting_auths:['alice'],
  json:JSON.stringify({n:'a'.repeat(16)})}]];
const base = {id:'act', account:'alice', attempt:1, operations:ops, since:new Date().toISOString(), updatedAt:new Date().toISOString()};
const first = confirmNoticeFor({...base, state:'never-sent', neverSentBy:'wallet-expired', neverAskedGeneration:1, error:WALLET_NEVER_ASKED});
confirmNoticeFor({...base, state:'pending', reopens:1});
const second = confirmNoticeFor({...base, state:'never-sent', neverSentBy:'wallet-expired', neverAskedGeneration:2, reopens:1, error:WALLET_NEVER_ASKED});
console.log('IR9545-RESULT ' + JSON.stringify({first:!!first, second:!!second, lines:confirmNoticesHtml()}));
""", NOTICES)
    assert out["first"] and out["second"] and out["lines"].count("Starting again is safe") == 1, out


def test_first_upgrade_suppresses_old_never_asked_notice():
    old_rows = r"""
const ops = [['custom_json', {id:'sm_claim_rewards', required_auths:[], required_posting_auths:['alice'], json:'{"n":"aaaaaaaaaaaaaaaa"}'}]];
const id = JSON.stringify(['alice', ops]);
let a=0x811c9dc5, b=0x9e3779b1;
for (const c of id) { a=Math.imul(a ^ c.charCodeAt(0),16777619); b=Math.imul(b ^ c.charCodeAt(0),2246822519); }
const key=(a>>>0).toString(16).padStart(8,'0')+(b>>>0).toString(16).padStart(8,'0');
const at=new Date(Date.now()-3600000).toISOString();
__ceresLocalStorage.setItem('ceres:confirm:v2:'+key, JSON.stringify({id, account:'alice', attempt:1, attemptId:'a'.repeat(16), operations:ops,
  state:'never-sent', neverSentBy:'wallet-expired', error:'old', orphan:true, since:at, updatedAt:at, rev:1}));
__ceresLocalStorage.setItem('ceres:confirm:v2-index', JSON.stringify([id]));
"""
    out = _module("console.log('IR9545-RESULT ' + JSON.stringify({lines:confirmNoticesHtml()}));", NOTICES, old_rows)
    assert out["lines"] == "", out


def test_action_match_retracts_other_attempt_ending_before_retry():
    out = _module(r"""
const op = n => [['custom_json', {id:'sm_claim_rewards', required_auths:[], required_posting_auths:['alice'], json:JSON.stringify({n})}]];
const a=op('aaaaaaaaaaaaaaaa'), b=op('bbbbbbbbbbbbbbbb'), actionId='c'.repeat(32);
beginAttempt('alice', a, {actionId});
beginAttempt('alice', b, {actionId});
markConfirmation('alice', a, 'never-sent', {by:'wallet-expired'});
markConfirmation('alice', b, 'never-sent', {by:'wallet-expired'});
fetch = async url => ({ok:true, status:200, json:async()=>String(url).includes('/api/confirm/find')
  ? {status:'found', tx_id:'d'.repeat(40), action_id:actionId, server_now:Date.now()} : {}});
await findSignedTransaction('alice', a);
let retryBlocked=false;
try { beginAttempt('alice', b, {actionId}); } catch (_) { retryBlocked=true; }
console.log('IR9545-RESULT ' + JSON.stringify({a:confirmationState('alice',a)?.state,
  b:confirmationState('alice',b)?.state, retryBlocked}));
""", CONFIRMER)
    assert out == {"a": "pending", "b": "pending", "retryBlocked": True}, out


def test_held_claim_never_spins_on_zero_delay_after_never_asked():
    out = _module(r"""
const ops=[['custom_json', {id:'sm_land_operation', required_auths:[], required_posting_auths:['alice'],
  json:JSON.stringify({op:'harvest_all',n:'aaaaaaaaaaaaaaaa'})}]];
const sleeps=[];
__setConfirmerClock({now:()=>Date.now(), sleep:ms=>{sleeps.push(ms); return new Promise(()=>{});}});
beginAttempt('alice', ops, {holdsServerClaim:true});
fetch = async () => ({ok:true, status:200, json:async()=>({status:'wait', next_in_s:0,
  unanswered_for_s:3901, server_now:Date.now()})});
findSignedTransaction('alice', ops).catch(()=>{});
for(let i=0;i<20;i++) await Promise.resolve();
const row=confirmationState('alice',ops);
console.log('IR9545-RESULT ' + JSON.stringify({state:row?.state, held:!!row?.neverAskedAt,
  paced:sleeps.includes(15000), reads:sleeps.length}));
""", CONFIRMER)
    assert out["state"] == "pending" and out["held"] and out["paced"] and out["reads"] < 10, out


def test_duplicate_watch_uses_server_coverage_and_keeps_each_game_verdict():
    out = _module(r"""
const ops=[['custom_json', {id:'sm_claim_rewards', required_auths:[], required_posting_auths:['alice'],
  json:JSON.stringify({n:'a'.repeat(16)})}]];
const actionId='c'.repeat(32), first='1'.repeat(40), second='2'.repeat(40);
const secondOps=[['custom_json', {...ops[0][1], json:JSON.stringify({n:'b'.repeat(16)})}]];
let finds=0, verdicts=0;
__setConfirmerClock({now:()=>Date.now(), sleep:()=>Promise.resolve()});
beginAttempt('alice',ops,{actionId});
fetch=async url=>{
  if(String(url).includes('/api/confirm/find')) {
    finds++;
    return {ok:true,status:200,json:async()=>({status:'found',tx_id:first,action_id:actionId,
      matched_tx_ids:finds===1?[first]:[first,second],
      matched_transactions:finds===1?[{tx_id:first,operations:ops}]:[{tx_id:first,operations:ops},{tx_id:second,operations:secondOps}],
      watch_duplicates:finds===1,next_in_s:0})};
  }
  if(String(url).includes('/api/confirm/verdict')) {
    verdicts++;
    return {ok:true,status:200,json:async()=>({tx_id:second,operations:[{index:0,state:'refused',error:'Duplicate'}]})};
  }
  return {ok:true,status:200,json:async()=>({})};
};
await findSignedTransaction('alice',ops);
for(let i=0;i<100;i++) await Promise.resolve();
const rows=allConfirmations().filter(row=>row.txId===second);
console.log('IR9545-RESULT '+JSON.stringify({finds,verdicts,extra:rows.length,state:rows[0]?.state,
  watching:confirmationState('alice',ops)?.watchDuplicates}));
""", CONFIRMER)
    assert out["finds"] >= 2 and out["verdicts"] >= 1, out
    assert out["extra"] == 1 and out["state"] == "refused" and out["watching"] is False, out
