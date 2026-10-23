"""P1/P2/P3 display-truth guards for the Ceres browser surfaces.

The editor's per-card footer row now shows each card's Boostable PP -- its Base PP after
the plot cap with this plot's terrain applied, "what this plot's boosts multiply" -- and the
cell tooltip carries the parts: the card's Base PP, the terrain and what the cap takes
(editor.js editorBasePpColumn). The cap guards below read the figure and those parts.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest
from playwright.sync_api import Browser, Page

from ceres.board import project_single_plot
from ceres.candidates import rank_candidates
from .conftest import spawn_live_ceres
from . import board_basic_recording as recording


CASCADE_DEED_UID = recording.OVERCAP.uid
CASCADE_PLOT = recording.OVERCAP.plot
RUNI_DEED_UID = recording.RUNI.uid
RUNI_PLOT = recording.RUNI.plot
CASCADE_FIGURES = (
    ("Lyanna Natura", 40_000.000, 40_000.000),
    ("Silver Shield Paladin", 32_941.176, 32_941.176),
    ("Spirit of the Forest", 25_000.000, 25_000.000),
    ("Prince Rennyn", 7_500.000, 2_058.824),
    ("Kinbound Druid", 2_200.000, 0.000),
)


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _open_badge_slideouts(page) -> None:
    """Open every plot's badge slide-out.

    Board badges are no longer a table column: each plot's chips render in its own row of
    the slide-out panel (`#badge-overlay .badge-overlay-row`), which badge-overlay.js fills
    only once that plot's handle in the right-edge badge bar (`#badge-bar .badge-bar-row`)
    is opened."""
    handles = page.locator("#badge-bar .badge-bar-row")
    handles.first.wait_for(timeout=30_000)
    handles.evaluate_all("rows => rows.forEach(row => { if (row.getAttribute('aria-expanded') !== 'true') row.click(); })")
    page.wait_for_function(
        """() => {
            const rows = [...document.querySelectorAll('#badge-overlay .badge-overlay-row')];
            return rows.length > 0 && rows.every(row => row.classList.contains('is-open'));
        }""",
        timeout=30_000,
    )


def _candidate(terrain: float | None, *, splinter: str = "Fire") -> dict:
    construction = (
        {"status": "point", "value": 2400, "low": 2400, "high": 2400, "reason": None}
        if terrain is not None else
        {"status": "unknown", "value": None, "low": None, "high": None, "reason": "terrain_modifier_unknown"}
    )
    return {
        "asset_ref": "display-truth-worker",
        "uid": "display-truth-worker",
        "card_uid": "display-truth-worker",
        "card_detail_id": 40001,
        "name": "Display Truth Worker",
        "base_pp": 2400,
        "base_pp_delta": {"status": "point", "value": 2400, "low": 2400, "high": 2400, "reason": None},
        "construction_pp": construction,
        "construction_pp_delta": construction,
        "plot_impact": {"status": "point", "value": 2400, "low": 2400, "high": 2400, "reason": None},
        "net_gain": 2400,
        "net_gain_breakdown": {},
        "terrain_boost": terrain,
        "splinter": splinter,
        "rarity": "Common",
        "card_set": "chaos",
        "edition": 1,
        "level": 1,
        "bucket": "idle",
        "land_abilities": [],
        "sole_holder_for": [],
        "cooldown": {"ready": True, "ready_at": None, "class": "none", "state": "ready"},
        "asset_kind": "worker",
    }


def _install_plot_fixture(page: Page, deed_uid: str, candidate: dict) -> None:
    encoded_uid = deed_uid

    def plot_route(route) -> None:
        path = route.request.url.split("?", 1)[0]
        candidate_path = f"/api/plots/{encoded_uid}/candidates"
        detail_paths = (f"/api/plots/{encoded_uid}", f"/api/plots/{encoded_uid}/expand")
        if path.endswith(candidate_path):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"candidates": [candidate], "total": 1, "source": "display-truth-fixture", "excluded_counts": {}, "notices": []}),
            )
            return
        if not any(path.endswith(detail_path) for detail_path in detail_paths):
            route.continue_()
            return
        response = route.fetch()
        payload = response.json()
        payload["assets_loaded"] = True
        payload["workers"] = [{
            "asset_ref": "cascade-staked-worker", "uid": "cascade-staked-worker", "card_uid": "cascade-staked-worker",
            "slot": 1, "name": "Cascade Staked Worker", "card_name": "Cascade Staked Worker", "card_detail_id": 40001,
            "editions": "1", "edition": 1, "card_set": "chaos", "foil": 0, "gold": 0, "level": 1,
            "base_pp": 2100, "base_pp_after_cap": 2100, "construction_pp": 2310, "harvest_pp": 2310,
            "terrain_boost": 0.1, "land_abilities": [], "badges": [], "sole_holder_for": [],
        }]
        payload["capacity"] = {**(payload.get("capacity") or {}), "potential_card_capacity": 2, "runi_staked": False}
        payload["staking_facts"] = {**(payload.get("staking_facts") or {}), "max_workers_allowed": 2, "is_power_core_staked": True, "is_runi_staked": False}
        payload["is_runi_staked"] = False
        route.fulfill(response=response, body=json.dumps(payload))

    page.route("**/api/plots/**", plot_route)


def _install_cascade_fixture(page: Page) -> None:
    def detail_route(route) -> None:
        path = route.request.url.split("?", 1)[0]
        detail_paths = (f"/api/plots/{CASCADE_DEED_UID}", f"/api/plots/{CASCADE_DEED_UID}/expand")
        if not any(path.endswith(detail_path) for detail_path in detail_paths):
            route.continue_()
            return
        response = route.fetch()
        payload = response.json()
        payload["assets_loaded"] = True
        payload["workers"] = [
            {
                "asset_ref": f"cascade-worker-{slot}", "uid": f"cascade-worker-{slot}", "card_uid": f"cascade-worker-{slot}",
                "slot": slot, "name": name, "card_name": name, "card_detail_id": 41000 + slot,
                "editions": "2", "edition": 2, "card_set": "chaos", "foil": 0, "gold": 0, "level": 1,
                # Boostable PP is Base PP after the cap with terrain applied (0% here), as the
                # game reports it; the footer row shows this figure.
                "base_pp": base_pp, "base_pp_after_cap": after_cap, "construction_pp": after_cap,
                "harvest_pp": 0, "terrain_boost": 0, "land_abilities": [], "badges": [], "sole_holder_for": [],
            }
            for slot, (name, base_pp, after_cap) in enumerate(CASCADE_FIGURES, start=1)
        ]
        payload["capacity"] = {**(payload.get("capacity") or {}), "potential_card_capacity": 5, "runi_staked": False}
        payload["staking_facts"] = {**(payload.get("staking_facts") or {}), "max_workers_allowed": 5, "is_power_core_staked": True, "is_runi_staked": False}
        payload["is_runi_staked"] = False
        payload["over_cap"] = False
        route.fulfill(response=response, body=json.dumps(payload))

    page.route("**/api/plots/**", detail_route)


def _install_over_cap_fixture(page: Page) -> None:
    def detail_route(route) -> None:
        path = route.request.url.split("?", 1)[0]
        detail_paths = (f"/api/plots/{CASCADE_DEED_UID}", f"/api/plots/{CASCADE_DEED_UID}/expand")
        if not any(path.endswith(detail_path) for detail_path in detail_paths):
            route.continue_()
            return
        response = route.fetch()
        payload = response.json()
        payload["staking_facts"] = {
            **(payload.get("staking_facts") or {}),
            "total_base_pp": 107641.176,
            "total_base_pp_after_cap": 100000.0,
            "total_base_pp_cap": 100000.0,
        }
        route.fulfill(response=response, body=json.dumps(payload))

    page.route("**/api/plots/**", detail_route)


def _runi_below_cap_projection() -> dict:
    report = {
        "plots": [{
            "deed_uid": RUNI_DEED_UID,
            "facts": {"plot": RUNI_PLOT, "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T"},
            "staking_facts": {
                "max_workers_allowed": 5, "total_base_pp": 101000, "total_base_pp_after_cap": 100000,
                "total_base_pp_cap": 100000, "is_power_core_staked": True, "is_runi_staked": True,
                "is_energized": False, "deed_rarity_boost": 0, "totem_boost": 0, "title_boost": 0,
                "runi_boost": 0, "deed_status_token_boost": 0, "red_biome_modifier": 0,
                "blue_biome_modifier": 0, "white_biome_modifier": 0, "black_biome_modifier": 0,
                "green_biome_modifier": 0, "gold_biome_modifier": 0,
            },
        }],
        "workers": [
            {
                "asset_ref": "synthetic-runi", "uid": "synthetic-runi", "card_uid": "synthetic-runi",
                "stake_ref_uid": RUNI_DEED_UID, "slot": 1, "name": "Runi", "card_detail_id": 505,
                "edition": 1, "card_set": "promo", "foil": 0, "gold": 0, "land_base_pp": 1234,
                "base_pp_after_cap": 1234, "total_construction_pp": 0, "total_harvest_pp": 0,
                "terrain_boost": 0,
            },
            {
                "asset_ref": "synthetic-worker", "uid": "synthetic-worker", "card_uid": "synthetic-worker",
                "stake_ref_uid": RUNI_DEED_UID, "slot": 2, "name": "Synthetic Worker", "card_detail_id": 41001,
                "edition": 2, "card_set": "chaos", "foil": 0, "gold": 0, "land_base_pp": 99766,
                "base_pp_after_cap": 98500, "total_construction_pp": 0, "total_harvest_pp": 0,
                "terrain_boost": 0,
            },
        ],
        "cards": [],
        "bonus_assets": [],
        "unassigned_items": {},
    }
    return project_single_plot(report, RUNI_DEED_UID, details_by_id={})


def _install_runi_below_cap_fixture(page: Page) -> dict:
    projected = _runi_below_cap_projection()

    def detail_route(route) -> None:
        path = route.request.url.split("?", 1)[0]
        detail_paths = (f"/api/plots/{RUNI_DEED_UID}", f"/api/plots/{RUNI_DEED_UID}/expand")
        if not any(path.endswith(detail_path) for detail_path in detail_paths):
            route.continue_()
            return
        response = route.fetch()
        payload = response.json()
        payload.update(projected)
        route.fulfill(response=response, body=json.dumps(payload))

    page.route("**/api/plots/**", detail_route)
    return projected


def _open_fixture_picker(page: Page, deed_uid: str) -> None:
    page.reload()
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    row = page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']")
    row.wait_for(timeout=30_000)
    row.click()
    editor = page.locator(".slot-editor:visible")
    editor.wait_for(timeout=30_000)
    empty = editor.locator(".slot-group[data-group-id='worker'] .slot-tile.is-empty").first
    empty.wait_for(timeout=30_000)
    empty.click()
    editor.locator(".inline-picker:visible .candidate-row").first.wait_for(timeout=30_000)


def _open_cascade_fixture(page: Page) -> Page:
    _install_cascade_fixture(page)
    page.reload()
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    row = page.locator(f"tr.plot-row[data-deed-uid='{CASCADE_DEED_UID}']")
    row.wait_for(timeout=30_000)
    row.click()
    editor = page.locator(".slot-editor:visible")
    editor.wait_for(timeout=30_000)
    # the band renders `preparing` first; its footer figures exist once it is `ready`
    editor.locator(".editor-band[data-plot-state='ready']").wait_for(timeout=30_000)
    return editor


def _base_after_cap(cell_body: str) -> float:
    """A footer cell's Base PP after the cap, from its tooltip parts
    ("<raw> Base PP · <terrain>[ · the plot's <cap> cap takes <n>]")."""
    raw = re.match(r"([\d,]+) Base PP", cell_body)
    assert raw, cell_body
    taken = re.search(r"cap takes ([\d,]+)", cell_body)
    return float(raw.group(1).replace(",", "")) - (float(taken.group(1).replace(",", "")) if taken else 0.0)


def _page(live_browser: tuple[Browser, str]) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(base_url)
    # A fixture route can still be inside `route.fetch` when a test closes its page; that
    # fetch then fails after the close and Playwright reports it on the NEXT test's
    # `new_page` (TargetClosedError), failing a test that did nothing wrong. Closing drops
    # the routes first.
    close = page.close

    def close_after_routes(*args, **kwargs):
        page.unroute_all(behavior="ignoreErrors")
        return close(*args, **kwargs)

    page.close = close_after_routes
    return page


def test_r56_over_cap_badge_uses_raw_total(live_browser: tuple[Browser, str]) -> None:
    """R-56: the rendered CAP badge identifies a plot whose raw total exceeds its cap.

    Injection: revert the predicate to ``base_pp_after_cap > base_pp_cap``. The raw
    107641.176 / capped 100000.0 fixture then produces no badge, turning this red.
    """
    page = _page(live_browser)
    _install_over_cap_fixture(page)
    page.reload()
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    _open_badge_slideouts(page)
    badges = page.locator(".badge-over_cap")
    assert badges.count() == 1
    badge = page.locator(f"#badge-overlay .badge-overlay-row[data-deed-uid='{CASCADE_DEED_UID}'] .badge-over_cap")
    badge.wait_for(timeout=30_000)
    # the chip's tooltip lists the plot's own figures: the raw total against the cap
    assert "Base PP 107,641 · cap 100,000" in (badge.get_attribute("data-tip-items") or "")
    page.close()


def test_r66_runi_base_pp_is_outside_plot_cap(live_browser: tuple[Browser, str]) -> None:
    """R-66: a Runi's Base PP is additive and does not consume the plot cap.

    The synthetic fixture has a raw total of 101,000, including 1,234 Runi Base PP,
    but only 99,766 from non-Runi workers. Injection: revert the predicate to
    ``base_pp > base_pp_cap``; the fixture then incorrectly produces the badge.
    """
    page = _page(live_browser)
    projected = _install_runi_below_cap_fixture(page)
    assert projected["over_cap"] is False
    page.reload()
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    row = page.locator(f"tr.plot-row[data-deed-uid='{RUNI_DEED_UID}']")
    row.wait_for(timeout=30_000)
    row.click()
    page.locator(".slot-editor:visible").wait_for(timeout=30_000)
    _open_badge_slideouts(page)
    assert page.locator(f"#badge-overlay .badge-overlay-row[data-deed-uid='{RUNI_DEED_UID}'] .badge-over_cap").count() == 0
    page.close()


def test_g1_served_clipping_uses_each_workers_after_cap_value(live_browser: tuple[Browser, str]) -> None:
    """G1: served clipping is decided independently for every worker.

    Injection: replace the per-worker ``base_pp_after_cap < base_pp`` predicate with
    the plot-level ``over_cap`` flag. The named deed's real flag is false, so the
    clipped slot 4 and slot 5 assertions go red.
    """
    page = _page(live_browser)
    editor = _open_cascade_fixture(page)
    cells = editor.locator(".editor-base-pp-cell").all()
    assert [cell.inner_text() for cell in cells[:5]] == ["40,000", "32,941", "25,000", "2,059", "0"]
    assert all("is-cap-clipped" not in cells[index].get_attribute("class") for index in range(3))
    assert all("is-cap-clipped" in cells[index].get_attribute("class") for index in (3, 4))
    page.close()


def test_g2_clipped_cell_explains_its_base_pp_shortfall(live_browser: tuple[Browser, str]) -> None:
    """G2: a clipped cell shows the figure after the cap and explains the shortfall.

    The cell is named for what it shows now (Boostable PP: Base PP after the cap, with
    terrain); its tooltip names the card's own Base PP and what the plot cap takes.
    Injection: keep the old pre-clip figure in the cell. The displayed figure, key and
    accessible loss label then go red.
    """
    page = _page(live_browser)
    editor = _open_cascade_fixture(page)
    clipped = editor.locator(".editor-base-pp-cell").nth(3)
    assert clipped.get_attribute("aria-label") == "Boostable PP 2,059, reduced 5,441 by the plot cap"
    assert clipped.get_attribute("data-tip") == "Boostable PP"
    assert clipped.get_attribute("data-tip-key") == "2,059"
    assert clipped.get_attribute("data-tip-body") == "7,500 Base PP · neutral terrain · the plot's 100,000 cap takes 5,441. What this plot's boosts multiply."
    assert clipped.get_attribute("data-tip-foot") is None
    page.close()


def test_g5_capped_zero_is_filled_red_and_marked(live_browser: tuple[Browser, str]) -> None:
    """G5: a game-allocated capped zero remains a visible clipped figure.

    Injection: use a falsy check such as ``if (!afterCap)`` or ``afterCap || value``
    before rendering. The slot 5 zero becomes blank or the raw `2,200`, turning this red.
    """
    page = _page(live_browser)
    editor = _open_cascade_fixture(page)
    zero = editor.locator(".editor-base-pp-cell").nth(4)
    assert zero.inner_text() == "0"
    assert "is-filled" in zero.get_attribute("class")
    assert "is-cap-clipped" in zero.get_attribute("class")
    # the clipped/penalised mark is a small down triangle now ("\\25BC\\00A0", ceres.css)
    assert zero.locator("b").evaluate("node => getComputedStyle(node, '::before').content") == '"\u25bc\u00a0"'
    page.close()


def test_g11_staged_worker_changes_cap_figures_and_preserves_total(live_browser: tuple[Browser, str]) -> None:
    """G11: staging a worker over the cap changes a previously-rendered figure immediately.

    On ``41-9-56``, replace slot 4 with a 1,500 PP worker and assert that at least one
    existing figure changes and the rendered total equals ``min(sum(raw), cap)``.
    Injection: revert the derivation input to ``row.workers``. The staged card is absent
    from the effective allocation, so the figures stay unchanged and this guard goes red.
    """
    page = _page(live_browser)
    _install_cascade_fixture(page)
    candidate = _candidate(0.1)
    candidate.update(
        base_pp=1500,
        base_pp_delta={"status": "point", "value": 1500, "low": 1500, "high": 1500, "reason": None},
    )

    def candidate_route(route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"candidates": [candidate], "total": 1, "source": "display-truth-fixture", "excluded_counts": {}, "notices": []}),
        )

    page.route(f"**/api/plots/{CASCADE_DEED_UID}/candidates**", candidate_route)
    page.reload()
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    row = page.locator(f"tr.plot-row[data-deed-uid='{CASCADE_DEED_UID}']")
    row.wait_for(timeout=30_000)
    row.click()
    editor = page.locator(".slot-editor:visible")
    editor.wait_for(timeout=30_000)
    editor.locator(".editor-band[data-plot-state='ready']").wait_for(timeout=30_000)
    before = editor.locator(".editor-base-pp-cell").all_inner_texts()

    editor.locator(".slot-group[data-group-id='worker'] .slot-tile[data-slot-index='3']").click()
    editor.locator(".inline-picker:visible .candidate-row").first.wait_for(timeout=30_000)
    editor.locator(".inline-picker:visible .candidate-row").first.click()
    editor.locator(".slot-group[data-group-id='worker'] .slot-tile.is-staged").first.wait_for(timeout=30_000)
    after = editor.locator(".editor-base-pp-cell").all_inner_texts()

    assert any(previous != current for previous, current in zip(before, after))
    # the figures shown are Boostable (terrain applied); the cap acts on Base PP, which each
    # cell's tooltip states with what the cap takes
    bodies = editor.locator(".editor-base-pp-cell[data-group-id='worker'][data-tip-body]").evaluate_all(
        "els => els.map(el => el.getAttribute('data-tip-body'))"
    )
    rendered = [_base_after_cap(body) for body in bodies]
    raw = [40_000.0, 32_941.176, 25_000.0, 1_500.0, 2_200.0]
    assert len(rendered) == len(raw), bodies
    assert sum(rendered) == pytest.approx(min(sum(raw), 100_000.0), abs=2)
    page.close()


def test_g3_staged_worker_keeps_plot_terrain_in_tile_tooltip(live_browser: tuple[Browser, str]) -> None:
    """G3: a staged worker retains the plot terrain in its hover-only tile tooltip.

    The worker tooltip is a structured list now; its terrain line names the plot's terrain
    ("Forest: +10%").
    Injection: restore ``terrain_boost: null`` in ``mutateEditorSlot``. The staged tile
    then loses the fixture's ``+10%`` terrain line and this assertion goes red.
    """
    page = _page(live_browser)
    _install_plot_fixture(page, CASCADE_DEED_UID, _candidate(0.1))
    _open_fixture_picker(page, CASCADE_DEED_UID)
    page.locator(".inline-picker:visible .candidate-row").first.click()
    staged = page.locator(".slot-editor:visible .slot-group[data-group-id='worker'] .slot-tile.is-staged").first
    staged.wait_for(timeout=30_000)
    assert f"{recording.OVERCAP.terrain}: +10%" in (staged.get_attribute("data-tip-body") or "")
    page.close()


def test_g4_missing_terrain_is_named_and_never_faked_as_zero(live_browser: tuple[Browser, str]) -> None:
    """G4: a missing terrain value stays visibly not calculated, never a fake 0%.

    The metric's caption is the plain "Terrain" label now; the reason moved to its tooltip
    ("Terrain effect not reported").
    Injection: change the fixture candidate's missing ``terrain_boost`` to ``0.0``.
    The metric then incorrectly becomes ``0%`` and this assertion goes red.
    """
    page = _page(live_browser)
    _install_plot_fixture(page, CASCADE_DEED_UID, _candidate(None))
    _open_fixture_picker(page, CASCADE_DEED_UID)
    metric = page.locator(".inline-picker:visible .candidate-row").first.locator(".candidate-terrain")
    assert metric.locator("strong").inner_text() == "—"
    assert metric.locator("small").text_content() == "Terrain"
    assert metric.get_attribute("data-tip") == "Terrain effect not reported"
    assert (metric.get_attribute("data-tip-body") or "").startswith("No modifier is known for ")
    assert "0%" not in metric.inner_text()
    assert "isn't known" in (page.locator(".inline-picker:visible .candidate-row").first.get_attribute("aria-label") or "")
    page.close()


def test_g4b_neutral_candidate_is_zero_terrain_point_at_ceres_boundary() -> None:
    """G4b: Ceres normalizes a Neutral candidate to a real 0% terrain point.

    Injection: remove the Neutral assignment block after ``row.terrain`` is copied from
    the evaluation. The candidate returns unknown terrain/construction and this goes red.
    The synthetic fixture is the named Runi plot ``41-2-27`` / ``I-295-116f9bb372e311``;
    it is not used as an over-cap fixture.
    """
    report = {
        "plots": [{
            "deed_uid": RUNI_DEED_UID,
            "facts": {"plot": RUNI_PLOT, "rarity": "common", "status": "occupied", "resource": "GRAIN", "territory": "T"},
            "staking_facts": {
                "max_workers_allowed": 5, "total_base_pp_cap": 100000, "is_power_core_staked": True,
                "is_energized": False, "is_runi_staked": False, "deed_rarity_boost": 0,
                "totem_boost": 0, "title_boost": 0, "runi_boost": 0, "deed_status_token_boost": 0,
                "red_biome_modifier": 0, "blue_biome_modifier": 0, "white_biome_modifier": 0,
                "black_biome_modifier": 0, "green_biome_modifier": 0, "gold_biome_modifier": 0,
            },
        }],
        "workers": [],
        "cards": [{
            "asset_ref": "neutral-display-worker", "classification": {"bucket": "unstaked_apparent"},
            "facts": {"card_detail_id": 40001, "land_base_pp": 2400, "level": 1},
        }],
        "unassigned_items": {},
    }
    body = rank_candidates(
        report, RUNI_DEED_UID, "worker", player="player0005", now=datetime(2026, 9, 3, tzinfo=timezone.utc),
        bucket="idle", details_by_id={40001: {"id": 40001, "name": "Neutral Display Worker", "color": "Gray"}},
    )
    row = next(row for row in body["candidates"] if row["asset_ref"] == "neutral-display-worker")
    assert row["terrain_boost"] == pytest.approx(0.0)
    assert row["construction_pp"]["status"] == "point"
    assert row["construction_pp"]["value"] == pytest.approx(2400.0)


def test_g6_operation_review_uses_ops_disclosure_and_keeps_json(live_browser: tuple[Browser, str]) -> None:
    """G6: the operation-review disclosure has the operations class and still exposes JSON.

    Injection: restore any ``op-review-raw`` class or one of the four banned synonym
    strings at its named site. The class/word assertions go red while the disclosure
    remains a real browser interaction rather than a source-text check.
    """
    page = _page(live_browser)
    _install_plot_fixture(page, CASCADE_DEED_UID, _candidate(0.1))
    _open_fixture_picker(page, CASCADE_DEED_UID)
    page.locator(".inline-picker:visible .candidate-row").first.click()
    page.locator("#checkout-btn").wait_for(timeout=30_000)
    page.route(
        "**/api/op-preview*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"account": "player0005", "chunks": [{"operations": [["custom_json", {
                "required_auths": [], "required_posting_auths": ["player0005"], "id": "ceres",
                "json": json.dumps({"stake": {"cards": ["display-truth-worker"]}}),
            }]]}]}),
        ),
    )
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.locator("button[data-checkout-action='prepare']").click()
    review = panel.locator(".op-preview-review")
    review.wait_for(timeout=30_000)
    disclosure = review.locator("details.op-review-ops")
    assert disclosure.count() == 1
    disclosure.click()
    disclosure_json = disclosure.locator("pre").inner_text()
    assert '"custom_json"' in disclosure_json
    assert "display-truth-worker" in disclosure_json
    assert "op-review-raw" not in review.inner_html()
    assert "reported" not in review.inner_text().lower()
    assert "supplied" not in review.inner_text().lower()
    assert "returned" not in review.inner_text().lower()
    assert "provided" not in review.inner_text().lower()
    page.close()
