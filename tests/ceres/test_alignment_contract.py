from pathlib import Path
import json
import urllib.parse
import urllib.request

import pytest
from playwright.sync_api import Browser

from .conftest import settle, spawn_live_ceres
from .vm_context import run_in_module_context

CSS = Path("ceres/static/ceres.css").read_text()


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def test_slot_labels_use_real_group_and_slot_names(live_browser: tuple[Browser, str]) -> None:
    """ (row 1, (b) half): replaces the source-substring checks
    (editorEmptyLabel/editorSlotNumber's signatures and format strings; "Add
    card"/"asset-label" absent) with a rendered read of a REAL empty item slot.
    `totem` is one of `editorGroups()`'s bundled 1-capacity item groups
    (`ceres/static/app.js`'s `itemGroup('totem', 'Totem', ...)`), rendered as
    an `.asset-slot` tile inside the worker `.slot-group` (not its own
    section) via `renderEditorTile()`'s non-worker branch, which has no
    `emptyLabel` override -- so its empty-tile text is exactly
    `editorEmptyLabel()`'s `Add ${group.label}` output, unlike the `runi`/
    `power_core` satellite tiles which `renderPowerSlot()` gives a bespoke
    override label. Proves the same behaviour the old assertions named by
    spelling: the empty tile's `.empty-label` text is `Add Totem` (the real
    `group.label`, not the retired "Add card" copy), and no element in the
    editor carries the retired `asset-label` class."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    rows = page.locator("tr.plot-row")
    empty_totem = None
    editor = None
    for i in range(min(rows.count(), 40)):
        rows.nth(i).click()
        editor = page.locator(".slot-editor:visible").last
        editor.wait_for(timeout=30_000)
        # the editor first paints a loading skeleton without slot tiles; read the slots once it is gone
        editor.locator(".slot-loading-spinner").first.wait_for(state="detached", timeout=30_000)
        candidate = editor.locator(".asset-slot .slot-tile.is-empty[data-group-id='totem'] .empty-label")
        if candidate.count() == 1:
            empty_totem = candidate.first
            break
    assert empty_totem is not None, "no live plot (of the first 40 checked) has an empty totem slot to test against"
    assert empty_totem.inner_text() == "Add Totem", "the totem item group's empty-slot label must read the real group.label, not a stale literal"
    assert editor.locator(".asset-label").count() == 0, "the retired asset-label class must not appear anywhere in the rendered editor"
    page.close()


def _non_contiguous_worker_row(base_url: str) -> dict:
    """A REAL plot detail row (`GET /api/plots/{deed}`, the exact fetch
    loadPlotDetail() makes when a board row is clicked -- app.js:8248-8268), with
    its worker list thinned to slot 2 and slot 4 only (both real worker objects,
    untouched) and `capacity.potential_card_capacity` lowered to 4 -- reproducing
    the non-contiguous-numbering case (slots 1 and 3 empty) without fabricating
    any card field."""
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    # The board summary's own `workers` list is always empty (populated only by the
    # per-plot detail fetch below), so candidate plots are found by trying open
    # worker-bearing plots from the board summary's capacity hint, then checking
    # their real detail fetch for slot 2 AND slot 4 occupants.
    for row in board["plots"]:
        capacity = row.get("capacity") or {}
        if not row.get("assets_loaded") or (capacity.get("worker_count") or 0) < 2:
            continue
        deed_uid = row["deed_uid"]
        with urllib.request.urlopen(f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}", timeout=10) as response:
            detail = json.loads(response.read().decode("utf-8"))
        workers_by_slot = {w["slot"]: w for w in detail.get("workers") or []}
        if 2 in workers_by_slot and 4 in workers_by_slot:
            detail["capacity"] = dict(detail["capacity"], potential_card_capacity=4)
            detail["workers"] = [w for w in detail["workers"] if w["slot"] in (0, 2, 4)]
            # The editor draws at least as many worker tiles as the plot has staked
            # cards (editor.js stakedCardSlots(): capacity.worker_count, Runi
            # excluded), so the thinned fixture's count must match its worker list.
            detail["capacity"]["worker_count"] = len(detail["workers"])
            return detail
    raise AssertionError("no live plot has real worker cards in both slot 2 and slot 4 to build this fixture from")


def test_non_contiguous_worker_slots_keep_their_addressable_numbers(live_browser: tuple[Browser, str]) -> None:
    r"""Replaces the node-sliced editorSlotNumber()/editorEmptyLabel()/editorSlotLabel()
    unit test with the rendered editor for a REAL plot (fetched live, only its worker
    list thinned and `potential_card_capacity` lowered to 4) whose worker slots 2 and 4
    are occupied by real cards and 1 and 3 are empty -- census row 1's node half; the
    (b) substring tests above are census row 1's (b) half and stay untouched (Batch B).

    Named injection, run and watched red, then reverted and watched green:
    `ceres/static/app.js`'s `function editorEmptyLabel(group, index)` had its worker
    branch `return \`Add Worker ${editorSlotNumber(group, index)}\`;` replaced with
    `return 'Add Worker';` (drops the slot number entirely). Red: this test's first
    assertion on the empty tile at index 0 failed --
    `AssertionError: assert 'Add Worker' == 'Add Worker 1'`. Restoring the original
    line made the same run pass again (6 passed)."""
    browser, base_url = live_browser
    row = _non_contiguous_worker_row(base_url)
    deed_uid = row["deed_uid"]
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    # A plot whose assets the board row does not carry yet is opened with
    # POST /api/plots/{deed}/expand (rows.js), which answers the same PlotRow
    # shape as GET /api/plots/{deed}; serve the thinned row for either.
    for detail_path in ("", "/expand"):
        page.route(
            f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}{detail_path}?*",
            lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(row)),
        )
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    worker_group = page.locator(".slot-group[data-group-id='worker']")
    worker_group.wait_for(timeout=30_000)
    # `.slot-tile-cell` wraps only the 4 addressable worker slots -- the worker
    # group's `.slot-grid` also carries the compact runi/totem/title tiles
    # (rendered inline as `.slot-power` satellites, unwrapped), so an unscoped
    # `.slot-tile` query over-counts.
    tiles = worker_group.locator(".slot-tile-cell .slot-tile")
    assert tiles.count() == 4, "capacity override to 4 did not reach the rendered editor"

    empty_1 = tiles.nth(0)
    assert "is-empty" in (empty_1.get_attribute("class") or "")
    assert empty_1.locator(".empty-label").inner_text() == "Add Worker 1"

    # The occupied tile's label reads "Worker slot N" (editor.js singularises the
    # group label "Workers slot" for the tile copy).
    occupied_2 = tiles.nth(1)
    assert "is-empty" not in (occupied_2.get_attribute("class") or "")
    assert "Worker slot 2" in (occupied_2.get_attribute("aria-label") or "")

    empty_3 = tiles.nth(2)
    assert "is-empty" in (empty_3.get_attribute("class") or "")
    assert empty_3.locator(".empty-label").inner_text() == "Add Worker 3"

    occupied_4 = tiles.nth(3)
    assert "is-empty" not in (occupied_4.get_attribute("class") or "")
    assert "Worker slot 4" in (occupied_4.get_attribute("aria-label") or "")
    page.close()


def test_land_ability_copy_is_plot_wide_and_click_targeted():
    """ (row 1): replaces the source-substring check (four
    literals hunted anywhere in app.js by text position) with a whole-module
    load (`vm_context`) that calls the REAL functions that produce this copy
    and markup, and drives the REAL delegated click handler `initTooltips()`
    registers -- proving the plot-wide sentence is what `bloodlineAbilityTip`
    actually returns (for the holder and for its companion card), that the ability icon markup
    `abilityIconHtml` builds actually carries `data-ability-tip="1"` and is a
    `[data-tip]` tooltip trigger, and that a click on such an element is
    routed differently from a plain element (the open tooltip stays for the
    trigger, closes for anything else) -- not merely that a helper name appears
    somewhere in the file."""
    run_in_module_context(
        r"""
        // Other modules of the loaded graph register their own document click
        // listeners at import time; the one under test is the single listener
        // initTooltips() itself adds.
        const clickListenersBefore = (__ceresListeners['doc:click'] || []).length;
        initTooltips();
        const clickListeners = (__ceresListeners['doc:click'] || []).slice(clickListenersBefore);
        if (clickListeners.length !== 1) {
          throw new Error('expected exactly one delegated click listener from initTooltips(), got ' + clickListeners.length);
        }
        const onClick = clickListeners[0];

        // The plot-wide copy, read off the real function that builds a
        // single Bloodline ability icon's tooltip.
        const tip = bloodlineAbilityTip(['BLOODLINE', 0.3, 'Elf', { active: true, companions: ['Prince Rennyn'] }]);
        if (tip.body.split('\n').pop() !== 'Affects every worker on this plot.') {
          throw new Error('bloodlineAbilityTip has the wrong plot-wide body line: ' + tip.body);
        }

        // The ability icon markup that carries that tip must carry the
        // click-targeted attribute the delegated handler keys on.
        const iconHtml = abilityIconHtml('GRAIN');
        if (!iconHtml.includes('data-ability-tip="1"')) {
          throw new Error('abilityIconHtml did not emit data-ability-tip="1": ' + iconHtml);
        }

        // The separate sole-holder badge (soleBadgeInfo(), "Moving it removes
        // these from every worker on this plot.") is retired: badges.js folds
        // `sole_ability_holder` into the land-card chip and the editor's sole
        // flag is empty. The other surface of the plot-wide copy is the
        // companion card's view of the same Bloodline entry, read off the real
        // function: it must end with the same plot-wide sentence.
        const companionTip = bloodlineAbilityTip(['BLOODLINE', 0.3, 'Elf', { active: true, companions: ['Prince Rennyn'] }], { role: 'companion', holderName: 'Test Holder' });
        if (companionTip.body.split('\n').pop() !== 'Affects every worker on this plot.'
            || !companionTip.body.includes('Test Holder')) {
          throw new Error('the companion Bloodline tip lost its plot-wide line: ' + companionTip.body);
        }

        // Click targeting: the per-ability click short-circuit is retired --
        // an ability icon is now an ordinary `[data-tip]` trigger (hover/focus,
        // touch long-press), which is what makes it click-targetable. The real
        // delegated click handler keeps an open tooltip for a click ON a trigger
        // (an ability icon) and closes it for a click anywhere else.
        if (!/\bdata-tip="/.test(iconHtml)) {
          throw new Error('abilityIconHtml is not a tooltip trigger (no data-tip): ' + iconHtml);
        }
        const tooltipEl = __ceresCreated[0];  // initTooltips()'s own #ceres-tooltip
        const abilityEl = {
          closest(sel) { return sel === '[data-tip]' ? this : null; },
          getAttribute(name) { return name === 'data-ability-tip' ? '1' : null; },
        };
        tooltipEl.hidden = false;
        onClick({ target: abilityEl, preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {} });
        if (tooltipEl.hidden) {
          throw new Error('a click on an ability icon (a tooltip trigger) closed its tooltip');
        }

        // A plain element (no trigger) must close it -- proves the handler
        // discriminates between the two.
        const plainEl = { closest() { return null; }, getAttribute() { return null; } };
        onClick({ target: plainEl, preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {} });
        if (!tooltipEl.hidden) {
          throw new Error('a click on a plain element did not close the tooltip');
        }
        """,
        extra_context_js=(
            "context.__ceresListeners = __ceresListeners;\n"
            # initTooltips() repositions an open tooltip when its content box
            # resizes (a ResizeObserver on `.tip-inner`); the vm sandbox has no
            # layout, so an inert observer stands in for the browser's.
            "context.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };\n"
            # initTooltips() builds its own `#ceres-tooltip` elements via
            # document.createElement() and sets `.dataset.variant` on them
            # the shared sandbox's bare createElement() stub has no
            # `dataset`; extended here rather than in vm_context.py, since no
            # other current caller of the shared loader calls initTooltips().
            "context.__ceresCreated = [];\n"
            "document.createElement = function () {\n"
            "  const el = {\n"
            "    style: {}, dataset: {}, setAttribute() {}, removeAttribute() {},\n"
            # initTooltips() looks for an existing `.tip-inner` child of the
            # tooltip element before creating one.
            "    querySelector() { return null; },\n"
            "    appendChild() {}, append() {}, replaceChildren() {},\n"
            "    addEventListener() {}, removeEventListener() {},\n"
            "    classList: { add() {}, remove() {}, toggle() {} },\n"
            "    contains(node) { return node === el; },\n"
            "  };\n"
            "  context.__ceresCreated.push(el);\n"
            "  return el;\n"
            "};"
        ),
    )


def test_asset_art_is_contained_and_centered_without_box_overflow():
    rule = ".slot-tile-item .slot-art img"
    start = CSS.index(rule)
    declaration = CSS[start:CSS.index("}", start)]
    assert "box-sizing: border-box" in declaration
    assert "object-fit: contain" in declaration
    assert "object-position: 50% 50%" in declaration


def test_editor_keeps_four_visible_rows_and_flush_scene_contract():
    assert ".slot-group-controls" in CSS
    controls_start = CSS.index(".slot-group-controls { ")
    controls = CSS[controls_start:CSS.index("}", controls_start)]
    assert "position: sticky" in controls
    assert "background: transparent" in controls
    assert "border-radius: 0" in controls
    scene = CSS[CSS.index(".editor-scene { "):CSS.index("}", CSS.index(".editor-scene { "))]
    assert "inset: 0" in scene
    assert "border-radius" not in scene
    assert "linear-gradient(180deg" in CSS
    assert "background-image: var(--scene-worksite" in CSS


def test_theme_toggle_avatar_image_keeps_the_edge_handles_aligned(live_browser: tuple[Browser, str]) -> None:
    """initEdgeGeometryObserver() (app.js, ) exists to re-run the
 edge-alignment pass when `.theme-toggle` changes size on its own -- e.g. a future
 avatar image finishing layout inside it. Proves the observer actually fires: inject
 a 56x56 img into the live `.theme-toggle`, let the ResizeObserver + its rAF-debounced
 handler settle, and check the three edge handles (`.theme-picker`, `#wallet-picker`,
 `.badge-bar-head`) are still the same width with the same right edge -- not just that
 the observer's source exists."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.wait_for_selector(".theme-picker .theme-toggle", state="attached", timeout=10_000)
    page.evaluate(
        """() => {
          const img = document.createElement('img');
          img.width = 56; img.height = 56;
          img.src = 'data:image/svg+xml;base64,' + btoa('<svg xmlns="http://www.w3.org/2000/svg" width="56" height="56"><rect width="56" height="56" fill="#fff"/></svg>');
          document.querySelector('.theme-picker .theme-toggle').appendChild(img);
        }"""
    )
    settle(page)
    geometry = page.evaluate(
        """() => {
          const rect = sel => document.querySelector(sel).getBoundingClientRect();
          return {picker: rect('.theme-picker'), wallet: rect('#wallet-picker'), flag: rect('.badge-bar-head')};
        }"""
    )
    picker, wallet, flag = geometry["picker"], geometry["wallet"], geometry["flag"]
    assert picker["width"] == pytest.approx(wallet["width"], abs=1)
    assert picker["width"] == pytest.approx(flag["width"], abs=1)
    assert picker["right"] == pytest.approx(wallet["right"], abs=1)
    assert picker["right"] == pytest.approx(flag["right"], abs=1)
    page.close()


