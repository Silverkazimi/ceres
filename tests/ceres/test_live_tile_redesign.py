"""DEDICATED TEST queue — GOLDEN RULE 6 EXEMPT.

Synchronous Playwright guards for the approved worker-tile phase-one geometry.
This file owns its live Ceres process and is intentionally separate from async
Eos/reveal coverage required by . It is not run by the implementation queue.
"""

from __future__ import annotations

import copy
import json
import re
import urllib.parse

import pytest
from playwright.sync_api import Browser, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .conftest import settle, spawn_live_ceres
from .test_candidate_redesign_frontend import _real_detail_row


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as live:
        yield live


_ABILITIES = [
    ["DD", -0.5],
    ["GRAIN", 0.6],
    ["BLOODLINE", 0.3, "Elf", {"active": True, "companions": ["Elf mate"]}],
    ["STONE", 0.85],
    ["ENERGIZED"],
]
# An Energized worker's abilities ring its Energized satellite as an arc (editor.js
# renderAbilityRow) that shows up to ABILITY_MAX = 5 icons; the four above all fit. The
# overflow guards need hidden abilities: with seven, the arc keeps the first four in
# tier order (Toil and Kin, then the resource bonuses in listing order) and folds the
# remaining three -- Bountiful Grain, Bountiful Stone, Dark Discount -- into "+3".
_OVERFLOW_ABILITIES = [
    ["DD", -0.5],
    ["WOOD", 0.4],
    ["IRON", 0.4],
    ["AURA", 0.4],
    ["GRAIN", 0.6],
    ["BLOODLINE", 0.3, "Elf", {"active": True, "companions": ["Elf mate"]}],
    ["STONE", 0.85],
    ["ENERGIZED"],
]


def _fixture_row(base_url: str, abilities: list | None = None) -> dict:
    row = copy.deepcopy(_real_detail_row(base_url))
    workers = [copy.deepcopy(worker) for worker in row["workers"] if not worker.get("is_runi")][:2]
    assert len(workers) == 2, "tile guard needs two real worker records"
    abilities = copy.deepcopy(abilities or _ABILITIES)
    workers[0].update(name="One Line", asset_ref="tile-redesign-one", land_abilities=abilities)
    workers[1].update(name="Two Line Worker Name", asset_ref="tile-redesign-two", land_abilities=abilities)
    for slot, worker in enumerate(workers, 1):
        worker["slot"] = slot
    row["workers"] = workers
    row["capacity"].update(worker_count=2, open_slots=3, max_workers_allowed=5, card_capacity=5)
    return row


_BAND_PRESENTED = (
    "() => ['ready','degraded'].includes("
    "document.querySelector('.slot-editor .editor-band')?.dataset.plotState)"
)

# Records in-page `fetch` activity for `/api/...` calls, ignoring the `/api/dev/watch`
# long-poll which never goes quiet.
_NET_PROBE = """(() => {
  const s = window.__ceresNet = {inflight: 0, count: 0, last: 0};
  const original = window.fetch;
  window.fetch = function (...args) {
    const url = String(args[0]?.url || args[0] || '');
    const tracked = url.includes('/api/') && !url.includes('/api/dev/watch');
    if (tracked) { s.inflight += 1; s.count += 1; s.last = performance.now(); }
    const result = original.apply(this, args);
    return tracked ? result.finally(() => { s.inflight -= 1; s.last = performance.now(); }) : result;
  };
})();"""

_NET_QUIET = (
    "() => { const s = window.__ceresNet;"
    " return !!s && s.count > 0 && s.inflight === 0 && performance.now() - s.last > 1200; }"
)

# The tile's design width: editor.js scales every tile decoration (satellite, ability arc)
# by `cell width / 107.2`, so the frozen satellite geometry is stated in these units.
_TILE_UNIT = 107.2

