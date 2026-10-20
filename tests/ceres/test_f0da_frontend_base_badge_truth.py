""" Phase 2 rendered Base-badge guards.

The injection outcomes below are recorded after each mutation was run against the
same live Chromium fixture and then restored. Phase 3/4 source-level injections also
ran and restored: G6's live half is the Harvest-unknown chip, and suppressing that
chip went red with one badge instead of two; G9 (removing the staged-worker terrain
null guard) went red with `served terrain boost +0%`; and G11 (re-aiming the checkout
Base-unknown catalog entry at cap wording) went red because the message contained
`cap`. The non-live G6 Base-unknown guard separately replaces the exact label
expression at editor.js:676; the mutation rendered `Base —` and went red, while the
restored reason-bearing label passed. The restored implementation made all four
guards green.

The live G6 case intentionally claims only Harvest coverage. A board scan over all
129 plots and every occupied worker replacement found zero candidates with
`base_pp_delta.status == "unknown"`: Phase 1 moved this badge to the pre-terrain,
pre-cap `base_pp_delta`, which is a point value throughout this board. The live
`_over_cap_replace_case` instead guarantees `plot_impact.status == "unknown"` and
`base_pp_delta.status == "point"`, so its unknown chip is `Harvest over cap`.

G8's live fixture probe selects by raw `base_pp > 100000`, not the derived `over_cap`
flag. On the 129-plot board it found plot `41-9-56` / deed
`I-294-99ed4b7a4d170e`: `base_pp=107641.176`, `base_pp_after_cap=100000.0`,
`over_cap=False`. Its five worker figures were `40000.0 -> 40000.0`,
`32941.176 -> 32941.176`, `25000.0 -> 25000.0`, `7500.0 -> 2058.824`, and
`2200.0 -> 0.0`. Because every worker slot was occupied, the probe enumerated all
five replacement slots rather than looking for an empty slot. Unknown
`plot_impact` did arise naturally: the live candidate counts were point=2342 and
unknown=1538 on slots 1–3, and point=0 and unknown=3880 on slots 4–5; the unknown
reason was the former cap-allocation suppression state.

The first live unknown candidate (`Alric Stormbringer`) on worker slot 1 rendered
`Base 0`; that was a real zero-delta replacement, not an estimate defect. The
suite therefore selects the same candidate on worker slot 2, where the served raw
Base delta is `+7058.824` and the rendered chip is `Base +7,059`. It renders exactly
two chips, and the Harvest chip carries the visible catalog reason that the plot is
above the 100,000 cap, the cap split was unpublished, and the change could not be shown
as a single figure. Suppressing the `harvestDelta !== 0`/unknown-Harvest path in the
browser mutation made that probe red with exactly one chip; restoring it made the
probe green with both chips again.


: G3, which read the plot header of the old checkout panel's `.checkout-plot-group` / `.checkout-move-delta` rows was retired; the Changes modal () renders no such elements and the dead CSS went with it.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from ceres.notices import NOTICE_REGISTRY
from .conftest import spawn_live_ceres
from .vm_context import ENTRY, run_in_module_context, read_entry_source


APP = Path(__file__).parents[2] / "ceres/static/app.js"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _get_json(base_url: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=20) as response:
        return json.load(response)


def _detail(base_url: str, deed_uid: str) -> dict:
    return _get_json(base_url, f"/api/plots/{urllib.parse.quote(deed_uid, safe='')}")


def _candidate_payload(base_url: str, deed_uid: str, slot_index: int, *, limit: int = 25) -> dict:
    params = urllib.parse.urlencode({
        "group_id": "worker", "limit": limit, "offset": 0, "sort": "construction_pp",
        "slot": slot_index, "bucket": "all",
    })
    return _get_json(
        base_url,
        f"/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates?{params}",
    )


def _nonzero_terrain_case(base_url: str) -> tuple[dict, dict] | None:
    board = _get_json(base_url, "/api/board")
    for row in board.get("plots", []):
        if int((row.get("capacity") or {}).get("potential_card_capacity", 0)) <= 0:
            continue
        detail = _detail(base_url, row["deed_uid"])
        for worker in detail.get("workers", []):
            if int(worker.get("slot", -1)) <= 0:
                continue
            terrain = worker.get("terrain_boost")
            if terrain is not None and float(terrain) != 0:
                return row, worker
    return None


def _positive_raw_replace_case(base_url: str) -> tuple[dict, dict, dict] | None:
    board = _get_json(base_url, "/api/board")
    for row in board.get("plots", []):
        if int((row.get("capacity") or {}).get("potential_card_capacity", 0)) <= 0:
            continue
        detail = _detail(base_url, row["deed_uid"])
        for worker in detail.get("workers", []):
            slot = int(worker.get("slot", -1))
            if slot <= 0:
                continue
            payload = _candidate_payload(base_url, row["deed_uid"], slot - 1)
            for candidate in payload.get("candidates", []):
                base_delta = candidate.get("base_pp_delta") or {}
                if (
                    candidate.get("base_pp") is not None
                    and float(candidate["base_pp"]) > float(worker.get("base_pp") or 0)
                    and base_delta.get("status") == "point"
                    and float(base_delta.get("value", 0)) > 0
                ):
                    return row, worker, candidate
    return None


def _over_cap_replace_case(base_url: str) -> tuple[dict, dict, dict] | None:
    board = _get_json(base_url, "/api/board")
    for row in board.get("plots", []):
        raw_base = row.get("base_pp")
        detail = _detail(base_url, row["deed_uid"])
        worker_over = any(
            worker.get("base_pp") is not None
            and worker.get("base_pp_after_cap") is not None
            and float(worker["base_pp_after_cap"]) < float(worker["base_pp"])
            for worker in detail.get("workers", [])
        )
        if not ((raw_base is not None and float(raw_base) > 100000) or worker_over):
            continue
        for worker in detail.get("workers", []):
            slot = int(worker.get("slot", -1))
            if slot <= 0:
                continue
            payload = _candidate_payload(base_url, row["deed_uid"], slot - 1, limit=100)
            for candidate in payload.get("candidates", []):
                base_delta = candidate.get("base_pp_delta") or {}
                impact = candidate.get("plot_impact") or {}
                if (
                    impact.get("status") == "unknown"
                    and base_delta.get("status") == "point"
                    and abs(float(base_delta.get("value", 0))) > 0
                ):
                    return row, worker, candidate
    return None


def _null_terrain_replace_case(base_url: str) -> tuple[dict, dict, dict] | None:
    board = _get_json(base_url, "/api/board")
    for row in board.get("plots", []):
        detail = _detail(base_url, row["deed_uid"])
        if not any(
            worker.get("terrain_boost") is not None and float(worker["terrain_boost"]) != 0
            for worker in detail.get("workers", [])
        ):
            continue
        for worker in detail.get("workers", []):
            slot = int(worker.get("slot", -1))
            if slot <= 0:
                continue
            payload = _candidate_payload(base_url, row["deed_uid"], slot - 1, limit=100)
            for candidate in payload.get("candidates", []):
                base_delta = candidate.get("base_pp_delta") or {}
                if (
                    candidate.get("terrain_boost") is None
                    and candidate.get("base_pp") is not None
                    and base_delta.get("status") == "point"
                ):
                    return row, worker, candidate
    return None


def _point_raw_stake_case(base_url: str) -> tuple[dict, int, dict] | None:
    board = _get_json(base_url, "/api/board")
    for row in board.get("plots", []):
        capacity = int((row.get("capacity") or {}).get("potential_card_capacity", 0))
        if capacity <= 0:
            continue
        detail = _detail(base_url, row["deed_uid"])
        occupied = {int(worker.get("slot", -1)) for worker in detail.get("workers", [])}
        slot_index = next((index for index in range(capacity) if index + 1 not in occupied), None)
        if slot_index is None:
            continue
        payload = _candidate_payload(base_url, row["deed_uid"], slot_index)
        for candidate in payload.get("candidates", []):
            if (
                candidate.get("base_pp") is not None
                and (candidate.get("base_pp_delta") or {}).get("status") == "point"
            ):
                return row, slot_index, candidate
    return None


def _editor(page: Page, deed_uid: str) -> Page:
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    page.locator(f"tr.expand-row[data-deed-uid='{deed_uid}'] .slot-editor").wait_for(timeout=30_000)
    return page


def _open_editor(browser: Browser, base_url: str, deed_uid: str) -> Page:
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(base_url)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").wait_for(timeout=30_000)
    return _editor(page, deed_uid)


def _base_figures(page: Page) -> list[int]:
    worker_slots = page.locator(".slot-group[data-group-id='worker'] .slot-tile-cell").count()
    cells = page.locator(".editor-base-pp-row .editor-base-pp-cell")
    values = []
    for index in range(worker_slots):
        cell = cells.nth(index)
        values.append(cell.locator("b").inner_text() if cell.locator("b").count() else "0")
    return [int(value.replace(",", "").replace("−", "-")) for value in values]


def _base_badge(page: Page) -> str:
    for text in page.locator(".editor-change-badge").all_inner_texts():
        if text.strip().startswith("Base "):
            return text.strip()
    raise AssertionError("staged editor rendered no Base change badge")


def _badge_value(text: str) -> int:
    match = re.search(r"^Base\s+([+−-]?[\d,]+)$", text)
    assert match, text
    return int(match.group(1).replace(",", "").replace("−", "-"))


def _stage_unstake(page: Page, slot: int) -> None:
    tile = page.locator(f".slot-tile[data-group-id='worker'][data-slot-index='{slot - 1}']")
    tile.hover()
    with page.expect_request("**/removal-impact*", timeout=30_000):
        tile.locator(".slot-remove").click()
        hazard = page.locator("#slot-hazard-overlay")
        if hazard.is_visible():
            hazard.locator(".hazard-confirm").click()
    page.locator(".editor-change-badge").first.wait_for(timeout=30_000)


def _stage_replace(page: Page, base_url: str, row: dict, worker: dict, candidate: dict) -> None:
    slot_index = int(worker["slot"]) - 1
    tile = page.locator(f".slot-tile[data-group-id='worker'][data-slot-index='{slot_index}']")
    tile.click()
    page.locator(".candidate-row").first.wait_for(timeout=30_000)
    target = None
    for index in range(page.locator(".candidate-row").count()):
        candidate_row = page.locator(".candidate-row").nth(index)
        if (
            candidate_row.locator(".candidate-name").inner_text().strip() == candidate["name"]
            and candidate_row.locator(".candidate-base strong").inner_text().replace(",", "").strip() == str(int(candidate["base_pp"]))
        ):
            target = candidate_row
            break
    assert target is not None, f"candidate {candidate['name']} was not rendered in the live picker"
    target.click()
    page.locator(".editor-change-badge").first.wait_for(timeout=30_000)


def _stage_stake(page: Page, candidate: dict, slot_index: int) -> None:
    tile = page.locator(f".slot-tile[data-group-id='worker'][data-slot-index='{slot_index}']")
    tile.click()
    page.locator(".candidate-row").first.wait_for(timeout=30_000)
    target = None
    for index in range(page.locator(".candidate-row").count()):
        candidate_row = page.locator(".candidate-row").nth(index)
        if (
            candidate_row.locator(".candidate-name").inner_text().strip() == candidate["name"]
            and candidate_row.locator(".candidate-base strong").inner_text().replace(",", "").strip() == str(int(candidate["base_pp"]))
        ):
            target = candidate_row
            break
    assert target is not None, f"candidate {candidate['name']} was not rendered in the live picker"
    target.click()
    page.locator(".editor-change-badge").first.wait_for(timeout=30_000)


def test_g1_unstake_base_badge_equals_raw_figure_delta(live_browser: tuple[Browser, str]) -> None:
    """G1 injection: app.js:5879 reading ``own_construction_pp`` instead of
 ``own_base_pp`` made this assertion red on the pinned +10% terrain plot: the
 badge used the terrain-adjusted value while the raw figure row dropped by the
 card's Base PP. Restoring ``own_base_pp`` made it green.
    """
    browser, base_url = live_browser
    case = _nonzero_terrain_case(base_url)
    if case is None:
        pytest.skip("G1 skipped loudly: live board has no occupied worker on a non-zero-terrain plot")
    row, worker = case
    page = _open_editor(browser, base_url, row["deed_uid"])
    before = sum(_base_figures(page))
    _stage_unstake(page, int(worker["slot"]))
    after = sum(_base_figures(page))
    assert _badge_value(_base_badge(page)) == after - before


def test_stake_base_badge_equals_raw_figure_delta(live_browser: tuple[Browser, str]) -> None:
    """Stake-path measurement: the rendered Base chip equals the signed change
 between the worker figure row before and after adding the selected candidate.
 This separately exercises app.js:6087, whose source injection is the stake
 analogue of G2's replace mutation.
    """
    browser, base_url = live_browser
    case = _point_raw_stake_case(base_url)
    if case is None:
        pytest.skip("stake measurement skipped loudly: live board has no open worker slot with a point raw Base PP candidate")
    row, slot_index, candidate = case
    page = _open_editor(browser, base_url, row["deed_uid"])
    before = sum(_base_figures(page))
    _stage_stake(page, candidate, slot_index)
    after = sum(_base_figures(page))
    assert _badge_value(_base_badge(page)) == after - before


def test_g2_replace_positive_raw_base_delta(live_browser: tuple[Browser, str]) -> None:
    """G2 injection: app.js:6285 passing ``candidate.construction_pp_delta``
 instead of ``candidate.base_pp_delta`` made the pinned Manticore → Zintar
 case render Base −59 against the +2,941 figure-row delta. Restoring the raw
 candidate field made the assertion red-to-green condition pass.
    """
    browser, base_url = live_browser
    case = _positive_raw_replace_case(base_url)
    if case is None:
        pytest.skip("G2 skipped loudly: live board has no occupied worker replacement with positive raw Base PP delta")
    row, worker, candidate = case
    page = _open_editor(browser, base_url, row["deed_uid"])
    slot_index = int(worker["slot"]) - 1
    before = _base_figures(page)[slot_index]
    _stage_replace(page, base_url, row, worker, candidate)
    after = _base_figures(page)[slot_index]
    badge = _base_badge(page)
    assert after - before > 0
    assert _badge_value(badge) == after - before


def _unknown_base_badge_assertion_js() -> str:
    return f"""
