""" Phase E guards for the T2/T3 plot and harvest sheets."""

from __future__ import annotations

import re

from .vm_context import STATIC, run_in_module_context


HTML = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
ROWS_JS = (STATIC / "rows.js").read_text(encoding="utf-8")
PICKER_JS = (STATIC / "picker-actions.js").read_text(encoding="utf-8")
EDITOR_JS = (STATIC / "editor.js").read_text(encoding="utf-8")
HARVEST_JS = (STATIC / "harvest-modal.js").read_text(encoding="utf-8")
HARVEST_STATE_JS = (STATIC / "harvest-state.js").read_text(encoding="utf-8")
CSS = (STATIC / "ceres.css").read_text(encoding="utf-8")


def test_plot_cards_are_accessible_sheet_openers() -> None:
    run_in_module_context(
        r"""
function assert(ok, message) { if (!ok) throw new Error(message); }
BOARD = {badge_meta: {}};
const row = {
 deed_uid: 'deed-a', plot: '65-7-8', region: 'PR-BRL-65', terrain: 'Forest',
 resource: 'grain', worksite_type: 'Grain Farm', work_per_hour: 1234.4,
 harvest_pp: 98765.4, capacity: {display: '2/5'}, badges: [], notices: [],
};
const card = plotCardHtml(row, 0);
assert(card.includes('role="button"'), 'plot card must be keyboard-operable');
assert(card.includes('aria-haspopup="dialog"'), 'plot card must announce the sheet');
assert(card.includes(plotStatsHtml(row)), 'card and sheet must share one stats renderer');
""",
        entry=STATIC / "rows.js",
    )
    assert "openMobilePlotSheet(card.dataset.deedUid, card)" in APP_JS
    assert "['Enter', ' ']" in APP_JS
    assert 'id="mobile-plot-sheet"' in HTML


def test_plot_sheet_uses_the_desktop_editor_and_live_row_model() -> None:
    sheet_renderer = ROWS_JS.split("function renderMobilePlotSheet(row) {", 1)[1].split("\n}", 1)[0]
    sheet_template = sheet_renderer.split("body.innerHTML = `", 1)[1].split("`;", 1)[0]
    assert "${workerSubtable(row, { mobile: true })}" in sheet_template
    assert "${mobileTotalPpHtml(row)}" in sheet_template
    assert "const presentation = mobile ? null : presentationForRow(row);" in PICKER_JS
    assert "return renderSlotEditor(row, presentation" in PICKER_JS
    assert "const row = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);" in ROWS_JS
    assert "if (cidx !== -1) CURRENT_ROWS[cidx] = updated;" in ROWS_JS
    assert "const layers = editorSceneLayers(row);" in ROWS_JS
    assert "harvestBand.appendChild(footer);" in ROWS_JS
    for marker in ("id: 'worker'", "id: 'runi'", "itemGroup('power_core'", "itemGroup('totem'", "itemGroup('title'"):
        assert marker in EDITOR_JS, marker


def test_per_plot_harvest_keeps_the_existing_handler_and_finished_build_route() -> None:
    assert 'data-harvest-action="open-plot"' in HARVEST_STATE_JS
    assert 'data-harvest-action="feed-workers"' in HARVEST_STATE_JS
    assert 'event.target.closest(\'[data-harvest-action="open-plot"]\')' in HARVEST_JS
    assert "openHarvestModal({ deedUid: chip.dataset.deedUid })" in HARVEST_JS
    assert 'event.target.closest(\'[data-harvest-action="feed-workers"]\')' in HARVEST_JS
    assert "openWorksiteFeedModal(feed.dataset.deedUid)" in HARVEST_JS
    constrained = ROWS_JS.split("function constrainMobileEditorActions(root) {", 1)[1].split("\n}", 1)[0]
    assert "data-harvest-action" not in constrained