# The y anchors were pinned pixels of the retired fixed band (77.6 / 239.39 at 1920x1080). The
# desktop band is scene-scaled now (`--plot-scene-unit`, ceres.css), so each anchor is derived
# from the band's own layout terms, each measured by a zero-width probe sized by those terms
# alone: the tiles sit at facts + controls + card top, and the Base PP row is top-anchored
# 3.2 effect units under the footer's top edge (facts + controls + cards). A layout change that
# moves a row without changing its terms -- the bottom-anchored footer -- misses its anchor.
_ANCHORS = """band => {
  const probe = expr => {
    const node = document.createElement('div');
    node.style.cssText = `position:absolute;inline-size:0;block-size:${expr}`;
    band.querySelector('.editor-main').append(node);
    const height = node.getBoundingClientRect().height;
    node.remove();
    return height;
  };
  return {
    tileTop: probe('calc(var(--eos-facts) + var(--eos-controls) + var(--eos-card-top))'),
    ppTop: probe('calc(var(--eos-facts) + var(--eos-controls) + var(--eos-cards) + 3.2 * var(--eos-effect-unit))'),
  };
}"""


def _mount_settled(page: Page, base_url: str, row: dict, candidates: list) -> None:
    """`test_live_r117_terrain_display._mount` with the board-reload race closed.

    `page.goto(base_url)` renders an unscoped board first and then reloads it
    player-scoped (`/api/board?player=...`). Clicking the plot row inside that window
    loses the row's detail permanently: the reload's recovery call (board.js's
    `for (const deedUid of EXPANDED_ROWS) loadPlotDetail(row, generation)`) is deduped
    into the still-in-flight request by rows.js's `existing?.generation === generation`,
    and that request then discards itself on `BOARD.plots[idx] !== row` because the
    board swap replaced the row object. `EXPANDED_DATA_LOADED` is never set, the band
    stays `dataReady:false` -- `data-plot-state="scene-visible"`, `aria-busy="true"`,
    zero `.slot-tile` -- and every guard below times out. Waiting for the player-scoped
    board to land before the click closes the window: measured 25 s stall -> 0.77 s to
    `degraded` on the same machine and the same product tree.
    """
    deed = urllib.parse.quote(row["deed_uid"], safe="")
    # The board opens a plot whose assets the page load's capture dropped through
    # POST /api/plots/<deed>/expand, not GET /api/plots/<deed>; serve the built row on both.
    page.route(re.compile(rf"/api/plots/{re.escape(deed)}(/expand)?(\?|$)"), lambda route:
               route.fulfill(status=200, content_type="application/json", body=json.dumps(row)))
    page.route(f"**/api/plots/{deed}/candidates*", lambda route:
               route.fulfill(status=200, content_type="application/json", body=json.dumps({
                   "candidates": candidates, "total": len(candidates), "source": "test-fixture",
                   "excluded_counts": {}, "notices": [],
               })))
    page.add_init_script(_NET_PROBE)
    page.goto(base_url)
    # Every board reload must have landed BEFORE the row is opened. `#player-input`
    # is not a usable signal: the first, unscoped `/api/board` already answers with
    # the server's default player, so board.js fills the input before the
    # player-scoped reload has even been issued. Quiet network is the real signal.
    page.wait_for_function(_NET_QUIET, timeout=30_000)
    plot_row = page.locator(f"tr.plot-row[data-deed-uid='{row['deed_uid']}']")
    for _attempt in range(4):
        plot_row.click(timeout=30_000)
        page.locator(".slot-editor .editor-band").wait_for(timeout=30_000)
        try:
            page.wait_for_function(_BAND_PRESENTED, timeout=8_000)
            break
        except PlaywrightTimeoutError:
            # Belt and braces for any residual reload race: closing and re-opening the
            # row re-issues the detail fetch and recovers (measured: stalled open ->
            # reopen reaches `degraded` with 8 tiles).
            plot_row.click(timeout=30_000)
            settle(page)
    else:
        raise AssertionError("editor band never reached data-plot-state ready|degraded")
    page.evaluate("() => document.fonts.ready")


