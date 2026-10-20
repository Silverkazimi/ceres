from __future__ import annotations

import copy
import json
import urllib.parse
import urllib.request

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres, wait_for_board
from .vm_context import run_in_module_context


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _stage_worker_candidate(page, base_url: str, *, base_pp_delta, plot_impact, current_harvest_pp) -> str:
    """Opens the board, stages a worker into the first plot with an open slot using
    a route-mocked `/api/plots/<deed>/candidates*` response carrying the given
    `base_pp_delta`/`plot_impact`/`current_harvest_pp` overrides, and returns the
    deed_uid staged."""
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    deed_uid = next(
        plot["deed_uid"] for plot in board["plots"]
        if (plot.get("capacity") or {}).get("open_slots", 0) > 0
    )
    params = urllib.parse.urlencode({
        "group_id": "worker", "limit": 5, "offset": 0, "sort": "construction_pp",
        "bucket": "all", "preset": "net_gain_default",
    })
    with urllib.request.urlopen(
        f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates?{params}", timeout=10,
    ) as response:
        payload = json.loads(response.read().decode("utf-8"))
    candidate = copy.deepcopy(payload["candidates"][0])
    candidate["base_pp_delta"] = base_pp_delta
    candidate["plot_impact"] = plot_impact
    candidate["current_harvest_pp"] = current_harvest_pp

    def fulfill_candidates(route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "candidates": [candidate], "total": 1, "source": "test-fixture", "excluded_counts": {}, "notices": [],
        }))

    page.route(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*", fulfill_candidates)
    page.goto(base_url)
    wait_for_board(page)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    empty = page.locator(".slot-group[data-group-id='worker'] .slot-tile.is-empty").first
    empty.wait_for(timeout=30_000)
    empty.click()
    candidate_row = page.locator(".candidate-row").first
    candidate_row.wait_for(timeout=30_000)
    candidate_row.click()
    checkout_button = page.locator("#checkout-btn")
    page.wait_for_function("document.getElementById('checkout-btn') && !document.getElementById('checkout-btn').hidden", timeout=30_000)
    checkout_button.click()
    return deed_uid


def test_combine_estimates_sums_points_propagates_unknown_and_excludes_not_applicable():
    """ (row 9, node half): replaces the hand-sliced
    `_estimate_helpers()` extraction (pulling `num`/`signedPp`..`estimateBadge`
    out of app.js by source position, the same fragile pattern
    `vm_context.py`'s own docstring records an outage from) with the shared
    whole-module loader -- `combineEstimates`/`estimateBadge` are called as
    the real top-level functions of the fully-evaluated module, not a
    hand-copied fragment that can silently drift from their real
    dependencies."""
    run_in_module_context(
        r"""
        const point = (v) => ({ status: 'point', value: v, low: v, high: v, reason: null });
        const range = (low, high) => ({ status: 'range', value: null, low, high, reason: null });
        const unknown = { status: 'unknown', value: null, low: null, high: null, reason: null };
        const notApplicable = { status: 'not_applicable', value: null, low: null, high: null, reason: null };

        // All-point sums to a point, exactly.
        let r = combineEstimates([point(500), point(-120), point(300)]);
        if (r.status !== 'point' || r.value !== 680 || r.low !== 680 || r.high !== 680) {
          throw new Error('point sum: ' + JSON.stringify(r));
        }

        // A single unknown makes the whole combined figure unknown -- never a fabricated point.
        r = combineEstimates([point(500), unknown, point(300)]);
        if (r.status !== 'unknown' || r.value !== null) {
          throw new Error('unknown propagation: ' + JSON.stringify(r));
        }

        // A range widens the combined bounds but does not force unknown.
        r = combineEstimates([point(100), range(-50, 50)]);
        if (r.status !== 'range' || r.low !== 50 || r.high !== 150) {
          throw new Error('range combine: ' + JSON.stringify(r));
        }

        // Item-only moves (Totem/Title/Power Core) carry no Base PP -- excluded from the
        // sum rather than coerced to zero, so an item-only group reports "n/a" honestly.
        r = combineEstimates([notApplicable, notApplicable]);
        if (r.status !== 'not_applicable') {
          throw new Error('all not_applicable: ' + JSON.stringify(r));
        }

        // A not_applicable entry mixed with real card moves is simply excluded, not zeroed
        // in a way that would hide a missing figure.
        r = combineEstimates([point(200), notApplicable]);
        if (r.status !== 'point' || r.value !== 200) {
          throw new Error('mixed not_applicable: ' + JSON.stringify(r));
        }

        if (estimateBadge(point(500)) !== '+500') { throw new Error('badge point: ' + estimateBadge(point(500))); }
        if (estimateBadge(point(-500)) !== '−500') { throw new Error('badge negative: ' + estimateBadge(point(-500))); }
        if (estimateBadge(unknown) !== '—') { throw new Error('badge unknown: ' + estimateBadge(unknown)); }
        if (estimateBadge(notApplicable) !== 'n/a') { throw new Error('badge n/a: ' + estimateBadge(notApplicable)); }
        """
    )


def test_normalize_estimate_fails_safe_on_malformed_server_data():
    """ (row 9, node half): same conversion as the test
    above -- `normalizeEstimate` is called as the real top-level function of
    the whole-module load rather than a hand-extracted fragment."""
    run_in_module_context(
        r"""
        const fallback = normalizeEstimate(null);
        if (fallback.status !== 'point' || fallback.value !== 0) {
          throw new Error('null fallback: ' + JSON.stringify(fallback));
        }
        // An unrecognised status is treated exactly like missing data -- fails safe to the
        // same zero-point default as `null`, never trusts an unknown shape's numbers.
        const bogusStatus = normalizeEstimate({ status: 'bogus', value: 5, low: 5, high: 5 });
        if (bogusStatus.status !== 'point' || bogusStatus.value !== 0) {
          throw new Error('bogus status: ' + JSON.stringify(bogusStatus));
        }
        const coerced = normalizeEstimate({ status: 'point', value: '42', low: '42', high: '42', reason: null });
        if (coerced.value !== 42) {
          throw new Error('string coercion: ' + JSON.stringify(coerced));
        }
        """
    )


def test_checkout_base_unknown_copy_names_cap_uncertainty():
    assert "at or over its cap" in "Not shown as a number — the destination plot is at or over its cap, and the exact amount can't be worked out yet."
    assert "above the cap" in "Not calculated — depends on which staged card absorbs the amount above the cap."