def test_open_render_close_executes_shared_composition_and_restores_scroll_in_vm() -> None:
    run_in_module_context(
        r"""
function assert(ok, message) { if (!ok) throw new Error(message); }
const calls = { worker: 0, scene: 0, actionMoved: 0, sceneMoved: 0, focus: 0, scroll: null };
const classList = { add() {}, remove() {}, toggle() {}, contains(name) { return name === 'ceres-lw-le-1279'; } };
const scene = { style: { setProperty() {} }, classList: { add(name) { if (name === 'mobile-plot-worksite-art') calls.scene++; } } };
const actions = { classList: { add(name) { if (name === 'mobile-plot-actions') calls.actionMoved++; } }, querySelector() { return null; } };
const harvestBand = { appendChild(node) { if (node === actions) calls.harvestMoved = 1; } };
const worksite = { prepend(node) { if (node === scene) calls.sceneMoved++; }, appendChild() {}, after() {}, querySelector(selector) { return selector === '.mobile-plot-harvest-band' ? harvestBand : null; } };
const status = { textContent: '' };
const retry = { hidden: false };
const band = {
 dataset: {}, classList, setAttribute() {}, querySelectorAll() { return []; },
 querySelector(selector) {
 return {'.eos-status-text': status, '.expand-retry-link': retry, '.editor-scene': scene, '.slot-editor-footer': actions}[selector] || null;
 },
};
const body = {
 html: '', appended: null,
 set innerHTML(value) { this.html = value; }, get innerHTML() { return this.html; },
 querySelectorAll() { return []; },
 querySelector(selector) { return selector === '.editor-band' ? band : selector === '.mobile-plot-worksite' ? worksite : selector === '.slot-editor-footer' ? actions : null; },
 appendChild(node) { this.appended = node; }, replaceChildren() { this.html = ''; },
};
// style.setProperty added for (v0.9.530) mobile picker header terrain overlay.
const sheet = { dataset: {}, hidden: true, classList, removeAttribute() {}, style: { setProperty() {} } };
const title = { textContent: '' }; const subtitle = { textContent: '' };
const close = { classList, setAttribute() {}, focus() { calls.focus++; } };
const elements = {'mobile-plot-sheet': sheet, 'mobile-plot-sheet-body': body, 'mobile-plot-sheet-title': title, 'mobile-plot-sheet-subtitle': subtitle, 'mobile-plot-sheet-close': close};
document.documentElement.classList = classList;
document.body.classList = classList;
document.getElementById = (id) => elements[id] || null;
window.scrollY = 314;
window.scrollTo = (x, y) => { calls.scroll = [x, y]; };
const returnTarget = { focus() { calls.focus++; } };
BOARD = { badge_meta: {}, plots: [{deed_uid:'deed-a', plot:'65-7-8', region:'PR-BRL-65', terrain:'Forest', resource:'grain', worksite_type:'Grain Farm', work_per_hour:12, harvest_pp:34, capacity:{display:'2/5'}, badges:[], notices:[]}] };
EXPANDED_DATA_LOADED.add('deed-a');
workerSubtable = (_row, options) => { assert(options.mobile === true, 'must use shared mobile editor route'); calls.worker++; return '<section class="slot-editor">SHARED_EDITOR</section>'; };
editorSceneLayers = () => ({terrain:'/terrain.jpg', worksite:'/worksite.webp'});
assert(openMobilePlotSheet('deed-a', returnTarget), 'mobile open must succeed');
assert(calls.worker === 1 && body.html.includes('SHARED_EDITOR'), 'open must render the shared editor');
assert(body.html.indexOf('mobile-plot-worksite') < body.html.indexOf('SHARED_EDITOR'), 'worksite art must precede tiles');
assert(body.html.indexOf('SHARED_EDITOR') < body.html.indexOf('mobile-plot-total-pp'), 'expanded Total PP must follow tiles');
assert(calls.harvestMoved === 1 && calls.actionMoved === 1, 'shared harvest actions must move into the art header');
assert(calls.scene === 1 && calls.sceneMoved === 1, 'shared scene art must move into the worksite header');
assert(closeMobilePlotSheet(), 'close must succeed');
assert(sheet.hidden && calls.scroll[1] === 314 && calls.focus === 2, 'close must restore list scroll and focus');
""",
        entry=STATIC / "rows.js",
    )


