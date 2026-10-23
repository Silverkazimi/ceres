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

CHECKOUT_JS = Path(__file__).parents[2] / "ceres" / "static" / "checkout.js"
SET_ASIDE_LINE = "  if (entry?.cooldown && entry.cooldown.ready === false) return 'blocked';"
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


def _real_worker_candidate(base_url: str, deed_uid: str, *, exclude_refs: frozenset[str] = frozenset()) -> dict:
    """A REAL worker candidate for `deed_uid`, straight from `/api/plots/{deed}/
    candidates` -- every field (name, base_pp, construction_pp_delta, plot_impact,
    ...) is exactly what candidates.py produced, so callers overriding only
    `cooldown`/`foil`/`name` still exercise the real renderCandidateRow() code path
    unmodified for everything else. `exclude_refs` keeps two staged candidates distinct
    cards: the same card staged on two plots is one move, not two."""
    params = urllib.parse.urlencode({
        "group_id": "worker", "limit": 10, "offset": 0, "sort": "construction_pp",
        "bucket": "all", "preset": "net_gain_default",
    })
    url = f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates?{params}"
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    candidates = [c for c in payload.get("candidates") or [] if c.get("asset_ref") not in exclude_refs]
    assert candidates, f"plot {deed_uid} returned no worker candidates to build a fixture from"
    return copy.deepcopy(candidates[0])


def _worker_candidate_with_cooldown(base_url: str, deed_uid: str, cooldown: dict, *, exclude_refs: frozenset[str] = frozenset()) -> dict:
    candidate = _real_worker_candidate(base_url, deed_uid, exclude_refs=exclude_refs)
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


def _open_ready(page: Page, deed_uid: str):
    """Open `deed_uid` and wait until its tiles are drawn (the band mounts "preparing" while
    the detail is read through POST .../expand)."""
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(".slot-editor:visible .editor-band[data-plot-state='ready']").wait_for(timeout=30_000)
    return page.locator(".slot-editor:visible .slot-group[data-group-id='worker']")


def _waiting_strip_slots(panel) -> list[str]:
    """Accessible names of the Changes strip tiles that wait (set aside, not signed)."""
    return [label for label in panel.locator(".ch-slot[aria-label]").evaluate_all("els => els.map((el) => el.getAttribute('aria-label'))") if ". Waiting · " in label]


def _record_previews(page: Page) -> list[str]:
    """Every `/api/op-preview` request body: the moves Changes offers to sign."""
    bodies: list[str] = []
    page.on("request", lambda request: bodies.append(request.post_data or "") if "/api/op-preview" in request.url else None)
    return bodies


def _route_mutated_checkout(page: Page, anchor: str, replacement: str) -> dict:
    """Serve checkout.js with `anchor` (present exactly once) replaced; never the file on disk."""
    source = CHECKOUT_JS.read_text(encoding="utf-8")
    assert source.count(anchor) == 1, f"checkout.js anchor moved or is not unique: {anchor!r}"
    mutated = source.replace(anchor, replacement)
    seen: dict = {}

    def fulfill(route) -> None:
        seen["hit"] = True
        route.fulfill(status=200, content_type="application/javascript", body=mutated)

    page.route(re.compile(r"/static/checkout\.js(\?|$)"), fulfill)
    return seen


def _stage_add_into_open_slot(page: Page, deed_uid: str, candidate: dict) -> None:
    """Opens `deed_uid`'s editor, mocks its candidate list down to a single entry,
    stages that one candidate into the first empty worker slot, waits for the staged
    tile (proof the stage actually landed), then collapses the row again so only one
    `.slot-editor` is ever in the DOM at once."""
    pattern = _mock_candidates_route(page, candidate)
    worker = _open_ready(page, deed_uid)
    empty = worker.locator(".slot-grid > .slot-tile-cell > .slot-tile.is-empty").first
    with page.expect_request(pattern):
        empty.click()
    row = page.locator(".candidate-row").first
    row.wait_for(timeout=30_000)
    row.click()
    worker.locator(".slot-tile.is-staged").filter(has_text=candidate["name"]).first.wait_for(timeout=30_000)
    page.unroute(pattern)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()


