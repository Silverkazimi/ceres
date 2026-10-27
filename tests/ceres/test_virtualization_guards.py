"""Mutation guards for the virtualized plot table on offline synthetic boards.

The large-board guards repeat the shared fixture's plots under distinct deed UIDs to
at least 6,303 rows, matching the scale of the private player0017 account.
"""

from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, Route
from .conftest import spawn_live_ceres
from .vm_context import read_entry_source


ROOT = Path(__file__).parents[2]
player0017_DEEDS = 6_303


@pytest.fixture(scope="module")
def player0017_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _player0017_repeats(base_url: str) -> int:
    return -(-player0017_DEEDS // _fixture_plot_count(base_url))


def _page(
    browser: Browser,
    base_url: str,
    mutation: tuple[str, str] | None = None,
    *,
    wait_timeout: int = 60_000,
    injection_seen: dict | None = None,
) -> Page:
    return _synthetic_page(
        browser, base_url, mutation, wait_timeout=wait_timeout,
        injection_seen=injection_seen, repeats=_player0017_repeats(base_url),
    )


def _in_window_mutations(page: Page) -> dict:
    return page.evaluate(
        """
        async () => {
          window.scrollTo(0, 0);
          await new Promise(resolve => requestAnimationFrame(resolve));
          const tbody = document.querySelector('#plot-tbody');
          const before = [...tbody.querySelectorAll('tr.plot-row')].map(row => row.dataset.deedUid);
          let mutations = 0;
          const observer = new MutationObserver(records => { mutations += records.length; });
          observer.observe(tbody, {subtree: true, childList: true, attributes: true, characterData: true});
          window.scrollTo(0, 1);
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          await new Promise(resolve => setTimeout(resolve, 50));
          observer.disconnect();
          const after = [...tbody.querySelectorAll('tr.plot-row')].map(row => row.dataset.deedUid);
          return {mutations, before, after, scrollY: window.scrollY};
        }
        """
    )


def test_small_in_window_scroll_has_zero_plot_tbody_mutations(player0017_browser: tuple[Browser, str]) -> None:
    """Injection: replace the unchanged-window return with tbody.innerHTML = tbody.innerHTML; this went RED, then the unmodified source went GREEN.
    G1 (campaign plan revision 3, §2.3): anchor asserted present and unique before
    mutating; injection_seen asserted after, so a routing miss (e.g. the anchor
    moving to a post-split module this glob no longer covers) fails loudly instead
    of silently serving the real, unmutated file."""
    browser, base_url = player0017_browser
    source = read_entry_source()
    anchor = "if (!force && start === MOUNTED_START && end === MOUNTED_END) return false;"
    assert source.count(anchor) == 1, "renderVirtualWindow()'s unchanged-window guard moved or is no longer unique"
    page = _page(browser, base_url)
    green = _in_window_mutations(page)
    page.close()
    assert green["mutations"] == 0, green
    assert green["before"] == green["after"], green

    replacement = "if (!force && start === MOUNTED_START && end === MOUNTED_END) { tbody.innerHTML = tbody.innerHTML; return false; }"
    injected = source.replace(anchor, replacement, 1)
    assert injected != source
    injection_seen: dict = {}
    page = _page(browser, base_url, (anchor, replacement), wait_timeout=180_000, injection_seen=injection_seen)
    red = _in_window_mutations(page)
    page.close()
    assert injection_seen.get("hit"), "the injected module response was not intercepted"
    assert red["mutations"] > 0, red

    page = _page(browser, base_url)
    reverted = _in_window_mutations(page)
    page.close()
    assert reverted["mutations"] == 0, reverted
    assert reverted["before"] == reverted["after"], reverted


def test_player0017_mounted_window_is_bounded(player0017_browser: tuple[Browser, str]) -> None:
    """Injection: replace tableWindow()'s range with [0, CURRENT_ROWS.length]; this went RED, then the unmodified source went GREEN.
    G1 (campaign plan revision 3, §2.3): anchor asserted present and unique before
    mutating; injection_seen asserted after."""
    browser, base_url = player0017_browser
    source = read_entry_source()
    anchor = "if (!force && start === MOUNTED_START && end === MOUNTED_END) return false;"
    assert source.count(anchor) == 1, "renderVirtualWindow()'s unchanged-window guard moved or is no longer unique"
    page = _page(browser, base_url)
    green_count = page.locator("#plot-tbody > tr.plot-row").count()
    page.close()
    assert green_count < 300, green_count

    replacement = "if (!force && start === MOUNTED_START && end === MOUNTED_END) { for (let index = 0; index < CURRENT_ROWS.length; index++) mountRowAt(tbody, index, BACK_SPACER); return false; }"
    injected = source.replace(anchor, replacement, 1)
    assert injected != source
    injection_seen: dict = {}
    page = _page(browser, base_url, (anchor, replacement), injection_seen=injection_seen)
    page.evaluate("window.scrollTo(0, 1)")
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length >= 300",
        timeout=180_000,
    )
    red_count = page.locator("#plot-tbody > tr.plot-row").count()
    page.close()
    assert injection_seen.get("hit"), "the injected module response was not intercepted"
    assert red_count >= 300, red_count

    page = _page(browser, base_url)
    reverted_count = page.locator("#plot-tbody > tr.plot-row").count()
    page.close()
    assert reverted_count < 300, reverted_count


