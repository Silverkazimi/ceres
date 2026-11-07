"""Revision 6 §2: the real confirmer carries a review's action id across changed vectors."""

from pathlib import Path

from tests.ceres.vm_context import run_in_module_context


STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_changed_terms_explicitly_keep_action_and_deliberate_repeat_does_not():
    run_in_module_context(r"""
const calls = [];
const firstId = 'a'.repeat(32);
const repeatedId = 'b'.repeat(32);
fetch = async (_url, init) => {
  const body = JSON.parse(init.body);
  calls.push(body);
  return { ok: true, json: async () => ({ begin_token: 'c'.repeat(32),
    action_id: body.action_id || (calls.length === 1 ? firstId : repeatedId),
    revision: body.action_id ? calls.length : 1, server_now: Date.now() }) };
};
const op = (nonce, price, survivors, envelope) => ['custom_json', { id: 'sm_land_operation',
  json: JSON.stringify({ op: 'buy', n: nonce, price, survivors, envelope }),
  required_auths: [], required_posting_auths: ['alice'] }];
(async () => {
  const original = [op('nonce-one-01', 1, ['A', 'B'], 'head-1')];
  beginAttempt('alice', original);
  if (!await announceBegin('alice', original)) throw Error('first pre-sign begin failed');
  const actionId = confirmationState('alice', original).actionId;
  if (actionId !== firstId) throw Error('first action id missing');
  const revisions = [
    [op('nonce-two-02', 2, ['A', 'B'], 'head-1')],
    [op('nonce-three-03', 2, ['A'], 'head-1')],
    [op('nonce-four-04', 2, ['A'], 'head-2')],
  ];
  for (const [index, changed] of revisions.entries()) {
    beginAttempt('alice', changed, { actionId });
    if (!await announceBegin('alice', changed)) throw Error('revised pre-sign begin failed');
    if (calls[index + 1].action_id !== firstId || confirmationState('alice', changed).actionRevision !== index + 2)
      throw Error('changed terms lost immutable action lineage');
  }
  const repeat = [op('nonce-five-05', 2, ['A'], 'head-2')];
  beginAttempt('alice', repeat);
  if (!await announceBegin('alice', repeat)) throw Error('repeat pre-sign begin failed');
  if (calls[4].action_id !== undefined || confirmationState('alice', repeat).actionId !== repeatedId)
    throw Error('deliberate repeat reused the old action id');
  if (new Set([original, ...revisions, repeat].map(ops => JSON.parse(ops[0][1].json).n)).size !== 5)
    throw Error('wallet vectors reused a signed nonce');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=STATIC / "shared-confirmer.js")