def _stage_remove_existing_occupant(page: Page, base_url: str, deed_uid: str) -> str:
    """Opens `deed_uid`'s editor and clicks the remove control on its (real, already
    staked) worker occupant -- a pure `unstake`, staged without any incoming move on
    this plot, then collapses the row again. Returns the removed card's asset ref."""
    worker = _open_ready(page, deed_uid)
    occupied_tile = worker.locator(".slot-tile[data-group-id='worker']:not(.is-empty):not(.is-staged)").first
    occupied_tile.wait_for(timeout=30_000)
    slot_index = int(occupied_tile.get_attribute("data-slot-index"))
    # The remove (x) control is CSS-hidden until its tile is hovered/focused -- a
    # real hover, not `force=True`, so this exercises the same reveal a user gets.
    occupied_tile.hover()
    remove_button = occupied_tile.locator(".slot-remove")
    remove_button.wait_for(timeout=30_000)
    remove_button.click()
    worker.locator(f".slot-tile.is-pending-removal[data-slot-index='{slot_index}']").wait_for(timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    with urllib.request.urlopen(f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}", timeout=10) as response:
        workers = json.loads(response.read().decode("utf-8")).get("workers") or []
    return next(w["asset_ref"] for w in workers if w.get("slot") == slot_index + 1)


def _cooling_candidate(base_url: str, deed_uid: str) -> dict:
    candidate = _real_worker_candidate(base_url, deed_uid)
    ready_at = (datetime.now(timezone.utc) + timedelta(hours=40, minutes=12)).isoformat().replace("+00:00", "Z")
    candidate["cooldown"] = {"ready": False, "ready_at": ready_at, "class": "worker", "state": "cooling"}
    return candidate


def _stage_cooling_candidate(page: Page, base_url: str, deed_uid: str) -> dict:
    candidate = _cooling_candidate(base_url, deed_uid)
    _goto_board(page, base_url)
    _stage_add_into_open_slot(page, deed_uid, candidate)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()  # reopen it for the tile checks
    page.locator(".slot-editor:visible .editor-band[data-plot-state='ready']").wait_for(timeout=30_000)
    return candidate


def test_staged_cooling_card_marks_tile_warns_checkout_and_disables_prepare(live_browser: tuple[Browser, str]) -> None:
    """A card the server marked not ready (`cooldown.ready: false`) is staged but never
    signed. The checkout no longer refuses the whole batch with a warning and a disabled
    Prepare button: Prepare is automatic, and a not-ready move is SET ASIDE as Waiting
    (checkout.js stageSetAside, "K6: a refusal is set aside, not a veto") while the rest
    of the batch stays signable. So: the tile is marked as waiting with its time left,
    Changes shows it waiting, and no transaction preview ever carries it."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    previews = _record_previews(page)
    deed_uid = _open_worker_plot(base_url)
    candidate = _stage_cooling_candidate(page, base_url, deed_uid)
    name = candidate["name"]

    # Item 1 -- the staged tile itself must look visibly different from a ready card.
    occupied = page.locator(".slot-group[data-group-id='worker'] .slot-tile.is-staged").filter(has_text=name).first
    occupied.wait_for(timeout=30_000)
    classes = occupied.get_attribute("class") or ""
    assert "is-cooling" in classes and "is-stage-blocked" in classes, classes
    badge = occupied.locator(".slot-wait-badge")
    assert badge.count() == 1
    assert badge.get_attribute("data-tip") == "Waiting"
    assert badge.locator(".slot-wait-time[data-countdown-until]").count() == 1
    foot = (occupied.get_attribute("data-tip-foot") or "").lower()
    assert "waiting, not signed" in foot and "cooling until" in foot, foot
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    page.locator(".slot-editor").screenshot(path=str(SCREEN_DIR / "tile-marker.png"))

    # Item 2 -- Changes names the asset and shows it waiting, with its time left.
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.wait_for(timeout=30_000)
    settle(page)
    assert name in panel.inner_text()
    # each changed tile of the Changes strip names its state in its accessible name
    waiting = _waiting_strip_slots(panel)
    assert len(waiting) == 1 and waiting[0].startswith(f"{name}. Waiting · can be signed in "), waiting
    page.locator("#checkout-panel").screenshot(path=str(SCREEN_DIR / "checkout-panel.png"))

    # Item 3 -- nothing doomed is ever prepared: no preview carries the waiting card.
    assert not [body for body in previews if candidate["asset_ref"] in body], previews
    page.close()


def test_prepare_transaction_disable_guard_actually_guards(live_browser: tuple[Browser, str]) -> None:
    """MUTATION TEST for the set-aside gate itself, run against the REAL server and a
    REAL staged move. Injection performed: checkout.js's `stageSetAside()` line that sets a
    not-ready card aside (`if (entry?.cooldown && entry.cooldown.ready === false) return
    'blocked';`) is removed from the served module (page.route; the checked-in file is
    never changed). The cooling card then becomes part of the prepared transaction --
    exactly what the sibling test asserts never happens."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    seen = _route_mutated_checkout(page, SET_ASIDE_LINE, "  /* MUTATED OUT: cooling set-aside */")
    previews = _record_previews(page)
    deed_uid = _open_worker_plot(base_url)
    candidate = _stage_cooling_candidate(page, base_url, deed_uid)
    assert seen.get("hit"), "injected checkout.js response was not intercepted"
    page.locator("#checkout-btn").click()
    page.locator("#checkout-panel").wait_for(timeout=30_000)
    settle(page)
    assert [body for body in previews if candidate["asset_ref"] in body], (
        "injection did not reproduce the expected wrong behaviour -- test is not exercising the guarded line"
    )
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
    """The fallback texts `cooldownReasonFragment()` produces for states the real
    server rarely/never emits with `ready: false` (`unknown`, `unverified`) plus the
    ordinary `cooling` text, each read off the REAL staged tile's tooltip foot
    (editor.js stageTileFoot/stageTileDetail: "Staged change -- Waiting, not signed:
    <fragment>.") for a synthetic candidate staged through the real picker -- not a
    direct function call. (The separate `.slot-cooldown-badge` is retired; a staged
    waiting card wears the Waiting badge and states its reason in the tile's tooltip.)
    `cooldownReasonFragment(null)` has no reachable rendered call site with a staged
    not-ready card and is not asserted here."""
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
        worker = _open_ready(page, deed_uid)
        tile = worker.locator(".slot-tile.is-staged").filter(has_text=candidate["name"]).first
        tile.wait_for(timeout=30_000)
        foot = (tile.get_attribute("data-tip-foot") or "").lower()
        assert "waiting, not signed" in foot, foot
        assert expected_fragment.lower() in foot, f"cooldown state {cooldown['state']!r} did not render its fragment text: {foot!r}"
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
    """Stages three entries across three plots: a normal ready add (must never be set
    aside), a cooling add (must be the only waiting entry) and a pure unstake removal on
    an unrelated plot (must never be set aside either) -- adds and removals on the SAME
    plot are refused, which is why this needs three. The two adds are distinct cards."""
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
        exclude_refs=frozenset({ready_candidate["asset_ref"]}),
    )
    cooling_candidate["name"] = "Mixed-Stage Cooling Worker"
    _stage_add_into_open_slot(page, cooling_deed, cooling_candidate)

    removed_ref = _stage_remove_existing_occupant(page, base_url, remove_deed)
    return {
        "ready_name": ready_candidate["name"], "cooling_name": cooling_candidate["name"],
        "ready_ref": ready_candidate["asset_ref"], "cooling_ref": cooling_candidate["asset_ref"], "removed_ref": removed_ref,
    }


