"""A plot mid worksite-change must still resolve worksite art.

The board returns `resource: None` and `worksite_type: ''` for a plot whose worksite
change is in progress -- byte-identical, on those two fields, to land that was never
built on. `worksiteOverlayPath()` keys off `resource` alone, so it returns null and the
panel renders as bare ground on a plot that HAS a worksite.

The distinguishing signal IS in the payload and is simply unused for art:
`row.pending_change = {kind: 'worksite_change', to: 'wood', to_label: 'Logging Camp', ...}`,
alongside a `worksite_incomplete` badge where never-built land carries `no_worksite`.

This state has a population of ONE plot in 130 (deed `I-294-f0b7b8d0bb89b6`, plot 65-5-1,
Forest, SPS -> Logging Camp), so a sweep reads 129/130 clean and any sampled fixture misses
it entirely.

census row 43: converted () from a node-sliced/tempfile unit test of
`worksiteOverlayPath()` to a real-browser test. `worksiteOverlayPath()`'s result is
consumed by `editorSceneStyle()` (app.js:2823-2827), which sets the `--scene-worksite`
CSS custom property (a `url('...')` or `none`) in the `style=` attribute of the editor's
`.editor-band` element (app.js:4703) -- read directly off the real, rendered element
rather than re-executed in a sandboxed slice."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres
from . import board_basic_recording as recording

MID_CHANGE_DEED = recording.PENDING.uid


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _scene_worksite_url(page, base_url: str, deed_uid: str) -> str | None:
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    band = page.locator(".editor-band")
    band.wait_for(timeout=30_000)
    style = band.get_attribute("style") or ""
    if "scene-worksite: none" in style:
        return None
    marker = "scene-worksite: url('"
    start = style.index(marker) + len(marker)
    return style[start:style.index("')", start)]


def test_plot_mid_worksite_change_resolves_art_for_its_incoming_worksite(live_browser: tuple[Browser, str]) -> None:
    r"""Named injection, run and watched red, then reverted and watched green: in
 `ceres/static/app.js`'s `worksiteOverlayPath()`, the branch that reads
 `row.pending_change` was removed so the function fell back to keying off
 `row.resource` alone (the exact pre-fix defect). Red: the real plot
 `I-294-f0b7b8d0bb89b6` -- the only one of 130 in this mid-worksite-change
 state -- rendered `--scene-worksite: none` on `.editor-band`, i.e. bare ground
 despite having a real worksite. Restoring the branch made this test pass
 again (whole-file run: 3 passed)."""
    browser, base_url = live_browser
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    row = next((p for p in board["plots"] if p["deed_uid"] == MID_CHANGE_DEED), None)
    if row is None:
        pytest.skip(f"maintainer snapshot no longer contains the one plot in this state ({MID_CHANGE_DEED})")

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    url = _scene_worksite_url(page, base_url, MID_CHANGE_DEED)
    assert url is not None, "a plot mid worksite-change rendered as never-built land (no scene-worksite background)"
    assert "wood" in url, f"resolved art for the wrong worksite: {url}"
    assert "forest" in url.lower(), f"resolved art for the wrong terrain: {url}"
    page.close()


def test_never_built_land_legitimately_has_no_worksite_art(live_browser: tuple[Browser, str]) -> None:
    """The control. A real never-built plot (no worksite, no pending change)
 resolves no `--scene-worksite` background. Without this the test above could
 pass against a function that returns art for every row regardless of state."""
    browser, base_url = live_browser
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    never_built = next(
        (
            p for p in board["plots"]
            if p.get("assets_loaded") and not p.get("resource") and not p.get("worksite_type") and not p.get("pending_change")
        ),
        None,
    )
    if never_built is None:
        pytest.skip("maintainer snapshot no longer contains a real never-built plot")

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    url = _scene_worksite_url(page, base_url, never_built["deed_uid"])
    assert url is None, f"never-built land rendered worksite art: {url}"
    page.close()


def test_the_two_states_are_indistinguishable_on_resource_alone(live_browser: tuple[Browser, str]) -> None:
    """Records WHY this defect is invisible: a real mid-worksite-change row and a
 real never-built row agree on every field the art path used to read
 (`resource`, `worksite_type`), and differ only in `pending_change` -- read off
 the real board payload, not a hand-built fixture.

 If this ever fails, the board has gained a distinguishing field on one of the
 two agreed keys and the fix may be simpler than reading `pending_change`."""
    browser, base_url = live_browser
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    mid_change = next((p for p in board["plots"] if p["deed_uid"] == MID_CHANGE_DEED), None)
    never_built = next(
        (
            p for p in board["plots"]
            if p.get("assets_loaded") and not p.get("resource") and not p.get("worksite_type") and not p.get("pending_change")
        ),
        None,
    )
    if mid_change is None or never_built is None:
        pytest.skip("maintainer snapshot no longer contains both real plots this comparison needs")
    for field in ("resource", "worksite_type"):
        assert mid_change[field] == never_built[field], field
    assert mid_change.get("pending_change") != never_built.get("pending_change")
