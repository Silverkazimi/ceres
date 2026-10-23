"""DEDICATED TEST queue — GOLDEN RULE 6 EXEMPT.

Synchronous Playwright guards for the approved worker-tile phase-one geometry.
This file owns its live Ceres process and is intentionally separate from async
Eos/reveal coverage required by . It is not run by the implementation queue.
"""

from __future__ import annotations

import copy
import json
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


def _fixture_row(base_url: str) -> dict:
    row = copy.deepcopy(_real_detail_row(base_url))
    workers = [copy.deepcopy(worker) for worker in row["workers"] if not worker.get("is_runi")][:2]
    assert len(workers) == 2, "tile guard needs two real worker records"
    abilities = [
        ["DD", -0.5],
        ["GRAIN", 0.6],
        ["BLOODLINE", 0.3, "Elf", {"active": True, "companions": ["Elf mate"]}],
        ["STONE", 0.85],
        ["ENERGIZED"],
    ]
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

_TILE_TOP_Y = 77.6
# Measured on the healthy band (four independent mounts, 1920x1080): 239.390625. The previous
# 239.984375 was stale by 0.59px and left the green anchor assertion below just 0.16px of its
# 0.75px tolerance -- a flake waiting to happen, and the reason a mutation that moves this row
# by 0.016px still read as "unchanged".
_PP_ROW_Y = 239.390625


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
    page.route(f"**/api/plots/{deed}?*", lambda route:
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
    page.locator(".slot-ability-row").first.wait_for(timeout=30_000)


def test_frozen_bubble_and_single_line_ability_row(live_browser: tuple[Browser, str]) -> None:
    """MUTATION: changing the satellite size/offset or adding a second ability row
    makes one of these measured boxes fail. No Eos async path is exercised here."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    row = _fixture_row(base_url)
    _mount_tile_fixture(page, base_url, row)
    state = page.evaluate("""() => {
      const cell = document.querySelector('.slot-tile-cell');
      const tile = cell.querySelector('.slot-tile');
      const bubble = cell.querySelector('.slot-energized-satellite');
      const ability = document.querySelector('.slot-ability-row');
      const host = ability.closest('.slot-tile');
      const icons = [...ability.querySelectorAll('.ability-icon, .slot-ability-overflow')];
      const rect = node => { const r = node.getBoundingClientRect(); return {left:r.left, top:r.top, width:r.width, height:r.height, bottom:r.bottom}; };
      return {tile:rect(tile), bubble:rect(bubble), ability:rect(ability), host:rect(host), caption:rect(host.querySelector('.slot-caption')), icons:icons.map(rect), rows:document.querySelectorAll('.slot-ability-row').length};
    }""")
    assert state["rows"] == 2
    assert state["bubble"]["width"] == pytest.approx(36.8, abs=0.2)
    assert state["bubble"]["height"] == pytest.approx(36.8, abs=0.2)
    assert state["bubble"]["left"] - state["tile"]["left"] == pytest.approx(35.2, abs=0.2)
    assert state["bubble"]["top"] - state["tile"]["top"] == pytest.approx(-18.4, abs=0.2)
    # the flat row sits INSIDE its tile, bottom-anchored just above the name strip.
    assert state["ability"]["top"] >= state["host"]["top"]
    assert state["ability"]["bottom"] <= state["caption"]["top"] - 2.5
    assert all(icon["width"] >= 22 and icon["height"] >= 22 for icon in state["icons"])
    page.close()


def test_overflow_tooltip_lists_hidden_abilities(live_browser: tuple[Browser, str]) -> None:
    """MUTATION: removing the automatic `data-tip` from `.slot-ability-overflow`
    leaves no portal tooltip and makes this guard fail."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    _mount_tile_fixture(page, base_url, _fixture_row(base_url))
    overflow = page.locator(".slot-ability-overflow").first
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
    makes the name tops/radius measurements diverge."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    _mount_tile_fixture(page, base_url, _fixture_row(base_url))
    values = page.evaluate("""() => {
      const tiles = [...document.querySelectorAll('.slot-group[data-group-id="worker"] .slot-tile:not(.is-empty)')].slice(0, 2);
      const names = tiles.map(tile => {
        const node = tile.querySelector('.slot-name'), range = document.createRange();
        range.selectNodeContents(node);
        return {top: node.getBoundingClientRect().top, lines: range.getClientRects().length};
      });
      const tile = tiles[0], cell = document.querySelector('.editor-base-pp-cell.is-filled');
      const tileStyle = getComputedStyle(tile), cellStyle = getComputedStyle(cell);
      return {names, tileRadius:parseFloat(tileStyle.borderTopLeftRadius), cellRadius:parseFloat(cellStyle.borderTopLeftRadius), cellHeight:cell.getBoundingClientRect().height};
    }""")
    assert values["names"][0]["top"] == pytest.approx(values["names"][1]["top"], abs=0.5)
    assert values["names"][0]["lines"] == 1 and values["names"][1]["lines"] >= 2
    assert values["cellRadius"] == pytest.approx(values["tileRadius"], abs=0.5)
    assert values["cellRadius"] <= values["cellHeight"] / 2 + 0.5
    page.close()


def test_band_contains_rail_and_preserves_tile_and_pp_y_anchors(live_browser: tuple[Browser, str]) -> None:
    """MUTATION: moving the footer back to bottom anchoring or growing the rail
    into the next row changes these band-relative geometry measurements."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    try:
        _mount_tile_fixture(page, base_url, _fixture_row(base_url))
        values = page.evaluate("""() => {
          const band = document.querySelector('.slot-editor .editor-band');
          const rect = node => { const r = node.getBoundingClientRect(); return {
            top: r.top, bottom: r.bottom, height: r.height
          }; };
          // Each tile carries its OWN `data-group-id` (editor.js's slot-tile template). The
          // band renders the Runi tile and the item tiles inside the `worker` SECTION, so
          // scoping by the section alone matched five occupied tiles -- "One Line"
          // "Two Line Worker Name", "Runi", "Epic Totem", "The Mystic" -- not the two workers
          // this fixture builds. The tile-level attribute is the discriminator.
          const tiles = [...band.querySelectorAll('.slot-group[data-group-id="worker"] .slot-tile[data-group-id="worker"]:not(.is-empty)')];
          const pp = band.querySelector('.editor-base-pp-row');
          const rail = band.querySelector('.editor-badge-rail');
          const bandRect = rect(band);
          return {
            band: bandRect,
            tileTops: tiles.map(tile => rect(tile).top - bandRect.top),
            ppTop: rect(pp).top - bandRect.top,
            rail: rect(rail),
          };
        }""")
        assert len(values["tileTops"]) == 2
        assert values["tileTops"] == pytest.approx([_TILE_TOP_Y, _TILE_TOP_Y], abs=0.75)
        assert values["ppTop"] == pytest.approx(_PP_ROW_Y, abs=0.75)
        assert values["rail"]["bottom"] <= values["band"]["bottom"] + 0.5
    finally:
        page.close()


_FOOTER_ANCHOR_MUTATION = ".editor-band .slot-editor-footer { inset: auto .8rem 0 !important; }"
_OLD_BAND_HEIGHT_MUTATION = ".editor-band { --eos-band-height: 17.5rem !important; }"


def _band_geometry_under(browser: Browser, base_url: str, mutation: str) -> dict:
    """Mount the tile fixture on a fresh page, apply one CSS mutation, measure the band."""
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    try:
        _mount_tile_fixture(page, base_url, _fixture_row(base_url))
        page.add_style_tag(content=mutation)
        return page.evaluate("""() => {
          const band = document.querySelector('.slot-editor .editor-band');
          const rail = band.querySelector('.editor-badge-rail').getBoundingClientRect();
          const pp = band.querySelector('.editor-base-pp-row').getBoundingClientRect();
          const bandRect = band.getBoundingClientRect();
          return {railBottom: rail.bottom, bandBottom: bandRect.bottom, ppTop: pp.top - bandRect.top};
        }""")
    finally:
        page.close()


def test_band_and_footer_anchor_mutations_are_red(live_browser: tuple[Browser, str]) -> None:
    """Red mutation: the old band height and the bottom-anchored footer must each fail.

    The two are applied SEPARATELY because together they cancel on the PP row. Measured on the
    healthy band, ppTop is 239.391; the bottom-anchored footer alone drives it to 260.406
    (+21px), the 17.5rem band alone leaves it at 239.391, and BOTH together land on 239.406 --
    0.016px from healthy. The old combined mutation was therefore invisible to the anchor
    assertion at any honest tolerance, and tightening the tolerance could not have rescued it.
    Dropping the PP row's own top anchor does not help either: `position: static` on
    `.editor-base-pp-row` measured 239.391, i.e. no movement at all.
    """
    browser, base_url = live_browser

    # The bottom-anchored footer is red on BOTH halves of the contract.
    footer = _band_geometry_under(browser, base_url, _FOOTER_ANCHOR_MUTATION)
    assert footer["railBottom"] > footer["bandBottom"] + 0.5
    assert footer["ppTop"] != pytest.approx(_PP_ROW_Y, abs=0.75)

    # The old band height is red on the rail-containment half. It does not move the PP row, so
    # this test does not pretend that it does.
    shrunk = _band_geometry_under(browser, base_url, _OLD_BAND_HEIGHT_MUTATION)
    assert shrunk["railBottom"] > shrunk["bandBottom"] + 0.5


def test_ability_hover_keeps_icon_size_and_has_no_long_task_over_100ms(live_browser: tuple[Browser, str]) -> None:
    """MUTATION: restoring the removed ability-icon zoom must change the icon box size."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    try:
        _mount_tile_fixture(page, base_url, _fixture_row(base_url))
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
        icons = page.locator('.slot-ability-row .ability-icon')
        assert icons.count() > 0
        page.mouse.move(0, 0)
        initial_sizes = page.evaluate("""() => [...document.querySelectorAll('.slot-ability-row .ability-icon')].map(icon => {
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
        overflow = page.locator('.slot-ability-row .slot-ability-overflow')
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
