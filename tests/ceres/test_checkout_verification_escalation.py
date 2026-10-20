"""Live Chromium coverage for : the checkout broadcast path's "Check again"
affordance and the escalated-pending state, driven through a real Runi unstake
(the smallest single-op flow already established by test_live_runi_confirmation.py),
with `/api/op-verify` mocked so no signing or real broadcast happens.
"""

from __future__ import annotations

import copy
import json

import pytest
from playwright.sync_api import Browser, Page

from .conftest import spawn_live_ceres, wait_for_board


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _page(live_browser: tuple[Browser, str], *, compressed_schedule: bool = False) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    if compressed_schedule:
        page.add_init_script("window.__ceresOperationPollSchedule = [10, 20, 30, 40, 50, 60];")
    page.goto(f"{base_url}?ir1882-guard=1" if compressed_schedule else base_url)
    wait_for_board(page)
    return page


def _runi_plot(page: Page) -> tuple[int, str, dict]:
    for index in range(page.locator("#plot-tbody > tr.plot-row").count()):
        row = page.locator("#plot-tbody > tr.plot-row").nth(index)
        if row.locator("xpath=following-sibling::tr[1]").evaluate("node => node.style.display") == "none":
            row.click()
        expanded = page.locator("tr.expand-row").nth(index)
        expanded.locator(".editor-band[data-plot-state='ready']").wait_for(timeout=30_000)
        runi_tile = expanded.locator(
            ".slot-power[data-power-state='both'], .slot-power[data-power-state='runi-only']"
        )
        if runi_tile.count() != 1:
            continue
        deed_uid = row.get_attribute("data-deed-uid")
        assert deed_uid
        detail = page.evaluate(
            """async uid => {
 const response = await fetch(`/api/plots/${encodeURIComponent(uid)}`);
 return response.json();
 }""",
            deed_uid,
        )
        return index, deed_uid, detail
    raise AssertionError("live snapshot did not expose a staked Runi")


def _stage_runi_unstake(page: Page, index: int) -> None:
    expanded = page.locator("tr.expand-row").nth(index)
    runi_tile = expanded.locator(".slot-power .slot-power-large .slot-tile[data-group-id='runi'][data-occupied='1']")
    runi_tile.hover()
    runi_tile.locator(".slot-remove").click()
    hazard = page.locator("#slot-hazard-overlay")
    if not hazard.is_hidden():
        hazard.locator(".hazard-confirm").click()
    # With a Power Core still staked, the combined power tile shows the Core
    # alone after Runi removal; the removed Runi does not occupy a second tile.
    expanded.locator(".slot-power[data-power-state='core-only']").wait_for(timeout=30_000)


def test_checkout_verification_escalates_after_repeated_check_again_clicks(
    live_browser: tuple[Browser, str], tmp_path,
) -> None:
    page = _page(live_browser)
    index, deed_uid, detail = _runi_plot(page)
    runi_uid = next(worker["asset_ref"] for worker in detail.get("workers", []) if int(worker.get("slot", -1)) == 0)
    operation = [
        "custom_json",
        {
            "id": "sm_stake_change",
            "json": json.dumps({
                "deed_uid": deed_uid,
                "unstake": {"cards": [{"card_uid": runi_uid}], "items": []},
                "stake": {"cards": [], "items": []},
            }),
            "required_auths": [], "required_posting_auths": ["sample01"],
        },
    ]
    page.route(
        "**/api/op-preview*",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"account": "sample01", "chunks": [{"operations": [operation]}]}),
        ),
    )

    # `/api/op-verify` returns `domain-pending` on attempt 1-4 and the escalated
    # notice on attempt 5+ -- exactly what fides_land/verify.py's real
    # PENDING_ESCALATION_ATTEMPTS=5 does, but driven deterministically rather than
    # waiting on five real chain round-trips.
    def _verify(route):
        body = route.request.post_data_json or {}
        attempt = int(body.get("verification_attempt", 1))
        escalated = attempt >= 5
        code = "verification_pending_escalated" if escalated else "verification_pending"
        message = (
            "Still not confirmed after several checks — this did not resolve in the "
            "expected window. It may still be propagating, or it may need attention "
            "check the game directly, or try again."
        ) if escalated else "Not confirmed yet — the game may still be catching up."
        notice = {
            "code": code, "kind": "verification", "severity": "caution" if escalated else "info",
            "blocking": "repeat", "message": message, "reason": None, "evidence": {},
            "action": {"id": "retry_verification", "label": "Check again"},
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "state": "domain-pending", "refreshed_deeds": [], "notices": [notice],
        }))

    page.route("**/api/op-verify*", _verify)
    page.evaluate(
        """window.__escKeychain = {requestBroadcast: (account, operations, key, callback) => {
 setTimeout(() => callback({success: true, result: {id: 'FAKE-ESC-TX'}}), 25);
 }};
 window.hive_keychain = window.__escKeychain;"""
    )

    _stage_runi_unstake(page, index)
    checkout = page.locator("#checkout-btn")
    checkout.click()
    panel = page.locator("#checkout-panel")
    panel.locator("button[data-op-action='confirm']").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()

    banner = page.locator("#ceres-confirmation-banner.phase-pending")
    banner.wait_for(state="visible", timeout=30_000)
    retry_button = page.locator("#ceres-confirmation-banner [data-confirmation-action='retry-verification']")
    retry_button.wait_for(timeout=5_000)
    page.screenshot(path=str(tmp_path / "pending_change_check_again.png"))

    # Click "Check again" four more times (attempts 2, 3, 4, 5) -- the 5th response
    # is the escalated notice.
    for _ in range(4):
        retry_button.click()
        page.locator("#ceres-confirmation-banner.phase-pending "
                     "#ceres-confirmation-banner.phase-pending-escalated").first.wait_for(timeout=30_000)

    page.locator("#ceres-confirmation-banner.phase-pending-escalated").wait_for(timeout=30_000)
    page.screenshot(path=str(tmp_path / "escalated_pending.png"))

    # The abandoned-chunk defect this whole surface guards against: nothing must
    # have been aborted -- the Confirm/broadcast button was consumed by the single
    # real broadcast, never re-shown as if the batch had failed.
    assert page.locator("#ceres-confirmation-banner.phase-mismatch").count() == 0
    page.close()


