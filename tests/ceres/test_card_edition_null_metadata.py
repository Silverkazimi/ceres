"""Browser coverage for the remote-board card-art bug (2026-08-27): the public board
rendered 69 of 178 monster worker card images while local rendered 178/178. Root cause
(layer b of two): `editorCardEdition()` built its edition list from
`String(asset.editions ?? '').split(',')`, and `''.split(',')` yields `['']`, not `[]`.
`Number('')` is `0`, which passes the `Number.isInteger(v) && v >= 0` filter, so every
worker whose `editions` was null or absent (as it is for every card the remote box's
missing `card_details` cache leaves undetailed -- see `ceres/capture.py`'s
`_refresh_card_details()`) silently resolved to edition `0` and 403'd against
`CARD_CDN_SET[0]` instead of falling through to the real `selected` edition.

Retired the Node-slice harness per  (campaign §3.1 batch C row 6b): the fix
lives in `editorCardEdition()` (`ceres/static/app.js:3394`), whose only observable
surface is the CDN URL an editor slot's `<img>` resolves to, via `editorCardImage()`
-> `editorAssetImage()` -> `slotAssetImageHtml()`. This now opens a real plot's worker
slot editor through `spawn_live_ceres` with one worker's `editions`/`edition` fields
set to the remote-board shape (route-mocked plot-detail response, all other fields a
real worker's) and reads the rendered `<img src>`'s CDN set segment.

Named injection (/, not performed here -- independent verification only): in
`editorCardEdition()`, remove `.filter((value) => value !== '')` from the `raw` line
(restoring the pre-fix `String(asset.editions ?? '').split(',')` shape). Expected red:
`test_null_editions_resolves_to_the_real_edition_not_zero` and
`test_undefined_editions_resolves_to_the_real_edition_not_zero` both resolve to CDN set
`cards_v2.2` (edition 0) instead of the worker's real edition's set.
"""

from __future__ import annotations

import copy
import json
import urllib.parse
import urllib.request

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _real_detail_row(base_url: str) -> dict:
    """A real plot detail row with >=2 real card workers (not a Runi, which has no
    `card_detail_id`/edition-driven art) -- used only as a field-shape template."""
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:  # noqa: S310
        board = json.loads(response.read().decode("utf-8"))
    for plot in board["plots"]:
        if plot.get("assets_loaded") and (plot.get("capacity") or {}).get("worker_count", 0) >= 1:
            deed_uid = plot["deed_uid"]
            url = f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}"
            with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
                row = json.loads(response.read().decode("utf-8"))
            if any(w.get("card_detail_id") is not None for w in row.get("workers") or []):
                return row
    raise AssertionError("no live plot with a real card worker to build a detail-row template from")


def _target_worker(row: dict) -> dict:
    """A real monster-card worker (not Runi -- Runi also carries a `card_detail_id`
    but renders under a distinct `alt="... icon"`, not `"... card art"`, so it is
    excluded by name rather than by that field)."""
    for worker in row["workers"]:
        if worker.get("card_detail_id") is not None and worker.get("name") != "Runi":
            return worker
    raise AssertionError("fixture row carries no non-Runi card_detail_id worker to mutate")


def _worker_slot_image_src(page, base_url: str, row: dict, worker_name: str) -> str | None:
    """Route-mock the plot detail with `row` (one worker carrying the remote-board
    editions shape), open its editor, and return that worker's slot `<img src>`
    (located by its accessible `alt="{name} card art"`, not tile position) -- or
    None if the tile rendered no art at all."""
    deed_uid = row["deed_uid"]
    page.route(
        f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}?*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(row)),
    )
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(".slot-editor").first.wait_for(timeout=30_000)
    img = page.locator(f".slot-editor img[alt='{worker_name} card art']")
    if img.count() == 0:
        return None
    return img.first.get_attribute("src")


def test_null_editions_resolves_to_the_real_edition_not_zero(live_browser: tuple[Browser, str]) -> None:
    # This is exactly the shape a remote board's undetailed worker carries: editions
    # null, edition present (19, CARD_CDN_SET[19] == 'cards_land'). Before the fix
    # ''.split(',') -> [''] -> Number('') -> 0, which passes the
    # Number.isInteger(v) && v >= 0 filter and resolves to CARD_CDN_SET[0]
    # ('cards_v2.2') -- wrong CDN set, ORB-blocked image, no console error.
    browser, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    worker = _target_worker(row)
    worker["editions"] = None
    worker["edition"] = 19
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    src = _worker_slot_image_src(page, base_url, row, worker["name"])
    page.close()
    assert src, "worker slot rendered no art at all"
    assert "/cards_land/" in src
    assert "/cards_v2.2/" not in src


def test_undefined_editions_resolves_to_the_real_edition_not_zero(live_browser: tuple[Browser, str]) -> None:
    # Same defect, absent-key form rather than explicit null (edition 4
    # CARD_CDN_SET[4] == 'cards_untamed').
    browser, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    worker = _target_worker(row)
    worker.pop("editions", None)
    worker["edition"] = 4
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    src = _worker_slot_image_src(page, base_url, row, worker["name"])
    page.close()
    assert src, "worker slot rendered no art at all"
    assert "/cards_untamed/" in src
    assert "/cards_v2.2/" not in src


def test_both_editions_and_edition_absent_still_returns_null(live_browser: tuple[Browser, str]) -> None:
    # Unchanged by this fix: a worker record with neither field is genuinely
    # un-renderable, and editorCardEdition() must keep returning null for it
    # (no <img> at all) rather than guessing edition 0.
    browser, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    worker = _target_worker(row)
    worker.pop("editions", None)
    worker.pop("edition", None)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    src = _worker_slot_image_src(page, base_url, row, worker["name"])
    page.close()
    assert src is None, f"expected no art for a metadata-less worker, got {src!r}"