const rendered = renderChangeBadges(
 {{status: 'unknown', value: null, low: null, high: null, reason: 'not reported'}},
 0,
 false,
 1,
);
if (!rendered.includes('Base not calculated')) throw new Error('unknown Base reason-bearing label was not rendered');
if (!rendered.includes('editor-change-badge is-unknown')) throw new Error('unknown Base badge lost is-unknown');
if (rendered.includes('Base —')) throw new Error('unknown Base rendered a bare em dash');
if (!rendered.includes('data-tip="Base PP after the change"')) throw new Error('unknown Base badge lost its row-48 title');
if (!rendered.includes('data-tip-body="Not shown as a number — this plot has a staged move whose exact effect above the 100,000 cap can&#39;t be worked out yet."')) throw new Error('unknown Base badge lost its row-48 body');
if (!rendered.includes('data-tip-foot="Not calculated — depends on which staged card absorbs the amount above the cap."')) throw new Error('unknown Base badge lost its row-48 foot');
"""


def test_g6_unknown_base_badge_is_reason_bearing() -> None:
    """The REAL ``renderChangeBadges()`` receives a fabricated unknown Base estimate
 and must render ``Base not calculated`` with ``.is-unknown`` and the registry
 tooltip, never a bare ``—``. Full app.js evaluation (via the shared
 `tests/ceres/vm_context.py` sandbox, ) keeps this a non-live
 test without slicing source strings. Before the shared helper existed this
 test built its own context missing ``window`` and was RED on
 ``ReferenceError: window is not defined`` at the module-scope statement
 ``const OPERATION_POLL_SCHEDULE_MS = window.__ceresOperationPollSchedule ||
 ...`` (app.js:2891) -- fixed once, in the helper, not per test.
    """
    run_in_module_context(_unknown_base_badge_assertion_js(), entry=ENTRY)


def test_g6_unknown_base_badge_mutation_is_red() -> None:
    """Injection: replacing the exact editor.js:676 label expression with the
 unconditional ``Base ${estimateBadge(basePpEstimate)}`` rendered ``Base —``;
 the synthetic harness exited red. Restoring the reason-bearing expression was
 then green, proving this guard reaches the previously unreachable Base branch.
    """
    source = read_entry_source()
    mutated = source.replace(
        "const label = unknown ? 'Base not calculated' : `Base ${estimateBadge(basePpEstimate)}`;",
        "const label = `Base ${estimateBadge(basePpEstimate)}`;",
        1,
    )
    assert mutated != source, "editor.js:676 mutation target is missing"
    with pytest.raises(subprocess.CalledProcessError):
        run_in_module_context(_unknown_base_badge_assertion_js(), entry=ENTRY, source=mutated)


def test_g6_harvest_unknown_change_badge_has_visible_reason(live_browser: tuple[Browser, str]) -> None:
    """G6 live guard: the over-cap replacement's unknown Harvest estimate renders
 ``Harvest over cap`` with a visible reason. This deliberately does not claim
 to exercise unknown Base: the 129-plot board scan found zero
 ``base_pp_delta.status == "unknown"`` cases, because Phase 1 moved Base badges
 to the pre-terrain, pre-cap point estimate.
    """
    browser, base_url = live_browser
    case = _over_cap_replace_case(base_url)
    if case is None:
        pytest.skip("G6 Harvest half skipped loudly: live board has no raw-over-cap plot with an unknown-impact replacement and a nonzero Base delta")
    row, worker, candidate = case
    page = _open_editor(browser, base_url, row["deed_uid"])
    _stage_replace(page, base_url, row, worker, candidate)
    badges = page.locator(".editor-change-badge")
    texts = [text.strip() for text in badges.all_inner_texts()]
    assert texts, "G6 staged case rendered no editor change badges"
    assert all(not text.endswith("—") for text in texts), texts
    unknown_texts = [
        text.strip()
        for text in page.locator(".editor-change-badge.is-unknown").all_inner_texts()
    ]
    assert unknown_texts, f"G6 staged case rendered no unknown badge: {texts}"
    assert unknown_texts == ["Harvest over cap"], unknown_texts


def test_g8_over_cap_change_renders_base_and_harvest_badges(live_browser: tuple[Browser, str]) -> None:
    """G8 injection: restoring the ``harvestDelta !== 0`` suppression at
 ``app.js:4339`` removed the unknown-Harvest chip, leaving one badge. The live
 mutation was run and observed red with one chip; restoring the branch made it
 green with two. This test discovers over-cap by raw ``base_pp > 100000`` (or
 a worker whose ``base_pp_after_cap`` is below raw ``base_pp``), never by the
 clamped derived ``over_cap`` flag.
    """
    browser, base_url = live_browser
    case = _over_cap_replace_case(base_url)
    if case is None:
        pytest.skip("G8 skipped loudly: live board has no raw-over-cap plot with an unknown-impact replacement and a nonzero Base delta")
    row, worker, candidate = case
    page = _open_editor(browser, base_url, row["deed_uid"])
    _stage_replace(page, base_url, row, worker, candidate)
    badges = page.locator(".editor-facts .editor-change-badge")
    texts = [text.strip() for text in badges.all_inner_texts()]
    assert len(texts) == 2, texts
    base_text = next(text for text in texts if text.startswith("Base "))
    expected_base = int(round(float((candidate["base_pp_delta"] or {})["value"])))
    assert _badge_value(base_text) == expected_base, (base_text, expected_base)
    harvest = page.locator(".editor-change-badge.is-unknown")
    assert harvest.count() == 1, texts
    assert harvest.inner_text().strip() == "Harvest over cap"
    assert len(harvest.inner_text().split()) >= 3
    assert "above the 100,000 cap" in (harvest.get_attribute("data-tip-body") or "")


def test_g9_staged_card_base_tooltip_omits_null_terrain(live_browser: tuple[Browser, str]) -> None:
    """G9 injection: removing the null guard around the staged worker terrain
 value at ``app.js:4371`` rendered ``served terrain boost +0%`` in the staged
 card's Base tooltip. The live mutation was run and observed red; restoring the
 guard made it green with neither ``terrain boost`` nor ``+0%``.
    """
    browser, base_url = live_browser
    case = _null_terrain_replace_case(base_url)
    if case is None:
        pytest.skip("G9 skipped loudly: live board has no nonzero-terrain plot with a replacement candidate whose served terrain is unknown")
    row, worker, candidate = case
    page = _open_editor(browser, base_url, row["deed_uid"])
    _stage_replace(page, base_url, row, worker, candidate)
    slot_index = int(worker["slot"]) - 1
    tooltip = page.locator(".editor-base-pp-row .editor-base-pp-cell").nth(slot_index).get_attribute("data-tip") or ""
    assert tooltip, "G9 staged card rendered no Base-cell tooltip"
    assert "terrain boost" not in tooltip.lower(), tooltip
    assert "+0%" not in tooltip, tooltip
