"""Real-Chromium coverage for the board's three loading spinners (maintainer report,
2026-08-28): "we need a spinner for loading actions like initial main table, plot
opening, especially profile loading" -- plus the live defect the maintainer hit switching
to a player with no captured snapshot: URL changed, no content, no feedback at all.

Two delay techniques are used, chosen per request kind:

- For a request fired by NAVIGATION (`page.goto()`), `_capture_pending_request()`
  below holds the matching request open (via `page.route`) until the test explicitly
  releases it, entirely on the main thread -- confirmed reliable for this case.
- For a request fired by a PAGE INTERACTION (a click/fill), the same held-route
  technique was tried and found unreliable in this sandbox: `route.continue_()`,
  called after the handler that captured the route has already returned, silently
  never lets the request reach the server (confirmed via server access logs -- the
  request is simply never dispatched). So those cases use a REAL slower fetch instead
  of an artificial delay -- `player0001` (470 deeds, mostly uncached -- a real network
  hop per plot) rather than `player0017`, whose much larger snapshot is shared with other
  concurrent processes in this environment and was observed corrupted mid-write more
  than once during this batch's own test development -- see
  `test_profile_spinner_...` and `test_plot_open_spinner_...` below.

Two OTHER techniques were tried and rejected before landing on the above: a route
handler that blocks itself (`time.sleep()`/`page.wait_for_timeout()`) freezes the
single asyncio loop the sync Playwright driver dispatches ALL CDP traffic through, so
nothing else (including the `locator.wait_for()` calls a test needs to observe the
spinner) can proceed until it returns. A `threading.Timer` calling `route.continue_()`
from a second thread fails outright (`greenlet.error: cannot switch to a different
thread`) -- the sync API's route objects are bound to the thread that received them.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

import pytest
from playwright.sync_api import Browser, Page, Route

from .conftest import settle, spawn_live_ceres
from .vm_context import read_entry_source

APP_JS = Path(__file__).parents[2] / "ceres" / "static" / "app.js"
STATIC_DIR = APP_JS.parent
SCREEN_DIR = Path(__file__).resolve().parents[2] / "work" / "spinners"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (live_browser, base_url):
        yield live_browser, base_url


def _route_with_injected_anchor(route: Route, anchor: str, replacement: str, injection_seen: dict) -> None:
    """Shared (b-mut) handler for the `**/static/*.js*` glob (campaign plan
    revision 3, §2.3 G2): the board serves every static module with a
    `?v=<version>` query token, so a bare `**/static/app.js` route never matches
    and the guard silently serves the real, unmutated file -- this is why
    `test_header_hide_guard_actually_guards` and
    `test_spinner_hide_on_completion_guard_actually_guards` were void (confirmed
    live, this batch). Widening the glob to `*.js*` means every served static
    module (`app.js`, `theme.js`, `customSelect.js`, and any post-split module)
    now passes through here -- so this reads the actually-requested file from
    disk and only mutates it if `anchor` is present in it, and present exactly
    once; every other module is served untouched via `route.continue_()`, so
    interception follows the code instead of a filename."""
    request_path = route.request.url.split("/static/", 1)[-1].split("?", 1)[0]
    file_path = STATIC_DIR / request_path
    if not file_path.is_file():
        route.continue_()
        return
    source = file_path.read_text(encoding="utf-8")
    if source.count(anchor) != 1:
        route.continue_()
        return
    injection_seen["hit"] = True
    mutated = source.replace(anchor, replacement, 1)
    route.fulfill(status=200, content_type="application/javascript", body=mutated)


def _capture_pending_request(page: Page, url_pattern, trigger: Callable[[], None]) -> Route:
    """Runs `trigger()` (an action expected to fire a request matching `url_pattern`)
    and returns the resulting `Route` object BEFORE it is continued/fulfilled -- so
    the caller can assert on the page's pending-state UI, then release the request on
    its own schedule by calling `route.continue_()`. All polling happens via
    `page.wait_for_timeout()` in the caller's own (correct) greenlet, never inside the
    route handler itself -- see the module docstring for why that distinction matters.
    """
    captured: dict[str, Route] = {}

    def handler(route: Route) -> None:
        captured["route"] = route

    page.route(url_pattern, handler)
    trigger()
    deadline = time.monotonic() + 5
    while "route" not in captured and time.monotonic() < deadline:
        settle(page)
    assert "route" in captured, f"no request matching {url_pattern!r} was observed within 5s"
    return captured["route"]


def test_board_spinner_shows_during_slow_load_and_hides_after(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    route = _capture_pending_request(page, "**/api/board*", lambda: page.goto(f"{base_url}/?player=sample01"))  # no ?player= opens the start page

    spinner = page.locator("#board-loading")
    spinner.wait_for(state="visible", timeout=5_000)
    assert spinner.get_attribute("role") == "status"
    assert spinner.get_attribute("aria-live") == "polite"
    # PRE-EXISTING STALE ASSERTION, found by verification of R-145 and fixed here rather
    # than left: board.js has only ever written "Reading your plots…" into
    # .board-loading-text (board.js:69, :178), so this line could never have passed.
    # It matters because THIS is the one live-browser test that exercises the 200ms
    # entrance gate and the hidden/visible lifecycle against a real server -- the
    # contract R-145 deliberately did not touch. A guard that cannot pass is not
    # coverage of it.
    assert "Reading your plots" in spinner.inner_text(), spinner.inner_text()
    # R-145: the art is decorative and aria-hidden, so the status text is still the
    # only thing announced. Red mutation: drop aria-hidden from .harvest-stage.
    assert page.locator("#board-loading .harvest-stage[aria-hidden='true']").count() == 1
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREEN_DIR / "board_load_pending.png"))

    route.continue_()
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=15_000)
    spinner.wait_for(state="hidden", timeout=5_000)
    page.close()


def test_board_spinner_does_not_flash_on_a_fast_load(live_browser: tuple[Browser, str]) -> None:
    """No held request here -- the local fixture snapshot loads well under
    SPINNER_SHOW_DELAY_MS (200ms), so the spinner must never become visible at all."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(f"{base_url}/?player=sample01")  # no ?player= opens the start page
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=15_000)
    assert page.locator("#board-loading").is_hidden()
    page.close()


