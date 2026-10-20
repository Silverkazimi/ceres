"""Observable guards for exact foil identity and combine affordances.

 (census row 11): the pure-arithmetic guards (group-key
identity, CDN edition mapping, foil-name coverage) load the REAL, whole,
unsliced module through the shared `tests/ceres/vm_context.py` sandbox --
they have no rendered surface. The two exclusion guards (Black/Arcane foil,
maxed printings) DO have a rendered surface (the worker-table tile-combine-chip
and the candidate-picker's combine button) and are converted to route-mocked
live-browser tests per the campaign plan (census row 11: "browser test: stage
foiled/maxed cards, read the rendered worker-table/candidate-list combine
affordances"), reusing `test_tile_combine_chip.py`'s idle-cards mock pattern
and `test_bloodline_filter.py`'s real-candidate-template mock pattern -- both
already-sanctioned precedents for wiring synthetic collection data through a
real board.

The four `_slice()`-based markers this file used to rely on
(`renderEditorGroup`, `candidateQuery`) had already drifted stale (current
signature gained `powerGroup`/`columnDescriptors` params after this file was
written) -- exactly the Class-B failure the campaign method exists to retire,
not merely re-patch.

Mutation-tested per the internal rules's binding standard: each guard has had its own
defect injected, watched red, and the injection is recorded in the guard's
own docstring.
"""

from __future__ import annotations

import copy
import json
import subprocess
import urllib.parse
import urllib.request

import pytest
from playwright.sync_api import Browser, Page

from .conftest import LIVE_PLAYER, spawn_live_ceres
from .vm_context import ENTRY, read_entry_source, run_in_module_context

APP_JS = ENTRY


# Pure guards -- no rendered surface -- via the shared whole-module sandbox.

_DISTINCT_GROUP_KEYS_JS = r"""
const cards = [
 {uid: 'gold', card_detail_id: 7, edition: 14, foil: 1, bcx: 1, bcx_max: 10},
 {uid: 'gold-arcane', card_detail_id: 7, edition: 14, foil: 2, bcx: 1, bcx_max: 10},
 {uid: 'regular', card_detail_id: 7, edition: 14, foil: 0, bcx: 1, bcx_max: 10},
];
const groups = combineGroupsFrom(cards);
if (groups.size !== 3) throw new Error(`foil groups collapsed: ${[...groups.keys()]} (exit 1)`);
for (const foil of [0, 1, 2]) {
 const group = groups.get(`7|14|${foil}`);
 if (!group || group.length !== 1 || group[0].foil !== foil) throw new Error(`group for foil ${foil} malformed (exit 2)`);
}
"""


def test_distinct_foil_values_remain_distinct_group_keys() -> None:
    run_in_module_context(_DISTINCT_GROUP_KEYS_JS, entry=ENTRY)


def test_distinct_foil_group_guard_mutation_is_red() -> None:
    """Injected defect: restore truthiness foil grouping (`foil ? 1 : 0`); the observable group contents must fail."""
    source = read_entry_source(ENTRY)
    mutated = source.replace(
        "`${c.card_detail_id}|${c.edition}|${cardFoilValue(c)}`",
        "`${c.card_detail_id}|${c.edition}|${c.foil ? 1 : 0}`",
        1,
    )
    assert mutated != source, "combineGroupKey foil-truthiness mutation target is missing"
    check_js = r"""
const groups = combineGroupsFrom([
 {uid: 'one', card_detail_id: 7, edition: 14, foil: 1, bcx: 1, bcx_max: 10},
 {uid: 'two', card_detail_id: 7, edition: 14, foil: 2, bcx: 1, bcx_max: 10},
]);
if (groups.size !== 2 || groups.get('7|14|1')?.length !== 1 || groups.get('7|14|2')?.length !== 1) {
 throw new Error(`foil groups collapsed: ${[...groups.entries()]} (exit 1)`);
}
"""
    with pytest.raises(subprocess.CalledProcessError):
        run_in_module_context(check_js, entry=ENTRY, source=mutated)


