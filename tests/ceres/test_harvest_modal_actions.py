from pathlib import Path

from tests.ceres.vm_context import read_entry_source, run_in_module_context


ROOT = Path(__file__).parents[2]
ENTRY = ROOT / "ceres" / "static" / "harvest-modal.js"


def _source_with_mutable_modal_bindings() -> str:
    source = read_entry_source(ENTRY)
    return source.rsplit("}", 1)[0] + r"""
Object.defineProperties(globalThis, {
  M: {get: () => M},
  postJson: {get: () => postJson, set: value => {postJson = value;}},
  render: {get: () => render, set: value => {render = value;}}
});
}
"""


def test_results_hide_routing_when_the_feature_flag_is_off():
    run_in_module_context(r"""
(() => {
harvestSuccessHtml = () => '<section data-view="results"><div class="last-region"></div><section class="bonus-rewards"></section></section>';
Object.assign(M, {run:{phase:'success', busy:false, attemptIds:['receipt']}, mode:'batch', scopeKind:'all', player:'alice', selectedRegions:new Set(), routing:null, routingRunId:null});
const results = modalBodyHtml();
const footer = resultFooterHtml();
if (!results.includes('data-view="results"') || results.includes('harvest-routing') || results.includes('hr-entry')) throw Error(results);
const routingButton = footer.match(/<button[^>]*data-result-action="routing"[^>]*>Routing<\/button>/)?.[0];
const closeButton = footer.match(/<button[^>]*data-result-action="close"[^>]*>Close<\/button>/)?.[0];
if (routingButton) throw Error(footer);
if (!closeButton || !closeButton.includes('data-hm="close"') || !closeButton.includes('ceres-btn--secondary')) throw Error(footer);

let loaded = 0;
const routingPanel = '<section class="harvest-routing"><button data-hr="review">Refresh quotes</button><button data-hr="cancel">Cancel remaining steps</button></section>';
createHarvestRouting = () => ({html:() => routingPanel, load:() => {loaded += 1;}, clear:() => {}, runId:null, terminal:true, busy:false});
render = () => {};
handleAction({dataset:{hm:'routing'}}, {preventDefault(){}});
if (loaded !== 0 || M.routing) throw Error('Disabled routing action opened its controller');
const afterClick = modalBodyHtml();
if (afterClick.includes('harvest-routing')) throw Error(afterClick);
})()
""", entry=ENTRY, source=_source_with_mutable_modal_bindings())


def test_close_interrupts_one_active_run_clears_transient_state_and_preserves_signed_queue():
    run_in_module_context(r"""
(async () => {
const queueKey = 'ceres:signed-transaction-queue:v1:alice';
const queue = JSON.stringify([{id:'signed-row', state:'queued', signed_transaction:'immutable'}]);
localStorage.setItem(queueKey, queue);
const runId = 'a'.repeat(32);
const calls = [];
fetch = async (url, options) => {
  calls.push({url, options});
  if (url.includes('/state?')) return {ok:true, json:async () => ({regions:[], pools:[]})};
  if (url.includes('/runs?')) return {ok:true, json:async () => ({runs:[{run_id:runId, state:'planned', phase:'after', steps:[]}]})};
  if (url.includes('/stop?')) return {ok:true};
  throw Error('Unexpected fetch: ' + url);
};
Object.assign(M, {open:true, player:'alice', routingRunId:runId, routingTerminal:false, error:'old routing error', unsubscribe:() => {}, returnFocus:null});
const routing = createHarvestRouting({player:'alice', scope:{kind:'all'}, activeRunId:runId, onRender:() => {}, isCurrent:() => true});
M.routing = routing;
await routing.load();
if (routing.runId !== runId) throw Error('real routing controller did not load its active run');
CURRENT_PLAYER = 'bob';
await closeHarvestModal();
const stops = calls.filter(({url}) => url.includes('/api/harvest/routing/stop?'));
if (stops.length !== 1 || stops[0].url !== '/api/harvest/routing/stop?player=alice') throw Error(JSON.stringify(calls));
if (stops[0].options.method !== 'POST' || stops[0].options.headers['Content-Type'] !== 'application/json' || stops[0].options.body !== JSON.stringify({run_id:runId}) || stops[0].options.keepalive !== true) throw Error(JSON.stringify(stops));
if (routing.runId !== null || M.routing !== null || M.routingRunId !== null || M.routingTerminal || M.error !== null || M.open) throw Error('transient routing state survived Close');
if (localStorage.getItem(queueKey) !== queue) throw Error('signed queue changed');
})().then(() => process.exit(0), error => {console.error(error);process.exit(1);});
""", entry=ENTRY, source=_source_with_mutable_modal_bindings())