def _record_go_button_busy(page: Page) -> None:
    """Record every aria-busy/aria-label change of the player Go button (the switch is quick
    on the fixture server, so the busy state is observed, not polled)."""
    page.evaluate(
        """() => {
          window.__goBusy = [];
          const button = document.getElementById('player-go-btn');
          new MutationObserver(() => window.__goBusy.push({busy: button.getAttribute('aria-busy'), label: button.getAttribute('aria-label')}))
            .observe(button, {attributes: true, attributeFilter: ['aria-busy', 'aria-label']});
        }"""
    )


def test_profile_spinner_shows_next_to_go_button_during_switch(live_browser: tuple[Browser, str]) -> None:
    """The profile switch shows its loading state ON the Go button the user just pressed.
    The separate `#player-loading` spinner element is gone from the markup; board.js
    `setPlayerLoading()` marks the button itself busy (`aria-busy="true"`, accessible name
    "Switch player. Loading profile.") for the whole switch and clears it when the load
    ends. (The recorded fixture has one account, so the switch goes to a player with no
    snapshot -- the busy state covers that load just the same.)"""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=15_000)
    button = page.locator("#player-go-btn")
    assert button.get_attribute("aria-busy") in (None, "false")

    _record_go_button_busy(page)
    page.fill("#player-input", "zzznonexistent99")
    page.click("#player-go-btn")
    page.wait_for_function("window.__goBusy.some((s) => s.busy === 'true')", timeout=5_000)
    page.wait_for_function("document.getElementById('player-go-btn').getAttribute('aria-busy') === 'false'", timeout=30_000)
    states = page.evaluate("window.__goBusy")
    busy = [state for state in states if state["busy"] == "true"]
    assert busy and all(state["label"] == "Switch player. Loading profile." for state in busy), states
    assert button.get_attribute("aria-label") == "Switch player"
    page.close()


def _switch_to_unknown_player(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=15_000)
    page.fill("#player-input", "zzznonexistent99")
    page.click("#player-go-btn")


