"""Browser coverage for the candidate-picker redesign (row 6c,  migration,
its three (a) tests -- census row 6c). Was a Node harness that sliced
`candidateEvaluation`, `renderCandidateRow`/`candidateEvaluation`, and the
`setIconOnlyHtml`/`editionDisplayLabel` block out of `app.js` by source position
and hand-stubbed their callees; its own docstring (removed here) already recorded
a prior stale-stub outage on this exact file (`ReferenceError: isGoldFoil is not
defined` after v0.5.81's foil chip). Retired per  and campaign §3.1 batch C
row 6c, replaced with route-mocked live worker-picker rows (`spawn_live_ceres`,
real plot fixtures by deed) whose rendered metrics/identity icons are read off
the real DOM.

`test_worker_controls_and_candidate_layout_contract_are_scoped` was, at this
ticket's HEAD, still a source/CSS-text-position scan despite campaign §3.1's
own "Converted ... plus the (b) halves of rows 1, 6c, 9, 22, 34" line implying
otherwise --  names it directly and instructs conversion to a
rendered layout read, resolving that plan/code mismatch explicitly rather than
leaving it open; done below. `test_module_invariants_remain_closed` (the
`ready(...)` after the last `function` structural guard) stays UNCHANGED --
campaign §3.1 names only that one as ruled KEPT, a split-safety guard on the
entry file, to retarget at M8 and then join `ALLOWED`.

Named injections (/, not performed here -- independent verification only):
1. `candidateEvaluation()` (app.js:4034): change `evaluation.status` validation
   list to drop `'unknown'`, so a missing-metadata evaluation falls through to a
   fabricated `{status: 'point', value: 0, ...}` shape instead of `unknown`.
   Expected red: `test_missing_construction_metadata_never_falls_back_to_raw_base`
   reads a fabricated `0` where it asserts the real `unknown`/`—` rendering.
2. `renderCandidateRow()` (app.js:4325): remove the `foilBadgeHtml(candidate, ...)`
   call from the identity span. Expected red:
   `test_worker_row_uses_identity_helpers_and_server_certainty` finds no
   `is-foil`/`>Gold<` chip on its gold-foil candidate.
3. `setIconOnlyHtml()` (app.js:887): change the generic-icon fallback path to
   reuse `MAIN_SET_ICON.alpha` unconditionally. Expected red:
   `test_unknown_edition_uses_neutral_local_icon_with_exact_label` sees
   `alpha.svg` instead of `edition-generic.svg` for an unrecognised card set.
"""

from __future__ import annotations

import copy
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres

ROOT = Path(__file__).parents[2]
APP_JS = ROOT / "ceres" / "static" / "app.js"


def _between(source: str, start: str, end: str) -> str:
    return source[source.index(start):source.index(end, source.index(start))]


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _real_board(base_url: str) -> dict:
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _real_detail_row(base_url: str) -> dict:
    board = _real_board(base_url)
    for plot in board["plots"]:
        if plot.get("assets_loaded") and (plot.get("capacity") or {}).get("worker_count", 0) >= 1:
            deed_uid = plot["deed_uid"]
            url = f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}"
            with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
    raise AssertionError("no live plot with a real worker to build a detail-row template from")


def _open_worker_picker(page, base_url: str, row: dict, candidates: list[dict]):
    deed_uid = row["deed_uid"]
    page.route(
        re.compile(rf"/api/plots/{re.escape(urllib.parse.quote(deed_uid, safe=''))}(/expand)?(\?|$)"),  # GET detail or POST expand
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(row)),
    )
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"candidates": candidates, "total": len(candidates), "source": "test-fixture", "excluded_counts": {}, "notices": []},
        )),
    )
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    empty_slot = page.locator(".slot-editor .slot-group[data-group-id='worker'] .slot-tile[data-slot-action='add']").first
    empty_slot.wait_for(timeout=30_000)
    empty_slot.click()
    page.locator(".candidate-row").first.wait_for(timeout=30_000)