def _rewindow_count_over_scroll(page: Page, *, step_px: int = 29, steps: int = 60) -> dict:
    # Counts how many separate re-window events (a childList mutation batch on
    # #plot-tbody -- mountRowAt()/unmountRowAt() add/remove <tr> nodes) happen while
    # scrolling in small row-sized increments -- the observable VIRTUAL_WINDOW_BLOCK
    # quantization controls. app.js is loaded as `type="module"` (index.html), so its
    # top-level `let`/`const` bindings (MOUNTED_START etc.) are module-scoped, not
    # `window` properties -- unreachable from an outside page.evaluate() call. A
    # MutationObserver on the real DOM sidesteps that: each renderVirtualWindow() call
    # that actually mounts/unmounts rows runs synchronously inside one rAF callback, so
    # the observer's microtask-queued callback fires once per real re-window, not once
    # per scroll step.
    return page.evaluate(
        f"""
        async () => {{
          window.scrollTo(0, 0);
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const tbody = document.querySelector('#plot-tbody');
          let rewindows = 0;
          const observer = new MutationObserver(() => {{ rewindows++; }});
          observer.observe(tbody, {{childList: true}});
          for (let i = 0; i < {steps}; i++) {{
            window.scrollBy(0, {step_px});
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          }}
          observer.disconnect();
          return {{rewindows, scrollY: window.scrollY}};
        }}
        """
    )


def test_player0017_row_window_is_quantized_not_per_frame(player0017_browser: tuple[Browser, str]) -> None:
    """Injection: set VIRTUAL_WINDOW_BLOCK to 1 (the pre-fix behaviour, re-window on
    nearly every scroll frame); this went RED (many more re-windows over the same
    60-row scroll), then the unmodified source (VIRTUAL_WINDOW_BLOCK = 50) went GREEN.
    G1 (campaign plan revision 3, §2.3): anchor asserted present and unique before
    mutating (upgraded from a bare membership check); injection_seen asserted after."""
    browser, base_url = player0017_browser
    source = read_entry_source()
    anchor = "const VIRTUAL_WINDOW_BLOCK = 50;"
    assert source.count(anchor) == 1, "VIRTUAL_WINDOW_BLOCK declaration moved or is no longer unique"

    page = _page(browser, base_url)
    green = _rewindow_count_over_scroll(page)
    page.close()
    # 60 rows scrolled at block=50 should re-window at most a couple of times
    # (crossing a block boundary), never once per row.
    assert green["rewindows"] <= 5, green

    replacement = "const VIRTUAL_WINDOW_BLOCK = 1;"
    injected = source.replace(anchor, replacement, 1)
    assert injected != source
    injection_seen: dict = {}
    page = _page(browser, base_url, (anchor, replacement), injection_seen=injection_seen)
    red = _rewindow_count_over_scroll(page)
    page.close()
    assert injection_seen.get("hit"), "the injected module response was not intercepted"
    # At block=1 the window recomputes on essentially every step -- far more
    # re-windows over the same 60-row scroll than the quantized default.
    assert red["rewindows"] > green["rewindows"] * 3, (red, green)

    page = _page(browser, base_url)
    reverted = _rewindow_count_over_scroll(page)
    page.close()
    assert reverted["rewindows"] <= 5, reverted


