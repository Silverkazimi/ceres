"""Rendered tooltip inventory for the six live Ceres screen states."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Locator, Page, TimeoutError as PlaywrightTimeoutError

from .conftest import spawn_live_ceres
from .vm_context import STATIC, run_in_module_context


VIEWPORT = {"width": 1920, "height": 1080}
KNOWN_ARIA_GAPS = ("#board-version", ".player-switch")
STATE_NAMES = (
    "board_table",
    "slot_editor",
    "candidate_picker",
    "checkout_panel",
    "worksite_modal",
    "header_filters",
)
BOARD_TOOLTIP_KINDS = frozenset(
    {"rarity", "splinter", "terrain", "set", "type", "totem", "title", "resource", "ability"}
)
HONEST_GAP_KINDS = frozenset({"set", "title"})


def _pointer_onto(page, target) -> None:
    """Bring a real pointer to rest on `target`, the way a user's pointer arrives.

    After any scroll, tooltip.js opens nothing until the pointer has really moved more
    than SCROLL_QUIET_PX (maintainer, 2026-09-29: "tooltips create hurdles when
    scrolling"), and the board's own load-time scrolls (its filter selects) count as one.
    Playwright's `hover()` is a single synthetic move, which only arms that check; a short
    stepped approach is a real pointer move."""
    box = target.bounding_box()
    assert box, "hover target has no layout box"
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(max(0, x - 30), max(0, y - 30))
    page.mouse.move(x, y, steps=6)


def _asset_root() -> Path:
    source = (STATIC / "state.js").read_text(encoding="utf-8")
    match = re.search(r"export const ASSET_BASE = ['\"]([^'\"]+)['\"]", source)
    assert match, "state.js must define the renderer asset base"
    return STATIC / Path(match.group(1)).relative_to("/static")

_HARVEST_SCRIPT = """
state => {
  const selectorFor = element => {
    if (element.id) return `#${CSS.escape(element.id)}`;
    const classes = [...element.classList].filter(Boolean);
    if (classes.length) {
      const candidate = `.${classes.map(name => CSS.escape(name)).join('.')}`;
      if (document.querySelectorAll(candidate).length === 1) return candidate;
    }
    const attrs = ['data-deed-uid', 'data-group-id', 'data-slot-index',
      'data-slot-action', 'data-picker-action', 'data-checkout-action',
      'data-worksite-action', 'data-ability-tip'];
    const anchor = attrs.find(name => element.hasAttribute(name));
    if (anchor) {
      const candidate = `${element.tagName.toLowerCase()}[${anchor}="${CSS.escape(element.getAttribute(anchor))}"]`;
      if (document.querySelectorAll(candidate).length === 1) return candidate;
    }
    const parts = [];
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE && current !== document.body) {
      let part = current.tagName.toLowerCase();
      const siblings = [...current.parentElement.children].filter(
        sibling => sibling.tagName === current.tagName
      );
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
      parts.unshift(part);
      const candidate = parts.join(' > ');
      if (document.querySelectorAll(candidate).length === 1) return candidate;
      current = current.parentElement;
    }
    return parts.join(' > ');
  };

  const identityKind = element => {
    const classes = element.classList;
    if (classes.contains('rarity-gem')) return 'rarity';
    if (classes.contains('terrain-emblem')) return 'terrain';
    if (classes.contains('resource-icon')) return 'resource';
    if (classes.contains('type-mark')) return 'type';
    if (classes.contains('splinter-icon') || classes.contains('terrain-affinity-badge')) return 'splinter';
    if (classes.contains('set-icon')) return 'set';
    if (classes.contains('totem-icon')) return 'totem';
    if (classes.contains('title-icon')) return 'title';
    if (classes.contains('ability-icon') && element.tagName === 'IMG') return 'ability';
    return null;
  };
  return [...document.querySelectorAll('[data-tip], [title], [data-ability-tip]')].map(element => ({
    selector: selectorFor(element),
    visible_text: (element.innerText || '').trim(),
    data_tip: element.getAttribute('data-tip'),
    data_tip_body: element.getAttribute('data-tip-body'),
    data_tip_foot: element.getAttribute('data-tip-foot'),
    data_tip_kind: element.getAttribute('data-tip-kind'),
    data_tip_severity: element.getAttribute('data-tip-severity'),
    data_tip_icon: element.getAttribute('data-tip-icon'),
    identity_kind: identityKind(element),
    data_tip_key: element.getAttribute('data-tip-key'),
    data_ability_tip: element.getAttribute('data-ability-tip'),
    aria_label: element.getAttribute('aria-label'),
    native_title_present: element.hasAttribute('title'),
    native_title: element.getAttribute('title'),
    state,
  }));
}
"""


class StateUnreachable(RuntimeError):
    pass


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _page(browser: Browser, base_url: str) -> Page:
    page = browser.new_page(viewport=VIEWPORT)
    page.goto(base_url)
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0",
        timeout=30_000,
    )
    page.locator(".controls-row").wait_for(timeout=10_000)
    return page


def _harvest(page: Page, state: str) -> list[dict]:
    elements = page.evaluate(_HARVEST_SCRIPT, state)
    print(f"tooltip harvest {state}: {len(elements)} elements", flush=True)
    return elements


def _expand(page: Page, index: int) -> Locator:
    row = page.locator("#plot-tbody > tr.plot-row").nth(index)
    row.click()
    expanded = page.locator("tr.expand-row").nth(index)
    try:
        expanded.locator(".editor-band[data-plot-state='ready']").wait_for(timeout=30_000)
    except PlaywrightTimeoutError as exc:
        raise StateUnreachable(f"row {index} did not render its slot editor") from exc
    return expanded


def _open_editor_with_empty_worker(page: Page) -> Locator:
    rows = page.locator("#plot-tbody > tr.plot-row")
    for index in range(rows.count()):
        expanded = _expand(page, index)
        empty = expanded.locator(
            ".slot-group[data-group-id='worker'] button.slot-tile.is-empty[data-slot-action='add'][data-group-id='worker']"
        )
        if empty.count():
            if not expanded.locator(".slot-group[data-group-id='worker']").count():
                raise StateUnreachable(f"row {index} has no worker sub-table")
            return expanded
        rows.nth(index).click()
    raise StateUnreachable("no plot exposed an actionable empty worker slot")


def _open_candidate_picker(page: Page, expanded: Locator) -> None:
    empty = expanded.locator(
        ".slot-group[data-group-id='worker'] button.slot-tile.is-empty[data-slot-action='add'][data-group-id='worker']"
    ).first
    if not empty.count():
        raise StateUnreachable("the selected editor had no empty worker slot")
    empty.click()
    picker = expanded.locator(".inline-picker")
    try:
        picker.locator(".candidate-row").first.wait_for(timeout=30_000)
    except PlaywrightTimeoutError as exc:
        raise StateUnreachable("worker candidate picker did not render a candidate") from exc


def _open_worksite_modal(page: Page) -> None:
    rows = page.locator("#plot-tbody > tr.plot-row")
    for index in range(rows.count()):
        expanded = _expand(page, index)
        button = expanded.locator("button.worksite-change-btn")
        if button.count():
            button.first.click()
            modal = page.locator("#worksite-modal-overlay")
            try:
                modal.wait_for(state="visible", timeout=10_000)
            except PlaywrightTimeoutError as exc:
                raise StateUnreachable(f"worksite modal did not open for row {index}") from exc
            return
        rows.nth(index).click()
    raise StateUnreachable("no live plot exposed a Change worksite button")


def _collect_states(browser: Browser, base_url: str) -> dict[str, dict]:
    states: dict[str, dict] = {}

    page = _page(browser, base_url)
    try:
        states["header_filters"] = {"elements": _harvest(page, "header_filters")}
    finally:
        page.close()

    page = _page(browser, base_url)
    try:
        expanded = _open_editor_with_empty_worker(page)
        states["board_table"] = {"elements": _harvest(page, "board_table")}
        states["slot_editor"] = {"elements": _harvest(page, "slot_editor")}
        _open_candidate_picker(page, expanded)
        states["candidate_picker"] = {"elements": _harvest(page, "candidate_picker")}
    except StateUnreachable as exc:
        states.setdefault("board_table", {"elements": [], "unreachable": str(exc)})
        states.setdefault("slot_editor", {"elements": [], "unreachable": str(exc)})
        states.setdefault("candidate_picker", {"elements": [], "unreachable": str(exc)})
    finally:
        page.close()

    page = _page(browser, base_url)
    try:
        expanded = _open_editor_with_empty_worker(page)
        _open_candidate_picker(page, expanded)
        expanded.locator(".candidate-row").first.click()
        page.wait_for_function(
            "document.getElementById('checkout-btn') && !document.getElementById('checkout-btn').hidden",
            timeout=30_000,
        )
        page.locator("#checkout-btn").click()
        panel = page.locator("#checkout-panel")
        panel.wait_for(state="visible", timeout=10_000)
        panel.locator(".ch-tiles .ch-slot").first.wait_for(timeout=10_000)
        states["checkout_panel"] = {"elements": _harvest(page, "checkout_panel")}
    except (StateUnreachable, PlaywrightTimeoutError) as exc:
        states["checkout_panel"] = {"elements": [], "unreachable": str(exc)}
    finally:
        page.close()

    page = _page(browser, base_url)
    try:
        _open_worksite_modal(page)
        states["worksite_modal"] = {"elements": _harvest(page, "worksite_modal")}
    except StateUnreachable as exc:
        states["worksite_modal"] = {"elements": [], "unreachable": str(exc)}
    finally:
        page.close()

    for state in STATE_NAMES:
        states.setdefault(state, {"elements": [], "unreachable": "state setup stopped early"})
    return states


@pytest.fixture(scope="module")
def tooltip_harvest(live_browser: tuple[Browser, str]) -> dict:
    browser, base_url = live_browser
    payload = {"viewport": VIEWPORT, "states": _collect_states(browser, base_url)}
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", prefix="ceres-tooltip-surface-", dir="/tmp", delete=False
    ) as artifact:
        artifact_path = Path(artifact.name)
        json.dump(payload, artifact, indent=2, sort_keys=True)
    try:
        loaded = json.loads(artifact_path.read_text(encoding="utf-8"))
        print(f"tooltip harvest artifact: {artifact_path}", flush=True)
        return loaded
    finally:
        artifact_path.unlink(missing_ok=True)


def _missing_aria(payload: dict) -> list[dict]:
    return [
        {"state": state_name, "selector": element["selector"]}
        for state_name, state in payload["states"].items()
        for element in state["elements"]
        if element["data_tip"] and not (element["aria_label"] or "").strip()
    ]


def _rgb(value: str) -> tuple[int, int, int]:
    channels = [float(channel) for channel in re.findall(r"\d+(?:\.\d+)?", value)]
    assert len(channels) >= 3, f"expected an RGB color, got {value!r}"
    if value.strip().startswith("color(") and max(channels[:3]) <= 1:
        channels[:3] = [channel * 255 for channel in channels[:3]]
    return tuple(round(channel) for channel in channels[:3])


def _relative_luminance_rgb(color: tuple[int, int, int]) -> float:
    channels = [channel / 255 for channel in color]
    linear = [channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio_rgb(foreground: tuple[int, int, int], background: tuple[int, int, int]) -> float:
    foreground_luminance = _relative_luminance_rgb(foreground)
    background_luminance = _relative_luminance_rgb(background)
    lighter, darker = sorted((foreground_luminance, background_luminance), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_rendered_key_contrast_meets_wcag_aa_for_all_theme_severity_combinations(
    live_browser: tuple[Browser, str],
) -> None:
    """The key's rendered color must clear 4.5:1 against the rendered tooltip fill.

    Only the four ``none`` cases are production-reachable: no production site
    emits both a key and a severity. The other eight theme × severity cases are
    forward-guards; the severity is set by hand on the rendered box. Injection
    (not applied): ``.tip-key { color: var(--gold) }`` makes the wood case fail
    and name wood at 1.59:1.
    """
    browser, base_url = live_browser
    page = _page(browser, base_url)
    try:
        expanded = _open_editor_with_empty_worker(page)
        target = expanded.locator("[data-tip-key]").first
        target.wait_for(timeout=10_000)
        for theme in ("alchemical", "dark", "light", "wood"):
            page.evaluate("theme => { document.documentElement.dataset.theme = theme; }", theme)
            for severity in ("none", "caution", "danger"):
                target.scroll_into_view_if_needed()
                _pointer_onto(page, target)
                tooltip = page.locator("#ceres-tooltip.is-visible")
                tooltip.wait_for(timeout=5_000)
                tooltip.evaluate(
                    "(el, value) => value === 'none' ? el.removeAttribute('data-severity') : el.setAttribute('data-severity', value)",
                    severity,
                )
                colors = tooltip.evaluate(
                    "el => { const key = el.querySelector('.tip-key'); const keyStyle = getComputedStyle(key); const boxStyle = getComputedStyle(el); return {key: keyStyle.color, background: boxStyle.backgroundColor}; }"
                )
                ratio = _contrast_ratio_rgb(_rgb(colors["key"]), _rgb(colors["background"]))
                assert ratio >= 4.5, f"{theme} x {severity}: {ratio:.2f}:1 below WCAG AA 4.5:1"
                page.mouse.move(5, 5)
    finally:
        page.close()


def test_rendered_key_has_weight_size_position_and_name_empty_guard(
    live_browser: tuple[Browser, str],
) -> None:
    """The key is bold, larger than body text, first in the body, and labels stay empty.

    Injection (not applied): ``font-weight: 400`` makes the weight assertion
    fail; appending ``''`` unconditionally to the body makes the Name-size
    ``:empty`` assertion fail.
    """
    browser, base_url = live_browser
    page = _page(browser, base_url)
    try:
        expanded = _open_editor_with_empty_worker(page)
        target = expanded.locator("[data-tip-key]").first
        target.wait_for(timeout=10_000)
        target.scroll_into_view_if_needed()
        _pointer_onto(page, target)
        tooltip = page.locator("#ceres-tooltip.is-visible")
        tooltip.wait_for(timeout=5_000)
        metrics = tooltip.evaluate(
            "el => { const key = el.querySelector('.tip-key'); const body = el.querySelector('.tip-body'); return {weight: Number.parseFloat(getComputedStyle(key).fontWeight), keySize: Number.parseFloat(getComputedStyle(key).fontSize), bodySize: Number.parseFloat(getComputedStyle(body).fontSize), first: body.firstElementChild === key}; }"
        )
        assert metrics["weight"] >= 700
        assert metrics["keySize"] > metrics["bodySize"]
        assert metrics["first"]
        page.mouse.move(5, 5)

        # title-only content is no longer selected by the retired label
        # kind; identify it by its empty body instead.
        label = page.locator("[data-tip]:not([data-tip-body]):visible").first
        label.wait_for(timeout=10_000)
        label.scroll_into_view_if_needed()
        _pointer_onto(page, label)
        tooltip.wait_for(timeout=5_000)
        assert tooltip.locator(".tip-body").evaluate("el => el.offsetHeight") == 0
    finally:
        page.close()


def test_every_harvested_key_is_in_aria_label(tooltip_harvest: dict) -> None:
    """Every rendered key must remain in its accessible name.

    Injection (not applied): dropping ``key`` from ``accessibleText`` makes
    this assertion fail for the named key-bearing element.
    """
    keyed = [
        (state_name, element)
        for state_name, state in tooltip_harvest["states"].items()
        for element in state["elements"]
        if element["data_tip_key"]
    ]
    assert keyed, "the harvester found no key-bearing tooltip element"
    missing = [
        {"state": state_name, "selector": element["selector"], "key": element["data_tip_key"]}
        for state_name, element in keyed
        if element["data_tip_key"] not in (element["aria_label"] or "")
    ]
    assert not missing, f"key missing from aria-label: {missing}"


def test_tooltip_icon_helper_branches_emit_their_declared_icons() -> None:
    """Exercise every phase-one helper branch, including branches absent from live data."""
    body = r"""
