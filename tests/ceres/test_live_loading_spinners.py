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


def _capture_pending_request(page: Page, url_pattern: str, trigger: Callable[[], None]) -> Route:
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


def test_profile_spinner_shows_next_to_go_button_during_switch(live_browser: tuple[Browser, str]) -> None:
    """Switches to `player0001` (470 deeds; its own board load measured ~1s, well past
    SPINNER_SHOW_DELAY_MS's 200ms) rather than the default `player0005` -- see the module
    docstring for why a click-triggered request uses a real slower fetch rather than a
    held/delayed one."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(f"{base_url}/?player=sample01")  # no ?player= opens the start page
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=15_000)

    page.fill("#player-input", "player0001")
    page.click("#player-go-btn")

    player_spinner = page.locator("#player-loading")
    player_spinner.wait_for(state="visible", timeout=5_000)
    assert player_spinner.get_attribute("role") == "status"
    assert player_spinner.get_attribute("aria-live") == "polite"
    # The board spinner is expected to show alongside it (viaPlayerSwitch shows both
    # the content area AND the surface the user just acted on).
    assert page.locator("#board-loading").is_visible()
    page.locator(".player-switch").screenshot(path=str(SCREEN_DIR / "profile_load_pending.png"))

    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=15_000)
    player_spinner.wait_for(state="hidden", timeout=5_000)
    page.close()


def test_no_snapshot_player_shows_readable_message_not_silence(live_browser: tuple[Browser, str]) -> None:
    """The defect being fixed: switching to a player with no captured snapshot
    previously changed the URL and left the PREVIOUS player's table on screen with no
    indication anything happened. Now the table is hidden and a plain-language message
    with a next step takes its place."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(f"{base_url}/?player=sample01")  # no ?player= opens the start page
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=15_000)

    page.fill("#player-input", "zzznonexistent99")
    page.click("#player-go-btn")

    empty_state = page.locator("#board-empty-state")
    empty_state.wait_for(state="visible", timeout=10_000)
    text = empty_state.inner_text().lower()
    assert "zzznonexistent99" in text
    assert "refresh" in text
    assert page.locator("#plot-table-scroll").is_hidden()
    assert page.locator("#table-head-sticky").is_hidden()
    page.screenshot(path=str(SCREEN_DIR / "no_snapshot_message.png"))
    page.close()