def _mount_tile_fixture(page: Page, base_url: str, row: dict) -> None:
    _mount_settled(page, base_url, row, [])
    page.wait_for_function(_BAND_PRESENTED, timeout=30_000)
    # Both fixture workers are Energized: their abilities ring the Energized satellite as
    # an arc (editor.js renderAbilityRow), not a flat row. The arc itself is a zero-size
    # anchor; its icons are what is visible.
    page.locator(".slot-ability-arc .ability-icon").first.wait_for(timeout=30_000)


def test_frozen_bubble_and_single_line_ability_row(live_browser: tuple[Browser, str]) -> None:
    """MUTATION: changing the satellite size/offset or adding a second ability row
    makes one of these measured boxes fail. No Eos async path is exercised here.

    An Energized worker's abilities no longer sit in a flat row inside the tile: they ring
    the Energized satellite as ONE arc (editor.js renderAbilityRow). The satellite keeps its
    frozen geometry, stated in tile units (36.8 / 35.2 / -18.4 of a 107.2 tile, the old
    pixels at the old tile width); the arc is one ring per worker, centred on the satellite,
    clear of the satellite, the plot controls and the name strip, with full-size icons."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    row = _fixture_row(base_url)
    _mount_tile_fixture(page, base_url, row)
    state = page.evaluate("""() => {
      const rect = node => { const r = node.getBoundingClientRect(); return {left:r.left, top:r.top, width:r.width, height:r.height, bottom:r.bottom, right:r.right}; };
      const band = document.querySelector('.slot-editor .editor-band');
      const cells = [...band.querySelectorAll('.slot-group[data-group-id="worker"] .slot-tile-cell')]
        .filter(cell => cell.querySelector('.slot-ability-arc'));
      return {
        flatRows: band.querySelectorAll('.slot-ability-row').length,
        arcs: band.querySelectorAll('.slot-ability-arc').length,
        controls: rect(band.querySelector('.slot-group-controls')),
        cells: cells.map(cell => {
          const tile = cell.querySelector('.slot-tile');
          return {
            arcsInCell: cell.querySelectorAll('.slot-ability-arc').length,
            tile: rect(tile), bubble: rect(cell.querySelector('.slot-energized-satellite')),
            caption: rect(tile.querySelector('.slot-caption')),
            items: [...cell.querySelectorAll('.slot-ability-arc > .slot-ability-item')].map(rect),
            icons: [...cell.querySelectorAll('.slot-ability-arc .ability-icon, .slot-ability-arc .slot-ability-overflow')].map(rect),
          };
        }),
      };
    }""")
    assert state["flatRows"] == 0
    assert state["arcs"] == 2 and len(state["cells"]) == 2
    for cell in state["cells"]:
        unit = cell["tile"]["width"] / _TILE_UNIT
        bubble, tile = cell["bubble"], cell["tile"]
        assert cell["arcsInCell"] == 1
        assert bubble["width"] == pytest.approx(36.8 * unit, abs=0.3)
        assert bubble["height"] == pytest.approx(36.8 * unit, abs=0.3)
        assert bubble["left"] - tile["left"] == pytest.approx(35.2 * unit, abs=0.3)
        assert bubble["top"] - tile["top"] == pytest.approx(-18.4 * unit, abs=0.3)
        centre = (bubble["left"] + bubble["width"] / 2, bubble["top"] + bubble["height"] / 2)
        items = cell["items"]
        assert len(items) == 4  # Toil and Kin, Bountiful Grain, Bountiful Stone, Dark Discount
        points = [(item["left"] + item["width"] / 2, item["top"] + item["height"] / 2) for item in items]
        radii = [((x - centre[0]) ** 2 + (y - centre[1]) ** 2) ** 0.5 for x, y in points]
        assert max(radii) - min(radii) <= 1, radii  # one ring around the satellite
        assert min(radii) >= bubble["width"] / 2 + items[0]["width"] / 2 - 0.5, radii  # clear of it
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            assert ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 >= items[0]["width"] - 0.5  # no two icons touch
        for item in items:
            assert item["top"] >= state["controls"]["bottom"]  # clear of Fill plot / Clear workers / Clear plot
            assert item["bottom"] <= cell["caption"]["top"] - 2.5  # above the name strip
        assert all(icon["width"] >= 22 * unit - 0.5 and icon["height"] >= 22 * unit - 0.5 for icon in cell["icons"])
    page.close()


def test_overflow_tooltip_lists_hidden_abilities(live_browser: tuple[Browser, str]) -> None:
    """MUTATION: removing the automatic `data-tip` from `.slot-ability-overflow`
    leaves no portal tooltip and makes this guard fail."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    _mount_tile_fixture(page, base_url, _fixture_row(base_url, _OVERFLOW_ABILITIES))
    overflow = page.locator(".slot-ability-arc .slot-ability-overflow").first
    overflow.wait_for(timeout=30_000)
    assert overflow.inner_text() == "+3"
    overflow.hover()
    tooltip = page.locator("#ceres-tooltip:visible")
    tooltip.wait_for(timeout=5_000)
    text = tooltip.inner_text()
    for name in ("Bountiful Grain", "Bountiful Stone", "Dark Discount"):
        assert name in text
    page.close()