# Recording-backed virtualization guards imported from the public suite, at the smaller
# BOARD_REPEATS scale (three times the windowing threshold); the guards above run the same
# checks at player0017's scale.
STATIC_DIR = ROOT / "ceres" / "static"
BOARD_REPEATS = 30


@pytest.fixture(scope="module")
def synthetic_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _large_board(route: Route, repeats: int = BOARD_REPEATS) -> None:
    response = route.fetch()
    board = response.json()
    plots = board.get("plots") or []
    rows = []
    for repeat in range(repeats):
        for row in plots:
            clone = dict(row)
            if repeat:
                clone["deed_uid"] = f"{row['deed_uid']}-r{repeat}"
            rows.append(clone)
    board["plots"] = rows
    route.fulfill(response=response, body=json.dumps(board), content_type="application/json")


_PLOT_COUNT: dict[str, int] = {}


def _fixture_plot_count(base_url: str) -> int:
    if base_url not in _PLOT_COUNT:
        with urllib.request.urlopen(f"{base_url}/api/board?player=sample01", timeout=30) as response:
            plots = json.loads(response.read().decode("utf-8")).get("plots") or []
        assert plots, "the fixture board has no plots"
        _PLOT_COUNT[base_url] = len(plots)
    return _PLOT_COUNT[base_url]


def _large_count(base_url: str, repeats: int = BOARD_REPEATS) -> int:
    return _fixture_plot_count(base_url) * repeats


def _serve_mutation(route: Route, anchor: str, replacement: str, injection_seen: dict | None) -> None:
    request_path = route.request.url.split("/static/", 1)[-1].split("?", 1)[0]
    file_path = STATIC_DIR / request_path
    source = file_path.read_text(encoding="utf-8") if file_path.is_file() else ""
    if source.count(anchor) != 1:
        route.continue_()
        return
    if injection_seen is not None:
        injection_seen["hit"] = True
    route.fulfill(status=200, content_type="application/javascript", body=source.replace(anchor, replacement, 1))


def _synthetic_page(
    browser: Browser,
    base_url: str,
    mutation: tuple[str, str] | None = None,
    *,
    wait_timeout: int = 60_000,
    injection_seen: dict | None = None,
    repeats: int = BOARD_REPEATS,
) -> Page:
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.route(
        lambda url: urlsplit(url).path == "/api/board" and "plots=" not in urlsplit(url).query,
        lambda route: _large_board(route, repeats),
    )
    if mutation is not None:
        # G2 (campaign plan revision 3, §2.3): **/static/*.js* so interception
        # follows the code rather than a filename -- the module whose source holds
        # the anchor (exactly once) is served mutated, every other one untouched.
        anchor, replacement = mutation
        page.route("**/static/*.js*", lambda route: _serve_mutation(route, anchor, replacement, injection_seen))
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function(
        f"document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0"
        f" && document.querySelector('#table-head-sticky .count-total')?.textContent === '{_large_count(base_url, repeats)}'",
        timeout=wait_timeout,
    )
    return page