_CDN_MAPPING_JS = r"""
const expected = {
 12: 'cards_rebellion', 13: 'cards_soulbound', 15: 'cards_v2.2', 18: 'cards_v2.2',
};
for (const [edition, folder] of Object.entries(expected)) {
 const url = editorCardImage({card_name: 'Synthetic Card', edition: Number(edition), foil: 0});
 if (url !== `https://d36mxiodymuqjm.cloudfront.net/${folder}/Synthetic%20Card.jpg`) {
 throw new Error(`edition ${edition} resolved to ${url} (exit 20)`);
 }
}
"""


def test_known_modern_editions_resolve_card_art_urls() -> None:
    run_in_module_context(_CDN_MAPPING_JS, entry=ENTRY)


def test_known_cdn_mapping_mutation_is_red() -> None:
    """Injected defect: remove the edition-13 CDN mapping; the rendered resolver must stop returning its card-art URL."""
    source = read_entry_source(ENTRY)
    mutated = source.replace("CARD_CDN_SET[13] = 'cards_soulbound';\n", "", 1)
    assert mutated != source, "CARD_CDN_SET[13] mutation target is missing"
    check_js = r"""
const url = editorCardImage({card_name: 'Synthetic Card', edition: 13, foil: 0});
if (url !== 'https://d36mxiodymuqjm.cloudfront.net/cards_soulbound/Synthetic%20Card.jpg') {
 throw new Error(`known edition mapping returned no card-art URL: ${url} (exit 1)`);
}
"""
    with pytest.raises(subprocess.CalledProcessError):
        run_in_module_context(check_js, entry=ENTRY, source=mutated)


_FOIL_NAMES_JS = r"""
const expected = ['Regular', 'Gold', 'Gold Arcane', 'Black Foil', 'Black Foil Arcane'];
for (let foil = 0; foil < expected.length; foil += 1) {
 if (fmtFoil({foil}) !== expected[foil]) throw new Error(`foil ${foil} formatted as ${fmtFoil({foil})} (exit 30)`);
}
"""


def test_foil_names_cover_all_supported_variants() -> None:
    run_in_module_context(_FOIL_NAMES_JS, entry=ENTRY)


# Rendered-surface guards: live browser, route-mocked idle-cards/candidates.


def _get(base_url: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=30) as response:
        return json.loads(response.read().decode())


def _staked_worker(base_url: str) -> tuple[dict, dict]:
    """A real plot detail payload plus one of its staked worker occupants
 (card_detail_id/edition present, so combineGroupKey can group it)."""
    board = _get(base_url, "/api/board")
    for plot in board["plots"]:
        if not plot.get("assets_loaded"):
            continue
        deed_uid = plot["deed_uid"]
        detail = _get(base_url, f"api/plots/{urllib.parse.quote(deed_uid, safe='')}")
        for worker in detail.get("workers") or []:
            if worker.get("card_detail_id") is not None and worker.get("edition") is not None and isinstance(worker.get("slot"), int) and worker["slot"] >= 1:
                return detail, worker
    raise AssertionError("no live plot has a staked worker with card_detail_id/edition to build a combine fixture from")


def _open_slot_candidate_template(base_url: str) -> tuple[str, dict]:
    """A real candidate row payload from a plot with an open worker slot, plus
 that plot's deed_uid -- the exact shape `renderCandidateRow` expects,
 mutated below rather than hand-built."""
    board = _get(base_url, "/api/board")
    for plot in board["plots"]:
        if (plot.get("capacity") or {}).get("open_slots", 0) > 0:
            deed_uid = plot["deed_uid"]
            params = urllib.parse.urlencode({"group_id": "worker", "limit": 5, "offset": 0, "sort": "construction_pp", "bucket": "all", "preset": "net_gain_default"})
            payload = _get(base_url, f"api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates?{params}")
            if payload.get("candidates"):
                return deed_uid, copy.deepcopy(payload["candidates"][0])
    raise AssertionError("no live plot with an open worker slot returned a candidate to build a fixture from")


def _mock_plot_detail(page: Page, deed_uid: str, detail: dict) -> None:
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}?*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(detail)),
    )


