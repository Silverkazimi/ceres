""" live-browser guards for terminal game refusals."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Browser, Page

from .conftest import settle, spawn_live_ceres, wait_for_board


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _expand(page: Page, index: int) -> None:
    row = page.locator("#plot-tbody > tr.plot-row").nth(index)
    if row.locator("xpath=following-sibling::tr[1]").evaluate("node => node.style.display") == "none":
        row.click()
    page.locator("tr.expand-row").nth(index).locator(".slot-editor").wait_for(timeout=30_000)
    # The editor first draws a loading skeleton (view-only empty tiles with a spinner)
    # until the plot's details arrive; only then are its empty slots real.
    page.wait_for_function(
        f"!document.querySelectorAll('tr.expand-row')[{index}]?.querySelector('.slot-editor .slot-loading-spinner')",
        timeout=30_000,
    )


def _candidate_name(candidate) -> str:
    return candidate.locator(".candidate-name").evaluate(
        "element => element.firstChild?.textContent?.trim() || element.textContent.trim()"
    )


def _stage_worker(page: Page, index: int, used_name: str | None = None) -> tuple[str, str]:
    _expand(page, index)
    row = page.locator("#plot-tbody > tr.plot-row").nth(index)
    deed_uid = row.get_attribute("data-deed-uid")
    assert deed_uid
    expanded = page.locator("tr.expand-row").nth(index)
    worker = expanded.locator(".slot-group[data-group-id='worker']")
    if int(worker.locator(".slot-group-count").inner_text().split("/")[1]) == 0:
        raise AssertionError("selected plot has no worker capacity")
    empty = worker.locator(".slot-grid > .slot-tile-cell > .slot-tile.is-empty:not(.is-view-only)").first
    assert empty.count() == 1
    with page.expect_request("**/api/plots/*/candidates*"):
        empty.click()
    expanded.locator(".candidate-row").first.wait_for(timeout=30_000)
    candidates = expanded.locator(".candidate-row")
    chosen = 0
    if used_name:
        for candidate_index in range(candidates.count()):
            if _candidate_name(candidates.nth(candidate_index)) != used_name:
                chosen = candidate_index
                break
    chosen_name = _candidate_name(candidates.nth(chosen))
    before = int(page.locator("#checkout-count").text_content() or "0") if page.locator("#checkout-btn").is_visible() else 0
    candidates.nth(chosen).click()
    page.locator("#checkout-btn").wait_for(state="visible", timeout=30_000)
    page.wait_for_function(
        f"document.querySelector('#checkout-count')?.textContent === '{before + 1}'", timeout=30_000,
    )
    return deed_uid, chosen_name


def _stage_two_workers(page: Page) -> tuple[str, str]:
    first_index = None
    first_deed = None
    first_name = None
    for index in range(page.locator("#plot-tbody > tr.plot-row").count()):
        try:
            first_deed, first_name = _stage_worker(page, index)
            first_index = index
            break
        except AssertionError:
            continue
    assert first_index is not None and first_deed and first_name
    for index in range(first_index + 1, page.locator("#plot-tbody > tr.plot-row").count()):
        try:
            second_deed, _ = _stage_worker(page, index, first_name)
            return first_deed, second_deed
        except AssertionError:
            continue
    raise AssertionError("live snapshot did not expose two plots with worker capacity")


def _stage_one_worker(page: Page) -> str:
    for index in range(page.locator("#plot-tbody > tr.plot-row").count()):
        try:
            deed_uid, _ = _stage_worker(page, index)
            return deed_uid
        except AssertionError:
            continue
    raise AssertionError("live snapshot did not expose a plot with worker capacity")


def _two_chunk_preview(deed_a: str, deed_b: str) -> dict:
    def operation(deed_uid: str) -> list:
        return [
            "custom_json",
            {
                "id": "sm_stake_change",
                "json": json.dumps({"deed_uid": deed_uid, "stake": {"cards": [{"card_uid": "CARD-1"}]}, "unstake": {"cards": []}}),
                "required_auths": [],
                "required_posting_auths": ["player-0001"],
            },
        ]

    return {
        "account": "player-0001",
        "chunks": [{"operations": [operation(deed_a)]}, {"operations": [operation(deed_b)]}],
    }


def _one_chunk_preview(deed_uid: str) -> dict:
    return {
        "account": "player-0001",
        "chunks": [{"operations": _two_chunk_preview(deed_uid, deed_uid)["chunks"][0]["operations"]}],
    }


def _counting_keychain() -> str:
    return """
        window.__hkRefusalBroadcastCount = 0;
        let hkOtherCount = 0;
        window.hive_keychain = {requestBroadcast: (account, operations, key, callback) => {
          // Changes also signs the recording's ready worksite feed in the same run;
          // only the staged card changes (sm_stake_change) are counted here.
          if (!operations.some((operation) => operation[1]?.id === 'sm_stake_change')) {
            callback({success: true, result: {id: `HK-OTHER-TX-${++hkOtherCount}`}});
            return;
          }
          window.__hkRefusalBroadcastCount += 1;
          callback({success: true, result: {id: `HK-REFUSED-TX-${window.__hkRefusalBroadcastCount}`}});
        }};
    """


# The pill is shared with the recording's worksite feed, signed in the same run, whose own
# verdicts can replace it; this records every pill text that was shown.
PILL_RECORDER = """window.__hkPill = [];
new MutationObserver(() => {
  const text = document.querySelector('#ceres-confirmation-banner')?.textContent || '';
  if (text && window.__hkPill[window.__hkPill.length - 1] !== text) window.__hkPill.push(text);
}).observe(document, {subtree: true, childList: true, characterData: true});"""


def test_game_refusal_aborts_later_checkout_chunks(live_browser: tuple[Browser, str]) -> None:
    """A first-chunk game refusal is terminal for this checkout sequence.

    Mutation evidence for a separate verifier: replace the terminal throw in
    `verifyBroadcast()`'s `game-refused` branch with `return result`. This test then
    goes red because the integer Keychain count becomes 2 instead of 1.
    """
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(_counting_keychain())
    page.add_init_script(PILL_RECORDER)
    page.goto(base_url)
    wait_for_board(page)
    deed_a, deed_b = _stage_two_workers(page)
    page.route(
        "**/api/op-preview*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(_two_chunk_preview(deed_a, deed_b))),
    )
    refusal_reason = "The plot no longer has room for this worker."
    page.route(
        "**/api/op-verify*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "state": "game-refused",
                "game": {"evidence": {"reason": refusal_reason}},
                "notices": [{
                    "code": "verification_game_refused", "kind": "verification",
                    "severity": "danger", "blocking": "repeat", "reason": refusal_reason,
                    "message": f"The game refused this operation: {refusal_reason}", "aria": "assertive",
                }],
            }),
        ),
    )
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    # The pill reports the stopped batch (its title reads "Batch partially completed" only
    # once a transaction is marked failed or aborted) and the game's refusal.
    page.wait_for_function(
        "window.__hkPill.some((text) => text.includes('Batch partially completed') && text.includes('The plot no longer has room for this worker'))",
        timeout=30_000,
    )
    settle(page)
    assert page.evaluate("window.__hkRefusalBroadcastCount") == 1, "a later chunk was broadcast after chunk 1 was refused"
    page.close()


def test_polled_game_refusal_is_handled_without_an_unhandled_rejection(
    live_browser: tuple[Browser, str],
) -> None:
    """The scheduled re-check consumes the terminal refusal after the maintainer sees it."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script("window.__ceresOperationPollSchedule = [10];")
    page.add_init_script(_counting_keychain())
    # The pill is shared with the recording's worksite feed, signed in the same run, whose
    # own pending verdicts can replace it; record every pill text that was shown.
    page.add_init_script(PILL_RECORDER)
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.goto(base_url)
    wait_for_board(page)
    deed_a = _stage_one_worker(page)
    page.route(
        "**/api/op-preview*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(_one_chunk_preview(deed_a))),
    )
    attempts = {"count": 0}
    refusal_reason = "A later game check refused this operation."

    def verify(route) -> None:
        if deed_a not in (route.request.post_data or ""):
            # Changes also signs the recording's ready worksite feed in the same run;
            # its own verification is not part of this card change's checks.
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"state": "domain-pending", "notices": []}))
            return
        attempts["count"] += 1
        if attempts["count"] == 1:
            body = {"state": "domain-pending", "notices": [{
                "code": "verification_pending", "kind": "verification", "severity": "caution",
                "blocking": "repeat", "message": "Still waiting for the game.",
            }]}
        else:
            body = {"state": "game-refused", "game": {"evidence": {"reason": refusal_reason}}, "notices": [{
                "code": "verification_game_refused", "kind": "verification", "severity": "danger",
                "blocking": "repeat", "reason": refusal_reason,
                "message": f"The game refused this operation: {refusal_reason}", "aria": "assertive",
            }]}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/api/op-verify*", verify)
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    page.wait_for_function("window.__hkRefusalBroadcastCount === 1", timeout=30_000)
    page.wait_for_function(
        "window.__hkPill.some((text) => text.includes('The game refused this operation: A later game check refused') && text.includes('HK-REFUS…TX-1'))",
        timeout=30_000,
    )
    settle(page)
    assert attempts["count"] == 2
    assert page.evaluate("window.__hkRefusalBroadcastCount") == 1
    assert page_errors == []
    page.close()