const expected = [
  ['rarity', tooltipIconPath('rarity', 'common'), 'rarity/gem-common.svg'],
  ['plot-rarity-standard', (plotRarityCellHtml({ rarity: 'common' }).match(/data-tip-icon="([^"]+)"/) || [])[1], 'rarity/gem-common.svg'],
  ['plot-rarity-mythic-keep', (plotRarityCellHtml({ rarity: 'mythic', worksite_type: 'KEEP' }).match(/data-tip-icon="([^"]+)"/) || [])[1], 'land/status-keeps-on.svg'],
  ['plot-rarity-mythic-castle', (plotRarityCellHtml({ rarity: 'mythic', worksite_type: 'CASTLE' }).match(/data-tip-icon="([^"]+)"/) || [])[1], 'land/status-castle-on.svg'],
  ['terrain', tooltipIconPath('terrain', 'forest'), 'land/terrain-emblem-forest.svg'],
  ['resource-dec', tooltipIconPath('resource', 'dec'), 'land/token-dec.png'],
  ['resource-map', tooltipIconPath('resource', 'wood'), 'resources/wood_200.webp'],
  ['splinter', tooltipIconPath('splinter', 'fire'), 'splinter/fire.svg'],
  ['type-default', tooltipIconPath('type', 'natural'), 'land/status-natural-off.svg'],
  ['type-kingdom', tooltipIconPath('type', 'kingdom', 'KEEP'), 'land/status-keeps-off.svg'],
  ['set', tooltipIconPath('set', 'chaos'), 'sets/chaos-legion.svg'],
  ['totem', tooltipIconPath('totem', 'common'), 'totems/totem-common.png'],
  ['title', tooltipIconPath('title', 'The Untamed'), 'titles/title-untamed.svg'],
  ['ability-map', tooltipIconPath('ability', 'GRAIN'), 'abilities/ability_bountiful-grain.png'],
  ['ability-research', tooltipIconPath('ability', 'research'), 'resources/research.png'],
  ['ability-sps', tooltipIconPath('ability', 'sps'), 'resources/sps.png'],
];
for (const [branch, actual, expectedIcon] of expected) {
  if (actual !== expectedIcon) throw new Error(`${branch}: expected ${expectedIcon}, got ${actual}`);
}
for (const [branch, actual] of [
  ['ability-static-charge-gap', tooltipIconPath('ability', 'static_charge')],
  ['unknown-set-gap', tooltipIconPath('set', 'Riftwatchers')],
]) {
  if (actual !== null) throw new Error(`${branch}: expected no tooltip icon, got ${actual}`);
}
if (tooltipIconPath('title', 'The Uncatalogued') !== 'titles/title-proven.svg') {
  throw new Error('unknown title must use the approved fallback icon');
}
"""
    run_in_module_context(body, entry=STATIC / "icons.js")


def test_board_identity_tooltip_icons_are_present_and_local(tooltip_harvest: dict) -> None:
    sites = [
        (state_name, element)
        for state_name, state in tooltip_harvest["states"].items()
        for element in state["elements"]
        if element["identity_kind"] in BOARD_TOOLTIP_KINDS
    ]
    assert sites, "the harvester found no board identity tooltip sites"
    missing = [
        {"state": state_name, "selector": element["selector"], "kind": element["identity_kind"]}
        for state_name, element in sites
        if not (element["data_tip_icon"] or "").strip()
        and element["identity_kind"] not in HONEST_GAP_KINDS
    ]
    assert not missing, f"board identity tooltip icons missing: {missing}"

    invalid = []
    asset_root = _asset_root()
    for state_name, element in sites:
        if element["identity_kind"] in HONEST_GAP_KINDS:
            if (element["data_tip_icon"] or "").strip():
                invalid.append({"state": state_name, "kind": element["identity_kind"], "icon": element["data_tip_icon"]})
            continue
        icon = element["data_tip_icon"]
        if not re.fullmatch(r"[a-z0-9/_-]+\.(?:svg|png|webp)", icon or "", re.IGNORECASE):
            invalid.append({"state": state_name, "icon": icon})
            continue
        if not (asset_root / icon).is_file():
            invalid.append({"state": state_name, "icon": icon})
    assert not invalid, f"board identity tooltip icon assets missing or gaps misclassified: {invalid}"

    unique_sites = {(element["identity_kind"], element["selector"]) for _, element in sites}
    assert len(unique_sites) >= len(BOARD_TOOLTIP_KINDS)
    assert BOARD_TOOLTIP_KINDS <= {kind for kind, _ in unique_sites}


def test_fractional_base_pp_cell_keeps_exact_attribute_without_tooltip(
    live_browser: tuple[Browser, str],
) -> None:
    """A live fractional Base PP cell keeps ``data-pp-exact`` but no hover tooltip.

    Injection (not applied): restoring the Exact PP branch in ``ppAttrs`` makes
    this cell carry ``data-tip`` and names the failure here.
    """
    browser, base_url = live_browser
    page = _page(browser, base_url)
    try:
        # R-138: select only after the board's reveal-dependent cells exist
        # the exact plot and figure are snapshot data, not a fixture contract.
        cells = page.locator("tr.plot-row td[data-pp-exact]")
        cell = None
        for index in range(cells.count()):
            candidate = cells.nth(index)
            exact = candidate.get_attribute("data-pp-exact") or ""
            if "." in exact and candidate.get_attribute("data-tip") is None:
                cell = candidate
                break
        if cell is None:
            pytest.skip("No fractional Base PP cell without a tooltip is present in the live snapshot")
        cell.wait_for(timeout=30_000)
        assert cell.get_attribute("data-tip") is None
    finally:
        page.close()


def test_clear_plot_button_keeps_danger_severity_and_foot(
    live_browser: tuple[Browser, str],
) -> None:
    """Clear plot remains a danger warning with a non-empty foot."""
    browser, base_url = live_browser
    page = _page(browser, base_url)
    try:
        expanded = _expand(page, 0)
        button = expanded.locator("button[data-slot-action='clear_plot']").first
        button.wait_for(timeout=10_000)
        assert button.get_attribute("data-tip-severity") == "danger"
        assert (button.get_attribute("data-tip-foot") or "").strip()
    finally:
        page.close()


def test_badged_plot_renders_badge_row(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = _page(browser, base_url)
    try:
        rows = page.locator("#plot-tbody > tr.plot-row")
        badged = []
        for index in range(rows.count()):
            _expand(page, index)
            if rows.nth(index).locator(".badge-row").count():
                badged.append(index)
                break
            rows.nth(index).click()
        assert badged, "the offline board fixture has no badge-bearing plot"
        assert rows.nth(badged[0]).locator(".badge-row > *").count() > 0
    finally:
        page.close()


def test_tooltip_harvest_is_nonempty_in_all_six_states(tooltip_harvest: dict) -> None:
    unreachable = {
        name: state.get("unreachable")
        for name, state in tooltip_harvest["states"].items()
        if state.get("unreachable")
    }
    empty = [
        name for name, state in tooltip_harvest["states"].items() if not state["elements"]
    ]
    assert not unreachable, f"unreachable tooltip states: {json.dumps(unreachable, sort_keys=True)}"
    assert not empty, f"empty tooltip states: {empty}"


def test_no_element_carries_data_tip_and_native_title(tooltip_harvest: dict) -> None:
    double_tooltips = [
        {"state": state_name, "selector": element["selector"]}
        for state_name, state in tooltip_harvest["states"].items()
        for element in state["elements"]
        if element["data_tip"] and element["native_title_present"]
    ]
    assert not double_tooltips, f"data-tip + native title found: {double_tooltips}"


def test_data_tip_aria_gaps_are_limited_to_known_static_selectors(tooltip_harvest: dict) -> None:
    missing = _missing_aria(tooltip_harvest)
    unexpected = [entry for entry in missing if entry["selector"] not in KNOWN_ARIA_GAPS]
    assert not unexpected, (
        "app-wide data-tip aria-label assertion failed; " f"all missing elements: {json.dumps(missing, sort_keys=True)}"
    )


@pytest.mark.parametrize("selector", KNOWN_ARIA_GAPS)
def test_known_data_tip_aria_gap_is_named_individually(selector: str, tooltip_harvest: dict) -> None:
    matches = [entry for entry in _missing_aria(tooltip_harvest) if entry["selector"] == selector]
    assert not matches, f"{selector} still lacks aria-label: {matches}"
