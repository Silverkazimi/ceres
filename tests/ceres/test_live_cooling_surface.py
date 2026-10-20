"""Real-Chromium coverage for the cooling-card checkout surface (maintainer report,
2026-08-27): staging a candidate the server already marked not-ready
(`candidates.py::cooldown_state` -> `ready: false`) rendered identically to a ready
card -- no tile marker, no checkout wording, and "Prepare Transaction" offered a
doomed transaction. This module drives the actual click path against a real running
Ceres server and a real staged move, plus the mutation test for the
Prepare-transaction gate. `tests/ceres/test_cooling_surface.py` (the former
node-level unit-slice harness) was retired whole into this file
, Batch C, §15 ruling 5) -- see the block comment above the
`test_cooldown_reason_fragment_never_invents_a_verdict` test below for what moved.
"""

from __future__ import annotations

import copy
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from .conftest import settle, spawn_live_ceres
from .vm_context import read_entry_source

APP_JS = Path(__file__).parents[2] / "ceres" / "static" / "app.js"
# Evidence goes to the main checkout (not this worktree's own copy) per the task brief
# `Path(__file__).parents[2]` would resolve inside the worktree, which is discarded.
SCREEN_DIR = Path(__file__).resolve().parents[2] / "work" / "cooling-surface"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (live_browser, base_url):
        yield live_browser, base_url


def _open_worker_plot(base_url: str, *, exclude: frozenset[str] = frozenset()) -> str:
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    for row in board["plots"]:
        if row["deed_uid"] in exclude:
            continue
        if (row.get("capacity", {}) or {}).get("open_slots", 0) > 0:
            return row["deed_uid"]
    raise AssertionError("no live plot has an open worker slot to stage into")


def _plot_with_worker_occupant(base_url: str, *, exclude: frozenset[str] = frozenset()) -> str:
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    for row in board["plots"]:
        if row["deed_uid"] in exclude:
            continue
        cap = row.get("capacity", {}) or {}
        # Also requires an open slot -- a handful of fully-occupied/Runi-overcapacity
        # plots render their worker tiles in a state where the remove control is
        # present but not interactable; steering clear of that edge case here.
        if cap.get("worker_count", 0) >= 1 and cap.get("open_slots", 0) >= 1:
            return row["deed_uid"]
    raise AssertionError("no live plot has both an existing worker occupant and an open slot")


def _real_worker_candidate(base_url: str, deed_uid: str) -> dict:
    """A REAL worker candidate for `deed_uid`, straight from `/api/plots/{deed}/
    candidates` -- every field (name, base_pp, construction_pp_delta, plot_impact,
    ...) is exactly what candidates.py produced, so callers overriding only
    `cooldown`/`foil`/`name` still exercise the real renderCandidateRow() code path
    unmodified for everything else."""
    params = urllib.parse.urlencode({
        "group_id": "worker", "limit": 5, "offset": 0, "sort": "construction_pp",
        "bucket": "all", "preset": "net_gain_default",
    })
    url = f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates?{params}"
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    candidates = payload.get("candidates") or []
    assert candidates, f"plot {deed_uid} returned no worker candidates to build a fixture from"
    return copy.deepcopy(candidates[0])


def _worker_candidate_with_cooldown(base_url: str, deed_uid: str, cooldown: dict) -> dict:
    candidate = _real_worker_candidate(base_url, deed_uid)
    candidate["cooldown"] = cooldown
    return candidate


def _mock_candidates_route(page: Page, candidate: dict):
    pattern = "**/api/plots/*/candidates*"
    mock_payload = json.dumps({"candidates": [candidate], "total": 1, "source": "test-fixture", "excluded_counts": {}, "notices": []})
    page.route(pattern, lambda route: route.fulfill(status=200, content_type="application/json", body=mock_payload))
    return pattern


def _goto_board(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)


def _stage_add_into_open_slot(page: Page, deed_uid: str, candidate: dict) -> None:
    """Opens `deed_uid`'s editor, mocks its candidate list down to a single entry,
    stages that one candidate into the first empty worker slot, waits for the
    checkout panel to become reachable (proof the stage actually landed), then
    collapses the row again so only one `.slot-editor` is ever in the DOM at once."""
    pattern = _mock_candidates_route(page, candidate)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(".slot-editor:visible").wait_for(timeout=30_000)
    worker = page.locator(".slot-editor:visible .slot-group[data-group-id='worker']")
    empty = worker.locator(".slot-grid > .slot-tile-cell > .slot-tile.is-empty").first
    with page.expect_request(pattern):
        empty.click()
    row = page.locator(".candidate-row").first
    row.wait_for(timeout=30_000)
    row.click()
    page.wait_for_function("document.getElementById('checkout-btn') && !document.getElementById('checkout-btn').hidden", timeout=30_000)
    page.unroute(pattern)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()


