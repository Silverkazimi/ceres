""" Phase D guards for the read-only T2/T3 mobile board."""

from __future__ import annotations

import re

from .vm_context import STATIC, run_in_module_context


HTML = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
TABLE_JS = (STATIC / "table.js").read_text(encoding="utf-8")
CSS = (STATIC / "ceres.css").read_text(encoding="utf-8")


def test_mobile_card_uses_the_table_badges_numbers_and_field_order_in_vm() -> None:
    run_in_module_context(
        r"""
function assert(ok, message) { if (!ok) throw new Error(message); }
BOARD = {badge_meta: {attention: {label: 'Attention', tooltip: 'Needs attention', kind: 'problem'}}};
const row = {
  deed_uid: 'deed-a', plot: '65-7-8', region: 'PR-BRL-65', rarity: 'Rare', terrain: 'Forest',
  resource: 'grain', worksite_type: 'Grain Farm', work_per_hour: 1234.4, harvest_pp: 98765.4,
  capacity: {display: '2/5', worker_count: 2, open_slots: 3, runi_staked: false},
  badges: ['attention'], notices: [], workers: [], ability_holders: [], bonus: {},
};
const card = plotCardHtml(row, 0);
const badges = badgeHtml(row);
assert(card.includes(badges), 'mobile card must embed badgeHtml(row) unchanged');
assert(card.includes(fmtWorkPerHour(row)), 'Harvest/hr must use the desktop formatter');
assert(!card.includes('<dt>Workers</dt>'), 'Mobile board omits worker count');
assert(card.includes('Rare') && card.includes('plot-card-classification'), 'rarity is visible');
// : a production building's card zooms ~2.5x into its full-size scene (thumbnails blur); others keep thumbnails.
assert(/\/(thumbs|web)\/land\/terrain-backgrounds\/[^'"]+\.webp/.test(card), 'mobile card uses webp terrain art');
assert(card.includes(`<dd>${num(row.harvest_pp, 0)}</dd>`), 'Total PP must use num(row.harvest_pp, 0)');
assert(card.includes(formatPlotLabel(row.plot)), 'plot label must use the desktop formatter');
assert(card.indexOf('<dt>Harvest/hr</dt>') < card.indexOf('<dt>Total PP</dt>'), 'Harvest/hr precedes Total PP');
""",
        entry=STATIC / "rows.js",
    )


def test_mobile_renderer_mounts_current_rows_in_their_existing_order_in_vm() -> None:
    run_in_module_context(
        r"""
function assert(ok, message) { if (!ok) throw new Error(message); }
document.documentElement.classList = {contains(name) { return name === 'ceres-lw-le-1279'; }};
const host = {
  html: '', replaceChildren() { this.html = ''; },
  insertAdjacentHTML(_where, value) { this.html += value; },
  setAttribute() {}, removeAttribute() {},
};
document.getElementById = (id) => id === 'plot-card-list' ? host : null;
BOARD = {badge_meta: {}};
const row = (uid, plot) => ({
  deed_uid: uid, plot, region: 'PR-BRL-1', terrain: null, resource: null,
  work_per_hour: null, harvest_pp: 0, capacity: {display: '0/5', worker_count: 0, open_slots: 5},
  badges: [], notices: [], workers: [], ability_holders: [], bonus: {},
});
CURRENT_ROWS = [row('third', '1-1-3'), row('first', '1-1-1'), row('second', '1-1-2')];
renderMobileCards();
assert(host.html.indexOf('data-deed-uid="third"') < host.html.indexOf('data-deed-uid="first"'), 'first CURRENT_ROWS item must mount first');
assert(host.html.indexOf('data-deed-uid="first"') < host.html.indexOf('data-deed-uid="second"'), 'card order must remain CURRENT_ROWS order');
assert((host.html.match(/class="plot-card(?: |")/g) || []).length === 3, 'each CURRENT_ROWS item mounts exactly once');
""",
        entry=STATIC / "table.js",
    )


def test_t3_returns_before_any_desktop_row_can_mount() -> None:
    body = TABLE_JS.split("function renderTable(isNewDataset) {", 1)[1].split("\n}", 1)[0]
    mobile = body.index("if (isMobileBoardTier()) {")
    assert body.index("tbody?.replaceChildren();", mobile) < body.index("renderMobileCards();", mobile)
    assert body.index("renderMobileCards();", mobile) < body.index("return;", mobile)
    assert body.index("return;", mobile) < body.index("renderVirtualWindow(true);")
    assert "ceres-lw-le-1279" in TABLE_JS
    assert "ceres-lw-le-767" in HTML


def test_mobile_shell_reuses_the_one_filter_control_row() -> None:
    assert HTML.count('<div class="controls-row">') == 1
    assert "controls.id = 'board-controls'" in APP_JS
    # (v0.9.527) removed the duplicate mobile Search button; Filters is the sole opener now.
    assert HTML.count('id="mobile-search-btn"') == 0
    assert HTML.count('id="mobile-filters-btn"') == 1
    # sort-select removed (far-left Sort dropdown dropped, column-header sorting remains)
    # hide-idle removed /.
    for field in (
        "text-filter", "region-filter", "rarity-filter", "terrain-filter",
        "resource-filter", "status-filter", "badge-filter", "staged-only",
    ):
        assert HTML.count(f'id="{field}"') == 1
    assert re.search(r'id="mobile-changes-btn"[^>]*\bdisabled\b', HTML)
    # mobile Harvest was enabled by v0.9.502 ("Enable mobile Harvest all and add a receipts sheet").
    assert re.search(r'id="mobile-harvest-btn"[^>]*>', HTML)


