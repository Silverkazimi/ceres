""" jobs 1-3 -- badges must tell the truth (queue.md "BADGE ORDER",
"SPLIT THE INC BADGE", "COOLDOWN LABELLING IS WRONG FOR STAKED CARDS",
2026-08-25).

Covers:
  1. The backend badge list has a stable transport order. 's browser
     rendering/filter order is guarded separately by test_badge_colours.py.
  2. The single "incomplete" badge is split into `no_worksite` (never cleared,
     open-ended) and `worksite_incomplete` (worksite exists, always 24h) per
     the 2026-08-25 worksite ruling.
  3. `cooldown_state()` never lies to a currently-staked card by calling it
     "cooling" -- it reports `state: "staked"` instead, distinct from genuine
     `state: "cooling"` and the fail-safe `state: "unknown"`.
"""

from __future__ import annotations

import copy
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import Browser

from ceres.board import BADGE_META, project_board
from ceres.candidates import CandidateRow, cooldown_state, serialize_candidate
from ceres.notices import BADGE_ORDER, badge_rank
from praetoria.land_reallocate import MovableCard
from tests.ceres.conftest import build_report, spawn_live_ceres, wait_for_board

CSS = Path(__file__).parents[2] / "ceres" / "static" / "ceres.css"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _real_board_row_with_open_slot(base_url: str) -> dict:
    """A REAL `GET /api/plots/{deed}` row for a plot with >=1 open worker slot."""
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    for plot in board["plots"]:
        if (plot.get("capacity") or {}).get("open_slots", 0) > 0:
            deed_uid = plot["deed_uid"]
            with urllib.request.urlopen(f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}", timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
    raise AssertionError("no live plot with an open worker slot to build a fixture from")


def _real_worker_candidate_template(base_url: str, deed_uid: str) -> dict:
    params = urllib.parse.urlencode({
        "group_id": "worker", "limit": 5, "offset": 0, "sort": "construction_pp",
        "bucket": "all", "preset": "net_gain_default",
    })
    url = f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates?{params}"
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("candidates"):
        return copy.deepcopy(payload["candidates"][0])
    raise AssertionError(f"plot {deed_uid} returned no worker candidates to build a fixture template from")


def _open_worker_picker(page, base_url: str, deed_uid: str) -> None:
    page.goto(base_url)
    wait_for_board(page)
    page.locator(f"tr.plot-row[data-deed-uid='{deed_uid}']").click()
    empty = page.locator(".slot-group[data-group-id='worker'] .slot-tile.is-empty").first
    empty.wait_for(timeout=30_000)
    empty.click()
    page.locator(".candidate-row").first.wait_for(timeout=30_000)

NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)


# Job 1 -- canonical badge order

def test_badge_order_matches_final_spec():
    """ keeps the legacy API transport order aligned with its groups.

    `sole_ability_holder` was renamed `land_ability_card` (v0.9.513, "Keep Land Card and VoV
    badges in the green category"; see test_badge_colours.py), and inside the neutral group
    `idle` (group_order 0) now comes before `building_in_box` (group_order 1), which matches
    BADGE_META's own within-group order."""
    assert BADGE_ORDER == [
        "unpowered", "unpowered_worker", "over_cap", "region_dec",
        "staged_changes", "staged_cooldown",
        "dark_discount_wasted", "no_worksite", "redundant_power_core", "underfilled",
        "bloodline_solo", "worksite_incomplete", "bloodline_paired",
        "pending_change", "runi", "land_ability_card",
        "idle", "building_in_box", "unstable_totem", "assets_not_loaded",
    ]
    assert BADGE_META["idle"]["group_order"] < BADGE_META["building_in_box"]["group_order"]


def test_badge_rank_unknown_code_sorts_last():
    assert badge_rank("not_a_real_badge") == len(BADGE_ORDER)


