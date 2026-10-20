""" worksite-plan restore behavior through the shared frontend VM harness."""

from .vm_context import run_in_module_context


DETAIL = """
{
 deed_uid: 'D-1',
 worksite_facts: { project_type: '' },
 pending_change: null,
 swap_targets: [{ target: 'stone', is_current: false, first_clear: false, worksite_name: 'Quarry' }]
}
"""


def _extra_context(responses: str) -> str:
    return f"""
context.AbortController = class {{ constructor() {{ this.signal = {{}}; }} abort() {{}} }};
context.queueMicrotask = queueMicrotask;
context.__ceresListeners = __ceresListeners;
context.__worksiteResponses = {responses};
context.fetch = async () => context.__worksiteResponses.shift() || {{ ok: false, status: 503, json: async () => ({{}}) }};
context.document.body.appendChild = (node) => {{ context.__lastToast = node; }};
"""


def _board_load_context() -> str:
    return _extra_context("[]") + f"""
context.__captureStatusCalls = 0;
context.__worksiteDetailCalls = 0;
context.fetch = async (url) => {{
 const path = String(url);
 if (path.includes('/static/assets/avatar-layers.json')) return {{ ok: true, status: 200, json: async () => ({{}}) }};
 if (path.includes('api.splinterlands.com')) return {{ ok: false, status: 404, json: async () => ({{}}) }};
 if (path.includes('/api/capture/refresh')) return {{ ok: false, status: 409, json: async () => ({{}}) }};
 if (path.includes('/api/capture/status')) {{
 context.__captureStatusCalls += 1;
 return context.__captureStatusCalls === 1
 ? {{ ok: true, status: 200, json: async () => ({{ state: 'running', board_ready: true, latest_snapshot: {{ token: 'capture-1' }} }}) }}
 : {{ ok: true, status: 200, json: async () => ({{ state: 'idle', latest_snapshot: {{ token: 'capture-1' }} }}) }};
 }}
 if (path.includes('/api/board')) return {{ ok: true, status: 200, json: async () => ({{ player: 'player0005', generated_at: 'capture-1', plots: [{{ deed_uid: 'D-1' }}], totals: {{}}, notices: [], sortable: [], notice_catalog: {{}} }}) }};
 if (path.includes('/api/plots/D-1')) {{
 context.__worksiteDetailCalls += 1;
 if (context.__worksiteDetailCalls <= 3) return {{ ok: false, status: 503, json: async () => ({{}}) }};
 return {{ ok: true, status: 200, json: async () => ({DETAIL}) }};
 }}
 return {{ ok: false, status: 503, json: async () => ({{}}) }};
}};
context.window.scrollTo = () => {{}};
context.window.scrollBy = () => {{}};
context.document.documentElement.classList = {{ contains: () => false, add() {{}}, remove() {{}}, toggle() {{}} }};
context.document.documentElement.style.setProperty = () => {{}};
context.history = {{ replaceState() {{}} }};
context.__vmElements = {{}};
context.__vmElement = () => ({{
 style: {{ setProperty() {{}}, removeProperty() {{}} }}, classList: {{ add() {{}}, remove() {{}}, toggle() {{}}, contains() {{}} }}, dataset: {{}}, children: [], options: [],
 value: '', checked: false, hidden: false, textContent: '', innerHTML: '',
 addEventListener() {{}}, removeEventListener() {{}}, setAttribute() {{}}, removeAttribute() {{}}, getAttribute() {{ return null; }},
 appendChild() {{}}, prepend() {{}}, remove() {{}}, replaceChildren() {{}}, insertBefore() {{}}, querySelector() {{ return null; }}, querySelectorAll() {{ return []; }},
 getBoundingClientRect() {{ return {{ width: 0, height: 0, top: 0, bottom: 0, left: 0, right: 0 }}; }},
 parentNode: {{ insertBefore() {{}}, appendChild() {{}} }},
}});
context.document.getElementById = (id) => [
 'region-filter', 'rarity-filter', 'terrain-filter', 'resource-filter', 'status-filter', 'badge-filter'
].includes(id) ? (context.__vmElements[id] ||= context.__vmElement()) : null;
context.document.createElement = () => context.__vmElement();
context.render = () => {{}};
"""


