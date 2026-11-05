import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../../ceres/static/shared-confirmer.js', import.meta.url), 'utf8');
const frozenNow = Date.parse('2026-11-03T09:00:00.000Z');
globalThis.Date = class FrozenDate extends Date {
  constructor(...args) { super(...(args.length ? args : [frozenNow])); }
  static now() { return frozenNow; }
};
const rows = new Map();
const listeners = new Map();
const saved = { getItem: key => rows.get(key) ?? null, setItem: (key, value) => rows.set(key, value) };
globalThis.localStorage = saved;
globalThis.window = { addEventListener: (name, fn) => listeners.set(name, fn),
  removeEventListener: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); },
  dispatchEvent: event => listeners.get(event.type)?.(event) };
globalThis.document = { visibilityState: 'hidden' };
globalThis.CustomEvent = class { constructor(name, options) { this.type = name; this.detail = options.detail; } };
const timers = [];
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
const module = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const op = ['custom_json', { id: 'sm_land_operation', required_auths: [], required_posting_auths: ['alice'], json: JSON.stringify({ n: 'a'.repeat(16) }) }];

async function settle() { for (let i = 0; i < 60; i++) await Promise.resolve(); }   // bounded requests add a few awaits (fix round 1)

test('lost ack, one failed lookup, then pageshow immediately finds exact signed action', async () => {
  let calls = 0;
  globalThis.fetch = async (url, request) => {
    assert.match(url, /^\/api\/confirm\/find\?player=alice$/);
    assert.deepEqual(JSON.parse(request.body).operations, [op]);
    calls += 1;
    if (calls === 1) throw Error('service restarting');
    return { ok: true, json: async () => ({ status: 'found', tx_id: 'b'.repeat(40) }) };
  };
  module.trackSigning('Alice', [op]);
  const unresolved = module.findSignedTransaction('alice', [op]);
  await settle();
  assert.equal(calls, 1);
  assert.equal(module.confirmationState('alice', [op]).state, 'pending');
  listeners.get('pageshow')();
  await settle();
  assert.equal(calls, 2);
  assert.equal(module.confirmationState('alice', [op]).txId, 'b'.repeat(40));
  assert.equal(module.confirmationState('alice', [op]).state, 'pending');
  // Drain the fake retry clock; it observes the id found on pageshow.
  for (const timer of timers.splice(0)) timer.fn();
  assert.equal(await unresolved, 'b'.repeat(40));
});

test('states are distinct and an explicit refusal is retained', () => {
  assert.equal(module.markConfirmation('alice', [op], 'refused', { error: 'Game refused this operation' }).state, 'refused');
  assert.equal(module.confirmationState('alice', [op]).error, 'Game refused this operation');
  assert.throws(() => module.markConfirmation('alice', [op], 'unknown'));
});

test('known tx serves each game operation independently, including the -1 refusal', async () => {
  const tx = 'c'.repeat(40);
  const second = ['custom_json', { ...op[1], json: JSON.stringify({ n: 'b'.repeat(16) }) }];
  let readCount = 0;
  globalThis.fetch = async (url, request) => {
    assert.match(url, /^\/api\/confirm\/verdict\?player=bob$/);
    assert.deepEqual(JSON.parse(request.body).operations, [op, second]);
    readCount += 1;
    return { ok: true, json: async () => ({ tx_id: tx, operations: [
      { index: 0, lookup_id: tx, state: 'confirmed', result: { amount: 3 } },
      { index: 1, lookup_id: `${tx}-1`, state: readCount === 1 ? 'pending' : 'refused', error: 'Game rejected op 2' },
    ] }) };
  };
  const observed = [];
  const stop = module.subscribeConfirmation('bob', [op, second], row => observed.push(row.state));
  const final = module.confirmAction({ account: 'bob', operations: [op, second], txId: tx });
  await settle();
  assert.equal(module.confirmationState('bob', [op, second]).state, 'pending');
  assert.equal(module.confirmationState('bob', [op, second]).perOp[0].result.amount, 3);
  listeners.get('pageshow')();
  await settle();
  assert.equal(module.confirmationState('bob', [op, second]).state, 'refused');
  assert.equal(module.confirmationState('bob', [op, second]).perOp[1].error, 'Game rejected op 2');
  assert.ok(observed.includes('refused'));
  assert.equal((await final).state, 'refused');
  stop();
});