def test_project_board_sorts_badges_per_canonical_order(details_by_id):
    """Real fixture data already produces multi-badge plots (2026-08-25 fixture
    snapshot) -- assert their `badges` list comes out pre-sorted by the one rank
    table, not by append order."""
    report = build_report()
    board = project_board(report, details_by_id)
    checked_a_multi_badge_row = False
    for row in board["plots"]:
        badges = row["badges"]
        if len(badges) < 2:
            continue
        checked_a_multi_badge_row = True
        ranks = [badge_rank(code) for code in badges]
        assert ranks == sorted(ranks), (
            f"plot {row['deed_uid']} badges not in canonical order: {badges}"
        )
    assert checked_a_multi_badge_row, "fixture must contain at least one multi-badge plot"


def test_board_payload_ships_badge_order_for_the_frontend(details_by_id):
    report = build_report()
    board = project_board(report, details_by_id)
    assert board["badge_order"] == BADGE_ORDER


def test_rendered_badge_chips_follow_the_canonical_order(live_browser: tuple[Browser, str]) -> None:
    """Census row 3 ( batch B): replaces the substring check that `BOARD.badge_order`
    and `chips.sort(...)` appear verbatim in app.js. Reads a real multi-badge plot's rendered
    `.badge-row .badge` chip order and confirms it matches `badge_rank()` ascending -- proving
    the browser actually sorts by the shipped order rather than a second hardcoded list,
    without caring where in the split modules the sort code ends up living.

    Named injection (not applied by this migration; independently watched red/green per
    ): in `app.js`'s chip-building loop, `chips.push({ rank: badgeRank(b), ... })` changed
    to `chips.push({ rank: 0, ... })` -- every chip collapses to the same rank and the
    `chips.sort((a, b) => a.rank - b.rank)` call becomes a no-op, so a multi-badge row's
    chips would render in raw badge-list order instead of canonical rank order. Expected red:
    `assert ranks == sorted(ranks)` fails for any row whose raw badge order differs from rank
    order (the live fixture's own `no_worksite`+`construction_incomplete`-adjacent rows do)."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(base_url)
    wait_for_board(page)
    rows = page.locator("#plot-tbody > tr.plot-row")
    codes: list[str] | None = None
    for index in range(rows.count()):
        badges = rows.nth(index).locator(".badge-row .badge")
        count = badges.count()
        if count >= 2:
            classes = [badges.nth(i).get_attribute("class") or "" for i in range(count)]
            found = []
            for cls in classes:
                token = next((p for p in cls.split() if p.startswith("badge-") and p != "badge-row"), None)
                if token:
                    found.append(token[len("badge-"):])
            if len(found) >= 2:
                codes = found
                break
    assert codes is not None, "fixture must expose a live plot row with >=2 rendered badge chips"
    ranks = [badge_rank(code) for code in codes]
    assert ranks == sorted(ranks), f"rendered chip order {codes} is not rank-sorted: ranks {ranks}"
    page.close()


# Job 2 -- split the INC badge

def test_incomplete_badge_no_longer_exists():
    assert "incomplete" not in BADGE_META
    assert {"no_worksite", "worksite_incomplete"} <= set(BADGE_META)


def test_never_cleared_plot_gets_no_worksite_not_generic_incomplete(details_by_id):
    """Live-measured 2026-08-25: all 8 currently-affected plots on player0005's board
    carry BOTH `no_worksite` and the deed-level `construction_incomplete` flag
    together, and none carry a bare `unfinished_worksite`. Per the worksite ruling
    (`no_worksite` = never cleared, open-ended) that specific fact wins."""
    report = build_report()
    plot = next(p for p in report["plots"] if p["deed_uid"] == "I-298-b6a3e67e1dd6d3")
    assert {"no_worksite", "construction_incomplete"} <= {
        item["type"] for item in plot["diagnostics"]
    }
    board = project_board(report, details_by_id)
    row = next(r for r in board["plots"] if r["deed_uid"] == "I-298-b6a3e67e1dd6d3")
    assert "no_worksite" in row["badges"]
    assert "worksite_incomplete" not in row["badges"]


def test_worksite_mid_change_without_no_worksite_gets_the_other_code(details_by_id):
    """Synthetic case (not present in the live account today, per the queue's
    'do not assume all 8 are the same case' instruction) -- a worksite EXISTS and
    is mid-change, with no accompanying `no_worksite` diagnostic. Must raise
    `worksite_incomplete`, never `no_worksite`."""
    report = build_report()
    plot = next(p for p in report["plots"] if p["deed_uid"] == "I-298-5e8500ce647e4e")
    plot["diagnostics"] = [{"type": "unfinished_worksite"}]
    board = project_board(report, details_by_id)
    row = next(r for r in board["plots"] if r["deed_uid"] == "I-298-5e8500ce647e4e")
    assert "worksite_incomplete" in row["badges"]
    assert "no_worksite" not in row["badges"]


def test_plots_incomplete_total_counts_both_split_codes(details_by_id):
    report = build_report()
    board = project_board(report, details_by_id)
    expected = sum(
        1 for row in board["plots"]
        if "no_worksite" in row["badges"] or "worksite_incomplete" in row["badges"]
    )
    assert board["totals"]["plots_incomplete"] == expected
    assert expected >= 1


# Job 3 -- "staked" is not "cooling"

def test_currently_staked_card_reports_staked_state_not_cooling():
    """The root cause (2026-08-25, live testing): a currently-staked card has
    never been unstaked, so it has no `stake_end_date` -- `cooldown_state` must
    no longer collapse that into the same shape as a genuine cooldown."""
    result = cooldown_state("staked", None, "STK-LND-WKR", NOW)
    assert result["ready"] is False, "the hard-refusal gate must be unchanged"
    assert result["state"] == "staked"
    assert result["state"] != "cooling"


def test_genuinely_cooling_card_reports_cooling_state_with_ready_at():
    result = cooldown_state("stake_expired_or_historical", "2026-08-26T00:00:00Z", "STK-LND-WKR", NOW)
    assert result["ready"] is False
    assert result["state"] == "cooling"
    assert result["ready_at"] is not None


def test_unrecognised_stake_class_reports_unknown_state_not_a_fake_countdown():
    result = cooldown_state("staked", None, "STK-LND-DOES-NOT-EXIST", NOW)
    assert result["ready"] is False, "must still fail closed (project rule: fail safe, not open)"
    assert result["state"] == "unknown"
    assert result["ready_at"] is None


def test_expired_cooldown_reports_ready_state():
    result = cooldown_state("stake_expired_or_historical", "2026-01-01T00:00:00Z", "STK-LND-WKR", NOW)
    assert result["ready"] is True
    assert result["state"] == "ready"


def test_rendered_staked_candidate_tag_says_staked_not_cooling(live_browser: tuple[Browser, str]) -> None:
    """Census row 3: replaces the source-text scan of `cooldownTagHtml()`'s 'staked' branch.
    Route-mocks a worker candidate whose `cooldown` is `{state: 'staked'}` and reads the
    rendered `.candidate-tag` text directly.

    Named injection (not applied here; independently watched): `cooldownTagHtml()`'s staked
    branch template literal `staked${plot ? ...}` changed to `cooling${plot ? ...}`. Expected
    red: `assert "cooling" not in text` fails because the visible tag now reads "cooling on
    <plot>"."""
    browser, base_url = live_browser
    row = _real_board_row_with_open_slot(base_url)
    deed_uid = row["deed_uid"]
    candidate = _real_worker_candidate_template(base_url, deed_uid)
    candidate["cooldown"] = {"ready": False, "state": "staked", "ready_at": None}

    def fulfill(route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "candidates": [candidate], "total": 1, "source": "test-fixture", "excluded_counts": {}, "notices": [],
        }))

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.route(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*", fulfill)
    _open_worker_picker(page, base_url, deed_uid)
    tag = page.locator(".candidate-row .candidate-tag.is-info").first
    tag.wait_for(timeout=30_000)
    text = tag.inner_text().lower()
    assert "staked" in text
    assert "cooling" not in text, f"a staked candidate's visible tag must never say cooling, got: {text!r}"
    page.close()


def test_staked_candidate_serializes_the_real_plot_number_not_the_internal_id():
    """Internal deed UIDs like "I-294" are not user-readable -- the plot number
    (e.g. "65-10-88") is what should show instead.
    `source_deed_uid` stays as the internal staging/lookup key (unchanged
    contract, `test_frontend_removal_impact.py` covers it); `source_plot` is the
    new DISPLAY field, resolved from the source plot's own `facts.plot`."""
    card = MovableCard("Donor", "Red", 1000, 1000, "staked", "source-deed", asset_ref="donor")
    row = CandidateRow(card, {}, {}, "staked", "STK-LND-WKR", {"ready": True, "state": "staked"})
    result = serialize_candidate(row, {"source-deed": "65-10-88"})
    assert result["source_deed_uid"] == "source-deed"
    assert result["source_plot"] == "65-10-88"
    assert "I-" not in str(result["source_plot"])


def test_staked_candidate_without_a_resolved_plot_number_gets_none_not_the_deed_uid():
    card = MovableCard("Donor", "Red", 1000, 1000, "staked", "source-deed", asset_ref="donor")
    row = CandidateRow(card, {}, {}, "staked", "STK-LND-WKR", {"ready": True, "state": "staked"})
    result = serialize_candidate(row)  # no plot_number_by_deed supplied
    assert result["source_plot"] is None, "must never fall back to the internal deed_uid as display text"


def test_idle_candidate_never_carries_a_source_plot():
    card = MovableCard(
        "Historical idle", "Red", 1000, 0, "idle", "stale-prior-deed",
        bucket="stake_expired_or_historical", asset_ref="historical-idle",
    )
    row = CandidateRow(card, {}, {}, "stake_expired_or_historical", "STK-LND-WKR", {"ready": True, "state": "ready"})
    assert serialize_candidate(row, {"stale-prior-deed": "12-3-45"})["source_plot"] is None


def test_rank_candidates_resolves_real_plot_numbers_end_to_end(details_by_id):
    """Same fixture used by the badge-order/INC tests above -- every currently-
    staked candidate this endpoint returns must carry the real plot number, never
    a bare `I-###` internal id, as its `source_plot`."""
    from datetime import datetime, timezone as tz

    from ceres.candidates import rank_candidates

    report = build_report()
    deed = next(p["deed_uid"] for p in report["plots"] if p["deed_uid"] != "I-294-62529e285ddcf0")
    body = rank_candidates(
        report, deed, "worker", player="player0005", now=datetime(2026, 8, 25, tzinfo=tz.utc),
        bucket="staked", include_cooling=True, limit=50, details_by_id=details_by_id,
    )
    staked = [c for c in body["candidates"] if c["bucket"] == "staked"]
    assert staked, "fixture must produce at least one staked candidate for this check"
    for candidate in staked:
        assert candidate["source_plot"], f"{candidate['name']} is missing a resolved source_plot"
        assert not re.match(r"^-?[A-Za-z]-\d", candidate["source_plot"]), (
            f"source_plot looks like an internal deed_uid, not a plot number: {candidate['source_plot']!r}"
        )


# Follow-up -- never show the deed_uid, humanise the cooldown ready-at

def test_rendered_staked_tag_shows_source_plot_not_source_deed_uid(live_browser: tuple[Browser, str]) -> None:
    """Census row 3: replaces the source-text check that `cooldownTagHtml()`'s staked branch
    reads `candidate.source_plot` rather than `candidate.source_deed_uid`. Route-mocks a
    staked candidate carrying a real-looking plot number as `source_plot` and an internal
    deed id as `source_deed_uid`, and reads which one actually appears in the rendered tag.

    Named injection (not applied here; independently watched): `cooldownTagHtml()`'s staked
    branch changed from `candidate.source_plot` to `candidate.source_deed_uid`. Expected red:
    `assert "65-10-88" in text` fails and the internal deed id appears instead."""
    browser, base_url = live_browser
    row = _real_board_row_with_open_slot(base_url)
    deed_uid = row["deed_uid"]
    candidate = _real_worker_candidate_template(base_url, deed_uid)
    candidate["cooldown"] = {"ready": False, "state": "staked", "ready_at": None}
    candidate["source_plot"] = "65-10-88"
    candidate["source_deed_uid"] = "I-999-internalonly"

    def fulfill(route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "candidates": [candidate], "total": 1, "source": "test-fixture", "excluded_counts": {}, "notices": [],
        }))

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.route(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*", fulfill)
    _open_worker_picker(page, base_url, deed_uid)
    tag = page.locator(".candidate-row .candidate-tag.is-info").first
    tag.wait_for(timeout=30_000)
    text = tag.inner_text()
    assert "65-10-88" in text, f"staked tag must show the real plot number, got: {text!r}"
    assert "I-999-internalonly" not in text, "staked tag must never leak the internal deed_uid"
    page.close()


def test_rendered_cooling_candidate_tag_is_humanized_and_drops_seconds(live_browser: tuple[Browser, str]) -> None:
    """Census row 3: replaces the source-text checks that `humanizeInstant()` uses
    `timeStyle: 'short'`/`timeZoneName` and that the cooling branch reads `when.absolute`
    (visible label) and `when.relative` (tooltip). Route-mocks a genuinely-cooling candidate
    and reads the actual rendered tag text and its tooltip (`data-tip`).

    Named injection (not applied here; independently watched): `cooldownTagHtml()`'s cooling
    branch's visible template `cooling until ${when ? esc(when.absolute) : ...}` changed to
    interpolate `when.relative` instead of `when.absolute`. Expected red: the visible text no
    longer contains a formatted absolute date/time (`Aug`/`Sep`-style month token) and instead
    reads e.g. 'cooling until in 18h'."""
    browser, base_url = live_browser
    row = _real_board_row_with_open_slot(base_url)
    deed_uid = row["deed_uid"]
    candidate = _real_worker_candidate_template(base_url, deed_uid)
    candidate["cooldown"] = {"ready": False, "state": "cooling", "ready_at": "2026-08-26T00:00:00Z"}

    def fulfill(route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "candidates": [candidate], "total": 1, "source": "test-fixture", "excluded_counts": {}, "notices": [],
        }))

    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.route(f"**/api/plots/{urllib.parse.quote(deed_uid, safe='')}/candidates*", fulfill)
    _open_worker_picker(page, base_url, deed_uid)
    tag = page.locator(".candidate-row .candidate-tag.is-warn").first
    tag.wait_for(timeout=30_000)
    text = tag.inner_text().lower()
    assert "cooling until" in text
    assert not re.search(r"\d{1,2}:\d{2}:\d{2}", text), f"visible cooling label must not carry second precision, got: {text!r}"
    tip = tag.get_attribute("data-tip-body") or ""
    assert "ready to restake" in tip.lower(), f"tooltip must state the absolute ready time, got: {tip!r}"
    assert re.search(r"\(in \d|\( \d|\(\d+[dh]", tip) or "ago" in tip, (
        f"tooltip must also carry the relative form alongside the absolute one, got: {tip!r}"
    )
    page.close()