def _stage_remove_existing_occupant(page: Page, deed_uid: str) -> None:
    """Opens `deed_uid`'s editor and clicks the remove control on its (real, already
    staked) worker occupant -- a pure `unstake`, staged without any incoming move on
    this plot, then collapses the row again."""
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(".slot-editor:visible").wait_for(timeout=30_000)
    worker = page.locator(".slot-editor:visible .slot-group[data-group-id='worker']")
    occupied_tile = worker.locator(".slot-tile:not(.is-empty):not(.is-staged)").first
    occupied_tile.wait_for(timeout=30_000)
    # The remove (x) control is CSS-hidden until its tile is hovered/focused -- a
    # real hover, not `force=True`, so this exercises the same reveal a user gets.
    occupied_tile.hover()
    remove_button = occupied_tile.locator(".slot-remove")
    remove_button.wait_for(timeout=30_000)
    remove_button.click()
    page.wait_for_function("document.getElementById('checkout-btn') && !document.getElementById('checkout-btn').hidden", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()


def _cooling_candidate(base_url: str, deed_uid: str) -> dict:
    candidate = _real_worker_candidate(base_url, deed_uid)
    ready_at = (datetime.now(timezone.utc) + timedelta(hours=40, minutes=12)).isoformat().replace("+00:00", "Z")
    candidate["cooldown"] = {"ready": False, "ready_at": ready_at, "class": "worker", "state": "cooling"}
    return candidate


def _stage_cooling_candidate(page: Page, base_url: str, deed_uid: str) -> dict:
    candidate = _cooling_candidate(base_url, deed_uid)
    mock_payload = json.dumps({"candidates": [candidate], "total": 1, "source": "test-fixture", "excluded_counts": {}, "notices": []})
    page.route("**/api/plots/*/candidates*", lambda route: route.fulfill(status=200, content_type="application/json", body=mock_payload))
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(".slot-editor:visible").wait_for(timeout=30_000)
    worker = page.locator(".slot-group[data-group-id='worker']")
    empty = worker.locator(".slot-grid > .slot-tile-cell > .slot-tile.is-empty").first
    with page.expect_request("**/api/plots/*/candidates*"):
        empty.click()
    page.locator(".candidate-row").first.wait_for(timeout=30_000)
    page.locator(".candidate-row").first.click()
    page.wait_for_function("document.getElementById('checkout-btn') && !document.getElementById('checkout-btn').hidden", timeout=30_000)
    return candidate


def test_staged_cooling_card_marks_tile_warns_checkout_and_disables_prepare(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    deed_uid = _open_worker_plot(base_url)
    candidate = _stage_cooling_candidate(page, base_url, deed_uid)
    name = candidate["name"]

    # Item 1 -- the staged tile itself must look visibly different from a ready card.
    # `.is-staged` (not `.first` of every occupied tile) picks out the specific slot we
    # just staged into -- the plot may already carry other, genuinely-ready workers.
    occupied = page.locator(".slot-group[data-group-id='worker'] .slot-tile.is-staged").first
    occupied.wait_for(timeout=30_000)
    assert "is-cooling" in (occupied.get_attribute("class") or "")
    assert occupied.locator(".slot-cooldown-badge").count() == 1
    assert "not ready" in occupied.locator(".slot-cooldown-badge").get_attribute("aria-label").lower()
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    page.locator(".slot-editor").screenshot(path=str(SCREEN_DIR / "tile-marker.png"))

    # Item 2 -- the checkout panel names the asset and states its ready time, in words
    # BEFORE any transaction is prepared.
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.wait_for(timeout=30_000)
    panel_text = panel.inner_text()
    assert name in panel_text
    assert "not ready to stake" in panel_text.lower()
    warning = panel.locator(".checkout-cooldown-warning")
    assert warning.count() >= 1
    page.locator(".slot-editor").screenshot(path=str(SCREEN_DIR / "checkout-panel.png"))

    # Item 3 -- Prepare Transaction must not be offered for a batch that will be
    # refused: disabled, with the reason reachable in its accessible name (not only a
    # tooltip and not only a toast).
    prepare = panel.locator("button[data-checkout-action='prepare']")
    assert prepare.is_disabled()
    aria = (prepare.get_attribute("aria-label") or "").lower()
    assert name.lower() in aria
    assert "not ready to stake" in aria
    page.locator("#checkout-panel").screenshot(path=str(SCREEN_DIR / "prepare-disabled.png"))

    # A genuinely disabled <button> refuses a real (non-forced) Playwright click
    # outright -- confirming that here, rather than only reading the `disabled`
    # attribute, is what actually proves no /api/op-preview request can escape through
    # this control while the batch is blocked.
    op_preview_requests: list[str] = []
    page.on("request", lambda request: op_preview_requests.append(request.url) if "/api/op-preview" in request.url else None)
    with pytest.raises(Exception):
        prepare.click(timeout=2_000)
    settle(page)
    assert not op_preview_requests
    page.close()


def test_prepare_transaction_disable_guard_actually_guards(live_browser: tuple[Browser, str]) -> None:
    """MUTATION TEST for the item-3 gate itself, run against the REAL server and a
    REAL staged move (not a slice). Injection performed: a multiline regex anchors
    the single `const prepareDisabled = ...;` declaration and replaces only its
    expression with `false`, so future edits to that expression do not deaden setup.
    The injected `/static/app.js` response is supplied through `page.route` and never
    changes the checked-in file. Injection run, watched red: the sibling disabled-
    button assertion observed Prepare enabled, reproducing the guard failure.
    Restored: the route was removed and the checked-in source was used again; watched
    green: the sibling assertion observed Prepare disabled."""
    browser, base_url = live_browser
    real_source = read_entry_source()
    prepare_disabled = re.compile(r"(?m)^(?P<prefix>\s*const prepareDisabled\s*=\s*)[^;\n]+(?P<suffix>;\s*)$")
    matches = list(prepare_disabled.finditer(real_source))
    assert len(matches) == 1, "renderCheckoutSurface's prepareDisabled declaration must remain a unique single-line anchor"
    match = matches[0]
    broken_source = real_source[:match.start()] + f"{match.group('prefix')}false{match.group('suffix')}" + real_source[match.end():]
    assert broken_source != real_source

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    injection_seen = False

    def fulfill_injected_app(route) -> None:
        nonlocal injection_seen
        injection_seen = True
        route.fulfill(status=200, content_type="application/javascript", body=broken_source)

    page.route(
        "**/static/app.js*",
        fulfill_injected_app,
    )
    deed_uid = _open_worker_plot(base_url)
    _stage_cooling_candidate(page, base_url, deed_uid)
    assert injection_seen, "injected app.js response was not intercepted"
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.wait_for(timeout=30_000)
    prepare = panel.locator("button[data-checkout-action='prepare']")
    # With the real code this candidate would disable Prepare Transaction (see the
    # sibling test) -- with the guard line neutralized it stays enabled, proving the
    # sibling test's assertion is genuinely sensitive to this exact line, not a
    # tautology that would pass regardless of what the code does.
    assert not prepare.is_disabled(), "injection did not reproduce the expected wrong behaviour -- test is not exercising the guarded line"
    page.close()


# (Batch C, ruling 5 / row 18): test_cooling_surface.py migrated in
# `test_cooling_surface.py` (node-level unit slices) is retired whole; per §15 ruling 5 the
# live sibling above did NOT already cover three gaps: the fragment-text fallbacks
# `cooldownReasonFragment()` produces (three of its four branches are unreachable through the
# server's real cooldown_state() output, but are reachable with a synthetic/mocked candidate
# payload, exactly the technique `_cooling_candidate` above already uses); the foil chip on a
# candidate row; and a mutation guard on `checkoutBlockedCooldownEntries`'s `ready === false`
# filter proving a ready candidate and a pure unstake are never blocked in a MIXED stage (not
# just a single staged entry, per the ruling's own wording). Only the tile-marking and
# checkout-blocking assertions of the old file were droppable (already covered by
# `test_staged_cooling_card_marks_tile_warns_checkout_and_disables_prepare` above).


def test_cooldown_reason_fragment_never_invents_a_verdict(live_browser: tuple[Browser, str]) -> None:
    """The three fallback texts `cooldownReasonFragment()` produces for states the real
    server rarely/never emits with `ready: false` (`unknown`, `unverified`) plus the
    ordinary `cooling` text, each read off the REAL rendered tile badge
    (`.slot-cooldown-badge`'s `aria-label`, `ceres/static/app.js:3782-3784`) for a
    synthetic candidate staged through the real picker -- not a direct function call.
    `cooldownReasonFragment(null)` (the fourth branch) has NO reachable rendered call
    site at all: both call sites (`ceres/static/app.js:3782`, `:4260`) require a
    truthy `cooldown` before invoking it, so that branch is unrenderable and is not
    asserted here -- reported to the dispatcher rather than faked."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _goto_board(page, base_url)
    used_deeds: set[str] = set()

    cases = [
        ({"ready": False, "ready_at": None, "class": "unknown", "state": "unknown"}, "stake class not recognised"),
        ({"ready": False, "ready_at": None, "class": "unverified", "state": "unverified"}, "no published cooldown duration for this stake class"),
        ({"ready": False, "ready_at": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat().replace("+00:00", "Z"), "class": "worker", "state": "cooling"}, "cooling until"),
    ]
    for cooldown, expected_fragment in cases:
        deed_uid = _open_worker_plot(base_url, exclude=frozenset(used_deeds))
        used_deeds.add(deed_uid)
        candidate = _worker_candidate_with_cooldown(base_url, deed_uid, cooldown)
        _stage_add_into_open_slot(page, deed_uid, candidate)
        page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
        badge = page.locator(".slot-editor:visible .slot-group[data-group-id='worker'] .slot-tile.is-staged .slot-cooldown-badge").first
        badge.wait_for(timeout=30_000)
        aria = (badge.get_attribute("aria-label") or "").lower()
        assert expected_fragment.lower() in aria, f"cooldown state {cooldown['state']!r} did not render its fragment text: {aria!r}"
        page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.close()


def test_candidate_row_shows_a_foil_chip_using_the_shared_formatter(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _goto_board(page, base_url)
    deed_uid = _open_worker_plot(base_url)
    real = _real_worker_candidate(base_url, deed_uid)
    gold = {**real, "name": "Gold Test Worker", "foil": 1}
    regular = {**real, "name": "Regular Test Worker", "foil": 0}
    black = {**real, "name": "Black Foil Test Worker", "foil": 3}
    mock_payload = json.dumps({"candidates": [gold, regular, black], "total": 3, "source": "test-fixture", "excluded_counts": {}, "notices": []})
    page.route("**/api/plots/*/candidates*", lambda route: route.fulfill(status=200, content_type="application/json", body=mock_payload))
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(".slot-editor:visible").wait_for(timeout=30_000)
    worker = page.locator(".slot-group[data-group-id='worker']")
    empty = worker.locator(".slot-grid > .slot-tile-cell > .slot-tile.is-empty").first
    with page.expect_request("**/api/plots/*/candidates*"):
        empty.click()
    rows = page.locator(".candidate-row")
    rows.first.wait_for(timeout=30_000)

    gold_row = page.locator(".candidate-row", has_text="Gold Test Worker")
    assert gold_row.locator(".foil-badge.is-foil").count() == 1, "gold foil candidate got no foil chip"
    assert gold_row.locator(".foil-badge.is-foil").text_content() == "Gold"

    regular_row = page.locator(".candidate-row", has_text="Regular Test Worker")
    assert regular_row.locator(".foil-badge.is-foil").count() == 0, "a regular (non-foil) candidate must not render a gold chip"

    # Black Foil (3/4, the internal rules) is distinct from gold and must carry its own badge.
    black_row = page.locator(".candidate-row", has_text="Black Foil Test Worker")
    assert black_row.locator(".foil-badge.is-foil").count() == 1, "a Black Foil (foil:3) candidate got no black foil badge"
    assert black_row.locator(".foil-badge.is-foil").text_content() == "Black Foil"
    page.close()


def _mixed_stage_deeds(base_url: str) -> tuple[str, str, str]:
    ready_deed = _open_worker_plot(base_url)
    cooling_deed = _open_worker_plot(base_url, exclude=frozenset({ready_deed}))
    remove_deed = _plot_with_worker_occupant(base_url, exclude=frozenset({ready_deed, cooling_deed}))
    return ready_deed, cooling_deed, remove_deed


def _stage_mixed_cooldown_scenario(page: Page, base_url: str) -> dict:
    """Stages three entries across three plots: a normal ready add (must never be
    blocked), a cooling add (must be the only blocked entry) and a pure unstake
    removal on an unrelated plot (must never be blocked either) --
    `refuseIncomingWithRemovals`/`deedHasIncomingStage` (app.js:3051-3061, :6240)
    refuse mixing add+remove on the SAME plot, which is why this needs three."""
    _goto_board(page, base_url)
    ready_deed, cooling_deed, remove_deed = _mixed_stage_deeds(base_url)
    ready_candidate = _worker_candidate_with_cooldown(
        base_url, ready_deed, {"ready": True, "ready_at": None, "class": "worker", "state": "ready"},
    )
    ready_candidate["name"] = "Mixed-Stage Ready Worker"
    _stage_add_into_open_slot(page, ready_deed, ready_candidate)

    future = (datetime.now(timezone.utc) + timedelta(hours=40)).isoformat().replace("+00:00", "Z")
    cooling_candidate = _worker_candidate_with_cooldown(
        base_url, cooling_deed, {"ready": False, "ready_at": future, "class": "worker", "state": "cooling"},
    )
    cooling_candidate["name"] = "Mixed-Stage Cooling Worker"
    _stage_add_into_open_slot(page, cooling_deed, cooling_candidate)

    _stage_remove_existing_occupant(page, remove_deed)
    return {"ready_name": ready_candidate["name"], "cooling_name": cooling_candidate["name"]}


def test_checkout_blocked_cooldown_filter_never_blocks_ready_or_unstake_in_a_mixed_stage(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    names = _stage_mixed_cooldown_scenario(page, base_url)
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.wait_for(timeout=30_000)
    warnings = panel.locator(".checkout-cooldown-warning")
    assert warnings.count() == 1, f"expected exactly one blocked (cooling) entry in a 3-entry mixed stage, got {warnings.count()}"
    warning_text = warnings.inner_text()
    assert names["cooling_name"] in warning_text
    assert names["ready_name"] not in warning_text
    prepare = panel.locator("button[data-checkout-action='prepare']")
    assert prepare.is_disabled()
    page.close()


def test_checkout_blocked_cooldown_filter_guard_actually_guards(live_browser: tuple[Browser, str]) -> None:
    """MUTATION TEST: `checkoutBlockedCooldownEntries`'s `entry.cooldown.ready === false`
    is replaced with `entry.cooldown.ready !== undefined` -- the literal shape of the
    original bug (a cooling candidate looked the same as a ready one because nothing
    distinguished them). In the SAME 3-entry mixed stage as the sibling test, the ready
    add's `cooldown.ready` is defined (`true`), so the mutated filter blocks it too --
    proving the sibling test's single-warning assertion is genuinely sensitive to this
    line. `action !== 'unstake'` is untouched by this injection, so the removal entry
    (which carries no `cooldown` at all -- `stageForSlot`'s own comment) stays excluded
    either way; that half of the ruling's claim is what the sibling (intact) test
    proves, not this one."""
    real_source = read_entry_source()
    real_line = "return CERES_STAGE.filter((entry) => entry.action !== 'unstake' && entry.cooldown && entry.cooldown.ready === false);"
    assert real_source.count(real_line) == 1, "checkoutBlockedCooldownEntries's exact filter line moved; update this test's injection string"
    mutated_line = "return CERES_STAGE.filter((entry) => entry.action !== 'unstake' && entry.cooldown && entry.cooldown.ready !== undefined);"
    broken_source = real_source.replace(real_line, mutated_line)
    assert broken_source != real_source

    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    injection_seen = False

    def fulfill_injected_app(route) -> None:
        nonlocal injection_seen
        injection_seen = True
        route.fulfill(status=200, content_type="application/javascript", body=broken_source)

    page.route("**/static/app.js*", fulfill_injected_app)
    names = _stage_mixed_cooldown_scenario(page, base_url)
    assert injection_seen, "injected app.js response was not intercepted"
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.wait_for(timeout=30_000)
    warnings = panel.locator(".checkout-cooldown-warning")
    # With the real filter this is 1 (only the cooling entry); the mutated filter
    # also reports the ready entry blocked, proving the sibling test's assertion is
    # actually sensitive to the `ready === false` check and not a tautology.
    assert warnings.count() == 2, "injection did not reproduce the expected wrong behaviour -- test is not exercising the guarded line"
    warning_text = " ".join(warnings.all_inner_texts())
    assert names["ready_name"] in warning_text
    page.close()
