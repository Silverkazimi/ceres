"""Live Chromium coverage for the confirmed Runi unstake surface."""

from __future__ import annotations

import copy
import json
import urllib.parse
from types import SimpleNamespace

import pytest
from playwright.sync_api import Browser, Page

from ceres import capture as capture_mod
import ceres.routes_op_preview as routes_op_preview_mod
from ceres.config import CeresConfig
from ceres.routes_op_preview import op_verify
from praetoria.transport import ResponseData

from .conftest import settle, spawn_live_ceres, wait_for_board
from . import board_basic_recording as recording


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _page(live_browser: tuple[Browser, str]) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(base_url)
    wait_for_board(page)
    return page


def _runi_plot(page: Page) -> tuple[int, str, dict]:
    # The synthetic recording's Runi-only plot. The Runi occupies the shared Power slot's
    # large tile, not a group of its own; with no Power Core beside it (`runi-only`) its
    # staged unstake stays drawn there as a pending-removal tile -- on a `both` plot the
    # Power Core takes the large tile over and the Runi leaves the slot at once.
    deed_uid = recording.RUNI_SLOT5.uid
    rows = page.locator("#plot-tbody > tr.plot-row")
    index = rows.evaluate_all("(els, uid) => els.findIndex((el) => el.dataset.deedUid === uid)", deed_uid)
    assert index >= 0, deed_uid
    rows.nth(index).click()
    expanded = page.locator("tr.expand-row").nth(index)
    assert expanded.get_attribute("data-deed-uid") == deed_uid
    # the reveal draws a placeholder Power slot (no state) until the plot's detail is in
    expanded.locator(
        ".editor-band:is([data-plot-state='ready'],[data-plot-state='degraded']) .slot-power[data-power-state]"
    ).wait_for(timeout=30_000)
    assert expanded.locator(".slot-power").get_attribute("data-power-state") == "runi-only", (
        "live snapshot did not expose a staked Runi"
    )
    detail = page.evaluate(
        """async uid => {
          const response = await fetch(`/api/plots/${encodeURIComponent(uid)}`);
          return response.json();
        }""",
        deed_uid,
    )
    return index, deed_uid, detail


def _install_card_mocks(
    page: Page,
    deed_uid: str,
    detail: dict,
    state: str | None,
    verification_result: dict | None = None,
    *,
    install_verification_route: bool = True,
) -> None:
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
            "required_auths": [],
            "required_posting_auths": ["player0005"],
        },
    ]
    page.route(
        "**/api/plots/*/op-preview*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"account": "player0005", "chunks": [{"operations": [operation]}]}),
        ),
    )
    if install_verification_route:
        page.route(
            "**/api/op-verify*",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(verification_result or {"state": state, "refreshed_deeds": [deed_uid] if state in {"domain-confirmed", "domain-not-applied"} else []}),
            ),
        )
    fresh = copy.deepcopy(detail)
    fresh["workers"] = [worker for worker in fresh.get("workers", []) if int(worker.get("slot", -1)) != 0]
    fresh.setdefault("capacity", {})["runi_staked"] = False
    fresh["capacity"]["worker_count"] = max(0, int(fresh["capacity"].get("worker_count", 1)) - 1)
    fresh["bonus"] = dict(fresh.get("bonus") or {})
    fresh["bonus"].pop("runi", None)
    encoded = urllib.parse.quote(deed_uid, safe="")
    page.route(
        f"**/api/plots/{encoded}*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(fresh)),
    )
    page.evaluate(
        """window.__runiKeychain = {callback: false, requestBroadcast: (account, operations, key, callback) => {
          setTimeout(() => { window.__runiKeychain.callback = true; callback({success: true, result: {id: 'FAKE-RUNI-TX'}}); }, 25);
        }};
        window.hive_keychain = window.__runiKeychain;"""
    )


def _stage_runi_unstake(page: Page, index: int) -> None:
    expanded = page.locator("tr.expand-row").nth(index)
    runi_tile = expanded.locator(".slot-power .slot-power-large .slot-tile[data-group-id='runi'][data-occupied='1']")
    runi_tile.hover()
    runi_tile.locator(".slot-remove").click()
    hazard = page.locator("#slot-hazard-overlay")
    if not hazard.is_hidden():
        hazard.locator(".hazard-confirm").click()
    expanded.locator(".slot-tile.is-pending-removal").wait_for(timeout=30_000)


@pytest.mark.parametrize("state, remains", [("domain-confirmed", False), ("domain-pending", True)])
def test_runi_confirmation_controls_tile_notice_and_keychain_surface(
    live_browser: tuple[Browser, str], state: str, remains: bool,
) -> None:
    page = _page(live_browser)
    index, deed_uid, detail = _runi_plot(page)
    _install_card_mocks(page, deed_uid, detail, state)
    _stage_runi_unstake(page, index)

    checkout = page.locator("#checkout-btn")
    checkout.click()
    panel = page.locator("#checkout-panel")
    # no Prepare step: opening Changes requests the portfolio preview itself (checkout.js), and
    # the footer's one Sign button arms once that preview is in
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    page.wait_for_function("window.__runiKeychain.callback === true")
    # once the wallet has signed, the Changes panel closes and its Sign button is gone
    page.wait_for_function(
        "!document.querySelector('#checkout-panel button[data-op-action=confirm]')"
        " || document.querySelector('#checkout-panel button[data-op-action=confirm]')?.hidden === true"
        " || document.querySelector('#checkout-btn')?.hidden === true",
        timeout=30_000,
    )

    if remains:
        page.wait_for_function(
            "document.querySelector('#checkout-btn')?.hidden === false && document.querySelector('.slot-tile.is-pending-removal')",
            timeout=30_000,
        )
        assert page.locator("#ceres-confirmation-banner").count() == 0
    else:
        page.wait_for_function(
            "index => !document.querySelectorAll('tr.expand-row')[index]?.querySelector(\".slot-power .slot-power-large .slot-tile[data-group-id='runi'][data-occupied='1']\")",
            arg=index,
            timeout=30_000,
        )
        banner = page.locator("#ceres-confirmation-banner")
        banner.wait_for(timeout=30_000)
        assert "Runi removed from the Power slot" in banner.inner_text()
        assert "+100% production boost" in banner.inner_text()
        assert "no unstake cooldown" in banner.inner_text()
        assert page.locator("#checkout-panel").is_hidden()
        assert banner.is_visible()
    page.close()