def _mock_idle_cards(page: Page, copies: list[dict]) -> None:
    page.route(
        "**/api/idle-cards*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"cards": copies})),
    )


def _mock_candidates(page: Page, deed_uid: str, candidate: dict) -> None:
    # NOTE: Playwright always invokes a route handler as `handler(route, request)`
    # (`_impl_to_api_mapping.py`'s `wrap_handler` truncates to the handler's own
    # parameter COUNT, not by name) -- a `lambda route, candidate=candidate: ...`
    # default-arg closure here would silently bind `candidate` to the `request`
    # object instead, which is why this is a real function capturing `candidate`
    # by closure rather than by a second parameter.
    def fulfill(route) -> None:
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"candidates": [candidate], "total": 1, "source": "test-fixture", "excluded_counts": {}, "notices": []}),
        )

    page.route(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*", fulfill)


def _assert_no_worker_table_chip(page: Page, base_url: str, deed_uid: str) -> None:
    page.goto(f"{base_url}/?player={LIVE_PLAYER}")
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}'] + tr.expand-row .slot-editor").wait_for(timeout=30_000)
    assert page.locator(f".tile-combine-chip[data-deed-uid='{deed_uid}']").count() == 0, (
        "a forbidden combine chip rendered on the worker-table surface"
    )