def test_names_share_the_same_top_anchor_and_base_pp_is_a_pill(live_browser: tuple[Browser, str]) -> None:
    """MUTATION: restoring flex-end caption alignment or the unclamped radius
    makes the name tops/radius measurements diverge.

    The Base PP cell's corner is the cards' 6px shape, `--ceres-pp-radius` (ceres.css), not
    the theme's `--radius`: that is 11px in dark/light/wood and would turn the short cell into
    a pill. So the cell matches the tile's corner where the theme's radius IS the card shape
    (alchemical, 6px) and keeps that shape in every other theme."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    _mount_tile_fixture(page, base_url, _fixture_row(base_url))
    measure = """() => {
      const tiles = [...document.querySelectorAll('.slot-group[data-group-id="worker"] .slot-tile:not(.is-empty)')].slice(0, 2);
      const names = tiles.map(tile => {
        const node = tile.querySelector('.slot-name'), range = document.createRange();
        range.selectNodeContents(node);
        return {top: node.getBoundingClientRect().top, lines: range.getClientRects().length};
      });
      const tile = tiles[0], cell = document.querySelector('.editor-base-pp-cell.is-filled');
      const tileStyle = getComputedStyle(tile), cellStyle = getComputedStyle(cell);
      const probe = document.createElement('div');
      probe.style.cssText = 'position:absolute;inline-size:0;block-size:var(--ceres-pp-radius)';
      cell.append(probe);
      const token = probe.getBoundingClientRect().height;
      probe.remove();
      return {names, tileRadius:parseFloat(tileStyle.borderTopLeftRadius), cellRadius:parseFloat(cellStyle.borderTopLeftRadius), cellHeight:cell.getBoundingClientRect().height, token};
    }"""
    for theme in ("alchemical", "dark", "light", "wood"):
        page.evaluate("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
        values = page.evaluate(measure)
        assert values["names"][0]["top"] == pytest.approx(values["names"][1]["top"], abs=0.5), theme
        assert values["names"][0]["lines"] == 1 and values["names"][1]["lines"] >= 2, theme
        if theme == "alchemical":
            assert values["cellRadius"] == pytest.approx(values["tileRadius"], abs=0.5), values
        assert values["token"] == pytest.approx(6, abs=0.01), (theme, values)
        assert values["cellRadius"] == pytest.approx(values["token"], abs=0.5), (theme, values)
        assert values["cellRadius"] <= values["cellHeight"] / 2 + 0.5, (theme, values)
    page.close()


_BADGES_AND_FOOTER = """band => {
  const bandRect = band.getBoundingClientRect();
  const bottom = selector => band.querySelector(selector).getBoundingClientRect().bottom - bandRect.top;
  return {
    bandHeight: bandRect.height,
    badgesBottom: bottom('.slot-group-controls .editor-splinter-affinity'),
    footerBottom: bottom('.slot-editor-footer'),
  };
}"""


def test_band_contains_rail_and_preserves_tile_and_pp_y_anchors(live_browser: tuple[Browser, str]) -> None:
    """MUTATION: moving the footer back to bottom anchoring or growing the rail
    into the next row changes these band-relative geometry measurements.

    The badge rail (`.editor-badge-rail`) is retired: the plot's terrain badges are the
    terrain-affinity strip at the head of the worker controls row, and the footer is the
    band's last row. Both must stay inside the band."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    try:
        _mount_tile_fixture(page, base_url, _fixture_row(base_url))
        band = page.locator(".slot-editor .editor-band")
        anchors = band.evaluate(_ANCHORS)
        contained = band.evaluate(_BADGES_AND_FOOTER)
        values = page.evaluate("""() => {
          const band = document.querySelector('.slot-editor .editor-band');
          const rect = node => { const r = node.getBoundingClientRect(); return {
            top: r.top, bottom: r.bottom, height: r.height
          }; };
          // Each tile carries its OWN `data-group-id` (editor.js's slot-tile template). The
          // band renders the Runi tile and the item tiles inside the `worker` SECTION, so
          // scoping by the section alone matched five occupied tiles -- "One Line",
          // "Two Line Worker Name", "Runi", "Epic Totem", "The Mystic" -- not the two workers
          // this fixture builds. The tile-level attribute is the discriminator.
          const tiles = [...band.querySelectorAll('.slot-group[data-group-id="worker"] .slot-tile[data-group-id="worker"]:not(.is-empty)')];
          const pp = band.querySelector('.editor-base-pp-row');
          const bandRect = rect(band);
          return {
            band: bandRect,
            tileTops: tiles.map(tile => rect(tile).top - bandRect.top),
            ppTop: rect(pp).top - bandRect.top,
          };
        }""")
        assert len(values["tileTops"]) == 2
        assert values["tileTops"] == pytest.approx([anchors["tileTop"]] * 2, abs=0.75)
        assert values["ppTop"] == pytest.approx(anchors["ppTop"], abs=0.75)
        assert contained["badgesBottom"] <= contained["bandHeight"] + 0.5
        assert contained["footerBottom"] <= contained["bandHeight"] + 0.5
    finally:
        page.close()