def test_iris_close_never_publishes_transition_widths_to_other_edge_handles(
    live_browser: tuple[Browser, str],
) -> None:
    """Closing Iris must not resize the badge bar, wallet, flag, or header reserve."""
    browser, base_url = live_browser
    page = browser.new_page(
        viewport={"width": 1536, "height": 864},
        device_scale_factor=1.25,
    )
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.wait_for_selector("#badge-flag", state="visible", timeout=10_000)
    # Open every badge slide-out. On desktop the flag opens its status tray
    # (badge-overlay.js); "all visible plots" is offered there once a status has
    # been chosen, and a flag click with slide-outs open closes them all.
    flag = page.locator("#badge-flag")
    tray_open = "document.getElementById('badge-filter-tray')?.dataset.open === 'true'"
    flag.click()
    page.wait_for_function(tray_open)
    page.locator("#badge-filter-tray .badge-filter-opt").first.click()
    page.wait_for_function(f"!({tray_open})")
    if flag.get_attribute("aria-expanded") != "true":
        if page.evaluate("document.querySelector('.badge-bar-row[aria-expanded=\"true\"]') !== null"):
            flag.click()  # closes the status's slide-outs
        flag.click()
        page.wait_for_function(tray_open)
        page.locator("#badge-filter-tray .badge-filter-opt[data-filter='all']").click()
    page.wait_for_function("document.getElementById('badge-flag')?.getAttribute('aria-expanded') === 'true'")

    settled = page.evaluate(
        """() => {
          const width = selector => document.querySelector(selector).getBoundingClientRect().width;
          return {
            edgeWidth: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--edge-w')),
            badgeBarWidth: width('#badge-bar'),
            walletWidth: width('#wallet-picker'),
            flagWidth: width('#badge-flag'),
            headerRightReserve: parseFloat(getComputedStyle(document.querySelector('header.board-header')).paddingRight),
          };
        }"""
    )
    page.evaluate(
        """() => {
          window.__ir7210Frames = [];
          window.__ir7210SamplerActive = true;
          const width = selector => document.querySelector(selector).getBoundingClientRect().width;
          const sample = () => {
            window.__ir7210Frames.push({
              edgeWidth: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--edge-w')),
              badgeBarWidth: width('#badge-bar'),
              walletWidth: width('#wallet-picker'),
              flagWidth: width('#badge-flag'),
              headerRightReserve: parseFloat(getComputedStyle(document.querySelector('header.board-header')).paddingRight),
            });
            if (window.__ir7210SamplerActive) requestAnimationFrame(sample);
          };
          requestAnimationFrame(sample);
        }"""
    )

    toggle = page.locator(".theme-picker .theme-toggle")
    toggle.click()
    page.wait_for_function("document.querySelector('.theme-picker')?.classList.contains('is-open')")
    settle(page)
    toggle.click()
    page.wait_for_function(
        """() => {
          const picker = document.querySelector('.theme-picker');
          const toggle = picker?.querySelector('.theme-toggle');
          if (!picker || !toggle || picker.classList.contains('is-open')) return false;
          const pickerStyle = getComputedStyle(picker);
          const px = value => parseFloat(value) || 0;
          const closedWidth = toggle.getBoundingClientRect().width
            + px(pickerStyle.paddingLeft) + px(pickerStyle.paddingRight)
            + px(pickerStyle.borderLeftWidth) + px(pickerStyle.borderRightWidth);
          return Math.abs(picker.getBoundingClientRect().width - closedWidth) <= 0.5;
        }""",
        timeout=3_000,
    )
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    frames = page.evaluate(
        """() => {
          window.__ir7210SamplerActive = false;
          return window.__ir7210Frames;
        }"""
    )

    assert frames, "the requestAnimationFrame sampler captured no frames"
    for metric, settled_value in settled.items():
        maximum = max(frame[metric] for frame in frames)
        assert maximum <= settled_value + 1, (
            f"{metric} exceeded its settled closed value during Iris open/close: " f"settled={settled_value:.2f}px, max={maximum:.2f}px"
        )
    page.close()


