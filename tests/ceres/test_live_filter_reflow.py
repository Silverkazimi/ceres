"""Live browser regressions for Ceres' plot table through filters, fills and
font changes. : the table is sized by CSS to its board width (no JS
column lock, no horizontal scroll); these guards keep the original promises in
that model -- the full table returns to its exact reference width, nothing is
clipped, the window position survives a filter, and the table never scrolls
sideways. 1366 px is the narrowest common desktop width (1280 is the card view)."""

from __future__ import annotations

import json
import urllib.request

import pytest
from playwright.sync_api import Browser, Page

from .conftest import spawn_live_ceres
from .table_edge import OVERHANG_JS, TABLE_OVERHANG_PX, overhang_problems


@pytest.fixture(scope="module")
def browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (instance, base_url):
        yield instance, base_url


# A saved avatar-builder character (GET /players/player_avatar/{name}, avatar.js)
# for the fixture account. The board's refresh control is the account avatar
# medallion, shown only once the avatar draws; the fixture recording has no
# avatar for sample01, and adding one there would put the medallion into every
# board test's header, so the one test that needs the control serves it here.
_AVATAR = {
    "race": "human", "gender": 1, "body": 1, "skin_color": 1, "background": 1,
    "face": 1, "hair": 1, "hair_color": 1, "eyebrows": 1, "eyes": 1, "eyes_color": 1,
    "nose": 1, "mouth": 1, "complexion": 0, "scars": 0, "tattoo": 0,
    "earrings": 0, "earrings_color": 1, "accessory": 0, "accessory_color": 1,
    "cloth": 1, "cloth_color": 1, "hat": 0, "hat_color": 1,
}


def _page(browser: tuple[Browser, str], *, slow_fill: bool = False, avatar: bool = False) -> Page:
    instance, base_url = browser
    page = instance.new_page(viewport={"width": 1366, "height": 900})
    if avatar:
        page.route(
            "https://api.splinterlands.com/players/player_avatar/sample01*",
            lambda route: route.fulfill(status=200, content_type="application/json",
                                        headers={"Access-Control-Allow-Origin": "*"}, body=json.dumps(_AVATAR)),
        )
    if slow_fill:
        page.add_init_script(
            """
            window.requestIdleCallback = function (callback) {
              return window.setTimeout(function () {
                callback({timeRemaining: function () { return 0; }});
              }, 1000);
            };
            window.cancelIdleCallback = window.clearTimeout;
            """
        )
    page.goto(f"{base_url}/?player=sample01")  # no ?player= opens the start page
    return page


def _board_payload(base_url: str) -> dict:
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


# The account's plot count is not fixed (the internal rules: 161 on 2026-08-19, 130 on
# 2026-08-20 after a trade. Never hardcode it anywhere; read it live.)
# this file used to hardcode 130 in ~10 `_wait_rows` calls plus 6 bare `130`
# literals, and it already silently drifted from 105 -> 130 once and had to
# be hand-patched. `plot_count` reads the live total from /api/board once per
# module and every assertion below derives from it instead.
@pytest.fixture(scope="module")
def plot_count(browser: tuple[Browser, str]) -> int:
    _, base_url = browser
    return len(_board_payload(base_url)["plots"])


def _problem_plot_count(board: dict) -> int:
    badge_meta = board["badge_meta"]
    return sum(
        any(badge_meta.get(code, {"kind": "problem"}).get("kind", "problem") == "problem" for code in row.get("badges", []))
        for row in board["plots"]
    )


def _wait_rows(page: Page, count: int, *, settle: bool = True) -> None:
    page.wait_for_function(
        "expected => document.querySelectorAll('#plot-tbody > tr.plot-row').length === expected",
        arg=count,
        timeout=60_000,
    )
    if settle:
        _wait_stable_width(page)


