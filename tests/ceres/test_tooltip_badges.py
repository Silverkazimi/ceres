import re

import pytest

from tests.ceres.conftest import spawn_live_ceres, wait_for_board


def test_board_badge_tooltips():
    """INJECTION: restore a legacy badge tooltip or forbidden pending-state vocabulary."""
    with spawn_live_ceres() as (browser, base_url):
        page = browser.new_page()
        try:
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            wait_for_board(page)

            assert all(
                badge.get_attribute("data-tip") is None
                for badge in page.locator(".badge-underfilled").all()
            )
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
                assert badge.get_attribute("data-tip-key") == "+100%"
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
            for badge in page.locator("[data-tip]").all():
                for attribute in ("data-tip", "data-tip-body", "data-tip-foot"):
                    assert not forbidden.search(badge.get_attribute(attribute) or "")

            over_cap = page.locator(".badge-over_cap")
            for badge in over_cap.all():
                assert badge.get_attribute("data-tip")
                assert badge.get_attribute("data-tip-body")
        finally:
            page.close()
