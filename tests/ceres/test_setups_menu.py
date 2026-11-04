""" desktop Planning submenu source and render contracts."""
from pathlib import Path
import json

from tests.ceres.vm_context import run_in_module_context

ROOT = Path(__file__).parents[2]
MENU = ROOT / 'ceres/static/header-menu.js'
APP = ROOT / 'ceres/static/app.js'
CSS = ROOT / 'ceres/static/ceres.css'
BUTTONS = ROOT / 'ceres/static/buttons.css'


def _render(planning=True, name='Grain setup'):
    script = f"""
document.body.dataset = {{ boardMode: '{'planning' if planning else ''}', boardPlanName: {json.dumps(name)} }};
const html = hdrMenuPanelHtml('plots', {{ selectedId: 'id-1', plans: [
  {{ id: 'id-1', name: {json.dumps(name)} }}, {{ id: 'id-2', name: '<script>Bad</script>' }}] }});
console.log(JSON.stringify({{ html, place: hdrMenuPlace('board') }}));
"""
    return json.loads(run_in_module_context(script, entry=MENU).stdout.strip().splitlines()[-1])


def test_planning_submenu_has_attached_panel_and_trigger():
    got = _render()
    html = got['html']
    assert got['place']['name'] == 'Planning · Grain setup'
    assert 'data-menu-submenu hidden' in html and 'hdr-menu-setups' not in html
    assert 'class="hdr-menu-planning-label">Planning · Grain setup</span><span class="hdr-menu-chevron">›</span>' in html
    assert 'aria-haspopup="true" aria-expanded="false"' in html
    assert 'Back to live' in html
    assert 'data-setup-rename="id-1"' in html and 'data-setup-delete="id-1"' in html
    assert '&lt;script&gt;Bad&lt;/script&gt;' in html and '<script>' not in html
    assert "boardPlanningSetups({ includeLive: true })" in APP.read_text(encoding='utf-8')


def test_live_menu_omits_setup_panel_markup():
    html = _render(planning=False)['html']
    assert 'data-menu-entry="planning"' in html
    assert 'Plan placements on this board' in html
    assert 'data-menu-submenu' not in html
    assert 'New setup' not in html and 'Start from live' not in html


def test_planning_label_keeps_chevron_attached_and_ellipsizes_name():
    html = _render(name='A very long northern production setup name')['html']
    css = CSS.read_text(encoding='utf-8')
    assert 'class="hdr-menu-chevron">›</span>' in html
    assert 'white-space: nowrap' in css.split('.hdr-menu-chevron', 1)[1].split('}', 1)[0]
    planning_rule = next(rule for rule in css.splitlines() if '.hdr-menu-planning-label {' in rule)
    assert 'text-overflow: ellipsis' in planning_rule


def test_inline_rename_delete_undo_and_icon_contrast_contract():
    menu = MENU.read_text(encoding='utf-8')
    app = APP.read_text(encoding='utf-8')
    css = CSS.read_text(encoding='utf-8')
    buttons = BUTTONS.read_text(encoding='utf-8')
    assert "input.className = 'hdr-menu-rename'" in menu
    assert "keyEvent.key === 'Enter' || keyEvent.key === 'Escape'" in menu
    assert "event.key === 'ArrowRight'" in menu and "event.key === 'ArrowLeft'" in menu
    assert "event.key === 'Escape' && submenu()" in menu
    assert "options.setupAction?.('delete'" in menu
    assert 'prompt(' not in app and 'confirm(' not in app
    assert "boardPlanningSetupAction(operation, id)" in app
    assert "label: 'Undo'" in app
    assert 'body[data-board-mode="planning"] .stage-toast { top: auto; bottom:' in css
    assert '.stage-toast.has-action { pointer-events: auto; }' in css
    assert '.hdr-menu-setup-action .hdr-ic' in buttons
    glyph = buttons.split('.hdr-menu-setup-action .hdr-ic', 1)[1].split('}', 1)[0]
    assert 'inline-size: 1.2rem' in glyph and 'block-size: 1.2rem' in glyph
    assert 'color: inherit' in glyph and 'stroke: currentColor' in glyph and 'stroke-width: 2' in glyph


def test_menu_panel_styles_use_desktop_guard_or_panel_family():
    source = CSS.read_text(encoding='utf-8')
    assert 'data-hdr-menu-variant' not in source
    assert '.hdr-menu-setups' not in source
    assert 'data-board-mode="planning"] .hdr-menu-cols { grid-template-columns: repeat(4' not in source