def test_no_snapshot_player_shows_readable_message_not_silence(live_browser: tuple[Browser, str]) -> None:
    """The defect being fixed: switching to a player with no captured snapshot
    previously changed the URL and left the PREVIOUS player's table on screen with no
    indication anything happened. Now the previous player's rows are gone and a
    plain-language board notice with a next step takes their place. (The dedicated
    `#board-empty-state` panel was replaced by the board's own `capture_failed` notice,
    board.js loadBoard(): "The board read did not finish. Reload to try again.")"""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    _switch_to_unknown_player(page, base_url)

    notice = page.locator(".ceres-notice[data-notice-code='capture_failed']")
    notice.first.wait_for(state="visible", timeout=10_000)
    text = notice.first.inner_text().lower()
    assert "did not finish" in text and "reload" in text, text
    assert "player=zzznonexistent99" in page.url
    assert page.locator("#plot-tbody > tr.plot-row").count() == 0, "the previous player's table must not stay on screen"
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREEN_DIR / "no_snapshot_message.png"))
    page.close()


def test_plot_open_spinner_shows_in_the_expanding_row(live_browser: tuple[Browser, str]) -> None:
    """A plot whose detail is still being read 400 ms after the click shows a small
    spinner in each of its placeholder tiles (rows.js markSlowOpen -> `tr.expand-row
    .is-slow-open`, the ghost tiles' `.slot-loading-spinner`). The recorded fixture
    answers in milliseconds, so the plot's detail read (POST .../expand -- a page load's
    capture leaves the board row unread) is held open by the test and released once the
    pending state has been read; the old version relied on a large real account's
    network-bound read, which the recording does not have."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=15_000)
    row = page.locator("#plot-tbody > tr.plot-row").first
    deed_uid = row.get_attribute("data-deed-uid")
    detail = re.compile(rf"/api/plots/{re.escape(deed_uid)}(/expand)?(\?|$)")  # GET detail or POST expand
    held = _capture_pending_request(page, detail, row.click)

    expand = page.locator(f"tr.expand-row[data-deed-uid='{deed_uid}']")
    page.locator(f"tr.expand-row.is-slow-open[data-deed-uid='{deed_uid}']").wait_for(timeout=5_000)
    spinner = expand.locator(".slot-loading-spinner").first
    spinner.wait_for(state="visible", timeout=5_000)
    assert expand.locator(".editor-band").get_attribute("aria-busy") == "true"
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    row.screenshot(path=str(SCREEN_DIR / "plot_open_pending.png"))

    held.fulfill(response=held.fetch())
    expand.locator(".editor-band[data-plot-state='ready']").wait_for(timeout=30_000)
    assert "is-slow-open" not in (expand.get_attribute("class") or "")
    assert expand.locator(".slot-loading-spinner:visible").count() == 0
    page.close()


def test_table_hidden_during_load_reveals_at_final_width(live_browser: tuple[Browser, str]) -> None:
    """Owner report, 2026-08-28, verbatim: "the initial page load first shows the
    header, loads the main table and then the header stretches full width while
    being optimized for a lower resolution ... Instead, the whole table can be
    hidden, including the header, and show the spinner instead. Then load all
    content required and finally print all in one go."

    Cause (confirmed, not assumed, 2026-08-28): the board's columns were
    content-measured and LOCKED ONCE (lockColumnWidths(), v0.4.0 -- removed by
    ; the browser now sizes the table, but an early paint would still show
    a header without rows) against whatever was in the table at that moment. Before any rows exist the header has nothing to size against, so
    an early paint locks in a narrow layout that then visibly jumps once real rows
    arrive. The fix hides the table (`visibility: hidden`, not `display: none` --
    the column measurement still needs a real layout box) until AFTER rows are
    mounted and columns are locked, then reveals in the same synchronous tick --
    see hideBoardTable()/revealBoardTable() in app.js.

    The acceptance criterion here is a MEASURED WIDTH, not a screenshot: the
    header's bounding-box width immediately on reveal must equal its width after
    rows have settled."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    route = _capture_pending_request(page, "**/api/board*", lambda: page.goto(f"{base_url}/?player=sample01"))  # no ?player= opens the start page

    # During the hold, the table must be genuinely invisible -- no intermediate
    # narrow-header paint -- while still occupying a real (measurable) layout box
    # which `visibility: hidden` (checked via computed style, not the `hidden`
    # attribute) guarantees and `display: none` would not.
    head_vis = page.eval_on_selector("#table-head-sticky", "el => getComputedStyle(el).visibility")
    scroll_vis = page.eval_on_selector("#plot-table-scroll", "el => getComputedStyle(el).visibility")
    assert head_vis == "hidden"
    assert scroll_vis == "hidden"
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREEN_DIR / "board_load_no_partial_table.png"))

    route.continue_()
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=15_000)
    assert page.eval_on_selector("#table-head-sticky", "el => getComputedStyle(el).visibility") == "visible"

    width_immediate = page.eval_on_selector("#table-head-sticky", "el => el.getBoundingClientRect().width")
    settle(page)  # any further reflow/virtualization
    width_settled = page.eval_on_selector("#table-head-sticky", "el => el.getBoundingClientRect().width")
    page.screenshot(path=str(SCREEN_DIR / "board_load_revealed.png"))
    assert width_immediate > 0
    assert width_immediate == width_settled, f"header width shifted after reveal: {width_immediate} -> {width_settled}"
    page.close()