@pytest.mark.parametrize("operation_id", ["sm_stake_change", "sm_land_operation"])
def test_escalation_schedule_is_stake_only_for_the_settle_floor(
    live_browser: tuple[Browser, str], operation_id: str,
) -> None:
    """Guard 5. Mutation evidence: restore the unconditional escalation stop, or widen the
 carve-out to every family; the stake row then misses attempt 6, or the worksite row polls
 past escalation. The check uses observed poll requests and the exposed poller map, not a
 source position or a state-only assertion."""
    page = _page(live_browser, compressed_schedule=True)
    index, deed_uid, detail = _runi_plot(page)
    runi_uid = next(worker["asset_ref"] for worker in detail.get("workers", []) if int(worker.get("slot", -1)) == 0)
    operation = [
        "custom_json",
        {
            "id": operation_id,
            "json": json.dumps({
                "deed_uid": deed_uid,
                "unstake": {"cards": [{"card_uid": runi_uid}], "items": []},
                "stake": {"cards": [], "items": []},
            }),
            "required_auths": [], "required_posting_auths": ["sample01"],
        },
    ]
    page.route(
        "**/api/op-preview*",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"account": "sample01", "chunks": [{"operations": [operation]}]}),
        ),
    )
    attempts: list[int] = []

    def _verify(route):
        attempt = int((route.request.post_data_json or {}).get("verification_attempt", 1))
        attempts.append(attempt)
        if operation_id == "sm_stake_change" and attempt >= 6:
            notice = {"code": "verification_not_applied", "kind": "verification", "severity": "danger", "blocking": "repeat", "message": "The game did not apply this change."}
            payload = {"state": "domain-not-applied", "refreshed_deeds": [], "notices": [notice]}
        else:
            escalated = attempt >= 5
            notice = {"code": "verification_pending_escalated" if escalated else "verification_pending", "kind": "verification", "severity": "caution", "blocking": "repeat", "message": "Still not confirmed", "action": {"id": "retry_verification", "label": "Check again"}}
            payload = {"state": "domain-pending", "refreshed_deeds": [], "notices": [notice]}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/op-verify*", _verify)
    page.evaluate(
        """window.__floorBroadcastCount = 0;
 window.hive_keychain = {requestBroadcast: (account, operations, key, callback) => {
 window.__floorBroadcastCount += 1;
 callback({success: true, result: {id: 'FAKE-FLOOR-TX'}});
 }};"""
    )
    _stage_runi_unstake(page, index)
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.locator("button[data-op-action='confirm']").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()

    if operation_id == "sm_stake_change":
        page.locator("#ceres-confirmation-banner.phase-mismatch").wait_for(timeout=30_000)
        assert attempts == [1, 2, 3, 4, 5, 6]
        assert page.evaluate("window.__floorBroadcastCount") == 1
        # A not-applied verdict clears the tx marker but keeps the staged
        # change available for review; the power tile still shows Core only.
        page.locator("tr.expand-row").nth(index).locator(".slot-power[data-power-state='core-only']").wait_for(timeout=30_000)
    else:
        page.locator("#ceres-confirmation-banner.phase-pending-escalated").wait_for(timeout=30_000)
        page.wait_for_timeout(200)
        assert attempts == [1, 2, 3, 4, 5]
        assert page.evaluate("window.__floorBroadcastCount") == 1
        assert page.locator("#ceres-confirmation-banner [data-confirmation-action='retry-verification']").count() == 1
    page.close()
