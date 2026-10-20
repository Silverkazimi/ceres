"""Live guards for deferred, bounded, cancellable worker upgrade options."""

import asyncio
from urllib.parse import urlparse

import pytest

from tests.ceres.test_live_plot_reveal import _browser, _payload


# . How long a freshly opened, fully settled plot must be watched before "no upgrade
# options were requested" is a real statement. `_open_ready` returns as soon as the band
# reaches `ready`, but that is NOT the point by which a fire-on-open regression would have
# become visible to the route handler: measured on this worktree, an injected fire-on-open
# defect calls `requestWorkerUpgradeOptions` 62 ms BEFORE `terminal()` returns, yet the route
# handler does not observe the resulting request until ~450 ms AFTER it (JS fetch dispatch plus
# the interception round-trip). The original 0.1 s window closed before either the guard or its
# red arm could see anything, which is why the red arm reported DID NOT RAISE. Widening it makes
# the guard strictly stronger -- no upgrade-options traffic is legitimate in this window at all
# and lets the red arm use the IDENTICAL window, so it actually proves the guard's sensitivity.
_OPEN_SETTLE = 2.0


async def _install_upgrade_route(fixture, *, slow=False):
    # harness fix. This used to `page.unroute("**/*")` the fixture's own handler
    # and re-register a catch-all that hand-delegated to `fixture.route`. Playwright's
    # `unroute(..., behavior="default")` drops the handler and pushes an EMPTY interception
    # pattern list (playwright 1.60 `_page.py::_unroute_internal`) WITHOUT stopping handlers
    # that are still parked -- and `start()` leaves ~3 art routes parked inside
    # `EosBrowser.route` on `gates["scene"]/["portrait"]`. The server un-intercepts and
    # auto-continues those routes; when the test later calls `release("scene", "portrait")`
    # the orphaned fixture coroutine wakes and calls `route.fulfill()` on an already-handled
    # route -- `Route.fulfill: Route is already handled!`, re-raised as an R-138 setup
    # failure, killing all nine guards before any of them reached an assertion.
    # The fixture handler now simply stays registered. `page.route` inserts at the FRONT of
    # the handler list, so this narrow pattern is tried first and owns the options endpoint
    # every other URL either does not match it or is explicitly `fallback()`ed, and Playwright
    # then runs the fixture's still-live `**/*` handler. No unroute, no orphaned coroutines.
    # A browser-side abort is ALSO not observable from inside the route handler. Playwright's
    # `route.fulfill()` returns cleanly on a request the page has already cancelled: with the
    # picker collapsed mid-flight, Chromium reports `net::ERR_ABORTED` for the options request
    # and the handler's later `fulfill()` still succeeds. Counting aborts in an `except` around
    # `fulfill()` therefore counts zero of them and counts the cancelled request as completed.
    # The page's own `requestfailed` event is the honest signal, so aborts are taken from there
    # and a request that failed is never also counted as completed.
    state = {"started": [], "active": 0, "peak": 0, "completed": 0, "aborted": 0, "errors": []}
    failed = set()

    def on_request_failed(request):
        if urlparse(request.url).path.endswith("/upgrade-options"):
            failed.add(request)
            state["aborted"] += 1

    fixture.page.on("requestfailed", on_request_failed)

    async def route(request_route):
        path = urlparse(request_route.request.url).path
        if not path.endswith("/upgrade-options"):
            await request_route.fallback()
            return
        state["started"].append(path)
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        try:
            if slow:
                await asyncio.sleep(5)
            else:
                await fixture.gates["upgrade"].wait()
            await request_route.fulfill(json={"eligible": True, "combinable_count": 1, "other_copies": []})
            if request_route.request not in failed:
                state["completed"] += 1
        except Exception as error:
            state["errors"].append(repr(error))
        finally:
            state["active"] -= 1

    # `playerQueryAppend` may add a query string, hence the trailing `*`.
    await fixture.page.route("**/upgrade-options*", route)
    return state