# Real completion signal for ANY async column relock -- the debounced
# font-size relock (app.js's maybeRelockOnFontChange(), `app.js:933-953`, a
# 200ms setTimeout after 'resize'), a background-fill relock, or a
# filter/sort re-render -- instead of a fixed `wait_for_timeout` that can
# expire mid-relock under system load. v0.5.5 flakiness fix (2026-08-24)
# this file used to sprinkle fixed sleeps (300ms/350ms/500ms/1_300ms/2_500ms)
# at each of these points, tuned against an idle box. Characterised live
# (script + trace against a real running server, not guessed): a font-size
# relock across the full 130-row table took as long as 1_501ms to settle on
# an otherwise-idle box, well past the 350ms sleep this file used to rely on
# (already >4x, before any load) -- under the heavy concurrent Playwright
# load this box regularly carries (several agents' browsers at once), that
# gap widens further and the old fixed sleeps read a genuinely mid-relock
# not-yet-settled width. The value it eventually settles to (e.g. 2868px for
# a 130-row table at a 32px root font-size, exactly double the 16px-root
# 1441px reference -- rem-based padding/gaps double) is NOT a bug -- it's the
# correct fully-relocked state, reproduced by loading a fresh page straight
# at 32px font, which settles there on its own; the bug was always in the
# test's own timing, never in the app. This 1_501ms figure is the whole
# justification for polling instead of sleeping here -- re-measure it (same
# method: poll `#plot-table.scrollWidth` at ~25ms intervals through a font
# resize dispatch) before assuming it's stale, not from a hunch.
# Polls `#plot-table`'s scrollWidth via requestAnimationFrame (an accurate
# real-completion signal tied to actual paint frames, not a Python-side
# sleep loop) until it stops changing for `stable_ms` consecutive
# milliseconds, then returns the settled value. A superseding call
# self-terminates any still-running previous watch via a token check, so
# calling this repeatedly within one page/test never accumulates rAF loops.
def _wait_stable_width(page: Page, *, stable_ms: int = 250, timeout_ms: int = 20_000) -> int:
    page.evaluate(
        """(stableMs) => {
          const token = {};
          window.__ceresWidthToken = token;
          const state = { lastValue: null, lastChange: performance.now(), stableMs, stableSince: 0 };
          window.__ceresWidthWatch = state;
          const tick = () => {
            if (window.__ceresWidthToken !== token) return; // superseded -- stop this chain
            const table = document.getElementById('plot-table');
            const value = table ? table.scrollWidth : null;
            const now = performance.now();
            if (value !== state.lastValue) { state.lastValue = value; state.lastChange = now; }
            state.stableSince = now - state.lastChange;
            requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
        }""",
        stable_ms,
    )
    page.wait_for_function(
        "() => window.__ceresWidthWatch && window.__ceresWidthWatch.lastValue !== null"
        " && window.__ceresWidthWatch.stableSince >= window.__ceresWidthWatch.stableMs",
        timeout=timeout_ms,
    )
    return page.evaluate("window.__ceresWidthWatch.lastValue")


def _metrics(page: Page) -> dict:
    return page.evaluate(
        """
        () => ({
          tableWidth: document.querySelector('#plot-table').scrollWidth,
          scrollerWidth: document.querySelector('#plot-table-scroll').scrollWidth,
          left: document.querySelector('#plot-table-scroll').scrollLeft,
          scrollerOver: document.querySelector('#plot-table-scroll').scrollWidth
            - document.querySelector('#plot-table-scroll').clientWidth,
          pageOver: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          windowTop: window.scrollY,
          maxWindowTop: document.documentElement.scrollHeight - window.innerHeight,
          clipped: [...document.querySelectorAll('#plot-tbody tr.plot-row td')]
            .filter(td => td.scrollWidth > td.clientWidth + 2).length,
          rows: document.querySelectorAll('#plot-tbody > tr.plot-row').length,
          overhang: (""" + OVERHANG_JS + """)()
        })
        """
    )


def _assert_unclipped(page: Page) -> dict:
    metrics = _metrics(page)
    assert metrics["clipped"] == 0, json.dumps(metrics, sort_keys=True)
    # nothing but the deliberate 8px edge budget (the last column's reserve
    # padding, table_edge.py) passes the clipping scroller, nothing scrolls sideways.
    assert metrics["scrollerOver"] <= TABLE_OVERHANG_PX + 1 and metrics["pageOver"] <= 0, json.dumps(metrics, sort_keys=True)
    assert not overhang_problems(metrics["overhang"]), json.dumps(metrics, sort_keys=True)
    assert metrics["left"] == 0, json.dumps(metrics, sort_keys=True)
    return metrics