def test_worksite_restore_retries_transient_read_with_success_toast_and_restores() -> None:
    responses = f"""[
 {{ ok: false, status: 409, json: async () => ({{}}) }},
 {{ ok: true, status: 200, json: async () => ({DETAIL}) }}
 ]"""
    run_in_module_context(
        f"""
        (async () => {{
 CURRENT_PLAYER = 'player0005';
 BOARD = {{ player: 'player0005', generated_at: 'capture-1', plots: [{{ deed_uid: 'D-1' }}] }};
 localStorage.setItem('ceres:stage-plan:v1:player0005', JSON.stringify({{
 version: 1, revision: 1, plans: {{}}, dropped: {{}},
 worksites: {{ 'D-1': {{ deed_uid: 'D-1', target: 'stone', targetLabel: 'Quarry', firstClear: false, timeCrystals: 0 }} }}
 }}));
 await restoreStagePlans();
 if (pendingWorksitePlanCount() !== 0) throw new Error('transient worksite read stayed pending');
 if (CERES_STAGED_WORKSITES.get('D-1')?.target !== 'stone') throw new Error('worksite plan was not restored');
 if (!globalThis.__lastToast) throw new Error('worksite restore did not raise the success toast');
 }})().catch((error) => {{ console.error(error); process.exit(1); }});
        """,
        extra_context_js=_extra_context(responses),
    )


def test_worksite_restore_keeps_failed_read_pending_then_rechecks_on_board_reveal() -> None:
    responses = f"""[
 {{ ok: false, status: 503, json: async () => ({{}}) }},
 {{ ok: false, status: 503, json: async () => ({{}}) }},
 {{ ok: false, status: 503, json: async () => ({{}}) }},
 {{ ok: true, status: 200, json: async () => ({DETAIL}) }}
 ]"""
    run_in_module_context(
        f"""
        (async () => {{
 CURRENT_PLAYER = 'player0005';
 BOARD = {{ player: 'player0005', generated_at: 'capture-1', plots: [{{ deed_uid: 'D-1' }}] }};
 localStorage.setItem('ceres:stage-plan:v1:player0005', JSON.stringify({{
 version: 1, revision: 1, plans: {{}}, dropped: {{}},
 worksites: {{ 'D-1': {{ deed_uid: 'D-1', target: 'stone', targetLabel: 'Quarry', firstClear: false, timeCrystals: 0 }} }}
 }}));
 await restoreStagePlans();
 if (pendingWorksitePlanCount() !== 1) throw new Error('failed worksite read was not kept pending');
 if (!stagePlansReady()) throw new Error('pending worksite read left the planner unusable');
 for (const listener of (__ceresListeners['ceres:board-revealed'] || [])) listener(new CustomEvent('ceres:board-revealed'));
 await new Promise((resolve) => setTimeout(resolve, 50));
 if (pendingWorksitePlanCount() !== 0) throw new Error('board-ready retry did not finish');
 if (CERES_STAGED_WORKSITES.get('D-1')?.target !== 'stone') throw new Error('board-ready retry did not restore');
 }})().catch((error) => {{ console.error(error); process.exit(1); }});
        """,
        extra_context_js=_extra_context(responses),
    )


