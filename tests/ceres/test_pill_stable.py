"""the shared confirmation pill keeps fixed text and row geometry."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Browser, TimeoutError as PlaywrightTimeoutError

from .conftest import spawn_live_ceres


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def test_pending_worksite_verification_keeps_shared_pill_stable(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.add_init_script(
        "window.__ir8322KeychainCalls = 0; window.__ir8322TitleHistory = []; "
        "new MutationObserver(() => { const node = document.querySelector('#ceres-confirmation-banner .worksite-banner-title > span:last-child'); "
        "const title = node?.innerText || ''; const prior = window.__ir8322TitleHistory.at(-1)?.title; "
        "if (title && title !== prior) { const pill = document.querySelector('#ceres-confirmation-banner'); "
        "const rect = el => { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; }; "
        "window.__ir8322TitleHistory.push({title, outer: rect(pill), titleBox: rect(pill.querySelector('.worksite-banner-title')), "
        "detailBox: rect(pill.querySelector('.worksite-banner-detail'))}); } })"
        ".observe(document, {subtree: true, childList: true, characterData: true}); "
        "window.hive_keychain = {requestBroadcast(_account, _ops, _key, callback) { "
        "window.__ir8322KeychainCalls += 1; callback({success: true, result: {id: 'ir8322-mocked-tx'}, data: {key: 'Posting'}}); }};"
    )
    selected: dict[str, dict] = {}
    verify_calls: list[int] = []
    fresh_harvest = {"value": False}

    def completion_row(row: dict) -> dict:
        result = dict(row)
        result.update({"assets_loaded": True, "is_construction": True, "worksite_type": "Grain Farm"})
        result["worksite_completion"] = {
            "kind": "worksite_completion", "project_id": "ir8322-project", "to": "grain",
            "to_label": "Grain Farm", "grain_cost": 1,
        }
        result["harvest"] = {"state": "construction", "construction": {
            "to": "grain", "to_label": "Grain Farm", "started_at": "2026-09-20T00:00:00Z",
            "ready_at": "2026-09-25T00:00:00Z", "progress_pct": 100,
        }}
        return result

    def board_route(route) -> None:
        payload = route.fetch().json()
        row = next(plot for plot in payload["plots"] if plot.get("deed_uid"))
        selected["row"] = completion_row(row)
        payload["plots"][payload["plots"].index(row)] = selected["row"]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    def plot_route(route) -> None:
        url = route.request.url
        uid = selected.get("row", {}).get("deed_uid")
        if not uid or f"/api/plots/{uid}" not in url:
            route.continue_()
            return
        if "/harvest" in url:
            fresh_harvest["value"] = fresh_harvest["value"] or "fresh=1" in url
            projection = ({"state": "harvesting", "source": "fresh", "token": "GRAIN", "worksite_type": "Grain Farm",
                           "anchor_at": "2026-09-23T00:00:00Z", "net_at_anchor": 1, "gross_per_hour": 1,
                           "hours_at_anchor": 0, "tax_rate": 0, "costs": []}
                          if fresh_harvest["value"] else {"state": "construction", "source": "fresh"})
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "server_now": "2026-09-23T00:00:00Z", "projection": projection,
                "region_balances": None, "affordability": {"status": "unknown"}, "issues": [], "signing_enabled": True,
            }))
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(selected["row"]))

    def preview_route(route) -> None:
        uid = selected["row"]["deed_uid"]
        operation = ["custom_json", {"id": "sm_land_operation", "json": json.dumps({"op": "update_worksite", "deed_uid": uid}),
                                      "required_auths": [], "required_posting_auths": ["player0005"]}]
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "account": "player0005", "chunks": [{"operations": [operation]}],
        }))

    def verify_route(route) -> None:
        verify_calls.append(len(verify_calls) + 1)
        result = ({"state": "domain-confirmed", "refreshed_deeds": [selected["row"]["deed_uid"]], "refresh_errors": ["harvest refresh failed"]}
                  if len(verify_calls) == 3 else {"state": "chain_absent"})
        route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

    page.route("**/api/board*", board_route)
    page.route("**/api/plots/**", plot_route)
    page.route("**/worksite-preview*", preview_route)
    page.route("**/api/op-inclusion*", lambda route: route.fulfill(status=200, content_type="application/json", body='{"included":true,"block_num":123}'))
    page.route("**/api/op-verify*", verify_route)
    page.goto(f"{base_url}/?player=sample01", timeout=8_000)
    try:
        page.wait_for_function("document.querySelector('#plot-tbody > tr.plot-row') !== null", timeout=15_000)
    except PlaywrightTimeoutError:
        pytest.fail("live Ceres board did not render a plot row within 15 seconds", pytrace=False)

    uid = selected["row"]["deed_uid"]
    row = page.locator(f'tr.plot-row[data-deed-uid="{uid}"]')
    row.click(timeout=8_000)
    feed = page.locator(f'tr.expand-row[data-deed-uid="{uid}"] .harvest-bar [data-harvest-action="feed-workers"]')
    feed.wait_for(state="visible", timeout=8_000)
    feed.click(timeout=8_000)
    sign = page.locator('#worksite-modal-overlay [data-worksite-action="broadcast"]:not([disabled])')
    sign.wait_for(state="visible", timeout=8_000)
    try:
        with page.expect_response(lambda response: "/api/op-verify" in response.url, timeout=4_000):
            sign.click(timeout=8_000)
    except PlaywrightTimeoutError:
        pytest.fail("mocked worksite signing did not trigger verification", pytrace=False)

    try:
        page.wait_for_function("() => document.querySelector('#ceres-confirmation-banner .worksite-banner-title > span:last-child')?.innerText === 'Awaiting confirmation'", timeout=8_000)
        page.wait_for_function("() => window.__ir8322TitleHistory.filter(item => item.title === 'Awaiting confirmation').length === 1", timeout=2_000)
        page.wait_for_function("() => document.querySelector('#ceres-confirmation-banner .worksite-banner-title > span:last-child')?.innerText === 'Transaction confirmed'", timeout=8_000)
    except PlaywrightTimeoutError:
        pytest.fail("mocked pending → pending → confirmed worksite verification did not complete", pytrace=False)

    final_snapshot = page.evaluate("""() => {
 const pill = document.querySelector('#ceres-confirmation-banner');
 return {text: pill.querySelector('.worksite-banner-title > span:last-child').innerText,
 width: pill.getBoundingClientRect().width, height: pill.getBoundingClientRect().height};
 }""")
    assert final_snapshot["text"] == "Transaction confirmed", final_snapshot
    assert page.locator("#ceres-confirmation-banner").evaluate("el => el.classList.contains('is-confirmed-partial')"), "confirmed-with-refresh-error did not advance to confirmed-partial"
    history = page.evaluate("window.__ir8322TitleHistory")
    assert not any("Checking the chain" in item["title"] or "Not confirmed yet" in item["title"] for item in history), history
    phase_history = [item for item in history if item["title"] in {
        "Transaction sent", "Awaiting confirmation", "Transaction confirmed"
    }]
    assert len(phase_history) >= 3, history
    for item in phase_history[1:]:
        assert item["outer"] == phase_history[0]["outer"], (phase_history[0], item)
        assert item["titleBox"] == phase_history[0]["titleBox"], (phase_history[0], item)
        assert item["detailBox"] == phase_history[0]["detailBox"], (phase_history[0], item)
    assert verify_calls == [1, 2, 3], f"expected exactly three mocked verify calls, got {verify_calls}"
    assert page.evaluate("window.__ir8322KeychainCalls") == 1, "worksite cycle must use one mocked Keychain request"
    assert fresh_harvest["value"], "domain-confirmed worksite feed did not refresh the harvest projection"

    samples_by_viewport = {}
    for viewport_width in (1920, 1536):
        page.set_viewport_size({"width": viewport_width, "height": 1080})
        samples_by_viewport[str(viewport_width)] = page.evaluate("""async () => {
 const {setConfirmationBannerState} = await import('/static/operations.js');
 const phases = [
 ['accepted', 'Transaction sent', 'Waiting for confirmation.', 'info', 'is-sent', 'ir8322-main'],
 ['pending', 'Awaiting confirmation', 'The game has not shown this change yet.', 'info', 'is-pending', 'ir8322-main', true],
 ['pending-before-refresh-error', 'Awaiting confirmation', 'The game has not shown this change yet.', 'info', 'is-pending', 'ir8322-refresh-pending', true],
 ['confirmed-partial', 'Transaction confirmed', 'The transaction is confirmed, but the latest game refresh failed.', 'caution', 'is-confirmed-partial', 'ir8322-refresh-pending'],
 ['confirmed', 'Transaction confirmed', 'The 24-hour build has started.', 'success', 'is-confirmed', 'ir8322-main'],
 ['refused', 'Game refused', 'The game reported: account is not authorized to change this plot because it is currently leased to another player.', 'danger', 'is-failed', 'ir8322-refused'],
 ['mismatch', 'Game state mismatch', 'The game does not reflect this change.', 'danger', 'is-failed', 'ir8322-mismatch', true],
 ['pending-before-could-not-verify', 'Awaiting confirmation', 'The game has not shown this change yet.', 'info', 'is-pending', 'ir8322-unverified', true],
 ['could-not-verify', 'Could not verify', 'The game result could not be checked.', 'caution', 'is-unresolved', 'ir8322-unverified', true]
 ];
 const read = () => {
 const pill = document.querySelector('#ceres-confirmation-banner');
 const title = pill.querySelector('.worksite-banner-title');
 const titleText = title.querySelector(':scope > span:last-child').innerText;
 const detail = pill.querySelector('.worksite-banner-detail');
 const facts = pill.querySelector('.ceres-pill-facts');
 const transaction = pill.querySelector('.ceres-pill-transaction');
 const action = pill.querySelector('.ceres-pill-action-row');
 const mainAction = action.querySelector('.ceres-pill-action-slot.is-main');
 const secondaryAction = action.querySelector('.ceres-pill-action-slot:not(.is-main) .ceres-pill-action');
 const retryButton = action.querySelector('[data-confirmation-action="retry-verification"]');
 const rect = element => { const r = element.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; };
 const lineCount = element => { const range = document.createRange(); range.selectNodeContents(element); return range.getClientRects().length; };
 const tokenColor = token => {
 const probe = document.createElement('span');
 probe.style.cssText = `position:fixed;visibility:hidden;color:var(${token})`;
 document.body.append(probe);
 const color = getComputedStyle(probe).color;
 probe.remove();
 return color;
 };
 const rowRects = [pill.querySelector('.ceres-pill-title-row'), detail, facts, transaction, action].map(rect);
 return {title: titleText, detail: detail.innerText, detailTitle: detail.title, outer: rect(pill), titleBox: rect(title), detailBox: rect(detail), rowRects,
 factsBox: rect(facts), transactionBox: rect(transaction), actionBox: rect(action), secondaryActionBox: secondaryAction && !secondaryAction.hidden ? rect(secondaryAction) : null, mainActionBox: rect(mainAction),
 mainActionLabel: mainAction.innerText.trim(), colors: [getComputedStyle(pill).backgroundColor, getComputedStyle(pill).borderTopColor,
 getComputedStyle(pill).borderRightColor, getComputedStyle(pill).borderBottomColor, getComputedStyle(pill).borderLeftColor,
 getComputedStyle(title).color, tokenColor('--danger'), tokenColor('--warn'), tokenColor('--ok')],
 lines: [lineCount(title.querySelector(':scope > span:last-child')), lineCount(detail)],
 copy: [titleText, detail.innerText, ...[...facts.querySelectorAll('.ceres-pill-value')].map(el => el.innerText),
 transaction.innerText, transaction.querySelector('a')?.getAttribute('href') || '', action.innerText,
 retryButton ? retryButton.hidden : true, retryButton ? retryButton.disabled : true, retryButton ? retryButton.tabIndex : -1],
 actionVisible: !!retryButton && !retryButton.hidden,
 overflow: [pill, pill.querySelector('.ceres-pill-content'), title, detail, facts, transaction, action]
 .map(el => el.scrollHeight <= el.clientHeight)};
 };
 const samples = [];
 for (const [phase,title,detail,tone,state,txId,hasAction] of phases) {
 const options = {txId, phase, state};
 if (hasAction) options.action = {id:'retry_verification', label:'Check again'};
 setConfirmationBannerState(title, detail, tone, options);
 samples.push({phase, ...read()});
 if (phase === 'pending') {
 setConfirmationBannerState('A changed pending title', 'A changed pending detail.', tone,
 {txId, phase, state, action:{id:'retry_verification', label:'Check again'}});
 samples.push({phase:'same-phase-repeat', ...read()});
 }
 }
 return samples;
 }""")

    expected_phases = ["accepted", "pending", "same-phase-repeat", "pending-before-refresh-error", "confirmed-partial", "confirmed", "refused", "mismatch", "pending-before-could-not-verify", "could-not-verify"]
    for width, samples in samples_by_viewport.items():
        assert [sample["phase"] for sample in samples] == expected_phases, (width, samples)
        baseline = samples[0]
        for sample in samples:
            assert sample["outer"] == baseline["outer"], (width, baseline, sample)
            assert sample["titleBox"] == baseline["titleBox"], (width, baseline, sample)
            assert sample["detailBox"] == baseline["detailBox"], (width, baseline, sample)
            assert sample["factsBox"] == baseline["factsBox"], (width, baseline, sample)
            assert sample["transactionBox"] == baseline["transactionBox"], (width, baseline, sample)
            assert sample["actionBox"] == baseline["actionBox"], (width, baseline, sample)
            # pass 3: the main action owns a fixed bottom-right slot in every phase.
            assert sample["mainActionBox"] == baseline["mainActionBox"], (width, baseline, sample)
            assert abs(sample["mainActionBox"]["x"] + sample["mainActionBox"]["width"] - (sample["actionBox"]["x"] + sample["actionBox"]["width"])) < 0.5, (width, sample)
            assert sample["mainActionLabel"] == ("Check again" if sample["actionVisible"] else ""), (width, sample)
            if sample["secondaryActionBox"]:
                assert sample["secondaryActionBox"]["x"] + sample["secondaryActionBox"]["width"] <= sample["mainActionBox"]["x"], (width, sample)
            assert sample["lines"] == [1, 1], (width, sample)
            assert all(sample["overflow"]), (width, sample)
            for first_index, first in enumerate(sample["rowRects"]):
                for second in sample["rowRects"][first_index + 1:]:
                    assert first["y"] + first["height"] <= second["y"] + 1, (width, sample["phase"], first, second)
        assert samples[1]["title"] == samples[2]["title"], (width, samples[1:3])
        assert samples[1]["detail"] == samples[2]["detail"], (width, samples[1:3])
        assert samples[1]["copy"] == samples[2]["copy"], (width, samples[1:3])
        assert [sample["actionVisible"] for sample in samples] == [False, True, True, True, False, False, False, True, True, True], (width, samples)
        by_phase = {sample["phase"]: sample for sample in samples if sample["phase"] != "same-phase-repeat"}
        danger = by_phase["refused"]["colors"]
        warn = by_phase["pending"]["colors"]
        ok = by_phase["confirmed"]["colors"]
        neutral = by_phase["accepted"]["colors"]
        assert danger != warn and danger != ok and warn != ok, (width, danger, warn, ok)
        assert danger[1:5] == [danger[6]] * 4 and danger[5] == danger[6], (width, danger)
        assert warn[1:5] == [warn[7]] * 4 and warn[5] == warn[7], (width, warn)
        assert ok[1:5] == [ok[8]] * 4 and ok[5] == ok[8], (width, ok)
        assert danger[0] != neutral[0] and warn[0] != neutral[0] and ok[0] != neutral[0], (width, neutral, danger, warn, ok)
        assert all(sample["colors"][1:5] == danger[1:5] for name, sample in by_phase.items() if name in {"refused", "mismatch"}), (width, by_phase)
        assert all(sample["colors"][1:5] == warn[1:5] for name, sample in by_phase.items() if name in {"pending", "pending-before-refresh-error", "confirmed-partial", "pending-before-could-not-verify", "could-not-verify"}), (width, by_phase)
        assert by_phase["mismatch"]["colors"] == danger, (width, by_phase["mismatch"], danger)
        assert by_phase["could-not-verify"]["colors"] == warn, (width, by_phase["could-not-verify"], warn)
        assert by_phase["confirmed-partial"]["colors"] == warn, (width, by_phase["confirmed-partial"], warn)
        assert neutral != danger and neutral != warn and neutral != ok, (width, neutral, danger, warn, ok)
        expected_copy = {
            "accepted": ("Transaction sent", "Waiting for confirmation."),
            "pending": ("Awaiting confirmation", "The game has not shown this change yet."),
            "pending-before-refresh-error": ("Awaiting confirmation", "The game has not shown this change yet."),
            "confirmed-partial": ("Transaction confirmed", "The transaction is confirmed, but the latest game refresh failed."),
            "confirmed": ("Transaction confirmed", "The 24-hour build has started."),
            "refused": ("Game refused", None),
            "mismatch": ("Game state mismatch", "The game does not reflect this change."),
            "pending-before-could-not-verify": ("Awaiting confirmation", "The game has not shown this change yet."),
            "could-not-verify": ("Could not verify", "The game result could not be checked."),
        }
        for phase, expected in expected_copy.items():
            sample = by_phase[phase]
            assert sample["title"] == expected[0], (width, phase, sample)
            if expected[1] is not None:
                assert sample["detail"] == expected[1], (width, phase, sample)
        refused = next(sample for sample in samples if sample["phase"] == "refused")
        assert refused["detail"].endswith("…"), (width, refused)
        assert refused["detailTitle"].startswith(refused["detail"][:-1]), (width, refused)
    page.close()