def test_candidate_tag_is_info_uses_the_proven_contrast_pairing():
    """Same `--bg3` + `--heading` pairing already measured >=4.5:1 in every theme
    for `.candidate-tag.is-warn` / `.slot-power-warning` -- reused rather than a
    fresh, unverified color choice."""
    source = CSS.read_text(encoding="utf-8")
    match = re.search(r"\.candidate-tag\.is-info\s*\{([^}]*)\}", source)
    assert match is not None, ".candidate-tag.is-info rule not found"
    rule_body = match.group(1)
    assert "background: var(--bg3)" in rule_body
    assert "color: var(--heading)" in rule_body


# Shared constraint -- badges must never wrap

def test_badge_row_stays_single_line_nowrap():
    source = CSS.read_text(encoding="utf-8")
    match = re.search(r"\.badge-row\s*\{([^}]*)\}", source)
    assert match is not None, ".badge-row rule not found"
    assert "flex-wrap: nowrap" in match.group(1)


def test_no_worksite_and_worksite_incomplete_have_css_rules():
    """Badge colour is no longer one CSS rule per code: badges.js gives every chip a
    `badge-group-<group>` class from the shared badge table, and badge-colours.css paints
    the groups. Both split codes must therefore sit in a painted group (the rendered
    chips' classes are checked by the live badge tests in this file)."""
    colours = (CSS.parent / "badge-colours.css").read_text(encoding="utf-8")
    for code in ("no_worksite", "worksite_incomplete"):
        group = BADGE_META[code]["state_group"]
        assert group == "warning", f"{code} must stay in the warning group, got {group!r}"
        assert f".badge.badge-group-{group} {{" in colours