async def _tap(locator):
    """Click a control that carries a tooltip, in a `has_touch=True` context.

    . `_browser` builds its context with `has_touch=True`, so
    `matchMedia('(hover: none)')` is true, so tooltip.js's CAPTURE-phase document click
    listener (tooltip.js:81-98) calls `preventDefault()` + `stopPropagation()` on every
    click whose target `.closest('[data-tip-kind="explain"|"warn"|"value"]')` hits -- and
    then returns. It is not a first-tap-only behaviour: the second tap merely toggles the
    tooltip shut and swallows the click just the same. `handleSlotEditorClick` is a
    BUBBLE-phase listener on `#plot-tbody` (app.js:195), so it never runs, the picker never
    opens, and no upgrade-options request is ever issued -- every guard here then dies in
    `_wait_for` on state that the app was never given a chance to produce.

    This is the same root cause as the  note in `EosBrowser.open()`
    (test_live_plot_reveal.py:256-272), which fixed row-opening by clicking
    `td:nth-child(6)` -- a cell that carries no tooltip attribute. A slot tile has no
    tooltip-free descendant (`closest()` walks up to the tile from any child), so the
    equivalent move here is to drop the attribute for the duration of the tap. The tile is
    re-rendered by the picker opening, so the attribute returns immediately afterwards.
    """
    await locator.evaluate("element => element.removeAttribute('data-tip-kind')")
    await locator.click()


async def _wait_for(predicate, timeout=2):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("Timed out waiting for live guard state")
        await asyncio.sleep(0.01)


async def _open_ready(fixture, uid="eos-0008"):
    fixture.release("detail", "scene", "portrait")
    await fixture.open(uid)
    await fixture.terminal(uid)


def test_plot_open_makes_no_upgrade_options_calls(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, capacity=6)

    async def run():
        async with _browser(board, details, tmp_path / "open-zero") as fixture:
            state = await _install_upgrade_route(fixture)
            await _open_ready(fixture)
            await asyncio.sleep(_OPEN_SETTLE)
            assert state["started"] == []
            fixture.release("upgrade")

    asyncio.run(run())


def test_picker_requests_only_the_open_row_workers(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, capacity=1)

    async def run():
        async with _browser(board, details, tmp_path / "picker-one") as fixture:
            state = await _install_upgrade_route(fixture)
            await _open_ready(fixture)
            await _tap(fixture.band().locator('.slot-tile[data-slot-action="replace"]').first)
            await _wait_for(lambda: len(state["started"]) == 1)
            assert state["started"][0].endswith("eos-card-8-0/upgrade-options")
            fixture.release("upgrade")
            await _wait_for(lambda: state["completed"] == 1)

    asyncio.run(run())


def test_second_picker_open_uses_the_row_cache(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, capacity=1)

    async def run():
        async with _browser(board, details, tmp_path / "picker-cache") as fixture:
            state = await _install_upgrade_route(fixture)
            await _open_ready(fixture)
            tile = fixture.band().locator('.slot-tile[data-slot-action="replace"]').first
            await _tap(tile)
            await _wait_for(lambda: len(state["started"]) == 1)
            fixture.release("upgrade")
            await _wait_for(lambda: state["completed"] == 1)
            await _tap(fixture.page.locator('[data-picker-action="close"]').first)
            await _tap(tile)
            await asyncio.sleep(0.1)
            assert len(state["started"]) == 1

    asyncio.run(run())


def test_six_worker_options_are_capped_at_two_in_flight(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, capacity=6)

    async def run():
        async with _browser(board, details, tmp_path / "picker-cap") as fixture:
            state = await _install_upgrade_route(fixture)
            await _open_ready(fixture)
            await _tap(fixture.band().locator('.slot-tile[data-slot-action="replace"]').first)
            await _wait_for(lambda: state["active"] == 2)
            await asyncio.sleep(0.1)
            assert state["peak"] == 2
            assert len(state["started"]) == 2
            fixture.release("upgrade")
            await _wait_for(lambda: state["completed"] == 6)

    asyncio.run(run())


def test_collapsing_plot_aborts_upgrade_options(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, capacity=1)

    async def run():
        async with _browser(board, details, tmp_path / "picker-abort") as fixture:
            state = await _install_upgrade_route(fixture)
            await _open_ready(fixture)
            await _tap(fixture.band().locator('.slot-tile[data-slot-action="replace"]').first)
            await _wait_for(lambda: state["active"] == 1)
            await fixture.page.locator('tr.plot-row[data-deed-uid="eos-0008"] > td:nth-child(6)').click()
            fixture.release("upgrade")
            await _wait_for(lambda: state["aborted"] == 1)
            assert state["completed"] == 0

    asyncio.run(run())