def test_runi_not_applied_verdict_clears_in_flight_and_explains_re_stake(
    live_browser: tuple[Browser, str],
) -> None:
    """Mutation evidence: route `domain-not-applied` through the pending path; expected red:
    the signed Runi remains locked or the banner says it is still waiting instead of telling the
    maintainer that no game change occurred and exposing Prepare again."""
    page = _page(live_browser)
    index, deed_uid, detail = _runi_plot(page)
    _install_card_mocks(page, deed_uid, detail, "domain-not-applied")
    _stage_runi_unstake(page, index)

    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    # Opening Changes requests the portfolio preview and arms the Sign button.
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    banner = page.locator("#ceres-confirmation-banner.phase-mismatch")
    banner.wait_for(timeout=30_000)
    text = banner.inner_text()
    assert "landed on chain" in text
    assert "did not apply this change" in text
    # The pill truncates the notice; its title contains the full message.
    assert "Prepare and sign again" in (banner.locator(".ceres-pill-detail").get_attribute("title") or "")
    assert page.locator("#checkout-panel .checkout-move-status").count() == 0
    assert page.evaluate(
        "Object.keys(JSON.parse(localStorage.getItem('ceres:in-flight:v1:player0005') || '{\"records\":{}}').records).length"
    ) == 0
    page.wait_for_function("document.querySelector('#checkout-btn')?.hidden === false", timeout=30_000)
    page.locator("#checkout-btn").click()
    page.wait_for_function(
        "document.querySelector('#checkout-panel button[data-op-action=confirm]')?.disabled === false",
        timeout=30_000,
    )
    page.close()


def test_below_floor_pending_keeps_one_broadcast_after_two_portfolio_confirms(
    live_browser: tuple[Browser, str], tmp_path, monkeypatch,
) -> None:
    """Guard 1, decisive money assertion. Mutation evidence: remove the server age floor so
    the mocked below-floor read becomes `domain-not-applied`; the existing client terminal branch
    then clears `txId` and a second portfolio confirm produces two observed Keychain broadcasts."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script("window.__ceresOperationPollSchedule = [10, 20, 30];")
    page.goto(f"{base_url}?ir1882-guard=1")
    wait_for_board(page)
    index, deed_uid, detail = _runi_plot(page)
    _install_card_mocks(page, deed_uid, detail, "domain-pending", install_verification_route=False)
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", lambda *_args, **_kwargs: {
        "status": "chain_verified", "evidence": {"tx_id": "FAKE-BELOW-FLOOR-TX", "block_num": 100, "observed": []},
    })
    monkeypatch.setattr(
        routes_op_preview_mod.fides_verify,
        "_http_rpc_call",
        lambda node, method, params: {"head_block_number": 119},
    )
    # the below-floor read: the deed still shows the Runi the transaction unstakes (an empty
    # read would be the unstake applied, i.e. domain-confirmed, and clear the move)
    still_staked = [{"uid": recording.RUNI_SLOT5.runi.uid, "slot": 0}]
    monkeypatch.setattr(
        capture_mod.MnemosyneEvidence,
        "assets",
        lambda self, deed_uid: ResponseData({"status": "success", "data": {"cards": still_staked, "items": []}}, "2026-09-05T00:00:00Z", 1),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))

    def _real_verify(route):
        result = op_verify(route.request.post_data_json, request, player="player0005")
        route.fulfill(status=200, content_type="application/json", body=result.body)

    page.route("**/api/op-verify*", _real_verify)
    # Counts the Runi's stake-change broadcasts. Signing Changes also batches the recording's
    # finished construction ("Feed the workers", a land operation) into the same run.
    page.evaluate(
        """window.__floorBroadcastCount = 0;
        window.hive_keychain = {requestBroadcast: (account, operations, key, callback) => {
          if (JSON.stringify(operations).includes('sm_stake_change')) window.__floorBroadcastCount += 1;
          callback({success: true, result: {id: 'FAKE-BELOW-FLOOR-TX'}});
        }};"""
    )
    _stage_runi_unstake(page, index)
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    # no Prepare step: opening Changes requests the portfolio preview itself (checkout.js), and
    # the footer's one Sign button arms once that preview is in
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    # the signed move carries its transaction (the Changes panel and its move status close on signing)
    page.wait_for_function("window.__floorBroadcastCount === 1 && CERES_STAGE.some((entry) => entry.txId && entry.broadcast_at)", timeout=30_000)
    settle(page)
    page.evaluate("confirmOperationBroadcast({scope: 'portfolio'})")
    assert page.evaluate("window.__floorBroadcastCount") == 1
    entry = page.evaluate("({txId: CERES_STAGE[0]?.txId, broadcastAt: CERES_STAGE[0]?.broadcast_at})")
    assert entry["txId"] == "FAKE-BELOW-FLOOR-TX"
    assert entry["broadcastAt"]
    page.close()