def _synthetic_in_window_mutations(page: Page) -> dict:
    return page.evaluate(
        """
        async () => {
          window.scrollTo(0, 0);
          await new Promise(resolve => requestAnimationFrame(resolve));
          const tbody = document.querySelector('#plot-tbody');
          const before = [...tbody.querySelectorAll('tr.plot-row')].map(row => row.dataset.deedUid);
          let mutations = 0;
          const observer = new MutationObserver(records => { mutations += records.length; });
          observer.observe(tbody, {subtree: true, childList: true, attributes: true, characterData: true});
          window.scrollTo(0, 1);
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          await new Promise(resolve => setTimeout(resolve, 50));
          observer.disconnect();
          const after = [...tbody.querySelectorAll('tr.plot-row')].map(row => row.dataset.deedUid);
          return {mutations, before, after, scrollY: window.scrollY};
        }
        """
    )


def test_synthetic_small_in_window_scroll_has_zero_plot_tbody_mutations(synthetic_browser: tuple[Browser, str]) -> None:
    """Injection: replace the unchanged-window return with tbody.innerHTML = tbody.innerHTML; this went RED, then the unmodified source went GREEN.
    G1 (campaign plan revision 3, §2.3): anchor asserted present and unique before
    mutating; injection_seen asserted after, so a routing miss (e.g. the anchor
    moving to a post-split module this glob no longer covers) fails loudly instead
    of silently serving the real, unmutated file."""
    browser, base_url = synthetic_browser
    source = read_entry_source()
    anchor = "if (!force && start === MOUNTED_START && end === MOUNTED_END) return false;"
    assert source.count(anchor) == 1, "renderVirtualWindow()'s unchanged-window guard moved or is no longer unique"
    page = _synthetic_page(browser, base_url)
    green = _synthetic_in_window_mutations(page)
    page.close()
    assert green["mutations"] == 0, green
    assert green["before"] == green["after"], green

    replacement = "if (!force && start === MOUNTED_START && end === MOUNTED_END) { tbody.innerHTML = tbody.innerHTML; return false; }"
    injected = source.replace(anchor, replacement, 1)
    assert injected != source
    injection_seen: dict = {}
    page = _synthetic_page(browser, base_url, (anchor, replacement), wait_timeout=180_000, injection_seen=injection_seen)
    red = _synthetic_in_window_mutations(page)
    page.close()
    assert injection_seen.get("hit"), "the injected module response was not intercepted"
    assert red["mutations"] > 0, red

    page = _synthetic_page(browser, base_url)
    reverted = _synthetic_in_window_mutations(page)
    page.close()
    assert reverted["mutations"] == 0, reverted
    assert reverted["before"] == reverted["after"], reverted


def test_synthetic_mounted_window_is_bounded(synthetic_browser: tuple[Browser, str]) -> None:
    """Injection: replace tableWindow()'s range with [0, CURRENT_ROWS.length]; this went RED, then the unmodified source went GREEN.
    G1 (campaign plan revision 3, §2.3): anchor asserted present and unique before
    mutating; injection_seen asserted after."""
    browser, base_url = synthetic_browser
    source = read_entry_source()
    anchor = "if (!force && start === MOUNTED_START && end === MOUNTED_END) return false;"
    assert source.count(anchor) == 1, "renderVirtualWindow()'s unchanged-window guard moved or is no longer unique"
    page = _synthetic_page(browser, base_url)
    green_count = page.locator("#plot-tbody > tr.plot-row").count()
    page.close()
    assert green_count < 300, green_count

    replacement = "if (!force && start === MOUNTED_START && end === MOUNTED_END) { for (let index = 0; index < CURRENT_ROWS.length; index++) mountRowAt(tbody, index, BACK_SPACER); return false; }"
    injected = source.replace(anchor, replacement, 1)
    assert injected != source
    injection_seen: dict = {}
    page = _synthetic_page(browser, base_url, (anchor, replacement), injection_seen=injection_seen)
    page.evaluate("window.scrollTo(0, 1)")
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length >= 300",
        timeout=180_000,
    )
    red_count = page.locator("#plot-tbody > tr.plot-row").count()
    page.close()
    assert injection_seen.get("hit"), "the injected module response was not intercepted"
    assert red_count >= 300, red_count

    page = _synthetic_page(browser, base_url)
    reverted_count = page.locator("#plot-tbody > tr.plot-row").count()
    page.close()
    assert reverted_count < 300, reverted_count