def _wait_filtered_rows(page: Page, total: int) -> None:
    # Explicit 60s timeout (was Playwright's 30s default) -- v0.5.5 flakiness
    # audit: caught this specific default timing out under this session's
    # observed 9-17 load average (several concurrent agents' own Playwright
    # runs) in a full-suite run, even though the same test passed cleanly in
    # isolation immediately after. Not a `wait_for_timeout` fixed sleep (this
    # file's actual assigned audit target -- see `_wait_stable_width`'s own
    # comment), but the same principle applies: a real completion condition
    # deserves room to actually complete on a loaded box, not an arbitrary
    # ceiling tuned against an idle one. Identical condition, just more
    # patient -- does not weaken what is being waited for.
    page.wait_for_function(
        "total => { const count = document.querySelectorAll('#plot-tbody > tr.plot-row').length;"
        " return count > 0 && count < total; }",
        arg=total,
        timeout=60_000,
    )


def _full_width(browser: tuple[Browser, str], total: int, *, font_size: str | None = None) -> int:
    page = _page(browser)
    _wait_rows(page, total)
    if font_size:
        page.evaluate(
            "size => { document.documentElement.style.fontSize = size; window.dispatchEvent(new Event('resize')); }",
            font_size,
        )
        _wait_stable_width(page)
    width = _metrics(page)["tableWidth"]
    page.close()
    return width


def test_reload_while_filtered_then_clear_restores_full_width(
    browser: tuple[Browser, str], plot_count: int
) -> None:
    page = _page(browser, avatar=True)
    reference = _full_width(browser, plot_count)
    _wait_rows(page, plot_count)
    page.locator("#rarity-filter").select_option("common")
    _wait_filtered_rows(page, plot_count)

    page.route(
        "**/api/capture/refresh**",
        lambda route: route.fulfill(status=200, content_type="application/json", body='{"started":true}'),
    )
    page.route(
        "**/api/capture/status**",
        lambda route: route.fulfill(status=200, content_type="application/json", body='{"state":"done"}'),
    )
    # The board's reload control is the account avatar now ("Refresh board"
    # board.js refreshBoardFromAvatar()); the separate reload button is gone.
    page.locator("#player-avatar").click()
    _wait_filtered_rows(page, plot_count)
    _wait_stable_width(page)
    filtered_metrics = _assert_unclipped(page)
    assert filtered_metrics["rows"] < plot_count
    page.locator("#rarity-filter").select_option("")
    _wait_rows(page, plot_count)
    metrics = _assert_unclipped(page)
    assert metrics["tableWidth"] == reference
    page.close()


# Formerly "filter during background fill": the table has not filled in idle
# chunks since the windowed renderer (all rows up to VIRTUALIZE_THRESHOLD mount
# in one pass; table.js scheduleIdle() now only feeds the phone cards), so the
# mid-fill state the old test waited for never occurs -- on master either. The
# promise it guarded stays: a filter applied right after load, then cleared
# returns the table to its exact reference width with nothing clipped.
def test_filter_right_after_load_returns_full_width(
    browser: tuple[Browser, str], plot_count: int
) -> None:
    reference = _full_width(browser, plot_count)
    page = _page(browser)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=60_000)
    page.locator("#rarity-filter").select_option("common")
    _wait_filtered_rows(page, plot_count)
    _wait_stable_width(page)
    filtered_metrics = _assert_unclipped(page)
    assert filtered_metrics["rows"] < plot_count
    page.locator("#rarity-filter").select_option("")
    _wait_rows(page, plot_count)
    metrics = _assert_unclipped(page)
    assert metrics["tableWidth"] == reference
    page.close()