_FOOTER_ANCHOR_MUTATION = ".editor-band .slot-editor-footer { inset: auto .8rem 0 !important; }"
# The retired fixed band height. `--eos-band-height` no longer sizes the desktop band (it is
# the scene-scaled `--eos-content-height`, ceres.css), so the old mutation is applied to the
# term that does.
_OLD_BAND_HEIGHT_MUTATION = ".editor-band { --eos-content-height: 17.5rem !important; }"


def _band_geometry_under(browser: Browser, base_url: str, mutation: str) -> dict:
    """Mount the tile fixture on a fresh page, apply one CSS mutation, measure the band."""
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    try:
        _mount_tile_fixture(page, base_url, _fixture_row(base_url))
        band = page.locator(".slot-editor .editor-band")
        anchors = band.evaluate(_ANCHORS)
        page.add_style_tag(content=mutation)
        measured = band.evaluate(_BADGES_AND_FOOTER)
        measured["ppTop"] = band.evaluate(
            "band => band.querySelector('.editor-base-pp-row').getBoundingClientRect().top - band.getBoundingClientRect().top"
        )
        measured["anchorPpTop"] = anchors["ppTop"]
        return measured
    finally:
        page.close()


def test_band_and_footer_anchor_mutations_are_red(live_browser: tuple[Browser, str]) -> None:
    """Red mutation: the old band height and the bottom-anchored footer must each fail.

    The two are applied SEPARATELY because together they can cancel on the PP row (measured
    on the retired fixed band: the bottom-anchored footer alone moved it +21px, the 17.5rem
    band alone left it in place, both together landed 0.016px from healthy). On the
    scene-scaled band the bottom-anchored footer moves the PP row off its anchor (+21px at
    1920) while the footer itself still ends on the band's bottom edge, so that mutation is
    red on the anchor half only; the shrunken band leaves the PP row where it is and is red on
    the containment half -- the footer is pushed out of the band. Neither pretends otherwise.
    """
    browser, base_url = live_browser

    footer = _band_geometry_under(browser, base_url, _FOOTER_ANCHOR_MUTATION)
    assert footer["ppTop"] != pytest.approx(footer["anchorPpTop"], abs=0.75)

    shrunk = _band_geometry_under(browser, base_url, _OLD_BAND_HEIGHT_MUTATION)
    assert shrunk["footerBottom"] > shrunk["bandHeight"] + 0.5


