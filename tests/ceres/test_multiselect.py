""": opt-in multi-select in the shared
customSelect.js component and the board's filter row (OR within one filter, AND across filters).

Pure helpers and the board's filter logic run in the shared vm harness; the component's real DOM behaviour
(toggle, keyboard, ARIA, summary label, sort stays single) runs in headless Chromium against the module
alone, without a Ceres server."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"
COMPONENT = STATIC / "customSelect.js"
BOARD = STATIC / "board.js"

EQ = r"""
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw Error(what + ': ' + JSON.stringify(a) + ' != ' + JSON.stringify(b)); };
"""


def _vm(body: str, entry: Path = COMPONENT) -> None:
    run_in_module_context(EQ + body, entry=entry)


# pure helpers (vm harness)

def test_summary_label_any_one_two_many():
    _vm(r"""
eq(multiSummaryLabel([], 'Any terrain'), 'Any terrain', 'none');
eq(multiSummaryLabel(['Plains'], 'Any terrain'), 'Plains', 'one');
eq(multiSummaryLabel(['Plains', 'River'], 'Any terrain'), 'Plains, River', 'two that fit');
eq(multiSummaryLabel(['Plains', 'River', 'Lake'], 'Any terrain'), '3 terrains', 'three');
eq(multiSummaryLabel(['Land ability card', 'Runi'], 'Any badge'), '2 badges', 'two too long');
eq(multiSummaryLabel(['Epic', 'Rare', 'Common'], 'Any rarity'), '3 rarities', 'y plural');
eq(multiSummaryLabel(['065', '070', '101'], 'Any region'), '3 regions', 'regions');
eq(multiSummaryLabel(['a', 'b', 'c'], 'Any x', { pluralNoun: 'things' }), '3 things', 'override');
""")


def test_select_values_on_stand_ins():
    _vm(r"""
eq(selectValues(null), [], 'null');
eq(selectValues({ value: '' }), [], 'stand-in empty');
eq(selectValues({ value: 'Plains' }), ['Plains'], 'stand-in without selectedOptions');
eq(selectValues({ value: '', selectedOptions: [{ value: '' }] }), [], 'Any chosen');
eq(selectValues({ value: 'a', selectedOptions: [{ value: 'a' }, { value: 'b' }] }), ['a', 'b'], 'two chosen');
""")


# board filter logic (vm harness, whole app graph)

BOARD_STUBS = r"""
const els = {};
const sel = (values) => ({ value: values[0] || '', selectedOptions: values.map((value) => ({ value })), options: [] });
const setFilters = (f) => {
  for (const id of ['region-filter', 'rarity-filter', 'terrain-filter', 'resource-filter', 'status-filter', 'badge-filter']) els[id] = sel(f[id] || []);
};
els['text-filter'] = { value: '' };
els['staged-only'] = { checked: false };
document.getElementById = (id) => els[id] || null;
const rows = [
  { deed_uid: 'a', plot: '65-1-1', rarity: 'common', terrain: 'Plains', resource: 'GRAIN', plot_status: 'natural' },
  { deed_uid: 'b', plot: '70-2-2', rarity: 'rare', terrain: 'River', resource: 'WOOD', plot_status: 'magical', magic_type: 'Fire' },
  { deed_uid: 'c', plot: '65-3-3', rarity: 'epic', terrain: 'Lake', resource: 'STONE', plot_status: 'magical', magic_type: 'Water' },
  { deed_uid: 'd', plot: '101-4-4', rarity: 'legendary', terrain: 'Plains', resource: 'IRON', plot_status: 'kingdom' },
];
const pick = (f) => { setFilters(f); return rows.filter((row) => matchesFilters(row)).map((row) => row.deed_uid); };
"""


def test_board_filters_or_within_and_across():
    _vm(BOARD_STUBS + r"""
eq(pick({}), ['a', 'b', 'c', 'd'], 'no filter');
eq(pick({ 'terrain-filter': ['Plains'] }), ['a', 'd'], 'one terrain');
eq(pick({ 'terrain-filter': ['Plains', 'River'] }), ['a', 'b', 'd'], 'two terrains are OR');
eq(pick({ 'terrain-filter': ['Plains', 'River'], 'rarity-filter': ['common', 'rare'] }), ['a', 'b'], 'filters AND together');
eq(pick({ 'region-filter': ['65', '101'] }), ['a', 'c', 'd'], 'regions OR');
eq(pick({ 'resource-filter': ['GRAIN', 'IRON'] }), ['a', 'd'], 'worksites OR');
eq(pick({ 'status-filter': ['magical:fire', 'kingdom'] }), ['b', 'd'], 'magical element OR plain type');
eq(pick({ 'status-filter': ['magical:fire', 'magical:water'] }), ['b', 'c'], 'two magical elements');
""", entry=STATIC / "app.js")


def test_persisted_single_values_still_load_and_multi_round_trips():
    _vm(r"""