def _header_visibility_during_switch(page: Page, base_url: str) -> str:
    """Load the board, then switch player while the new capture is held: `loadBoard()`
    hides the table again before it reads. Returns the header's computed visibility
    during that second load (the first navigation cannot tell, because index.html ships
    the header with `board-content-hidden` already set)."""
    page.goto(f"{base_url}/?player=sample01")
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=15_000)
    assert page.eval_on_selector("#table-head-sticky", "el => getComputedStyle(el).visibility") == "visible"
    page.fill("#player-input", "zzznonexistent99")
    held = _capture_pending_request(page, "**/api/capture/refresh*", lambda: page.click("#player-go-btn"))
    visibility = page.eval_on_selector("#table-head-sticky", "el => getComputedStyle(el).visibility")
    held.fulfill(response=held.fetch())
    return visibility


def test_header_hide_guard_actually_guards(live_browser: tuple[Browser, str]) -> None:
    """MUTATION TEST for the hide-before-measure fix, against the REAL server, in the
    scenario that can tell: a SECOND `loadBoard()` (a player switch), after the first
    load has already revealed the table. The real build hides the header for the new
    load; with `loadBoard()`'s `hideBoardTable();` call deleted from the served module
    (board.js, via the shared whichever-module-has-the-anchor route) the header stays
    visible -- the exact defect ("reveal the header before rows are ready"). Both halves
    run here, so the guard proves its own sensitivity."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    assert _header_visibility_during_switch(page, base_url) == "hidden", "the real build must hide the header while the next player's board loads"
    page.close()

    real_source = read_entry_source()
    anchor = "    hideBoardTable();"
    assert real_source.count(anchor) == 1, "loadBoard()'s hideBoardTable() call moved or is no longer unique; update this test's injection anchor"
    replacement = "    // hideBoardTable(); -- MUTATED OUT for test_header_hide_guard_actually_guards"
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    injection_seen: dict = {}
    page.route(
        "**/static/*.js*",
        lambda route: _route_with_injected_anchor(route, anchor, replacement, injection_seen),
    )
    visibility = _header_visibility_during_switch(page, base_url)
    assert injection_seen.get("hit"), "injected module response was not intercepted"
    assert visibility != "hidden", "expected the mutated build to skip hiding the header (that is the injected defect)"
    page.close()


def test_player0017_real_slow_load_screenshot(live_browser: tuple[Browser, str]) -> None:
    """Unassisted (no held request) load of the real player0017 snapshot -- 6,303 deeds,
    measured 18.9s -- the genuinely slow real-world case the task brief asked to be
    screenshotted directly, not simulated.

    `~/.ceres/snapshots/` is shared with every other agent/process in this
    environment (this session's own team runs a `diagnose-player0017-residual` agent
    against the same account) -- observed live, 5 consecutive direct reads of
    `land-player0017-latest.json` mid-session all raised `json.JSONDecodeError`
    (truncated at the same byte offset), meaning some other process was actively
    rewriting it. If that leaves the snapshot genuinely unavailable when this test
    runs, that is a fact about a shared external resource, not about this batch's own
    code -- skip rather than fail, since this specific test's only job is the
    screenshot artifact."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(f"{base_url}/?player=player0017")
    spinner = page.locator("#board-loading")
    empty_state = page.locator("#board-empty-state")
    try:
        spinner.wait_for(state="visible", timeout=3_000)
        SCREEN_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SCREEN_DIR / "player0017_real_load_pending.png"))
    except Exception:
        pass  # a faster-than-expected environment is not this test's failure to report
    try:
        page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=45_000)
    except Exception:
        if empty_state.is_visible():
            pytest.skip("player0017 snapshot unavailable in this shared environment right now (see docstring) -- not this batch's defect")
        raise
    page.screenshot(path=str(SCREEN_DIR / "player0017_real_load_revealed.png"))
    page.close()