def test_ability_hover_keeps_icon_size_and_has_no_long_task_over_100ms(live_browser: tuple[Browser, str]) -> None:
    """MUTATION: restoring the removed ability-icon zoom must change the icon box size."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    try:
        # the overflow fixture, so the arc's "+3" control is hovered as well as its icons
        _mount_tile_fixture(page, base_url, _fixture_row(base_url, _OVERFLOW_ABILITIES))
        supported = page.evaluate("""() => {
          if (typeof PerformanceObserver !== 'function') return false;
          try {
            window.__ceresLongTasks = [];
            window.__ceresLongTaskObserver = new PerformanceObserver(list => {
              window.__ceresLongTasks.push(...list.getEntries().map(entry => entry.duration));
            });
            window.__ceresLongTaskObserver.observe({type: 'longtask', buffered: false});
            return true;
          } catch (_) { return false; }
        }""")
        assert supported, "browser lacks the longtask PerformanceObserver required by this guard"
        icons = page.locator('.slot-ability-arc .ability-icon')
        assert icons.count() > 0
        page.mouse.move(0, 0)
        initial_sizes = page.evaluate("""() => [...document.querySelectorAll('.slot-ability-arc .ability-icon')].map(icon => {
          const rect = icon.getBoundingClientRect();
          return {width: rect.width, height: rect.height};
        })""")
        for index in range(icons.count()):
            icon = icons.nth(index)
            icon.hover()
            settle(page)
            hovered = icon.bounding_box()
            assert hovered is not None
            assert abs(hovered["width"] - initial_sizes[index]["width"]) <= 1
            assert abs(hovered["height"] - initial_sizes[index]["height"]) <= 1
        overflow = page.locator('.slot-ability-arc .slot-ability-overflow')
        for index in range(overflow.count()):
            overflow.nth(index).hover()
            settle(page)
        durations = page.evaluate("""() => {
          window.__ceresLongTaskObserver?.disconnect();
          return window.__ceresLongTasks || [];
        }""")
        assert all(duration <= 100 for duration in durations), durations
    finally:
        page.close()


def test_picker_ring_stays_one_svg_inside_tile(live_browser: tuple[Browser, str]) -> None:
    """Explicit scope guard: the redesign must not alter the existing picker ring;
    the ring remains a single SVG child inside the focused `.slot-tile`."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    _mount_tile_fixture(page, base_url, _fixture_row(base_url))
    empty = page.locator('.slot-group[data-group-id="worker"] .slot-tile[data-slot-action="add"]').first
    focused = page.locator('.slot-tile.is-picker-focus').first
    for _attempt in range(4):
        empty.click()
        try:
            focused.wait_for(timeout=5_000)
            break
        except Exception:
            continue
    focused.wait_for(timeout=15_000)
    assert focused.locator(":scope > .picker-focus-ring").count() == 1
    assert page.locator('.slot-tile .picker-focus-ring').count() == 1
    page.close()