def test_close_saved_run_uses_the_modal_account_for_interrupt():
    run_in_module_context(r"""
(async () => {
const calls = [];
fetch = async (url, options) => {calls.push({url, options}); return {ok:true};};
Object.assign(M, {open:true, player:'alice', routing:null, routingRunId:'b'.repeat(32), routingTerminal:false, unsubscribe:() => {}, returnFocus:null});
CURRENT_PLAYER = 'bob';
await closeHarvestModal();
if (calls.length !== 1 || calls[0].url !== '/api/harvest/routing/stop?player=alice' || calls[0].options.method !== 'POST' || calls[0].options.headers['Content-Type'] !== 'application/json' || calls[0].options.keepalive !== true || calls[0].options.body !== JSON.stringify({run_id:'b'.repeat(32)})) throw Error(JSON.stringify(calls));
})().then(() => process.exit(0), error => {console.error(error);process.exit(1);});
""", entry=ENTRY, source=_source_with_mutable_modal_bindings())


def test_close_terminal_run_does_not_interrupt_but_clears_modal_state():
    run_in_module_context(r"""
(async () => {
const calls = [];
fetch = async (url, options) => {calls.push({url, options}); return {ok:true};};
const runId = 'c'.repeat(32);
let cleared = 0;
Object.assign(M, {open:true, player:'alice', routingRunId:runId, routingTerminal:true, error:'old routing error', unsubscribe:() => {}, returnFocus:null});
M.routing = {busy:false, runId, terminal:true, clear:() => {cleared += 1;}};
await closeHarvestModal();
if (calls.length || cleared !== 1 || M.routing !== null || M.routingRunId !== null || M.routingTerminal || M.error !== null || M.open) throw Error(JSON.stringify({calls, cleared, M}));
})().then(() => process.exit(0), error => {console.error(error);process.exit(1);});
""", entry=ENTRY, source=_source_with_mutable_modal_bindings())


def test_close_without_a_run_does_not_call_the_interrupt_endpoint():
    run_in_module_context(r"""
(async () => {
const calls = [];
fetch = async (url, options) => {calls.push({url, options}); return {ok:true};};
Object.assign(M, {open:true, player:'alice', routing:null, routingRunId:null, routingTerminal:false, unsubscribe:() => {}, returnFocus:null});
await closeHarvestModal();
if (calls.length) throw Error(JSON.stringify(calls));
})().then(() => process.exit(0), error => {console.error(error);process.exit(1);});
""", entry=ENTRY, source=_source_with_mutable_modal_bindings())


def test_close_after_harvest_failure_preserves_the_durable_routing_run():
    run_in_module_context(r"""
(async () => {
const calls = [];
fetch = async (url, options) => { calls.push({url, options}); return {ok:true}; };
const runId = 'd'.repeat(32);
Object.assign(M, {open:true, player:'alice', run:{phase:'failed', busy:false, message:'The game refused this harvest.', members:[]},
  routing:null, routingRunId:runId, routingTerminal:false, unsubscribe:() => {}, returnFocus:null});
await closeHarvestModal();
if (calls.some(({url}) => url.includes('/api/harvest/routing/stop?'))) throw Error(JSON.stringify(calls));
if (M.open || M.routing || M.routingRunId || !M.run || M.run.phase !== 'failed') throw Error(JSON.stringify(M));
})().then(() => process.exit(0), error => {console.error(error);process.exit(1);});
""", entry=ENTRY, source=_source_with_mutable_modal_bindings())


def test_failed_harvest_has_visible_saved_plan_message_and_no_ready_claim():
    modal_source = ENTRY.read_text()
    routing_source = (ROOT / "ceres" / "static" / "harvest-routing.js").read_text()
    assert "The routing plan remains saved" in modal_source
    assert 'role="alert"' in modal_source or "role='alert'" in modal_source
    assert "The routing plan remains saved. Refresh the harvest state before trying again." in modal_source
    assert "const preserveHarvestRun = Boolean(M.routingRunId && (M.run || M.harvestPreparing));" in modal_source
    assert "ready_harvest: 'Ready to harvest'" not in routing_source
    assert "ready_harvest: 'Harvest review required'" in routing_source