def _synthetic_rewindow_count_over_scroll(page: Page, *, step_px: int = 29, steps: int = 60) -> dict:
    # Counts how many separate re-window events (a childList mutation batch on
    # #plot-tbody -- mountRowAt()/unmountRowAt() add/remove <tr> nodes) happen while
    # scrolling in small row-sized increments -- the observable VIRTUAL_WINDOW_BLOCK
    # quantization controls. app.js is loaded as `type="module"` (index.html), so its
    # top-level `let`/`const` bindings (MOUNTED_START etc.) are module-scoped, not
    # `window` properties -- unreachable from an outside page.evaluate() call. A
    # MutationObserver on the real DOM sidesteps that: each renderVirtualWindow() call
    # that actually mounts/unmounts rows runs synchronously inside one rAF callback, so
    # the observer's microtask-queued callback fires once per real re-window, not once
    # per scroll step.
    return page.evaluate(
        f"""
        async () => {{
          window.scrollTo(0, 0);
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const tbody = document.querySelector('#plot-tbody');
          let rewindows = 0;
          const observer = new MutationObserver(() => {{ rewindows++; }});
          observer.observe(tbody, {{childList: true}});
          for (let i = 0; i < {steps}; i++) {{
            window.scrollBy(0, {step_px});
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          }}
          observer.disconnect();
          return {{rewindows, scrollY: window.scrollY}};
        }}
        """
    )


def test_synthetic_row_window_is_quantized_not_per_frame(synthetic_browser: tuple[Browser, str]) -> None:
    """Injection: set VIRTUAL_WINDOW_BLOCK to 1 (the pre-fix behaviour, re-window on
    nearly every scroll frame); this went RED (many more re-windows over the same
    60-row scroll), then the unmodified source (VIRTUAL_WINDOW_BLOCK = 50) went GREEN.
    G1 (campaign plan revision 3, §2.3): anchor asserted present and unique before
    mutating (upgraded from a bare membership check); injection_seen asserted after."""
    browser, base_url = synthetic_browser
    source = read_entry_source()
    anchor = "const VIRTUAL_WINDOW_BLOCK = 50;"
    assert source.count(anchor) == 1, "VIRTUAL_WINDOW_BLOCK declaration moved or is no longer unique"

    page = _synthetic_page(browser, base_url)
    green = _synthetic_rewindow_count_over_scroll(page)
    page.close()
    # 60 rows scrolled at block=50 should re-window at most a couple of times
    # (crossing a block boundary), never once per row.
    assert green["rewindows"] <= 5, green

    replacement = "const VIRTUAL_WINDOW_BLOCK = 1;"
    injected = source.replace(anchor, replacement, 1)
    assert injected != source
    injection_seen: dict = {}
    page = _synthetic_page(browser, base_url, (anchor, replacement), injection_seen=injection_seen)
    red = _synthetic_rewindow_count_over_scroll(page)
    page.close()
    assert injection_seen.get("hit"), "the injected module response was not intercepted"
    # At block=1 the window recomputes on essentially every step -- far more
    # re-windows over the same 60-row scroll than the quantized default.
    assert red["rewindows"] > green["rewindows"] * 3, (red, green)

    page = _synthetic_page(browser, base_url)
    reverted = _synthetic_rewindow_count_over_scroll(page)
    page.close()
    assert reverted["rewindows"] <= 5, reverted