def test_plot_open_spinner_shows_in_the_expanding_row(live_browser: tuple[Browser, str]) -> None:
    """Expands a plot on `player0001` rather than the default (`player0005`) board -- most
    of its deeds carry `assets_loaded: false`, so expanding one is a REAL network
    fetch against the live Splinterlands API (measured ~9.6s), comfortably past
    SPINNER_SHOW_DELAY_MS (200ms). player0005's rows are all already cached
    (`assets_loaded: true`) and resolve in ~0.4s -- a much thinner, flakier margin.
    See the module docstring for why a click-triggered request uses a real slow
    fetch rather than a held/delayed one.

    Picks the deed to click via a fresh `/api/board` read taken right before
    clicking, rather than assuming the first row is uncached -- a run of this same
    suite (or of this file's manual exploration) permanently caches whichever deed
    it expands, so "the first row is always uncached" silently stopped being true
    partway through this batch's own development and one run went flaky on exactly
    that."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(f"{base_url}/?player=player0001")
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=15_000)

    board = page.evaluate("() => fetch('/api/board?player=player0001').then(r => r.json())")
    uncached_uids = {p["deed_uid"] for p in board["plots"] if not p.get("assets_loaded")}
    assert uncached_uids, "player0001 has no uncached (assets_loaded: false) plot left to exercise a real expand fetch"
    # Intersect with what's actually MOUNTED right now, not the full 470-deed list
    # player0001 exceeds VIRTUALIZE_THRESHOLD (400), so only a scrolled-into-view window
    # of rows exists in the DOM; a locator for an unmounted deed_uid would just time out.
    mounted_uids = page.eval_on_selector_all("#plot-tbody tr.plot-row", "els => els.map(el => el.dataset.deedUid)")
    target_uid = next((uid for uid in mounted_uids if uid in uncached_uids), None)
    assert target_uid is not None, "no currently-mounted row is uncached -- widen the mounted window or pick differently"
    row = page.locator(f"tr.plot-row[data-deed-uid='{target_uid}']")
    row.click()

    row_spinner = page.locator(".expand-loading")
    row_spinner.wait_for(state="visible", timeout=5_000)
    assert row_spinner.get_attribute("role") == "status"
    assert "loading worker" in row_spinner.inner_text().lower()
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    row.screenshot(path=str(SCREEN_DIR / "plot_open_pending.png"))

    row_spinner.wait_for(state="detached", timeout=30_000)
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


def test_header_hide_guard_actually_guards(live_browser: tuple[Browser, str]) -> None:
    """MUTATION TEST for the hide-before-measure fix, against the REAL server.
    Injection performed: `loadBoard()`'s `hideBoardTable();` call is deleted from
    the served `/static/app.js` -- i.e. the header is never hidden and reveals (is
    visible) immediately, before rows exist, reproducing the exact defect being
    fixed ("reveal the header before rows are ready"). Confirmed this makes the
    sibling test's during-load `visibility: hidden` assertion fail (red); restoring
    the real, unmodified source makes it pass again (green). Re-anchored (campaign
    plan revision 3, §2.3 G1/G2): the routing half of the void was
    `page.route("**/static/app.js", ...)` never matching the real request
    (`/static/app.js?v=<version>`, per `ceres/app.py`'s cache-busting token), so the
    real, unmutated file was always served underneath -- fixed by asserting the
    anchor is present and unique before mutating, routing the widened
    `**/static/*.js*` glob through the shared whichever-module-has-the-anchor
    handler, and asserting the injection was actually observed (all now true and
    verified live, this batch). STILL VOID FOR A SECOND, DEEPER REASON not named
    by the campaign plan or census: `index.html:280` ships `table-head-sticky` with
    the `board-content-hidden` class already baked into the static markup, so on a
    fresh `page.goto()` the header reads `visibility: hidden` regardless of whether
    `hideBoardTable()` ran at all -- removing that call changes nothing observable
    on first navigation. Confirmed live: with the routing fixed and the injection
    verified `hit`, this assertion still fails identically. Detecting this specific
    defect needs a scenario where `revealBoardTable()` has already run once (e.g. a
    player switch triggering a second `loadBoard()`), which is a scenario redesign,
    not a re-anchor -- left as-is and reported rather than guessed at."""
    browser, base_url = live_browser
    real_source = read_entry_source()
    anchor = "  hideBoardTable();"
    assert real_source.count(anchor) == 1, "loadBoard()'s hideBoardTable() call moved or is no longer unique; update this test's injection anchor"
    replacement = "  // hideBoardTable(); -- MUTATED OUT for test_header_hide_guard_actually_guards"

    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    injection_seen: dict = {}
    page.route(
        "**/static/*.js*",
        lambda route: _route_with_injected_anchor(route, anchor, replacement, injection_seen),
    )
    route = _capture_pending_request(page, "**/api/board*", lambda: page.goto(f"{base_url}/?player=sample01"))  # no ?player= opens the start page
    assert injection_seen.get("hit"), "injected app.js response was not intercepted"
    head_vis = page.eval_on_selector("#table-head-sticky", "el => getComputedStyle(el).visibility")
    route.continue_()
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=15_000)
    assert head_vis != "hidden", "expected the mutated build to skip hiding the header (that is the injected defect)"
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
    `boardSpinnerGate.hide();` line is deleted from the served `/static/app.js` (via
    page.route, never the checked-in file on disk) -- the exact "spins forever" defect
    the mutation standard names. Confirmed this makes the sibling test's
    spinner-becomes-hidden assertion time out (red); restoring the real, unmodified
    source makes it pass again (green). Re-anchored (campaign plan revision 3,
    §2.3 G1/G2): this guard was void for the same reason as
    `test_header_hide_guard_actually_guards` -- the bare `**/static/app.js` route
    never matched the versioned `?v=<version>` request, so the real file was
    always served underneath. Now asserts the anchor is present and unique before
    mutating, routes through the widened `**/static/*.js*` shared handler, and
    asserts the injection was actually observed."""
    browser, base_url = live_browser
    real_source = read_entry_source()
    anchor = "    boardSpinnerGate.hide();"
    assert real_source.count(anchor) == 1, "loadBoard()'s finally-block hide() call moved or is no longer unique; update this test's injection anchor"
    replacement = "    // boardSpinnerGate.hide(); -- MUTATED OUT for test_spinner_hide_on_completion_guard_actually_guards"

    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    injection_seen: dict = {}
    page.route(
        "**/static/*.js*",
        lambda route: _route_with_injected_anchor(route, anchor, replacement, injection_seen),
    )
    route = _capture_pending_request(page, "**/api/board*", lambda: page.goto(f"{base_url}/?player=sample01"))  # no ?player= opens the start page
    assert injection_seen.get("hit"), "injected app.js response was not intercepted"
    page.locator("#board-loading").wait_for(state="visible", timeout=5_000)
    route.continue_()
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=15_000)

    with pytest.raises(Exception):
        page.locator("#board-loading").wait_for(state="hidden", timeout=2_000)
    page.close()


def test_no_snapshot_message_guard_actually_guards(live_browser: tuple[Browser, str]) -> None:
    """MUTATION TEST for the 503/no-snapshot empty-state branch, against the REAL
    server. Injection performed: `loadBoard()`'s `setBoardEmptyState(...)` call inside
    the 503 branch is deleted from the served `/static/app.js` -- the exact silent
    dead end this batch fixes (URL changes, no content, no feedback). Confirmed this
    makes the sibling test's empty-state-becomes-visible assertion time out (red);
    restoring the real, unmodified source makes it pass again (green)."""
    browser, base_url = live_browser
    real_source = read_entry_source()
    real_line = '      setBoardEmptyState(`No data captured yet for "${playerName}". Click Refresh above to fetch it.`);'
    assert real_line in real_source, "loadBoard()'s 503-branch setBoardEmptyState() call moved; update this test's injection string"
    broken_source = real_source.replace(real_line, "      // setBoardEmptyState(...) -- MUTATED OUT for test_no_snapshot_message_guard_actually_guards", 1)
    assert broken_source != real_source

    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.route(
        "**/static/*.js*",
        lambda route: route.fulfill(status=200, content_type="application/javascript", body=broken_source),
    )
    page.goto(f"{base_url}/?player=sample01")  # no ?player= opens the start page
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=15_000)

    page.fill("#player-input", "zzznonexistent99")
    page.click("#player-go-btn")

    with pytest.raises(Exception):
        page.locator("#board-empty-state").wait_for(state="visible", timeout=3_000)
    page.close()