def test_missing_construction_metadata_never_falls_back_to_raw_base(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    row["workers"] = [w for w in row["workers"] if int(w.get("slot") or 0) != 1]
    missing = {
        "name": "Missing Metadata Worker", "asset_ref": "missing-meta", "level": 1, "base_pp": 11000,
        "terrain_boost": 0, "splinter": "Fire", "rarity": "Common", "card_set": "chaos", "edition": 3,
        "construction_pp": None,
        "plot_impact": {"status": "unknown", "value": None, "low": None, "high": None, "reason": "missing_metadata"},
        "net_gain": None, "net_gain_breakdown": {},
    }
    point = {
        "name": "Point Metadata Worker", "asset_ref": "point-meta", "level": 1, "base_pp": 11000,
        "terrain_boost": 0, "splinter": "Fire", "rarity": "Common", "card_set": "chaos", "edition": 3,
        "construction_pp": {"status": "point", "value": 5500, "low": 5500, "high": 5500, "reason": None},
        "plot_impact": {"status": "point", "value": 100, "low": 100, "high": 100, "reason": None},
        "net_gain": 100, "net_gain_breakdown": {},
    }
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _open_worker_picker(page, base_url, row, [missing, point])

    missing_row = page.locator(".candidate-row", has_text="Missing Metadata Worker").first
    missing_row.wait_for(timeout=30_000)
    missing_html = missing_row.evaluate("(el) => el.outerHTML")
    # A missing construction_pp must render the real "unknown" state, never a
    # fabricated number derived from base_pp (11000) -- and the row must not be
    # classified as a gain/loss off an invented plot-impact figure either.
    assert "<strong>—</strong>" in missing_html or "candidate-construction\"><strong>—" in missing_html
    assert "11000" not in _between(missing_html, "candidate-construction", "candidate-terrain")
    assert "Not calculated" in missing_html
    assert "is-unknown" in missing_html
    assert "is-gain" not in missing_html and "is-loss" not in missing_html

    point_row = page.locator(".candidate-row", has_text="Point Metadata Worker").first
    point_row.wait_for(timeout=30_000)
    point_html = point_row.evaluate("(el) => el.outerHTML")
    assert "5,500" in _between(point_html, "candidate-construction", "candidate-terrain")
    page.close()


def test_worker_row_uses_identity_helpers_and_server_certainty(live_browser: tuple[Browser, str]) -> None:
    """Broke on v0.5.81's foil chip addition to the worker row (it now calls
    `isGoldFoil`/`fmtFoil`) with a bare `ReferenceError: isGoldFoil is not
    defined` under the old Node harness -- the harness's stub set was never
    extended for the new call. A real browser render has no stub set to fall
    behind: whatever `renderCandidateRow` actually calls, actually runs."""
    browser, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    row["workers"] = [w for w in row["workers"] if int(w.get("slot") or 0) != 1]
    base = {
        "name": "Zintar Mortalis", "asset_ref": "zintar", "skin": "splinterstorm", "level": 4, "base_pp": 11000, "terrain_boost": -0.5,
        "splinter": "Death", "rarity": "Legendary", "card_set": "beta", "edition": 3,
        "construction_pp": {"status": "point", "value": 5500, "low": 5500, "high": 5500, "reason": None},
        "plot_impact": {"status": "point", "value": -141382, "low": -141382, "high": -141382, "reason": None},
        "net_gain_breakdown": {"own_harvest_delta": 44000, "evicted_loss": -18000, "plot_residual_delta": -6500, "donor_plot_delta": -4000},
        "net_gain": -141382,
    }
    uncertain = {
        **base, "name": "Uncertain Worker", "asset_ref": "uncertain",
        "construction_pp": {"status": "unknown", "value": None, "low": None, "high": None, "reason": "missing_metadata"},
        "plot_impact": {"status": "range", "value": None, "low": -200, "high": 100, "reason": "missing_metadata"},
        "net_gain": None,
    }
    gold = {**base, "name": "Gold Worker", "asset_ref": "gold-worker", "foil": 1}
    regular = {**base, "name": "Regular Worker", "asset_ref": "regular-worker", "foil": 0}

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _open_worker_picker(page, base_url, row, [base, uncertain, gold, regular])

    base_row = page.locator(".candidate-row", has_text="Zintar Mortalis").first
    base_row.wait_for(timeout=30_000)
    base_html = base_row.evaluate("(el) => el.outerHTML")
    assert "splinter/death.svg" in base_html
    assert "rarity/gem-legendary.svg" in base_html
    assert "5,500" in base_html
    assert "candidate-base" in base_html and "<small>Base PP</small>" in base_html and "<strong>11,000</strong>" in base_html
    assert "−50%" in base_html and "penalty" in base_html
    assert "Incoming worker change" in base_html and "Current worker removed" in base_html
    assert "Other workers on this plot" in base_html and "Source plot after move" in base_html
    assert "is-loss" in base_html
    assert "candidate-art" in base_html and "splinterstorm" in base_html and "Zintar" in base_html

    uncertain_row = page.locator(".candidate-row", has_text="Uncertain Worker").first
    uncertain_row.wait_for(timeout=30_000)
    uncertain_html = uncertain_row.evaluate("(el) => el.outerHTML")
    assert "is-range" in uncertain_html
    assert "−200" in uncertain_html or "-200" in uncertain_html or "200" in uncertain_html
    assert "missing metadata" in uncertain_html

    gold_html = page.locator(".candidate-row", has_text="Gold Worker").first.evaluate("(el) => el.outerHTML")
    assert "is-foil" in gold_html and ">Gold<" in gold_html

    regular_html = page.locator(".candidate-row", has_text="Regular Worker").first.evaluate("(el) => el.outerHTML")
    assert "is-foil" not in regular_html
    page.close()


def test_unknown_edition_uses_neutral_local_icon_with_exact_label(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    row["workers"] = [w for w in row["workers"] if int(w.get("slot") or 0) != 1]

    def _candidate(name: str, ref: str, card_set: str | None, edition) -> dict:
        return {
            "name": name, "asset_ref": ref, "level": 1, "base_pp": 1000, "terrain_boost": 0,
            "splinter": "Fire", "rarity": "Common", "card_set": card_set, "edition": edition,
            "construction_pp": {"status": "point", "value": 1000, "low": 1000, "high": 1000, "reason": None},
            "plot_impact": {"status": "point", "value": 0, "low": 0, "high": 0, "reason": None},
            "net_gain": 0, "net_gain_breakdown": {},
        }

    candidates = [
        _candidate("Nightmare Edition Worker", "nightmare-9", "other", 9),
        _candidate("Future Edition Worker", "future-99", "other", 99),
        _candidate("Alpha Edition Worker", "alpha-2", "alpha", 2),
        _candidate("Beta Edition Worker", "beta-3", "beta", 3),
        _candidate("Conclave Edition Worker", "conclave-17", "conclave", 17),
        # No card_set: exercises editionDisplayLabel(3) === 'Reward' directly via
        # the identity icon's tooltip (setNameLabel falls to MAIN_SET_LABEL[3]).
        _candidate("Reward Edition Worker", "reward-3", None, 3),
    ]
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _open_worker_picker(page, base_url, row, candidates)

    def icon_of(name: str):
        row_locator = page.locator(".candidate-row", has_text=name).first
        row_locator.wait_for(timeout=30_000)
        return row_locator.locator(".candidate-identity img.set-icon").first

    nightmare = icon_of("Nightmare Edition Worker")
    assert "edition-generic.svg" in (nightmare.get_attribute("src") or "")
    # Identity tooltips are titled "Set: <label>" (icons.js setIconOnlyHtml, the shared card-tip convention).
    assert (nightmare.get_attribute("data-tip") or "") == "Set: Nightmare"

    future = icon_of("Future Edition Worker")
    assert "edition-generic.svg" in (future.get_attribute("src") or "")
    # The set label falls back to the row's own card_set ("other"); the exact unknown-edition
    # label is the identity's edition text (editionDisplayLabel), never an invented name.
    assert (future.get_attribute("data-tip") or "") == "Set: Other"
    future_identity = page.locator(".candidate-row", has_text="Future Edition Worker").first.locator(".candidate-identity")
    assert "Unknown edition (ID 99)" in future_identity.inner_text()

    assert "/alpha.svg" in (icon_of("Alpha Edition Worker").get_attribute("src") or "")
    assert "/beta.svg" in (icon_of("Beta Edition Worker").get_attribute("src") or "")
    assert "/conclave-arcana.svg" in (icon_of("Conclave Edition Worker").get_attribute("src") or "")

    reward = icon_of("Reward Edition Worker")
    assert (reward.get_attribute("data-tip") or "") == "Set: Reward"
    page.close()


def test_worker_controls_and_candidate_layout_contract_are_scoped(live_browser: tuple[Browser, str]) -> None:
    """ (row 6c): replaces the source/CSS-text-position scan
    (`_between()`-slicing `renderInlineCandidatePicker`'s source, then
    grepping `ceres.css` for six unrelated selector strings) with a rendered
    read of a REAL worker picker -- the sort chips, filter controls, and
    legend's absence read off the actual DOM; the sticky/container-query/
    is-loss/no-scroll layout claims read off ACTUAL computed style and
    ACTUAL responsive behaviour (resizing the real viewport across the
    54rem container-query threshold), not the CSS source text that merely
    claims them."""
    browser, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    row["workers"] = [w for w in row["workers"] if int(w.get("slot") or 0) != 1]
    loss = {
        "name": "Loss Worker", "asset_ref": "loss-worker", "level": 1, "base_pp": 11000, "terrain_boost": 0,
        "splinter": "Fire", "rarity": "Common", "card_set": "chaos", "edition": 3,
        "construction_pp": {"status": "point", "value": 5500, "low": 5500, "high": 5500, "reason": None},
        "plot_impact": {"status": "point", "value": -141382, "low": -141382, "high": -141382, "reason": None},
        "net_gain": -141382, "net_gain_breakdown": {},
    }
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    _open_worker_picker(page, base_url, row, [loss])

    # Sort chips: real buttons, real order, real labels -- not a source
    # substring naming the array literal that builds them.
    chips = page.locator(".picker-sort-chips button[data-picker-action='sort']")
    assert chips.count() == 2
    # The construction_pp chip is labelled "Production" (candidates.js sort chips; tip "Sort by Production").
    assert chips.nth(0).inner_text() == "Production"
    assert chips.nth(0).get_attribute("data-sort") == "construction_pp"
    assert chips.nth(1).inner_text() == "Name"
    assert chips.nth(1).get_attribute("data-sort") == "name"

    # Worker-group filter controls, read off the real rendered elements.
    edition_select = page.locator("select[data-picker-filter='edition']")
    assert edition_select.locator("option").first.get_attribute("value") == ""
    assert edition_select.locator("option").first.inner_text() == "Any edition"
    assert page.locator("input[data-picker-filter='ready_now']").count() == 1
    assert page.locator("input[data-picker-filter='bloodline_pairs']").count() == 1

    # The legend was removed on request -- must not exist anywhere in the
    # rendered picker (was a source-text absence check; now a real DOM count).
    assert page.locator(".candidate-legend").count() == 0
    # Worker cards caption every metric themselves -- the worker picker has no column header
    # row (candidates.js renders `.candidate-columns` for Totem/Title lists only); the plot's
    # terrain affinity, which used to sit in that header, is the filter row's splinter strip.
    assert page.locator(".candidate-columns").count() == 0
    worker_affinity = page.locator(".picker-bar-filters .candidate-worker-affinity [data-terrain-affinity]").first
    assert worker_affinity.is_visible()

    slice_el = page.locator(".candidate-visible-slice")
    assert slice_el.count() == 1
    computed = slice_el.evaluate(
        "(el) => { const s = getComputedStyle(el); "
        "return {position: s.position, left: s.left, containerType: s.getPropertyValue('container-type')}; }"
    )
    assert computed["position"] == "sticky"
    assert computed["left"] == "0px"
    assert computed["containerType"] == "inline-size", (
        "the picker column header must hide via a real container query, not a viewport media query"
    )

    # (The old 700px step checked how the worker column header collapsed below the
    # container-query threshold; that header no longer exists for worker pickers, and the
    # visible slice's reflow is covered by test_live_candidate_layout.)

    # An is-loss row is marked in the real --danger token, at full opacity (never faded)
    # read off the actual rendered row. The candidate cards no longer carry a coloured
    # left border; the loss is the row's Total PP change figure (ceres.css
    # `.candidate-plot-change > strong.is-negative`).
    loss_row = page.locator(".candidate-row", has_text="Loss Worker").first
    loss_row.wait_for(timeout=30_000)
    assert "is-loss" in (loss_row.get_attribute("class") or "")
    row_style = loss_row.evaluate(
        "(el) => { const s = getComputedStyle(el); const v = el.querySelector('.candidate-plot-change > strong'); "
        "return {lossColor: v && getComputedStyle(v).color, opacity: s.opacity}; }"
    )
    expected_danger = page.evaluate(
        "() => { const p = document.createElement('div'); p.style.color = 'var(--danger)'; "
        "document.body.appendChild(p); const c = getComputedStyle(p).color; document.body.removeChild(p); return c; }"
    )
    assert row_style["lossColor"] == expected_danger, "an is-loss row's plot change must resolve to the real --danger token"
    assert row_style["opacity"] == "1", "is-loss must never render at reduced opacity"

    # No horizontal scrollbar anywhere inside the picker's visible slice
    # the real absence of an `overflow-x` rule in that region of the
    # stylesheet, proved by measuring actual scroll geometry rather than
    # scanning the CSS source text between two selectors.
    overflow = slice_el.evaluate("(el) => el.scrollWidth - el.clientWidth")
    assert overflow <= 1, f"the candidate picker's visible slice scrolls horizontally by {overflow}px"
    page.close()


def test_module_invariants_remain_closed() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert source.count("-Number(occupant.current_harvest_pp || 0)") == 0
    assert source.rstrip().endswith("});")
    assert source.rfind("ready(() => {") > source.rfind("function ")