def _assert_no_candidate_combine_affordance(page: Page, base_url: str, deed_uid: str, candidate_uid: str) -> None:
    # renderCandidateRow carries no `data-candidate-uid` attribute (only
    # `data-candidate-index`); the mocked candidates response above supplies
    # exactly one candidate (this one), so its row is unambiguous by index 0.
    page.goto(f"{base_url}/?player={LIVE_PLAYER}")
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    empty = page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}'] + tr.expand-row .slot-tile.is-empty").first
    empty.wait_for(timeout=30_000)
    with page.expect_response(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*", timeout=30_000):
        empty.click()
    row = page.locator(".candidate-row[data-candidate-index='0']")
    row.wait_for(timeout=30_000)
    assert row.locator(".picker-combine").count() == 0, "a forbidden Combine button rendered in the candidate list"
    assert row.locator(".candidate-upgrade").count() == 0, "a forbidden combinable-copies chip rendered in the candidate list"


def _black_arcane_fixture(base_url: str, foil: int) -> tuple[dict, dict, str, dict]:
    """(worker_detail with occupant.foil forced to `foil`, idle duplicate list of
 two matching copies, open-slot deed_uid, candidate matching one duplicate's uid)."""
    detail, worker = _staked_worker(base_url)
    worker = dict(worker, foil=foil, bcx=1, bcx_max=10)
    detail = copy.deepcopy(detail)
    for entry in detail["workers"]:
        if entry.get("card_detail_id") == worker["card_detail_id"] and entry.get("slot") == worker["slot"]:
            entry["foil"] = foil
            entry["bcx"], entry["bcx_max"] = 1, 10
    duplicates = [
        {
            "uid": f"MOCK-{'ARCANE' if foil == 4 else 'BLACK'}-{i}", "card_detail_id": worker["card_detail_id"],
            "edition": worker["edition"], "foil": foil, "bcx": 1, "bcx_max": 10, "available": True,
            "name": worker.get("name", "Mock Card"),
        }
        for i in range(2)
    ]
    candidate_deed, candidate = _open_slot_candidate_template(base_url)
    candidate = dict(candidate, uid=duplicates[0]["uid"], card_detail_id=worker["card_detail_id"], edition=worker["edition"], foil=foil, bcx=1, bcx_max=10)
    return detail, duplicates, candidate_deed, candidate


def test_black_and_arcane_are_absent_from_all_combine_surfaces() -> None:
    with spawn_live_ceres() as (browser, base_url):
        for foil in (3, 4):
            detail, duplicates, candidate_deed, candidate = _black_arcane_fixture(base_url, foil)
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            _mock_plot_detail(page, detail["deed_uid"], detail)
            _mock_idle_cards(page, duplicates)
            _assert_no_worker_table_chip(page, base_url, detail["deed_uid"])
            page.close()

            page = browser.new_page(viewport={"width": 1400, "height": 900})
            _mock_idle_cards(page, duplicates)
            _mock_candidates(page, candidate_deed, candidate)
            _assert_no_candidate_combine_affordance(page, base_url, candidate_deed, candidate["uid"])
            page.close()


def test_black_and_arcane_exclusion_mutation_is_red() -> None:
    """Injected defect: drop the shared Black/Arcane exclusion (`isNonCombinableFoil`
 always returns false); the rendered worker-table chip must then appear where
 the guard above requires it absent."""
    with spawn_live_ceres() as (browser, base_url):
        detail, duplicates, _candidate_deed, _candidate = _black_arcane_fixture(base_url, 3)
        real_source = read_entry_source()
        mutated_source = real_source.replace("return ['3', '4'].includes(cardFoilValue(card));", "return false;", 1)
        assert mutated_source != real_source, "isNonCombinableFoil mutation target is missing"

        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.route(
            "**/static/app.js*",
            lambda route: route.fulfill(status=200, content_type="application/javascript", body=mutated_source),
        )
        _mock_plot_detail(page, detail["deed_uid"], detail)
        _mock_idle_cards(page, duplicates)
        with pytest.raises(AssertionError):
            _assert_no_worker_table_chip(page, base_url, detail["deed_uid"])
        page.close()


def _maxed_fixture(base_url: str) -> tuple[dict, dict, str, dict]:
    detail, worker = _staked_worker(base_url)
    detail = copy.deepcopy(detail)
    for entry in detail["workers"]:
        if entry.get("card_detail_id") == worker["card_detail_id"] and entry.get("slot") == worker["slot"]:
            entry["bcx"], entry["bcx_max"] = 1, 1  # already at the ceiling: maxed
            entry["foil"] = worker.get("foil", 0)
    duplicates = [
        {
            "uid": f"MOCK-MAXED-{i}", "card_detail_id": worker["card_detail_id"], "edition": worker["edition"],
            "foil": worker.get("foil", 0), "bcx": 1, "bcx_max": 1, "available": True,
            "name": worker.get("name", "Mock Card"),
        }
        for i in range(2)
    ]
    candidate_deed, candidate = _open_slot_candidate_template(base_url)
    candidate = dict(candidate, uid=duplicates[0]["uid"], card_detail_id=worker["card_detail_id"], edition=worker["edition"], foil=worker.get("foil", 0), bcx=1, bcx_max=1)
    return detail, duplicates, candidate_deed, candidate


def test_maxed_cards_are_absent_from_all_combine_surfaces() -> None:
    with spawn_live_ceres() as (browser, base_url):
        detail, duplicates, candidate_deed, candidate = _maxed_fixture(base_url)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        _mock_plot_detail(page, detail["deed_uid"], detail)
        _mock_idle_cards(page, duplicates)
        _assert_no_worker_table_chip(page, base_url, detail["deed_uid"])
        page.close()

        page = browser.new_page(viewport={"width": 1400, "height": 900})
        _mock_idle_cards(page, duplicates)
        _mock_candidates(page, candidate_deed, candidate)
        _assert_no_candidate_combine_affordance(page, base_url, candidate_deed, candidate["uid"])
        page.close()


def test_maxed_exclusion_mutation_is_red() -> None:
    """Injected defect: remove the maxed-card result from `isCardMaxed`; rendered combine affordances must fail."""
    with spawn_live_ceres() as (browser, base_url):
        detail, duplicates, _candidate_deed, _candidate = _maxed_fixture(base_url)
        real_source = read_entry_source()
        mutated_source = real_source.replace("return c.bcx_max != null && c.bcx != null && c.bcx >= c.bcx_max;", "return false;", 1)
        assert mutated_source != real_source, "isCardMaxed mutation target is missing"

        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.route(
            "**/static/app.js*",
            lambda route: route.fulfill(status=200, content_type="application/javascript", body=mutated_source),
        )
        _mock_plot_detail(page, detail["deed_uid"], detail)
        _mock_idle_cards(page, duplicates)
        with pytest.raises(AssertionError):
            _assert_no_worker_table_chip(page, base_url, detail["deed_uid"])
        page.close()