def test_phase_d_css_cannot_change_desktop_tiers_or_add_width_queries() -> None:
    block = CSS.split("/* PHASE D MOBILE BOARD START */", 1)[1].split(
        "/* PHASE D MOBILE BOARD END */", 1
    )[0]
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", block, flags=re.S))
    assert rules
    # This base rule hides a mobile-only header break and is a desktop no-op.
    unscoped_base_hides = {".board-header-break": "display: none;"}
    for selector_group, declarations in rules:
        if unscoped_base_hides.get(selector_group.strip()) == declarations.strip():
            continue
        for selector in re.split(r",(?![^()]*\))", selector_group):
            assert selector.strip().startswith((
                "html.ceres-lw-le-1919.ceres-lw-le-1279",
                "html.ceres-lw-le-1279",
            )), selector
    assert "@media" not in block
    assert not re.search(r"(?<![-A-Za-z])\d+(?:\.\d+)?(?:vw|vh)\b", block)
    assert ".plot-card-list, .mobile-action-bar, .mobile-filter-sheet-head, .mobile-filter-backdrop { display: none; }" in CSS


def test_mobile_filters_click_opens_the_visible_sheet_and_close_restores_it_in_vm() -> None:
    run_in_module_context(
        r"""
function assert(ok, message) { if (!ok) throw new Error(message); }
function classList(initial = []) {
  const values = new Set(initial);
  return {
    add(...names) { names.forEach((name) => values.add(name)); },
    remove(...names) { names.forEach((name) => values.delete(name)); },
    toggle(name, force) { force ? values.add(name) : values.delete(name); },
    contains(name) { return values.has(name); },
  };
}
function element(id, initial = []) {
  const listeners = {};
  return {
    id, hidden: id === 'mobile-filter-backdrop', classList: classList(initial), attributes: {}, focused: false,
    addEventListener(type, fn) { listeners[type] = fn; },
    click() { listeners.click?.({target: this}); },
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
    focus() { this.focused = true; },
  };
}
const controls = element('board-controls');
const elements = {
  'board-controls': controls,
  'mobile-search-btn': element('mobile-search-btn'),
  'mobile-filters-btn': element('mobile-filters-btn'),
  'mobile-filters-close': element('mobile-filters-close'),
  'mobile-filter-backdrop': element('mobile-filter-backdrop'),
  'mobile-filter-sheet-title': element('mobile-filter-sheet-title'),
  'sort-select': element('sort-select'),
};
document.documentElement.classList = classList(['ceres-lw-le-1279']);
document.querySelector = (selector) => selector === '.controls-row' ? controls : null;
document.getElementById = (id) => elements[id] || null;
document.addEventListener = () => {};
requestAnimationFrame = (fn) => { fn(); return 1; };
initMobileShell();
elements['mobile-filters-btn'].click();
assert(controls.classList.contains('is-mobile-open'), 'click must set the class the visible CSS targets');
assert(!elements['mobile-filter-backdrop'].hidden, 'click must clear the backdrop hidden state');
assert(elements['mobile-filters-btn'].attributes['aria-expanded'] === 'true', 'button must announce open');
elements['mobile-filters-close'].click();
assert(!controls.classList.contains('is-mobile-open'), 'close must remove the visible CSS state');
assert(elements['mobile-filter-backdrop'].hidden, 'close must restore backdrop hidden');
assert(elements['mobile-filters-btn'].attributes['aria-expanded'] === 'false', 'button must announce closed');
""",
        entry=STATIC / "app.js",
    )


def test_mobile_filter_sheet_stack_and_scope_match_the_js_mobile_tier() -> None:
    open_rule = re.search(r"html\.ceres-lw-le-1279 \.controls-row\.is-mobile-open \{([^}]*)\}", CSS)
    backdrop_rule = re.search(r"html\.ceres-lw-le-1279 \.mobile-filter-backdrop:not\(\[hidden\]\) \{([^}]*)\}", CSS)
    action_rule = re.search(r"\.mobile-action-bar \{([^}]*)\}", CSS)
    assert open_rule and "display: flex" in open_rule.group(1) and "visibility: visible" in open_rule.group(1)
    assert backdrop_rule and "display: block" in backdrop_rule.group(1)
    assert int(re.search(r"z-index: (\d+)", open_rule.group(1)).group(1)) > int(
        re.search(r"z-index: (\d+)", backdrop_rule.group(1)).group(1)
    ) > int(re.search(r"z-index: (\d+)", action_rule.group(1)).group(1))
    assert "html.ceres-lw-le-1919.ceres-lw-le-1279 .controls-row" not in CSS
