""" named setup Menu contracts; browser interactions live in CI-only test file."""
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

MENU = Path(__file__).parents[2] / 'ceres' / 'static' / 'header-menu.js'
APP = Path(__file__).parents[2] / 'ceres' / 'static' / 'app.js'
CSS = Path(__file__).parents[2] / 'ceres' / 'static' / 'ceres.css'


def test_named_setups_use_existing_menu_buttons_and_escape_visible_names():
    out = run_in_module_context(r"""
document.body.dataset = {};
document.body.dataset.boardMode = 'planning';
document.body.dataset.boardPlanName = 'Grain setup';
const html = hdrMenuPanelHtml('plots', { selectedId: 'id-1', plans: [
  { id: 'id-1', name: 'Grain setup' }, { id: 'id-2', name: '<script>Bad</script>' }] });
console.log(JSON.stringify({ html, place: hdrMenuPlace('board') }));
""", entry=MENU).stdout
    import json
    got = json.loads(out.strip().splitlines()[-1])
    html = got['html']
    assert got['place']['name'] == 'Planning · Grain setup'
    assert 'data-menu-entry="planning"' in html and '>Live<' in html
    for label in ('New setup', 'Start from live', 'Rename setup', 'Duplicate setup', 'Delete setup'):
        assert label in html
    assert '✓ Grain setup' in html
    assert '&lt;script&gt;Bad&lt;/script&gt;' in html and '<script>' not in html
    assert html.count('class="hdr-menu-entry ceres-btn ceres-btn--secondary ceres-btn--glass"') == 17


def test_board_wires_setup_actions_to_store_and_notice_surface():
    source = APP.read_text(encoding='utf-8')
    assert 'setupAction: boardMenuSetupAction' in source
    assert 'boardPlanningSetupAction(operation, id)' in source
    assert 'stageToast(`Deleted ${result.deleted.name}.`, false, { label: \'Undo\'' in source
    assert 'stageToast(`Setup could not be saved:' in source
    assert '.stage-toast.has-action { pointer-events: auto; }' in CSS.read_text(encoding='utf-8')


def test_menu_panel_scrolling_is_planning_only():
    source = CSS.read_text(encoding='utf-8')
    assert 'body[data-board-mode="planning"] .hdr-menu-panel { max-block-size: calc(100dvh - 6rem); overflow-y: auto; }' in source
    base = source.split('.hdr-menu-panel {', 1)[1].split('}', 1)[0]
    assert 'max-block-size' not in base and 'overflow-y' not in base
