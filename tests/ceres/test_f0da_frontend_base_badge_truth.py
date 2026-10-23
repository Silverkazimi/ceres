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


Since then the editor's change badges were redesigned (editor.js renderChangeBadges): the
plot shows ONE Base badge, "Total Base <after> (<change>)", for the plot's whole Base PP after
the cap, and an estimate that is not a point value gets no badge at all ("an unavailable
estimate has no badge because the authoritative figure remains on the row"). The per-card
footer row now shows each card's Boostable PP (Base PP after the cap, with terrain); its
tooltip names the card's Base PP and what the cap takes. The guards below therefore compare
the Total Base badge with the per-card Base PP after the cap read from those tooltips, and
the unknown cases assert that no fabricated figure is shown.

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
            if _slot(worker) <= 0:
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
            slot = _slot(worker)
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
            slot = _slot(worker)
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
            slot = _slot(worker)
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
        occupied = {_slot(worker) for worker in detail.get("workers", [])}
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
    # the band renders `preparing` first; the footer figures exist once it is `ready`
    page.locator(f"tr.expand-row[data-deed-uid='{deed_uid}'] .editor-band[data-plot-state='ready']").wait_for(timeout=30_000)
    return page


def _slot(worker: dict) -> int:
    """The worker's 1-based slot, or -1 for a row without one (a Runi or item row has `slot: null`)."""
    slot = worker.get("slot")
    return int(slot) if isinstance(slot, (int, float)) else -1


def _open_editor(browser: Browser, base_url: str, deed_uid: str) -> Page:
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(base_url)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").wait_for(timeout=30_000)
    return _editor(page, deed_uid)


def _base_figures(page: Page) -> list[int]:
    """Each worker slot's Base PP after the plot cap, as its footer cell's tooltip states it
    ("<raw> Base PP · <terrain>[ · the plot's <cap> cap takes <n>]"); an empty slot is 0."""
    worker_slots = page.locator(".slot-group[data-group-id='worker'] .slot-tile-cell").count()
    cells = page.locator(".editor-base-pp-row .editor-base-pp-cell[data-group-id='worker']")
    values = []
    for index in range(worker_slots):
        body = (cells.nth(index).get_attribute("data-tip-body") or "") if index < cells.count() else ""
        raw = re.match(r"([\d,]+) Base PP", body)
        taken = re.search(r"cap takes ([\d,]+)", body)
        value = int(raw.group(1).replace(",", "")) if raw else 0
        values.append(value - (int(taken.group(1).replace(",", "")) if taken else 0))
    return values


def _raw_figures(page: Page) -> list[int]:
    """Each worker slot's own Base PP before the cap, from its footer cell's tooltip; empty is 0."""
    worker_slots = page.locator(".slot-group[data-group-id='worker'] .slot-tile-cell").count()
    cells = page.locator(".editor-base-pp-row .editor-base-pp-cell[data-group-id='worker']")
    values = []
    for index in range(worker_slots):
        body = (cells.nth(index).get_attribute("data-tip-body") or "") if index < cells.count() else ""
        raw = re.match(r"([\d,]+) Base PP", body)
        values.append(int(raw.group(1).replace(",", "")) if raw else 0)
    return values


def _base_badge(page: Page) -> str:
    for text in page.locator(".editor-change-badge").all_inner_texts():
        if text.strip().startswith("Total Base "):
            return text.strip()
    raise AssertionError("staged editor rendered no Total Base change badge")


def _badge_value(text: str) -> int:
    """The signed change in brackets: "Total Base 100,000 (−141) · cap −141" -> -141."""
    match = re.search(r"^Total Base\s+[\d,]+\s+\(([+−-]?[\d,]+)\)", text)
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
    raw_before, before = _raw_figures(page), _base_figures(page)
    _stage_replace(page, base_url, row, worker, candidate)
    raw_after, after = _raw_figures(page), _base_figures(page)
    badge = _base_badge(page)
    # the replaced slot's own Base PP rises, and the plot's Total Base badge reports exactly
    # the change of the per-card figures after the cap (on a capped plot that can be 0)
    assert raw_after[slot_index] - raw_before[slot_index] > 0
    assert _badge_value(badge) == sum(after) - sum(before)


def _unknown_base_badge_assertion_js() -> str:
    return """
const stageTotal = (status, now, after) => ({estimate: {status}, effectiveNow: now, effectiveAfter: after, cap: 100000, loss: 0});
const unknown = renderChangeBadges(
  {status: 'unknown', value: null, low: null, high: null, reason: 'not reported'},
  NaN,
  1,
  stageTotal('unknown', 50000, null),
  null,
);
if (unknown.includes('Total Base')) throw new Error('an unknown Base estimate rendered a Total Base figure');
if (unknown.includes('—')) throw new Error('an unknown Base estimate rendered a bare em dash');
if (unknown !== '') throw new Error('an unknown estimate must render no badge, got: ' + unknown);
const point = renderChangeBadges(
  {status: 'point', value: -500, low: -500, high: -500, reason: null},
  NaN,
  1,
  stageTotal('point', 50000, 49500),
  null,
);
if (!point.includes('Total Base <strong>49,500</strong> (−500)')) throw new Error('a point Base estimate lost its Total Base figure: ' + point);
if (!point.includes('data-tip="Total Base PP after the change"')) throw new Error('the Total Base badge lost its title');
if (!point.includes('data-tip-body="Projected · −500 from 1 staged move against 50,000 now"')) throw new Error('the Total Base badge lost its projected body: ' + point);
"""


def test_g6_unknown_base_badge_is_reason_bearing() -> None:
    """The REAL ``renderChangeBadges()`` receives a fabricated unknown Base estimate and
    must render no figure for it -- no Total Base badge and never a bare ``—`` (the
    redesign drops a badge whose estimate is not a point value; the row keeps the
    authoritative figure) -- while a point estimate renders the Total Base badge with
    its projected tooltip. Full app.js evaluation (via the shared
    `tests/ceres/vm_context.py` sandbox, ) keeps this a non-live
    test without slicing source strings. Before the shared helper existed this
    test built its own context missing ``window`` and was RED on
    ``ReferenceError: window is not defined`` at the module-scope statement
    ``const OPERATION_POLL_SCHEDULE_MS = window.__ceresOperationPollSchedule ||
    ...`` (app.js:2891) -- fixed once, in the helper, not per test.
    """
    run_in_module_context(_unknown_base_badge_assertion_js(), entry=ENTRY)


def test_g6_unknown_base_badge_mutation_is_red() -> None:
    """Injection: dropping the point-estimate gate in renderChangeBadges() (always
    rendering the Total Base badge) shows a figure for an unknown estimate; the synthetic
    harness must exit red, proving this guard reaches the unknown branch.
    """
    source = read_entry_source()
    mutated = source.replace(
        "if (stageTotal?.estimate?.status === 'point') {",
        "if (stageTotal) {",
        1,
    )
    assert mutated != source, "renderChangeBadges point-estimate gate mutation target is missing"
    with pytest.raises(subprocess.CalledProcessError):
        run_in_module_context(_unknown_base_badge_assertion_js(), entry=ENTRY, source=mutated)


def test_g6_harvest_unknown_change_badge_has_visible_reason(live_browser: tuple[Browser, str]) -> None:
    """G6 live guard: an over-cap replacement whose candidate impact the picker reports
    as unknown never shows a bare ``—`` or a fabricated figure in the editor. The editor
    prices the staged plan itself (the stage impact), so every badge it does render is a
    number; an estimate that is not a point value is dropped rather than shown as a
    placeholder (``Harvest over cap`` went with that redesign).
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
    assert all(re.search(r"\d", text) for text in texts), texts
    assert page.locator(".editor-change-badge.is-unknown").count() == 0, texts


def test_g8_over_cap_change_renders_base_and_harvest_badges(live_browser: tuple[Browser, str]) -> None:
    """G8: an over-cap replacement renders both the plot's Total Base badge and its Total
    (PP after every boost) badge. This test discovers over-cap by raw ``base_pp > 100000``
    (or a worker whose ``base_pp_after_cap`` is below raw ``base_pp``), never by the
    clamped derived ``over_cap`` flag. The Total Base change must equal the change of the
    per-card Base PP after the cap, so the cap is applied, not the candidate's pre-cap delta.
    """
    browser, base_url = live_browser
    case = _over_cap_replace_case(base_url)
    if case is None:
        pytest.skip("G8 skipped loudly: live board has no raw-over-cap plot with an unknown-impact replacement and a nonzero Base delta")
    row, worker, candidate = case
    page = _open_editor(browser, base_url, row["deed_uid"])
    before = _base_figures(page)
    _stage_replace(page, base_url, row, worker, candidate)
    after = _base_figures(page)
    badges = page.locator(".slot-group[data-group-id='worker'] > .slot-group-controls .editor-change-badge")
    texts = [text.strip() for text in badges.all_inner_texts()]
    base_text = next((text for text in texts if text.startswith("Total Base ")), None)
    assert base_text, texts
    assert _badge_value(base_text) == sum(after) - sum(before), (base_text, before, after)
    harvest = [text for text in texts if re.match(r"^Total [+−-][\d,]+$", text)]
    assert len(harvest) == 1, texts
    body = badges.filter(has_text="Total Base").first.get_attribute("data-tip-body") or ""
    assert body.startswith("Projected · "), body
    if "· cap" in base_text:
        assert "cap takes" in body, body


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
