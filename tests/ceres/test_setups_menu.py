""" desktop setup submenu source and render contracts."""
from pathlib import Path
import json

from tests.ceres.vm_context import run_in_module_context

ROOT = Path(__file__).parents[2]
MENU = ROOT / 'ceres/static/header-menu.js'
APP = ROOT / 'ceres/static/app.js'
CSS = ROOT / 'ceres/static/ceres.css'


def _render(variant, planning=True):
    script = f"""
document.body.dataset = {{ hdrMenuVariant: '{variant}', boardMode: '{'planning' if planning else ''}', boardPlanName: 'Grain setup' }};
const html = hdrMenuPanelHtml('plots', {{ selectedId: 'id-1', plans: [
  {{ id: 'id-1', name: 'Grain setup' }}, {{ id: 'id-2', name: '<script>Bad</script>' }}] }});
console.log(JSON.stringify({{ html, place: hdrMenuPlace('board') }}));
"""
    return json.loads(run_in_module_context(script, entry=MENU).stdout.strip().splitlines()[-1])


def test_variant_a_is_fourth_column_with_inline_setup_controls():
    got = _render('A')
    html = got['html']
    assert got['place']['name'] == 'Planning · Grain setup'
    assert 'hdr-menu-setups' in html and '<h3>Setups</h3>' in html
    assert 'data-menu-entry="planning"' in html and '>Live<' in html
    assert '✓ Grain setup' in html
    assert 'data-setup-rename="id-1"' in html and 'data-setup-delete="id-1"' in html
    assert '&lt;script&gt;Bad&lt;/script&gt;' in html and '<script>' not in html
    assert 'Rename setup' not in html and 'Delete setup' not in html
    for label in ('New setup', 'Start from live', 'Duplicate setup'):
        assert label in html


def test_variant_b_has_attached_panel_and_planning_trigger():
    html = _render('B')['html']
    assert 'data-menu-submenu hidden' in html and 'hdr-menu-setups' not in html
    assert 'Planning · Grain setup ›' in html
    assert 'aria-haspopup="true" aria-expanded="false"' in html
    assert 'Back to live' in html
    assert 'data-setup-rename="id-1"' in html
    assert 'Planning ›' in _render('B', False)['html']


def test_inline_rename_immediate_delete_and_notice_contract():
    menu = MENU.read_text(encoding='utf-8')
    app = APP.read_text(encoding='utf-8')
    css = CSS.read_text(encoding='utf-8')
    assert "input.className = 'hdr-menu-rename'" in menu
    assert "keyEvent.key === 'Enter' || keyEvent.key === 'Escape'" in menu
    assert "event.key === 'ArrowRight'" in menu and "event.key === 'ArrowLeft'" in menu
    assert "event.key === 'Escape' && hdrMenuVariant() === 'B'" in menu
    assert "options.setupAction?.('delete'" in menu
    assert 'prompt(' not in app and 'confirm(' not in app
    assert "boardPlanningSetupAction(operation, id)" in app
    assert "label: 'Undo'" in app
    assert 'body[data-board-mode="planning"] .stage-toast { top:' in css
    assert '.stage-toast.has-action { pointer-events: auto; }' in css


def test_menu_panel_scrolling_is_only_variant_a_planning():
    source = CSS.read_text(encoding='utf-8')
    assert 'body[data-board-mode="planning"]:not([data-hdr-menu-variant="B"]) .hdr-menu-panel' in source
    base = source.split('.hdr-menu-panel {', 1)[1].split('}', 1)[0]
    assert 'max-block-size' not in base and 'overflow-y' not in base