def test_root_font_change_moves_between_table_and_cards(
    browser: tuple[Browser, str], plot_count: int
) -> None:
    """A larger browser text size (32px root) narrows the effective width like
    browser zoom: the width classes are in em, so the board switches to the card
    view instead of cutting the table off, with the filter intact and no side
    scroll. Back at 16px the table returns at its exact reference width."""
    reference = _full_width(browser, plot_count)
    page = _page(browser)
    _wait_rows(page, plot_count)
    page.locator("#rarity-filter").select_option("legendary")
    _wait_rows(page, 4)
    page.evaluate(
        """() => {
          document.documentElement.style.fontSize = '32px';
          window.dispatchEvent(new Event('resize'));
        }"""
    )
    page.wait_for_function("document.querySelectorAll('#plot-card-list .plot-card').length === 4", timeout=30_000)
    cards = page.evaluate(
        """() => ({phone: document.documentElement.classList.contains('ceres-lw-le-1279'),
          tableShown: getComputedStyle(document.getElementById('plot-table-scroll')).display !== 'none',
          pageOver: document.documentElement.scrollWidth - document.documentElement.clientWidth})"""
    )
    assert cards["phone"] and not cards["tableShown"] and cards["pageOver"] <= 0, cards
    page.evaluate(
        """() => {
          document.documentElement.style.fontSize = '';
          window.dispatchEvent(new Event('resize'));
        }"""
    )
    _wait_rows(page, 4)
    page.locator("#rarity-filter").select_option("")
    _wait_rows(page, plot_count)
    metrics = _assert_unclipped(page)
    assert metrics["tableWidth"] == reference
    page.close()


def test_v0413_width_and_window_position_guarantees_hold(
    browser: tuple[Browser, str], plot_count: int
) -> None:
    page = _page(browser)
    _wait_rows(page, plot_count)
    initial = _assert_unclipped(page)
    page.evaluate("window.scrollTo(0, 500)")
    preserved = _metrics(page)
    page.evaluate(
        """() => {
          const input = document.querySelector('#text-filter');
          input.value = '1';
          input.dispatchEvent(new Event('input', {bubbles: true}));
        }"""
    )
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=60_000
    )
    _wait_stable_width(page)
    after_filter = _assert_unclipped(page)
    assert after_filter["tableWidth"] == initial["tableWidth"]
    # The filter keeps the window where it was -- unless the filtered page is
    # too short to scroll that far (the 40-plot fixture board is; the account
    # this was written against was not), where the browser clamps it to the end.
    assert preserved["windowTop"] == 500 and after_filter["maxWindowTop"] > 0, (preserved, after_filter)
    assert after_filter["windowTop"] == min(preserved["windowTop"], after_filter["maxWindowTop"])
    page.evaluate(
        """() => {
          const input = document.querySelector('#text-filter');
          input.value = '';
          input.dispatchEvent(new Event('input', {bubbles: true}));
        }"""
    )
    _wait_rows(page, plot_count)
    after_clear = _assert_unclipped(page)
    assert after_clear["tableWidth"] == initial["tableWidth"]
    assert after_clear["windowTop"] == after_filter["windowTop"]
    page.close()


# Formerly the v0.5.24 "1px scroll race" (a clamped scrollLeft during a
# warm-cache refill). removed the horizontal scroll and the column cache
# and the windowed renderer re-mounts every row in one pass, so the race cannot
# exist. The same trigger -- a header sort click that re-renders the whole
# table -- must keep the table at its board width with no clipped cell, in the
# very next frame and after it settles, and actually re-order the rows.
def test_sort_rerender_keeps_the_table_whole(
    browser: tuple[Browser, str], plot_count: int
) -> None:
    page = _page(browser)
    _wait_rows(page, plot_count)
    reference = _assert_unclipped(page)
    first_before = page.evaluate("document.querySelector('#plot-tbody > tr.plot-row').dataset.deedUid")
    page.evaluate("() => document.querySelector('#plot-table thead th[data-key=plot]').click()")
    page.evaluate("() => new Promise(requestAnimationFrame)")
    next_frame = _assert_unclipped(page)
    assert next_frame["tableWidth"] == reference["tableWidth"], (reference, next_frame)
    _wait_rows(page, plot_count)
    final = _assert_unclipped(page)
    assert final["tableWidth"] == reference["tableWidth"], (reference, final)
    first_after = page.evaluate("document.querySelector('#plot-tbody > tr.plot-row').dataset.deedUid")
    assert first_after != first_before, "the plot-header sort did not re-order the rows"
    page.close()