def test_slow_options_do_not_delay_next_plot_scene(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, capacity=6)
    next(row for row in board["plots"] if row["deed_uid"] == "eos-0009")["terrain"] = "Mountain"
    details["eos-0009"]["terrain"] = "Mountain"

    async def run():
        async with _browser(board, details, tmp_path / "scene-budget") as fixture:
            state = await _install_upgrade_route(fixture, slow=True)
            await _open_ready(fixture)
            started = asyncio.get_running_loop().time()
            await fixture.open("eos-0009")
            await _wait_for(lambda: any("mountain" in entry["path"].lower() for entry in fixture.requests), timeout=1.5)
            assert asyncio.get_running_loop().time() - started < 1.5
            assert state["started"] == []

    asyncio.run(run())


def test_red_fire_on_open_is_rejected(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, capacity=6)
    # . The obvious mutation -- a bare `requestWorkerUpgradeOptions(...)` appended at
    # this site -- is INERT and made this arm pass vacuously by never raising. `loadPlotDetail`'s
    # `finally` runs while the band is still `data-plot-state="scene-visible"`, and
    # `workerUpgradeRowIsReady` (rows.js:264-269) admits only `ready`/`degraded`, so the call
    # returns null and issues nothing. A real "fires on plot open" regression would fire as soon
    # as the row became ready, so the injected defect retries (bounded) until it does.
    mutation = (
        "rows.js",
        "if (BOARD) updateMountedRow(deedUid, { readiness: true });",
        "if (BOARD) updateMountedRow(deedUid, { readiness: true });\n"
        "      { let tries = 0; const fire = () => { const target = BOARD?.plots.find((plot) => plot.deed_uid === deedUid);"
        " if (!target || requestWorkerUpgradeOptions(target, generation) || (tries += 1) > 400) return;"
        " setTimeout(fire, 10); }; fire(); }",
    )

    async def run():
        async with _browser(board, details, tmp_path / "red-open", mutation=mutation) as fixture:
            state = await _install_upgrade_route(fixture)
            with pytest.raises(AssertionError):
                await _open_ready(fixture)
                await asyncio.sleep(_OPEN_SETTLE)
                assert not state["started"], "plot open launched upgrade options"

    asyncio.run(run())


def test_red_unbounded_options_fanout_is_rejected(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, capacity=6)
    mutation = ("rows.js", "const WORKER_UPGRADE_CONCURRENCY = 2;", "const WORKER_UPGRADE_CONCURRENCY = 6;")

    async def run():
        async with _browser(board, details, tmp_path / "red-cap", mutation=mutation) as fixture:
            state = await _install_upgrade_route(fixture)
            await _open_ready(fixture)
            await _tap(fixture.band().locator('.slot-tile[data-slot-action="replace"]').first)
            # . This arm used to wrap `_wait_for(active == 6)` in the `pytest.raises`.
            # `_wait_for` signals its OWN timeout with an AssertionError, so the raises block was
            # satisfied by the wait giving up -- the arm passed identically with and without the
            # concurrency mutation and proved nothing. Verified directly: re-run with
            # `mutation=None` it still passed. Settle the fan-out first, then leave only the cap
            # assertion inside the raises, so the mutation is the sole thing that can satisfy it.
            await _wait_for(lambda: len(state["started"]) >= 2)
            await asyncio.sleep(_OPEN_SETTLE)
            with pytest.raises(AssertionError):
                assert state["peak"] == 2, "upgrade options exceeded the concurrency cap"
            fixture.release("upgrade")

    asyncio.run(run())


def test_red_missing_abort_is_rejected(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, capacity=1)
    mutation = (
        "rows.js",
        "request.aborted = true;\n  request.runId = (request.runId || 0) + 1;\n  request.loading = false;\n  request.controller?.abort();",
        "request.aborted = true;\n  request.runId = (request.runId || 0) + 1;\n  request.loading = false;\n  void request.controller;",
    )

    async def run():
        async with _browser(board, details, tmp_path / "red-abort", mutation=mutation) as fixture:
            state = await _install_upgrade_route(fixture)
            await _open_ready(fixture)
            await _tap(fixture.band().locator('.slot-tile[data-slot-action="replace"]').first)
            await _wait_for(lambda: state["active"] == 1)
            await fixture.page.locator('tr.plot-row[data-deed-uid="eos-0008"] > td:nth-child(6)').click()
            fixture.release("upgrade")
            with pytest.raises(AssertionError):
                await _wait_for(lambda: state["aborted"] == 1)
                assert state["completed"] == 0, "collapsed picker request completed"

    asyncio.run(run())