eq(decodeFilterChoice(''), [], 'empty');
eq(decodeFilterChoice(null), [], 'missing');
eq(decodeFilterChoice('65'), ['65'], 'old single region string');
eq(decodeFilterChoice('land_ability_card'), ['land_ability_card'], 'old single badge string');
eq(decodeFilterChoice('[bad json'), ['[bad json'], 'garbage stays one value (then drops as unknown)');
eq(encodeFilterChoice([]), '', 'none stored as empty, as before');
eq(encodeFilterChoice(['65']), '65', 'one value stored as the old plain string');
eq(encodeFilterChoice(['65', '70']), '["65","70"]', 'several as JSON');
eq(decodeFilterChoice(encodeFilterChoice(['65', '70'])), ['65', '70'], 'round trip');
""", entry=STATIC / "app.js")


# the component in a real browser

PAGE = """<!doctype html><html><body>
<select id="terrain"><option value="">All</option><option value="Plains">Plains</option>
<option value="River">River</option><option value="Lake">Lake</option></select>
<select id="sort"><option value="pp">PP</option><option value="plot">Plot</option></select>
<select id="region"><option value="">All</option><option value="65">065 Alpha</option><option value="70">070 Beta</option></select>
</body></html>"""


@pytest.fixture(scope="module")
def page():
    from playwright.sync_api import sync_playwright
    source = COMPONENT.read_text(encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1536, "height": 864})
        pg.set_content(PAGE)
        pg.add_script_tag(type="module", content=source + """
