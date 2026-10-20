from pathlib import Path

from tests.ceres.vm_context import run_in_module_context


CHAIN = Path(__file__).parents[2] / "ceres" / "static" / "routing-signature-chain.js"
QUEUE = Path(__file__).parents[2] / "ceres" / "static" / "transaction-queue.js"


def test_next_prompt_opens_before_previous_submission_resolves():
    run_in_module_context(r"""
(async () => {
 const events = [];
 let firstSubmitted = false;
 let resolveFirstSubmission;
 const firstSubmission = new Promise(resolve => { resolveFirstSubmission = resolve; });
 const statuses = [];
 const chain = createRoutingSignatureChain({
 sign: async item => {
 events.push(`prompt:${item.txId}`);
 if (item.txId === 'tx-two' && firstSubmitted) throw new Error('second prompt waited for submission');
 return { txId: item.txId, submitted: item.txId === 'tx-one' ? firstSubmission : Promise.resolve({ txId: item.txId }) };
 },
 onStepChange: update => statuses.push(`${update.id}:${update.state}`),
 });
 const running = chain.run([{ txId: 'tx-one' }, { txId: 'tx-two' }]);
 await Promise.race([
 running,
 new Promise((_, reject) => setTimeout(() => reject(new Error('chain waited for submission')), 100)),
 ]);
 if (events.join(',') !== 'prompt:tx-one,prompt:tx-two') throw new Error(events.join(','));
 if (firstSubmitted) throw new Error('first submission resolved unexpectedly');
 if (!statuses.includes('tx-one:signed') || !statuses.includes('tx-two:signed')) throw new Error(statuses.join(','));
 resolveFirstSubmission({ txId: 'tx-one' });
 firstSubmitted = true;
 await Promise.resolve();
 if (!statuses.includes('tx-one:submitted')) throw new Error(statuses.join(','));
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=CHAIN)


def test_chain_stops_when_second_signature_is_rejected():
    run_in_module_context(r"""
(async () => {
 const prompts = [];
 const chain = createRoutingSignatureChain({
 sign: async item => {
 prompts.push(item.txId);
 if (item.txId === 'tx-two') throw new Error('wallet rejected');
 return { txId: item.txId, submitted: Promise.resolve({ txId: item.txId }) };
 },
 });
 const error = await chain.run([{ txId: 'tx-one' }, { txId: 'tx-two' }, { txId: 'tx-three' }]).catch(value => value);
 if (error.message !== 'wallet rejected') throw error;
 if (prompts.join(',') !== 'tx-one,tx-two') throw new Error(prompts.join(','));
 if (!chain.stopped || chain.getState('tx-two')?.state !== 'rejected') throw new Error('rejection state missing');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=CHAIN)


def test_chain_stops_and_marks_an_explicit_wallet_cancellation():
    run_in_module_context(r"""
(async () => {
 const prompts = [];
 const chain = createRoutingSignatureChain({
 sign: async item => {
 prompts.push(item.txId);
 const error = new Error('wallet cancelled');
 error.cancelled = true;
 throw error;
 },
 });
 await chain.run([{ txId: 'tx-one' }, { txId: 'tx-two' }]).catch(() => {});
 if (prompts.join(',') !== 'tx-one') throw new Error(prompts.join(','));
 if (chain.getState('tx-one')?.state !== 'cancelled') throw new Error('cancelled state missing');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=CHAIN)


def test_attachment_failure_stops_after_signature_without_reporting_wallet_rejection():
    run_in_module_context(r"""
(async () => {
 const prompts = [];
 const statuses = [];
 const submitted = Promise.resolve({ txId: 'stable-one' });
 const chain = createRoutingSignatureChain({
 sign: async item => {
 prompts.push(item.txId);
 const error = new Error('receipt attachment unavailable');
 error.txId = item.txId;
 error.signedResult = { txId: item.txId, submitted };
 throw error;
 },
 onStepChange: update => statuses.push(`${update.id}:${update.state}`),
 });
 const error = await chain.run([{ txId: 'stable-one' }, { txId: 'stable-two' }]).catch(value => value);
 await Promise.resolve();
 if (error.message !== 'receipt attachment unavailable') throw error;
 if (prompts.join(',') !== 'stable-one' || !chain.stopped) throw new Error(prompts.join(','));
 if (!statuses.includes('stable-one:signed') || !statuses.includes('stable-one:submitted')) throw new Error(statuses.join(','));
 if (statuses.some(status => /:rejected$/.test(status))) throw new Error(statuses.join(','));
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=CHAIN)


def test_stable_ids_are_never_signed_twice():
    run_in_module_context(r"""
(async () => {
 const prompts = [];
 const chain = createRoutingSignatureChain({
 sign: async item => {
 prompts.push(item.txId);
 return { txId: item.txId, submitted: Promise.resolve({ txId: item.txId }) };
 },
 });
 const first = { txId: 'stable-one' };
 await chain.run([first, first, { txId: 'stable-two' }]);
 await chain.run([{ txId: 'stable-one' }, { txId: 'stable-two' }]);
 if (prompts.join(',') !== 'stable-one,stable-two') throw new Error(prompts.join(','));
 if (!chain.hasHandled('stable-one') || chain.snapshot().length !== 2) throw new Error('stable state missing');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=CHAIN)


def test_release_dependency_preserves_valid_child_and_expires_stale_child_without_resigning():
    run_in_module_context(r"""
(async () => {
 navigator.locks = { request: async (_name, _options, callback) => callback() };
 const account = 'player0005';
 const parentId = '1'.repeat(40);
 const childId = '2'.repeat(40);
 const futureId = '3'.repeat(40);
 const old = '2000-01-01T00:00:00';
 const future = '2999-01-01T00:00:00';
 const transaction = expiration => ({expiration, operations: [], extensions: []});
 const signed = expiration => ({...transaction(expiration), signatures: ['a'.repeat(130)]});
 const rows = [
 {account, txId: parentId, state: 'unknown', transaction: transaction(old), signedTransaction: null},
 {account, txId: childId, state: 'queued', dependsOn: parentId, transaction: transaction(old), signedTransaction: signed(old)},
 {account, txId: futureId, state: 'queued', dependsOn: parentId, transaction: transaction(future), signedTransaction: signed(future)},
 ];
 localStorage.setItem('ceres:signed-queue:v1:' + account, JSON.stringify(rows));
 const expired = await releaseSignedQueueDependency(account, childId, parentId);
 if (!expired.notSent || expired.state !== 'expired') throw new Error(JSON.stringify(expired));
 let saved = signedQueueRows(account);
 const stale = saved.find(row => row.txId === childId);
 if (stale.dependsOn !== null || stale.releasedDependency !== parentId || stale.signedTransaction !== null || stale.state !== 'expired') throw new Error(JSON.stringify(stale));
 const retrySync = await releaseSignedQueueDependency(account, childId, parentId);
 if (!retrySync.notSent || retrySync.state !== 'expired') throw new Error(JSON.stringify(retrySync));
 const parentBefore = JSON.stringify(saved.find(row => row.txId === parentId));
 const released = await releaseSignedQueueDependency(account, futureId, parentId);
 if (released.notSent || released.state !== 'queued') throw new Error(JSON.stringify(released));
 saved = signedQueueRows(account);
 const valid = saved.find(row => row.txId === futureId);
 if (valid.dependsOn !== null || valid.txId !== futureId || JSON.stringify(valid.signedTransaction) !== JSON.stringify(signed(future))) throw new Error(JSON.stringify(valid));
 if (JSON.stringify(saved.find(row => row.txId === parentId)) !== parentBefore) throw new Error('unknown parent changed');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=QUEUE)


def test_routing_controller_finds_and_releases_the_queued_child_not_the_unknown_parent():
    routing = Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js"
    run_in_module_context(r"""
(async () => {
 const parentId = '1'.repeat(40), childId = '2'.repeat(40);
 const run = {run_id: 'a'.repeat(32), state: 'pending', phase: 'after', signatures_total: 2,
 signed_signatures: 2, confirmed_signatures: 0, scope: {kind: 'regions', region_uids: ['PR-BRL-41']},
 phase_steps: {before: []}, steps: [
 {step_id: 'b'.repeat(32), state: 'pending', kind: 'sell', tx_id: parentId},
 {step_id: 'c'.repeat(32), state: 'submitted', kind: 'sell', tx_id: childId},
 ]};
 const rows = [
 {account: 'player0005', txId: parentId, state: 'unknown', transaction: {expiration: '2000-01-01T00:00:00'}},
 {account: 'player0005', txId: childId, state: 'queued', dependsOn: parentId,
 transaction: {expiration: '2999-01-01T00:00:00'}, signedTransaction: {signatures: ['a'.repeat(130)]}},
 ];
 signedQueueRows = () => rows;
 let released = null;
 releaseSignedQueueDependency = async (_account, txId, dependsOn) => {
 released = {txId, dependsOn}; return {txId, dependsOn, state: 'queued', notSent: false};
 };
 globalThis.fetch = window.fetch = async (url) => {
 const action = String(url).match(/\/([^/?]+)\?player=/)?.[1];
 const data = action === 'state'
 ? {regions: [], inventory: {}, pools: [], signing_enabled: true, issues: []}
 : action === 'runs' ? {runs: [run]} : {logged: true};
 return {ok: true, status: 200, json: async () => data};
 };
 const controller = createHarvestRouting({player: 'player0005', scope: run.scope, activeRunId: run.run_id,
 onRender() {}, onBack() {}, onDone() {}, onHarvest() {}, isCurrent: () => true});
 await controller.load();
 if (!controller.html().includes('data-hr="release"')) throw new Error('Release is not visible for queued child');
 await controller.action('release', null);
 if (released?.txId !== childId || released?.dependsOn !== parentId) throw new Error(JSON.stringify(released));
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=routing)


def test_real_routing_action_composes_handoff_queue_attach_and_next_review():
    routing = Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js"
    run_in_module_context(r"""
(async () => {
 const runId = 'a'.repeat(32);
 const firstId = 'b'.repeat(32);
 const secondId = 'c'.repeat(32);
 const reviews = {
 first: {digest: 'review-one', member_ids: [firstId], operations: [['custom_json', {id: 'sm_land_operation', json: '{"n":"one"}'}]], authority: 'Posting'},
 second: {digest: 'review-two', member_ids: [secondId], operations: [['custom_json', {id: 'sm_land_operation', json: '{"n":"two"}'}]], authority: 'Posting'},
 };
 const run = {run_id: runId, player: 'player0005', state: 'ready', phase: 'after',
 scope: {kind: 'regions', region_uids: ['PR-BRL-41']}, phase_steps: {before: []}, group_review: reviews.first,
 steps: [
 {step_id: firstId, state: 'reviewed', kind: 'sell', review: reviews.first},
 {step_id: secondId, state: 'planned', kind: 'sell'},
 ]};
 const events = [];
 let reviewCalls = 0;
 let firstSubmitted = false;
 let resolveFirst;
 const firstSubmission = new Promise(resolve => { resolveFirst = resolve; });
 walletReady = () => true;
 signedQueueRows = () => [];
 prepareSigningBatch = async (_account, groups) => [{
 account: 'player0005', authority: 'Posting', transaction: {operations: groups[0]},
 txId: groups[0][0][1].json.includes('one') ? '1'.repeat(40) : '2'.repeat(40),
 }];
 requestSignatureThroughGate = async (_account, operations, _callback, _authority, envelope) => {
 events.push(`prompt:${envelope.txId}`);
 if (envelope.txId === '2'.repeat(40) && firstSubmitted) throw new Error('second prompt waited for admission');
 return {txId: envelope.txId, queued: true,
 submitted: envelope.txId === '1'.repeat(40) ? firstSubmission : Promise.resolve({txId: envelope.txId})};
 };
 globalThis.fetch = window.fetch = async (url, options = {}) => {
 const action = String(url).match(/\/([^/?]+)\?player=/)?.[1];
 const body = options.body ? JSON.parse(options.body) : {};
 let data;
 if (action === 'state') data = {regions: [], inventory: {}, pools: [], signing_enabled: true, issues: []};
 else if (action === 'runs') data = {player: 'player0005', runs: [run]};
 else if (action === 'handoff') {
 const first = body.review_digest === reviews.first.digest;
 data = {run_id: runId, step_id: first ? firstId : secondId,
 operations: (first ? reviews.first : reviews.second).operations, authority: 'Posting'};
 events.push(`handoff:${first ? firstId : secondId}`);
 } else if (action === 'transaction') {
 const step = run.steps.find(item => item.step_id === body.step_id);
 step.state = 'submitted'; step.tx_id = body.tx_id;
 events.push(`attach:${body.step_id}`);
 data = run;
 } else if (action === 'prefetch') {
 const first = body.step_id === firstId;
 if (first) {
 run.steps[1].state = 'reviewed'; run.steps[1].review = reviews.second; run.group_review = reviews.second;
 data = {run, review: reviews.second, prefetched: true};
 } else data = {run, review: null, prefetched: false};
 events.push(`prefetch:${body.step_id}`);
 } else if (action === 'review') {
 reviewCalls += 1;
 delete run.group_review; data = {run, review: null};
 events.push(`review:${reviewCalls}`);
 } else throw new Error(`unexpected API action: ${action}`);
 return {ok: true, json: async () => data};
 };
 const instance = createHarvestRouting({player: 'player0005', scope: run.scope, activeRunId: runId,
 onRender() {}, onBack() {}, onDone() {}, onHarvest() {}, isCurrent: () => true});
 await instance.load();
 await instance.action('sign', {});
 const expected = [`handoff:${firstId}`, `prefetch:${firstId}`, `prompt:${'1'.repeat(40)}`, `attach:${firstId}`,
 `handoff:${secondId}`, `prefetch:${secondId}`, `prompt:${'2'.repeat(40)}`, `attach:${secondId}`, 'review:1'];
 if (events.join('|') !== expected.join('|')) throw new Error(events.join('|'));
 if (firstSubmitted) throw new Error('first admission completed before second prompt');
 resolveFirst({txId: '1'.repeat(40)}); firstSubmitted = true;
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=routing)


def test_one_consent_skips_an_empty_optional_group_and_prompts_for_the_next_group():
    routing = Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js"
    run_in_module_context(r"""
(async () => {
 const runId = 'a'.repeat(32), optionalId = 'b'.repeat(32), requiredId = 'c'.repeat(32);
 const firstReview = {digest: 'review-optional', member_ids: [optionalId],
 operations: [['custom_json', {id: 'sm_land_operation', json: '{"n":"optional"}'}]], authority: 'Posting'};
 const secondReview = {digest: 'review-required', member_ids: [requiredId],
 operations: [['custom_json', {id: 'sm_land_operation', json: '{"n":"required"}'}]], authority: 'Posting'};
 const run = {run_id: runId, state: 'ready', phase: 'after', signatures_total: 2,
 signed_signatures: 0, confirmed_signatures: 0, scope: {kind: 'regions', region_uids: ['PR-BRL-41']},
 phase_steps: {before: []}, group_review: firstReview, steps: [
 {step_id: optionalId, state: 'reviewed', kind: 'sell', review: firstReview,
 quote: {source_symbol: 'WOOD', destination_symbol: 'DEC', external: false}},
 {step_id: requiredId, state: 'planned', kind: 'sell', two_step: {role: 'first', final_symbol: 'SPS'},
 quote: {source_symbol: 'STONE', destination_symbol: 'DEC', external: false}},
 ]};
 walletReady = () => true;
 const handoffs = [], prompts = [];
 let reviewCalls = 0;
 prepareSigningBatch = async () => [{account: 'player0005', authority: 'Posting',
 transaction: {operations: secondReview.operations}, txId: '1'.repeat(40)}];
 requestSignatureThroughGate = async (_account, _operations, _callback, authority, envelope) => {
 prompts.push(authority); return {txId: envelope.txId, queued: true, submitted: Promise.resolve({txId: envelope.txId})};
 };
 globalThis.fetch = window.fetch = async (url, options = {}) => {
 const action = String(url).match(/\/([^/?]+)\?player=/)?.[1];
 const body = options.body ? JSON.parse(options.body) : {};
 let data;
 if (action === 'state') data = {regions: [], inventory: {}, pools: [], signing_enabled: true, issues: []};
 else if (action === 'runs') data = {runs: [run]};
 else if (action === 'handoff') {
 handoffs.push(body.member_ids);
 if (!body.member_ids.length) {
 run.steps[0].state = 'skipped'; run.signatures_total = 1; data = {run_id: runId, skipped: true, run};
 } else data = {run_id: runId, step_id: requiredId, operations: secondReview.operations, authority: 'Posting'};
 } else if (action === 'review') {
 reviewCalls += 1;
 if (reviewCalls === 1) {
 run.steps[1].state = 'reviewed'; run.steps[1].review = secondReview; run.group_review = secondReview;
 data = {run, review: secondReview};
 } else data = {run, review: null};
 } else if (action === 'transaction') {
 run.steps[1].state = 'submitted'; run.steps[1].tx_id = body.tx_id; data = run;
 } else if (action === 'failure') data = {logged: true};
 else throw new Error(`unexpected ${action}`);
 return {ok: true, status: 200, json: async () => data};
 };
 const controller = createHarvestRouting({player: 'player0005', scope: run.scope, activeRunId: runId,
 onRender() {}, onBack() {}, onDone() {}, onHarvest() {}, isCurrent: () => true});
 await controller.load();
 controller.change({dataset: {hrMember: optionalId}, checked: false});
 if (/data-hr="sign"[^>]* disabled/.test(controller.footerHtml())) throw new Error('consent disabled');
 await controller.action('sign', null);
 if (JSON.stringify(handoffs) !== JSON.stringify([[], [requiredId]])) throw new Error(JSON.stringify(handoffs));
 if (prompts.join(',') !== 'Posting') throw new Error(prompts.join(','));
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=routing)


def test_routing_controller_exposes_one_consent_plain_states_and_chained_active_funding():
    routing = Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js"
    run_in_module_context(r"""
(async () => {
 walletReady = () => true;
 const stepId = 'b'.repeat(32);
 const review = {digest: 'route-review', member_ids: [stepId], operations: [], authority: 'Posting'};
 const baseRun = {
 run_id: 'a'.repeat(32), phase: 'after', signatures_total: 3, signed_signatures: 1,
 confirmed_signatures: 1, scope: {kind: 'regions', region_uids: ['PR-BRL-41']},
 phase_steps: {before: []}, group_review: review,
 steps: [{step_id: stepId, state: 'reviewed', kind: 'sell', review}],
 };
 let currentRun = baseRun;
 globalThis.fetch = window.fetch = async (url) => {
 const action = String(url).match(/\/([^/?]+)\?player=/)?.[1];
 if (action === 'state') return {ok: true, json: async () => ({regions: [], inventory: {}, pools: [], signing_enabled: true, issues: []})};
 if (action === 'runs') return {ok: true, json: async () => ({runs: [currentRun]})};
 throw new Error(`unexpected API action: ${action}`);
 };
 const render = async run => {
 currentRun = run;
 const controller = createHarvestRouting({player: 'player0005', scope: run.scope, activeRunId: run.run_id,
 onRender() {}, onBack() {}, onDone() {}, onHarvest() {}, isCurrent: () => true});
 await controller.load();
 return controller.html() + controller.footerHtml();
 };
 const reviewed = await render(baseRun);
 if ((reviewed.match(/data-hr="sign"/g) || []).length !== 1) throw new Error('one consent button missing');
 for (const [needle, state, stepState] of [
 ['Waiting for your signature', 'ready', 'reviewed'],
 ['Confirming with the game', 'ready', 'pending'],
 ['Waiting for the game to settle the DEC → SPS swap and prepare the SPS funding transfer.', 'ready', 'settling'],
 ['Needs attention', 'failed', 'failed'],
 ['Done', 'complete', null],
 ]) {
 const run = JSON.parse(JSON.stringify(baseRun));
 run.state = state;
 run.steps = stepState ? [{...run.steps[0], state: stepState}] : [];
 const html = await render(run);
 if (!html.includes(needle)) throw new Error(`${needle} missing`);
 }
 if (!reviewed.includes('Sign routing · 3 approvals')) throw new Error('approval count missing');
 if (!reviewed.includes('Signed 1 of 3 · Confirmed 1 of 3')) throw new Error('signature counters missing');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=routing)


def test_stub_wallet_prompt_gap_harness_reports_before_and_after():
    routing = Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js"
    result = run_in_module_context(r"""
(async () => {
 const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
 const REVIEW_DELAY_MS = 80;
 const WALLET_DELAY_MS = 120;
 const runCase = async fastPath => {
 const runId = (fastPath ? 'f' : 'b').repeat(32);
 const firstId = '1'.repeat(32), secondId = '2'.repeat(32);
 const firstReview = {digest: 'first-' + fastPath, member_ids: [firstId], operations: [['custom_json', {id: 'sm_land_operation', required_auths: [], required_posting_auths: ['player0005'], json: '{"n":"one"}'}]], authority: 'Posting'};
 const secondReview = {digest: 'second-' + fastPath, member_ids: [secondId], operations: [['custom_json', {id: 'sm_land_operation', required_auths: [], required_posting_auths: ['player0005'], json: '{"n":"two"}'}]], authority: 'Posting'};
 const run = {run_id: runId, player: 'player0005', state: 'ready', phase: 'after', signatures_total: 2, signed_signatures: 0, confirmed_signatures: 0,
 scope: {kind: 'regions', region_uids: ['PR-BRL-41']}, phase_steps: {before: []}, group_review: firstReview,
 steps: [{step_id: firstId, state: 'reviewed', kind: 'sell', review: firstReview}, {step_id: secondId, state: 'planned', kind: 'sell', review: null}]};
 const prompts = [];
 let reviewCalls = 0;
 walletReady = () => true;
 signedQueueRows = () => [];
 prepareSigningBatch = async (_account, groups) => { await wait(5); return [{account: 'player0005', authority: 'Posting', transaction: {operations: groups[0]}, txId: groups[0][0][1].json.includes('one') ? 'a'.repeat(40) : 'b'.repeat(40)}]; };
 requestSignatureThroughGate = async (_account, _operations, _callback, _authority, envelope) => {
 const prompt = {txId: envelope.txId, at: performance.now()}; prompts.push(prompt); await wait(WALLET_DELAY_MS); prompt.closedAt = performance.now();
 return {txId: envelope.txId, queued: true, submitted: Promise.resolve({txId: envelope.txId})};
 };
 globalThis.fetch = window.fetch = async (url, options = {}) => {
 const action = String(url).match(/\/([^/?]+)\?player=/)?.[1];
 const body = options.body ? JSON.parse(options.body) : {};
 let data;
 if (action === 'state') data = {regions: [], inventory: {}, pools: [], signing_enabled: true, issues: []};
 else if (action === 'runs') data = {runs: [run]};
 else if (action === 'handoff') {
 await wait(5);
 const first = body.review_digest === firstReview.digest;
 const review = first ? firstReview : secondReview;
 data = {run_id: runId, step_id: first ? firstId : secondId, operations: review.operations, authority: 'Posting'};
 } else if (action === 'transaction') {
 await wait(5);
 const step = run.steps.find(item => item.step_id === body.step_id);
 step.state = 'submitted'; step.tx_id = body.tx_id;
 data = {...run};
 } else if (action === 'prefetch') {
 const first = body.step_id === firstId;
 if (fastPath && first) {
 await wait(REVIEW_DELAY_MS);
 run.steps[1].state = 'reviewed'; run.steps[1].review = secondReview; run.group_review = secondReview;
 data = {run, review: secondReview, prefetched: true};
 } else data = {run, review: null, prefetched: false};
 } else if (action === 'review') {
 reviewCalls += 1;
 await wait(REVIEW_DELAY_MS);
 if (run.steps[1].state === 'submitted') data = {run, review: null};
 else { run.steps[1].state = 'reviewed'; run.steps[1].review = secondReview; run.group_review = secondReview; data = {run, review: secondReview}; }
 } else throw new Error(`unexpected API action: ${action}`);
 return {ok: true, status: 200, json: async () => data};
 };
 const controller = createHarvestRouting({player: 'player0005', scope: run.scope, activeRunId: runId,
 onRender() {}, onBack() {}, onDone() {}, onHarvest() {}, isCurrent: () => true});
 await controller.load();
 await controller.action('sign', null);
 return {prompts, reviewCalls};
 };
 const before = await runCase(false);
 const after = await runCase(true);
 const beforeGap = before.prompts[1].at - before.prompts[0].closedAt;
 const afterGap = after.prompts[1].at - after.prompts[0].closedAt;
 console.log('PROMPT_GAPS ' + JSON.stringify({beforeGap: Math.round(beforeGap), afterGap: Math.round(afterGap), REVIEW_DELAY_MS}));
 if (before.reviewCalls !== 2 || after.reviewCalls !== 1 || before.prompts.length !== 2 || after.prompts.length !== 2
 || !(beforeGap >= REVIEW_DELAY_MS && beforeGap > afterGap * 2)) {
 throw new Error(JSON.stringify({beforeGap, afterGap, before, after, REVIEW_DELAY_MS}));
 }
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=routing)
    print(result.stdout, end='')


def test_one_consent_auto_prompts_for_active_funding_after_settlement():
    routing = Path(__file__).parents[2] / "ceres" / "static" / "harvest-routing.js"
    run_in_module_context(r"""
(async () => {
 const runId = 'a'.repeat(32), stepId = 'b'.repeat(32);
 const review = {digest: 'route-review', member_ids: [stepId], operations: [['custom_json', {id: 'sm_land_operation', json: '{"n":"route"}'}]], authority: 'Posting'};
 const run = {run_id: runId, state: 'ready', phase: 'after', signatures_total: 2, signed_signatures: 0,
 confirmed_signatures: 0, scope: {kind: 'regions', region_uids: ['PR-BRL-41']}, phase_steps: {before: []},
 group_review: review, steps: [{step_id: stepId, state: 'reviewed', kind: 'sell', review}]};
 const prompts = [];
 walletReady = () => true;
 signedQueueRows = () => [];
 prepareSigningBatch = async (_account, groups, authority) => [{account: 'player0005', authority,
 transaction: {operations: groups[0]}, txId: (authority === 'Active' ? '2' : '1').repeat(40)}];
 requestSignatureThroughGate = async (_account, _operations, _callback, authority, envelope) => {
 prompts.push(authority);
 return {txId: envelope.txId, queued: true, submitted: Promise.resolve({txId: envelope.txId})};
 };
 globalThis.fetch = window.fetch = async (url, options = {}) => {
 const action = String(url).match(/\/([^/?]+)\?player=/)?.[1];
 const body = options.body ? JSON.parse(options.body) : {};
 let data;
 if (action === 'state') data = {regions: [], inventory: {}, pools: [], signing_enabled: true, issues: []};
 else if (action === 'runs') data = {runs: [run]};
 else if (action === 'handoff') data = {run_id: runId, step_id: stepId, operations: review.operations, authority: 'Posting'};
 else if (action === 'transaction') { run.steps[0].state = 'submitted'; run.steps[0].tx_id = body.tx_id; data = run; }
 else if (action === 'prefetch') data = {run, review: null, prefetched: false};
 else if (action === 'review') data = {run, review: null};
 else if (action === 'verify') {
 run.steps[0].state = 'settling';
 run.steps[0].game_result = {result: {data: {external_swap_required: true, pending_swap_id: 7}}};
 data = run;
 } else if (action === 'funding' && body.action === 'review') {
 run.steps[0].funding = {state: 'reviewed', review: {digest: 'funding-review', operations: [['custom_json', {id: 'sm_sps', json: '{"params":{"n":"fund"}}'}]], authority: 'Active'}};
 data = run;
 } else if (action === 'funding' && body.action === 'handoff') {
 data = {run_id: runId, step_id: stepId, operations: run.steps[0].funding.review.operations, authority: 'Active'};
 } else if (action === 'funding' && body.action === 'transaction') {
 run.steps[0].funding = {...run.steps[0].funding, state: 'submitted', tx_id: body.tx_id}; data = run;
 } else if (action === 'funding' && body.action === 'verify') data = run;
 else if (action === 'failure') data = {logged: true};
 else throw new Error(`unexpected ${action}:${body.action || ''}`);
 return {ok: true, status: 200, json: async () => data};
 };
 const controller = createHarvestRouting({player: 'player0005', scope: run.scope, activeRunId: runId,
 onRender() {}, onBack() {}, onDone() {}, onHarvest() {}, isCurrent: () => true});
 await controller.load();
 await controller.action('sign', null);
 const deadline = Date.now() + 4000;
 while (!prompts.includes('Active') && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 25));
 if (prompts.join(',') !== 'Posting,Active') throw new Error(prompts.join(','));
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=routing)