def test_worksite_restore_drops_only_a_successful_contradiction() -> None:
    contradiction = """
    [{ ok: true, status: 200, json: async () => ({
 deed_uid: 'D-1', worksite_facts: { project_type: '' }, pending_change: null,
 swap_targets: [{ target: 'wood', is_current: false, first_clear: false, worksite_name: 'Logging Camp' }]
 }) }]
    """
    run_in_module_context(
        """
        (async () => {
 CURRENT_PLAYER = 'player0005';
 BOARD = { player: 'player0005', generated_at: 'capture-1', plots: [{ deed_uid: 'D-1' }] };
 localStorage.setItem('ceres:stage-plan:v1:player0005', JSON.stringify({
 version: 1, revision: 1, plans: {}, dropped: {},
 worksites: { 'D-1': { deed_uid: 'D-1', target: 'stone', targetLabel: 'Quarry', firstClear: false, timeCrystals: 0 } }
 }));
 await restoreStagePlans();
 if (pendingWorksitePlanCount() !== 0) throw new Error('contradiction stayed pending');
 if (CERES_STAGED_WORKSITES.has('D-1')) throw new Error('contradiction restored a plan');
 if (PLANS.dropped[0]?.reason !== 'worksite_unavailable') throw new Error('contradiction was not recorded');
 })().catch((error) => { console.error(error); process.exit(1); });
        """,
        extra_context_js=_extra_context(contradiction),
    )


def test_worksite_restore_rechecks_after_final_capture_settles() -> None:
    responses = f"""[
 {{ ok: false, status: 503, json: async () => ({{}}) }},
 {{ ok: false, status: 503, json: async () => ({{}}) }},
 {{ ok: false, status: 503, json: async () => ({{}}) }},
 {{ ok: true, status: 200, json: async () => ({DETAIL}) }}
 ]"""
    run_in_module_context(
        f"""
        (async () => {{
 CURRENT_PLAYER = 'player0005';
 BOARD = {{ player: 'player0005', generated_at: 'capture-1', plots: [{{ deed_uid: 'D-1' }}] }};
 localStorage.setItem('ceres:stage-plan:v1:player0005', JSON.stringify({{
 version: 1, revision: 1, plans: {{}}, dropped: {{}},
 worksites: {{ 'D-1': {{ deed_uid: 'D-1', target: 'stone', targetLabel: 'Quarry', firstClear: false, timeCrystals: 0 }} }}
 }}));
 await restoreStagePlans();
 if (pendingWorksitePlanCount() !== 1) throw new Error('failed worksite read was not kept pending');
 for (const listener of (__ceresListeners['ceres:board-capture-settled'] || [])) listener(new CustomEvent('ceres:board-capture-settled'));
 await new Promise((resolve) => setTimeout(resolve, 50));
 if (pendingWorksitePlanCount() !== 0) throw new Error('final-capture retry did not finish');
 if (CERES_STAGED_WORKSITES.get('D-1')?.target !== 'stone') throw new Error('final-capture retry did not restore');
 }})().catch((error) => {{ console.error(error); process.exit(1); }});
        """,
        extra_context_js=_extra_context(responses),
    )


def test_board_emits_the_final_capture_signal_after_settle() -> None:
    run_in_module_context(
        f"""
        (async () => {{
 const events = [];
 window.dispatchEvent = (event) => {{
 events.push(event.type);
 for (const listener of (__ceresListeners[event.type] || [])) listener(event);
 return true;
 }};
 CURRENT_PLAYER = 'player0005';
 localStorage.setItem('ceres:stage-plan:v1:player0005', JSON.stringify({{
 version: 1, revision: 1, plans: {{}}, dropped: {{}},
 worksites: {{ 'D-1': {{ deed_uid: 'D-1', target: 'stone', targetLabel: 'Quarry', firstClear: false, timeCrystals: 0 }} }}
 }}));
 await loadBoard();
 if (events.filter((type) => type === 'ceres:board-revealed').length !== 1) throw new Error('board reveal signal did not fire once');
 if (events.filter((type) => type === 'ceres:board-capture-settled').length !== 1) throw new Error('capture-settled signal did not fire once');
 await new Promise((resolve) => setTimeout(resolve, 1200));
 if (__worksiteDetailCalls !== 4) throw new Error('capture-settled retry did not re-run the worksite detail read');
 if (pendingWorksitePlanCount() !== 0) throw new Error('capture-settled retry did not clear the pending worksite');
 if (CERES_STAGED_WORKSITES.get('D-1')?.target !== 'stone') throw new Error('capture-settled retry did not restore the worksite');
 }})().catch((error) => {{ console.error(error); process.exit(1); }});
        """,
        extra_context_js=_board_load_context(),
    )