window.CS = { initCustomSelect, syncCustomSelect, selectValues, setSelectValues };
window.CHANGES = 0;
const t = document.getElementById('terrain');
t.addEventListener('change', () => { window.CHANGES += 1; });
initCustomSelect(t, { ariaLabel: 'Terrain filter', emptyLabel: 'Terrain', multiple: true });
initCustomSelect(document.getElementById('sort'), { ariaLabel: 'Sort' });
initCustomSelect(document.getElementById('region'), { ariaLabel: 'Region filter', emptyLabel: 'Region', multiple: true, decorateTrigger: (v) => (v ? { label: v.padStart(3, '0') } : null) });
window.READY = true;
""")
        pg.wait_for_function("window.READY === true")
        yield pg
        browser.close()


def _state(page, sel_id):
    return page.evaluate(
        """(id) => { const s = document.getElementById(id); const c = s._csel;
        return { values: CS.selectValues(s), value: s.value, label: c.trigger.textContent.trim(), open: !c.list.hidden,
          multi: c.list.getAttribute('aria-multiselectable'),
          selected: [...c.list.querySelectorAll('.csel-option')].filter((li) => li.getAttribute('aria-selected') === 'true').map((li) => li.dataset.value),
          checks: [...c.list.querySelectorAll('.csel-option.is-selected .csel-check')].length,
          checkSlots: c.list.querySelectorAll('.csel-check').length }; }""",
        sel_id,
    )


def _click_option(page, sel_id, value):
    page.locator(f"#{sel_id}").locator("xpath=..").locator(f'.csel-option[data-value="{value}"]').click()


def test_component_toggles_keeps_open_and_summarises(page):
    trigger = page.locator("#terrain").locator("xpath=..").locator(".csel-trigger")
    s = _state(page, "terrain")
    assert s["multi"] == "true" and s["label"] == "Terrain" and s["values"] == [] and s["selected"] == [""]
    assert page.locator("#terrain").locator("xpath=..").locator(".csel-option").first.text_content().strip() == "All"
    trigger.click()
    _click_option(page, "terrain", "Plains")
    s = _state(page, "terrain")
    assert s["open"] and s["values"] == ["Plains"] and s["label"] == "Plains" and s["value"] == "Plains"
    _click_option(page, "terrain", "River")
    s = _state(page, "terrain")
    assert s["open"] and s["values"] == ["Plains", "River"] and s["label"] == "Plains, River"
    assert s["selected"] == ["Plains", "River"] and s["checks"] == 2 and s["checkSlots"] == 4
    _click_option(page, "terrain", "Lake")
    assert _state(page, "terrain")["label"] == "3 terrains"
    _click_option(page, "terrain", "Plains")  # toggles off
    assert _state(page, "terrain")["values"] == ["River", "Lake"]
    assert page.evaluate("window.CHANGES") == 4
    _click_option(page, "terrain", "")  # All clears and closes
    s = _state(page, "terrain")
    assert s["values"] == [] and s["value"] == "" and s["label"] == "Terrain" and not s["open"] and s["selected"] == [""]


def test_component_keyboard_space_enter_toggle_and_escape(page):
    trigger = page.locator("#terrain").locator("xpath=..").locator(".csel-trigger")
    trigger.focus()
    page.keyboard.press("ArrowDown")  # opens
    assert _state(page, "terrain")["open"]
    page.keyboard.press("ArrowDown")  # All -> Plains
    page.keyboard.press(" ")
    page.keyboard.press("ArrowDown")  # River
    page.keyboard.press("Enter")
    s = _state(page, "terrain")
    assert s["open"] and s["values"] == ["Plains", "River"]
    page.keyboard.press(" ")  # River again -> off; focus kept across the re-render
    assert _state(page, "terrain")["values"] == ["Plains"]
    page.keyboard.press("Escape")
    s = _state(page, "terrain")
    assert not s["open"] and s["values"] == ["Plains"]
    page.evaluate("() => { const s = document.getElementById('terrain'); s.value = ''; s.dispatchEvent(new Event('change')); }")
    assert _state(page, "terrain")["label"] == "Terrain"


def test_sort_select_stays_single(page):
    trigger = page.locator("#sort").locator("xpath=..").locator(".csel-trigger")
    s = _state(page, "sort")
    assert s["multi"] is None and s["checkSlots"] == 0
    trigger.click()
    _click_option(page, "sort", "plot")
    s = _state(page, "sort")
    assert not s["open"] and s["value"] == "plot" and s["label"] == "Plot"


def test_programmatic_set_values_and_trigger_decoration(page):
    page.evaluate("() => { const s = document.getElementById('region'); CS.setSelectValues(s, ['65', '70', 'gone']); CS.syncCustomSelect(s); }")
    s = _state(page, "region")
    assert s["values"] == ["65", "70"] and s["label"] == "065, 070"
    page.evaluate("() => { const s = document.getElementById('region'); CS.setSelectValues(s, ['70']); CS.syncCustomSelect(s); }")
    assert _state(page, "region")["label"] == "070"
    page.evaluate("() => { const s = document.getElementById('region'); CS.setSelectValues(s, []); CS.syncCustomSelect(s); }")
    s = _state(page, "region")
    assert s["values"] == [] and s["label"] == "Region"


def test_icon_choices_use_name_then_icons_then_count(page):
    result = page.evaluate("""() => {
      const s = document.createElement('select');
      s.multiple = true;
      s.innerHTML = '<option value="">All</option>' + ['Fire','Water','Earth','Life'].map(v => `<option value="${v}">${v}</option>`).join('');
      document.body.appendChild(s);
      CS.initCustomSelect(s, {ariaLabel:'Splinter filter', emptyLabel:'Splinter', pluralNoun:'splinters',
        decorateOption:(v) => v ? {icon:`<span class="test-icon">${v[0]}</span>`} : null});
      const states = [];
      for (const values of [[], ['Fire'], ['Fire','Water','Earth'], ['Fire','Water','Earth','Life']]) {
        CS.setSelectValues(s, values); CS.syncCustomSelect(s);
        states.push({text:s._csel.trigger.querySelector('.csel-value-text').textContent,
          icons:s._csel.trigger.querySelectorAll('.csel-picked-icon, .test-icon').length,
          aria:s._csel.trigger.getAttribute('aria-label'), title:s._csel.trigger.title});
      }
      s._csel.wrap.remove();
      return states;
    }""")
    assert result[0]["text"] == "Splinter"
    assert result[1]["text"] == "Fire" and result[1]["icons"] == 1
    assert result[2]["text"] == "" and result[2]["icons"] >= 3
    assert "Fire, Water, Earth" in result[2]["aria"] and result[2]["title"] == "Fire, Water, Earth"
    assert result[3]["text"] == "4 splinters"