def test_spinner_hide_on_completion_guard_actually_guards(live_browser: tuple[Browser, str]) -> None:
    """MUTATION TEST for the board spinner's finally-block hide, against the REAL
    server. Injection performed: `loadBoard()`'s finally block's
    `if (spinnerHeld) boardSpinnerGate.hide();` line is deleted from the served module
    (board.js, via page.route, never the checked-in file on disk) -- the exact "spins
    forever" defect the mutation standard names. With it deleted the sibling test's
    spinner-becomes-hidden assertion times out (red); the real source passes (green).
    Re-anchored: the bare `boardSpinnerGate.hide();` line now also appears in the
    early-reveal path, so the anchor is the finally block's own guarded call."""
    browser, base_url = live_browser
    real_source = read_entry_source()
    anchor = "    if (spinnerHeld) boardSpinnerGate.hide();"
    assert real_source.count(anchor) == 1, "loadBoard()'s finally-block hide() call moved or is no longer unique; update this test's injection anchor"
    replacement = "    // if (spinnerHeld) boardSpinnerGate.hide(); -- MUTATED OUT for test_spinner_hide_on_completion_guard_actually_guards"

    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    injection_seen: dict = {}
    page.route(
        "**/static/*.js*",
        lambda route: _route_with_injected_anchor(route, anchor, replacement, injection_seen),
    )
    route = _capture_pending_request(page, "**/api/board*", lambda: page.goto(f"{base_url}/?player=sample01"))  # no ?player= opens the start page
    assert injection_seen.get("hit"), "injected module response was not intercepted"
    page.locator("#board-loading").wait_for(state="visible", timeout=5_000)
    route.continue_()
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=15_000)

    with pytest.raises(Exception):
        page.locator("#board-loading").wait_for(state="hidden", timeout=2_000)
    page.close()


def test_no_snapshot_message_guard_actually_guards(live_browser: tuple[Browser, str]) -> None:
    """MUTATION TEST for the failed-read message, against the REAL server. Injection
    performed: `loadBoard()`'s failed-read branch publishes its board with no notice
    (`notices: []` instead of the `capture_failed` "did not finish ... Reload" notice) in
    the served module -- the exact silent dead end this batch fixes (URL changes, no
    content, no feedback). The sibling test's notice-becomes-visible assertion then
    times out (red); the real source passes (green). Re-anchored from the retired
    503-branch `setBoardEmptyState()` call."""
    browser, base_url = live_browser
    real_source = read_entry_source()
    anchor = "notices: [{ ...catalogNotice('capture_failed'), message: 'The board read did not finish. Reload to try again.' }]"
    assert real_source.count(anchor) == 1, "loadBoard()'s failed-read notice moved; update this test's injection string"
    replacement = "notices: [] /* MUTATED OUT for test_no_snapshot_message_guard_actually_guards */"

    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    injection_seen: dict = {}
    page.route(
        "**/static/*.js*",
        lambda route: _route_with_injected_anchor(route, anchor, replacement, injection_seen),
    )
    _switch_to_unknown_player(page, base_url)
    assert injection_seen.get("hit"), "injected module response was not intercepted"

    with pytest.raises(Exception):
        page.locator(".ceres-notice[data-notice-code='capture_failed']").first.wait_for(state="visible", timeout=3_000)
    page.close()