def _last_preview_after_opening_changes(page: Page, previews: list[str]) -> str:
    page.locator("#checkout-btn").click()
    page.locator("#checkout-panel").wait_for(timeout=30_000)
    settle(page)
    assert previews, "opening Changes prepared no transaction preview for the signable moves"
    return previews[-1]


def test_checkout_blocked_cooldown_filter_never_blocks_ready_or_unstake_in_a_mixed_stage(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    previews = _record_previews(page)
    staged = _stage_mixed_cooldown_scenario(page, base_url)
    preview = _last_preview_after_opening_changes(page, previews)
    panel = page.locator("#checkout-panel")
    waiting = _waiting_strip_slots(panel)
    assert len(waiting) == 1 and waiting[0].startswith(staged["cooling_name"]), f"expected exactly one waiting (cooling) entry in a 3-entry mixed stage: {waiting}"
    assert staged["ready_ref"] in preview, "the ready add must be prepared"
    assert staged["removed_ref"] in preview, "the pure unstake must be prepared"
    assert staged["cooling_ref"] not in preview, "the cooling add must be set aside, never prepared"
    page.close()


def test_checkout_blocked_cooldown_filter_guard_actually_guards(live_browser: tuple[Browser, str]) -> None:
    """MUTATION TEST: checkout.js `stageSetAside()`'s `entry.cooldown.ready === false` is
    replaced with `entry.cooldown.ready !== undefined` -- the literal shape of the original
    bug (a cooling candidate looked the same as a ready one because nothing distinguished
    them). In the SAME 3-entry mixed stage as the sibling test, the ready add's
    `cooldown.ready` is defined (`true`), so the mutated filter sets it aside too and it
    drops out of the prepared transaction -- proving the sibling test is sensitive to this
    line. A removal returns before the cooldown check either way."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    seen = _route_mutated_checkout(page, SET_ASIDE_LINE, SET_ASIDE_LINE.replace("entry.cooldown.ready === false", "entry.cooldown.ready !== undefined"))
    previews = _record_previews(page)
    staged = _stage_mixed_cooldown_scenario(page, base_url)
    assert seen.get("hit"), "injected checkout.js response was not intercepted"
    preview = _last_preview_after_opening_changes(page, previews)
    assert staged["ready_ref"] not in preview, "injection did not reproduce the expected wrong behaviour -- test is not exercising the guarded line"
    assert len(_waiting_strip_slots(page.locator("#checkout-panel"))) == 2
    page.close()