def test_wallet_handle_and_panel_share_every_open_close_frame(
    live_browser: tuple[Browser, str],
) -> None:
    """The edge handle and wallet panel must use one measured motion timeline."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.wait_for_selector("#wallet-edge-tab", state="visible", timeout=10_000)
    page.evaluate(
        """() => {
          window.__ir7330Frames = [];
          window.__ir7330Sampling = true;
          const clamp = value => Math.max(0, Math.min(1, value));
          const read = () => {
            const picker = document.querySelector('#wallet-picker');
            const tray = picker?.querySelector('.edge-tray');
            const drawer = document.querySelector('#regional-dec-drawer');
            const panel = drawer?.querySelector('.dec-drawer-panel');
            const trayWidth = tray?.getBoundingClientRect().width || 0;
            const trayTarget = tray?.scrollWidth || 1;
            const transform = panel && getComputedStyle(panel).transform;
            const offset = !panel || transform === 'none' ? 0 : Number.parseFloat(transform.split(',')[4]) || 0;
            const panelWidth = panel?.getBoundingClientRect().width || 1;
            window.__ir7330Frames.push({
              pickerTarget: picker?.dataset.open || 'false',
              pickerAria: document.querySelector('#wallet-edge-tab')?.getAttribute('aria-expanded') || 'false',
              drawerTarget: drawer?.dataset.walletMotion || 'closed',
              handleProgress: clamp(trayWidth / trayTarget),
              panelProgress: drawer?.open ? clamp(1 - offset / panelWidth) : 0,
              drawerOpen: Boolean(drawer?.open),
            });
            if (window.__ir7330Sampling) requestAnimationFrame(read);
          };
          requestAnimationFrame(read);
        }"""
    )

    page.locator("#wallet-edge-tab").click()
    page.wait_for_function("document.querySelector('#regional-dec-drawer')?.open === true", timeout=5_000)
    settle(page)
    page.set_viewport_size({"width": 1536, "height": 864})
    settle(page)
    page.mouse.click(10, 10)
    page.wait_for_function("document.querySelector('#regional-dec-drawer')?.open === false", timeout=5_000)
    page.wait_for_function("document.querySelector('#wallet-picker')?.dataset.open === 'false'", timeout=5_000)
    page.evaluate(
        """() => {
          window.__ir7330Sampling = false;
          return window.__ir7330Frames;
        }"""
    )
    frames = page.evaluate("() => window.__ir7330Frames")

    assert frames, "the requestAnimationFrame sampler captured no frames"
    for frame in frames:
        assert frame["pickerTarget"] == ("true" if frame["drawerTarget"] == "open" else "false")
        assert abs(frame["handleProgress"] - frame["panelProgress"]) <= 0.08, frame
        if frame["handleProgress"] >= 0.99:
            assert frame["panelProgress"] >= 0.90, frame
        if frame["panelProgress"] >= 0.99:
            assert frame["handleProgress"] >= 0.90, frame
        if frame["handleProgress"] <= 0.01 or frame["panelProgress"] <= 0.01:
            assert frame["handleProgress"] <= 0.01 and frame["panelProgress"] <= 0.01, frame
    assert any(frame["pickerTarget"] == "true" and frame["drawerOpen"] for frame in frames)
    assert any(frame["pickerTarget"] == "false" and not frame["drawerOpen"] for frame in frames)
    assert frames[-1]["pickerAria"] == "false"
    page.close()


def test_theme_toggle_stays_topmost_at_its_centre(live_browser: tuple[Browser, str]) -> None:
    """the root-level Iris dock must paint above the sticky board header."""
    browser, base_url = live_browser
    page = browser.new_page(
        viewport={"width": 1536, "height": 864},
        device_scale_factor=1.25,
    )
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.wait_for_selector(".theme-picker .theme-toggle", state="visible", timeout=10_000)

    def _theme_toggle_hit():
        return page.evaluate(
            """() => {
              const toggle = document.querySelector('.theme-picker .theme-toggle');
              const rect = toggle.getBoundingClientRect();
              const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
              const hit = document.elementFromPoint(x, y);
              const describe = element => ({
                tag: element?.tagName || null,
                className: typeof element?.className === 'string'
                  ? element.className
                  : element?.className?.baseVal || '',
              });
              return {
                matches: !!hit && (hit === toggle || toggle.contains(hit)),
                hit: describe(hit),
                toggle: describe(toggle),
                x,
                y,
                scrollY: window.scrollY,
              };
            }"""
        )

    initial = _theme_toggle_hit()
    assert initial["matches"], f"theme toggle centre hit {initial['hit']} at scroll 0; expected {initial['toggle']}"
    page.evaluate("window.scrollTo(0, 600)")
    settle(page)
    scrolled = _theme_toggle_hit()
    assert scrolled["matches"], (
        f"theme toggle centre hit {scrolled['hit']} at scrollY={scrolled['scrollY']}; " f"expected {scrolled['toggle']}"
    )
    page.close()


def test_mobile_theme_toggle_only_occupies_the_open_filter_sheet(live_browser: tuple[Browser, str]) -> None:
    """the root-level Iris dock must not cover mobile header controls."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 430, "height": 900})
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function(
        "document.documentElement.classList.contains('ceres-lw-le-1279')"
        " && document.querySelector('#plot-card-list .plot-card') !== null",
        timeout=30_000,
    )
    page.wait_for_selector("#mobile-filters-btn", state="visible", timeout=10_000)

    def _dock_state():
        return page.locator("#header.theme-picker-dock").evaluate(
            """dock => {
              const rect = dock.getBoundingClientRect();
              return {
                display: getComputedStyle(dock).display,
                rect: {
                  x: rect.x, y: rect.y, top: rect.top, right: rect.right,
                  bottom: rect.bottom, left: rect.left, width: rect.width, height: rect.height,
                },
              };
            }"""
        )

    def _centre_hit(selector: str):
        return page.locator(selector).evaluate(
            """element => {
              const rect = element.getBoundingClientRect();
              const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
              const hit = document.elementFromPoint(x, y);
              return {
                matches: !!hit && (hit === element || element.contains(hit)),
                hit: hit?.tagName || null,
                x,
                y,
              };
            }"""
        )

    closed_dock = _dock_state()
    assert closed_dock["display"] == "none"
    assert all(value == 0 for value in closed_dock["rect"].values()), "closed mobile dock must have a zero rect"

    go_hit = _centre_hit("#player-go-btn")
    assert go_hit["matches"], f"mobile Go centre hit {go_hit['hit']} at ({go_hit['x']}, {go_hit['y']})"

    page.locator("#mobile-filters-btn").click()
    picker = page.locator(".theme-picker .theme-toggle")
    picker.wait_for(state="visible", timeout=10_000)
    page.wait_for_function(
        """() => {
          const toggle = document.querySelector('.theme-picker .theme-toggle');
          const rect = toggle?.getBoundingClientRect();
          if (!toggle || !rect) return false;
          const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
          return !!hit && (hit === toggle || toggle.contains(hit));
        }""",
        timeout=3_000,
    )
    open_geometry = page.evaluate(
        """() => {
          const dock = document.querySelector('#header.theme-picker-dock');
          const controls = document.querySelector('#board-controls');
          const head = controls.querySelector('.mobile-filter-sheet-head');
          const dockRect = dock.getBoundingClientRect();
          const sheetRect = controls.getBoundingClientRect();
          return {
            dockTop: dockRect.top,
            dockRight: dockRect.right,
            expectedTop: sheetRect.top + controls.clientTop,
            expectedRight: sheetRect.left + controls.clientLeft + controls.clientWidth,
            headPaddingInlineEnd: parseFloat(getComputedStyle(head).paddingInlineEnd),
            rootFontSize: parseFloat(getComputedStyle(document.documentElement).fontSize),
          };
        }"""
    )
    assert open_geometry["dockTop"] == pytest.approx(open_geometry["expectedTop"], abs=1)
    assert open_geometry["dockRight"] == pytest.approx(open_geometry["expectedRight"], abs=1)
    assert open_geometry["headPaddingInlineEnd"] == pytest.approx(3.5 * open_geometry["rootFontSize"], abs=1)
    picker_hit = _centre_hit(".theme-picker .theme-toggle")
    assert picker_hit["matches"], (
        f"open-sheet theme toggle centre hit {picker_hit['hit']} at ({picker_hit['x']}, {picker_hit['y']})"
    )

    page.locator("#board-controls").evaluate("element => element.classList.add('is-search-mode')")
    assert picker.is_hidden(), "search mode must hide the mobile filter sheet's theme picker"
    search_dock = _dock_state()
    assert search_dock["display"] == "none"
    assert all(value == 0 for value in search_dock["rect"].values()), "search-mode mobile dock must have a zero rect"
    page.close()


