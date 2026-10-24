import re

import pytest

from tests.ceres.conftest import LIVE_PLAYER, spawn_live_ceres, wait_for_board


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


def test_board_badge_tooltips():
    """INJECTION: restore a legacy badge tooltip or forbidden pending-state vocabulary."""
    with spawn_live_ceres() as (browser, base_url):
        page = browser.new_page()
        try:
            # The app reads the account from the URL; the harness only adds it to a bare
            # origin, so a "/" URL opened the start page and never a board.
            page.goto(f"{base_url}/?player={LIVE_PLAYER}", wait_until="domcontentloaded")
            wait_for_board(page)
            _open_badge_slideouts(page)

            # The underfilled chip shows a live open-slot count now and its accessible
            # tooltip states that count in plain words -- never the legacy "Underfilled" label.
            # (Chips in the slide-out never raise a hover tooltip: tooltip.js NO_TIP_ZONE.)
            underfilled = page.locator(".badge-underfilled").all()
            assert underfilled, "the fixture board has underfilled plots"
            assert all(
                re.fullmatch(r"\d+ of \d+ worker slots? empty", badge.get_attribute("data-tip") or "")
                for badge in underfilled
            ), [badge.get_attribute("data-tip") for badge in underfilled]
            assert all(
                badge.get_attribute("data-tip") is None
                for badge in page.locator(".foil-badge").all()
            )

            board_response = page.request.get(f"{base_url}/api/board")
            assert board_response.ok
            board = board_response.json()
            badge_meta = board.get("badge_meta", {})
            backend_severity = badge_meta["unpowered"]["severity"]
            unpowered = page.locator(".badge-unpowered")
            # peer-group tooltip coverage may skip only when this live snapshot state is absent.
            if unpowered.count() == 0:
                pytest.skip("No unpowered plot is present in the live snapshot")
            for badge in unpowered.all():
                assert badge.get_attribute("data-tip") == "Unpowered"
                assert "Power Core" in (badge.get_attribute("data-tip-body") or "")
                assert badge.get_attribute("data-tip-kind") == "warn"
                assert badge.get_attribute("data-tip-severity") == backend_severity

            runi = page.locator(".badge-runi")
            # R-138: select from the current rendered snapshot instead of a stale hard-coded fixture.
            if runi.count() == 0:
                pytest.skip("No plot with a staked Runi is present in the live snapshot")
            for badge in runi.all():
                # the boost moved from a headline key into the title line
                assert "+100% production" in (badge.get_attribute("data-tip") or "")
                assert badge.get_attribute("data-tip-body")

            idle = page.locator(".badge-idle")
            # the tooltip contract is stable even when this optional snapshot state is absent.
            if idle.count() == 0:
                pytest.skip("No idle plot is present in the live snapshot")
            for badge in idle.all():
                tip = badge.get_attribute("data-tip")
                assert tip and len(tip) <= 40
                assert badge.get_attribute("data-tip-body") is None

            forbidden = re.compile(r"\b(?:pending|queued|in progress|complete|done)\b", re.IGNORECASE)
            # The right-edge badge bar sorts plots into status groups, one of them named
            # "Pending change" (badges.js badgeStripStatus, with its "Show plots with pending
            # changes" filter); that group name is the bar's own vocabulary. Every other
            # tooltip, every badge chip included, stays free of the pending-state words.
            for badge in page.locator("[data-tip]:not(#badge-bar *):not(#badge-filter-tray *)").all():
                for attribute in ("data-tip", "data-tip-body", "data-tip-foot"):
                    assert not forbidden.search(badge.get_attribute(attribute) or "")

            over_cap = page.locator(".badge-over_cap")
            for badge in over_cap.all():
                assert badge.get_attribute("data-tip")
                assert badge.get_attribute("data-tip-body")
        finally:
            page.close()