def test_layered_escape_and_back_execute_one_surface_at_a_time_in_vm() -> None:
    run_in_module_context(
        r"""
function assert(ok, message) { if (!ok) throw new Error(message); }
const element = { addEventListener() {} };
document.getElementById = () => element;
const listeners = {};
window.addEventListener = (type, fn) => { (listeners['window:' + type] ||= []).push(fn); };
document.addEventListener = (type, fn) => { (listeners['document:' + type] ||= []).push(fn); };
let plotOpen = true; let harvestOpen = false; let harvestClosable = true; let worksiteOpen = false;
let plotCloses = 0; let harvestCloses = 0; let worksiteCloses = 0; let pushes = 0;
mobilePlotSheetIsOpen = () => plotOpen;
closeMobilePlotSheet = () => { plotOpen = false; plotCloses++; return true; };
harvestModalIsOpen = () => harvestOpen;
harvestModalCanClose = () => harvestClosable;
closeHarvestModal = () => { harvestOpen = false; harvestCloses++; };
worksiteModalIsOpen = () => worksiteOpen;
closeWorksiteModal = () => { worksiteOpen = false; worksiteCloses++; };
history = { state: {}, pushState(state) { this.state = state; pushes++; }, back() {} };
initMobilePlotSheet();
const escape = listeners['document:keydown'].at(-1);
const pop = listeners['window:popstate'].at(-1);
harvestOpen = true;
let prevented = false;
escape({key:'Escape', defaultPrevented:false, preventDefault() { prevented = true; }});
assert(prevented && harvestCloses === 1 && plotCloses === 0 && plotOpen, 'Escape must close only harvest');
worksiteOpen = true; prevented = false;
escape({key:'Escape', defaultPrevented:false, preventDefault() { prevented = true; }});
assert(prevented && worksiteCloses === 1 && plotCloses === 0 && plotOpen, 'Escape must close only worksite');
harvestOpen = true; harvestClosable = false; prevented = false;
escape({key:'Escape', defaultPrevented:false, preventDefault() { prevented = true; }});
assert(prevented && harvestCloses === 1 && plotCloses === 0 && harvestOpen, 'busy harvest must block Escape');
harvestClosable = true;
pop();
assert(harvestCloses === 2 && plotCloses === 0 && plotOpen && pushes === 1, 'first Back closes harvest and restores plot marker');
pop();
assert(plotCloses === 1 && !plotOpen, 'second Back closes plot sheet');
plotOpen = true; worksiteOpen = true;
pop();
assert(worksiteCloses === 2 && plotCloses === 1 && plotOpen && pushes === 2, 'first Back closes worksite and restores plot marker');
pop();
assert(plotCloses === 2 && !plotOpen, 'second Back closes plot sheet after worksite');
plotOpen = true; harvestOpen = true; harvestClosable = false;
pop();
assert(plotCloses === 2 && harvestCloses === 2 && pushes === 3, 'Back cannot dismiss a busy harvest or its plot');
"""
    )


def test_phase_e_stays_mobile_only_and_leaves_combine_desktop_only() -> None:
    block = CSS.split("* PHASE E MOBILE PLOT SHEET START *", 1)[1].split(
        "* PHASE E MOBILE PLOT SHEET END *", 1
    )[0]
    selectors = re.findall(r"([^{}]+)\{", re.sub(r"/\*.*?\*/", "", block, flags=re.S))
    assert selectors
    for selector_group in selectors:
        for selector in selector_group.split(","):
            assert selector.strip().startswith("html.ceres-lw-le-1919.ceres-lw-le-1279"), selector
    assert "@media" not in block
    assert not re.search(r"(?<![-A-Za-z])\d+(?:\.\d+)?(?:vw|vh)\b", block)
    assert "constrainMobileEditorActions(body)" in ROWS_JS
    # v0.9.505 renamed the mobile action class to mobile-plot-actions during the worksite/harvest rework.
    assert "mobile-plot-actions" in ROWS_JS
    assert re.search(r'id="mobile-changes-btn"[^>]*\bdisabled\b', HTML)
    assert "#harvest-modal-overlay > .harvest-panel" in block
    assert "#worksite-modal-overlay > .harvest-panel" in block