def test_badge_flag_hit_target_survives_scroll(live_browser: tuple[Browser, str]) -> None:
    """the flag-over-sticky-header clickability fix () was only proven
 by ad-hoc headless hit-tests -- this makes it an executed guard. `document.
 elementFromPoint` at the centre of `.badge-bar-head` must resolve to `#badge-flag`
 (or one of its descendants) both before any scroll and after scrolling past the
 sticky table header, proving nothing else painted over the flag's hit area."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.wait_for_selector(".badge-bar-head", state="visible", timeout=10_000)

    def _hit_is_flag():
        return page.evaluate(
            """() => {
              const flag = document.getElementById('badge-flag');
              const rect = flag.getBoundingClientRect();
              const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
              const hit = document.elementFromPoint(cx, cy);
              return !!hit && (hit === flag || flag.contains(hit));
            }"""
        )

    assert _hit_is_flag(), "badge-bar-head hit target must resolve to #badge-flag at scroll 0"
    page.evaluate("window.scrollTo(0, 600)")
    settle(page)
    assert _hit_is_flag(), "badge-bar-head hit target must still resolve to #badge-flag after scrolling 600px"
    page.close()


def test_slot_text_is_centered_and_badges_never_wrap():
    tile_start = CSS.index("\n.slot-tile { ") + 1
    tile = CSS[tile_start:CSS.index("}", tile_start)]
    # The base `.slot-caption` rule starts its own line; a bare substring search
    # first hits the descendant `.slot-tile .slot-caption { transition: ... }` rule.
    caption_start = CSS.index("\n.slot-caption { ") + 1
    caption = CSS[caption_start:CSS.index("}", caption_start)]
    badges_start = CSS.index(".slot-badges { display: block;")
    badges = CSS[badges_start:CSS.index("}", badges_start)]
    assert "text-align: center" in tile
    assert "align-items: center" in caption
    assert "text-align: center" in caption
    assert "white-space: nowrap" in badges
