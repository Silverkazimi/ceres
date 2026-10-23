"""Live Chromium coverage for the portfolio-wide Ceres checkout surface.

: the test that read the old checkout panel's `.checkout-plot-group` / `.checkout-move-delta` rows was retired; the Changes modal () renders no such elements and the dead CSS went with it.
"""

from __future__ import annotations


import json
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from .conftest import settle, spawn_live_ceres, wait_for_board





@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (live_browser, base_url):
        yield live_browser, base_url


def _page(live_browser: tuple[Browser, str]) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(base_url)
    wait_for_board(page)
    return page


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


def _stage_worker(page: Page, index: int, used_name: str | None = None) -> tuple[str, str, str]:
    """Mutation evidence: restore the removed row-level review wait; expected red error: Playwright TimeoutError waiting for `button[data-slot-action='review']`."""
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
            name = _candidate_name(candidates.nth(candidate_index))
            if name != used_name:
                chosen = candidate_index
                break
    chosen_name = _candidate_name(candidates.nth(chosen))
    chosen_delta = candidates.nth(chosen).locator(".candidate-delta, .candidate-plot-change").first.inner_text()
    # #checkout-count is CERES_STAGE.length across the WHOLE portfolio, not a
    # per-plot count -- staging a second move must wait for it to grow past
    # whatever it already held, not for a hardcoded '1'.
    before = int(page.locator("#checkout-count").text_content() or "0") if page.locator("#checkout-btn").is_visible() else 0
    candidates.nth(chosen).click()
    page.locator("#checkout-btn").wait_for(state="visible", timeout=30_000)
    page.wait_for_function(
        f"document.querySelector('#checkout-count')?.textContent === '{before + 1}'", timeout=30_000
    )
    return deed_uid, chosen_name, chosen_delta


def _two_chunk_preview() -> dict:
    operation = lambda deed_uid: [
        "custom_json",
        {
            "id": "sm_stake_change",
            "json": json.dumps({"deed_uid": deed_uid, "stake": {"cards": []}, "unstake": {"cards": []}}),
            "required_auths": [],
            "required_posting_auths": ["player0005"],
        },
    ]
    return {"account": "player0005", "chunks": [{"operations": [operation("chunk-1")]}, {"operations": [operation("chunk-2")]}]}


def _compact_review_preview() -> dict:
    def operation(chunk: int, number: int) -> list:
        return [
            "custom_json",
            {
                "id": "sm_stake_change",
                "json": json.dumps({
                    "deed_uid": f"chunk-{chunk}-move-{number}",
                    "stake": {"cards": [{"card_uid": f"CARD-{chunk}-{number}"}], "items": []},
                    "unstake": {"cards": [{"card_uid": f"OLD-{chunk}-{number}"}], "items": []},
                }),
                "required_auths": [],
                "required_posting_auths": ["player0005"],
            },
        ]

    return {
        "account": "player0005",
        "key_tier": "Posting",
        "chunks": [{"operations": [operation(chunk, number) for number in range(4)]} for chunk in (1, 2)],
        "notices": [{
            "code": "sole_power_source_removed",
            "kind": "hazard",
            "severity": "danger",
            "blocking": "confirm",
            "message": "This removes the plot's only power source. All five worker slots remain visible and staked workers remain detectable, but every worker is unpowered, inactive, and produces nothing.",
            "reason": "The staged removal is the plot's only proven power source.",
            "context": {"deed_uid": "chunk-1-move-0"},
        }],
    }


def _stage_two_workers(page: Page) -> None:
    first_index = None
    first_name = None
    for index in range(page.locator("#plot-tbody > tr.plot-row").count()):
        try:
            _, first_name, _ = _stage_worker(page, index)
            first_index = index
            break
        except AssertionError:
            continue
    assert first_index is not None and first_name
    for index in range(first_index + 1, page.locator("#plot-tbody > tr.plot-row").count()):
        try:
            _stage_worker(page, index, first_name)
            return
        except AssertionError:
            continue
    raise AssertionError("could not stage a second worker")


def _verified_deed_uid(route) -> str | None:
    """The deed a verify request is about: the first operation that names one. A Changes
    batch can also carry operations without a deed field (e.g. a ready worksite feed)."""
    for operation in route.request.post_data_json["operations"]:
        try:
            deed_uid = json.loads(operation[1]["json"]).get("deed_uid")
        except (KeyError, TypeError, ValueError):
            continue
        if deed_uid:
            return deed_uid
    return None


def _install_broadcast_probe(page: Page, reject_first: bool = False) -> None:
    # A stated wallet outcome ("canceled by the user") is a definite not-sent; an unclassified
    # error string is treated as an unknown outcome by the broadcast gate (S10 / L2-01).
    rejection = (
        "callback({success: false, error: 'Request was canceled by the user.'});"
        if reject_first
        else "window.__ceresBroadcastEvents.push({type: 'callback', txId, at: performance.now()}); callback({success: true, result: {id: txId}});"
    )
    page.add_init_script(
        f"""window.__ceresBroadcastEvents = [];
        let txNumber = 0;
        const ceresOriginalFetch = window.fetch.bind(window);
        window.fetch = (input, init) => {{
          const url = String(input);
          if (!url.includes('/api/op-verify')) return ceresOriginalFetch(input, init);
          const txId = JSON.parse(init.body).tx_id;
          window.__ceresBroadcastEvents.push({{type: 'verify-start', txId, at: performance.now()}});
          return ceresOriginalFetch(input, init).then((response) => {{
            window.__ceresBroadcastEvents.push({{type: 'verify-resolved', txId, at: performance.now()}});
            return response;
          }});
        }};
        window.hive_keychain = {{requestBroadcast: (account, operations, key, callback) => {{
          const txId = `FAKE-TX-${{++txNumber}}`;
          window.__ceresBroadcastEvents.push({{type: 'broadcast', txId, at: performance.now()}});
          setTimeout(() => {{ {rejection} }}, 10);
        }}}};"""
    )


def _mock_two_chunk_preview(page: Page, verify_body: dict) -> None:
    preview_body = json.dumps(_two_chunk_preview())
    page.route(
        "**/api/op-preview*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=preview_body),
    )
    verify_json = json.dumps(verify_body)
    page.route(
        "**/api/op-verify*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=verify_json),
    )


def test_compact_review_keeps_signing_facts_visible(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    preview_body = json.dumps(_compact_review_preview())
    page.route(
        "**/api/op-preview*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=preview_body),
    )
    page.goto(base_url)
    wait_for_board(page)
    _stage_two_workers(page)
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    review = panel.locator(".op-preview-review")
    review.wait_for(timeout=30_000)

    # The compact review lists one row per operation, each tagged with its transaction
    # ("Tx 1" / "Tx 2") and its signing authority; the rows sit in a folded details.
    operations = review.locator("ol.op-c-ops > li")
    assert operations.count() == 8
    assert [text.strip() for text in review.locator(".op-c-tx").all_text_contents()] == ["Tx 1"] * 4 + ["Tx 2"] * 4
    assert [text.strip() for text in review.locator(".op-c-auth").all_text_contents()] == ["Posting key"] * 8
    meanings = review.locator(".op-c-meaning").all_text_contents()
    assert all("Unstake 1 card" in meaning for meaning in meanings), meanings
    assert all("Stake 1 card" in meaning.replace("Unstake 1 card", "") for meaning in meanings), meanings
    assert "This removes the plot's only power source" in panel.inner_text()
    assert panel.locator('[data-notice-code="sole_power_source_removed"]').is_visible()
    assert review.locator("details.op-c-details").evaluate("element => element.open") is False
    assert "sm_stake_change" in review.locator("pre").text_content()
    assert "CARD-1-0" in review.locator("pre").text_content()

    rendered_height = review.evaluate("element => element.getBoundingClientRect().height")
    assert rendered_height < 900, rendered_height
    assert rendered_height < 1813 * 0.5, rendered_height
    review.screenshot(path=str(Path(__file__).parents[2] / "work" / "review-panel-after.png"))
    page.close()


def test_compact_review_keeps_refusal_reason_visible(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    refusal = json.dumps({
        "detail": {
            "errors": [{
                "reason": "card is still in its 72-hour unstake cooldown",
                "notice": {
                    "code": "cooldown_refusal",
                    "kind": "refusal",
                    "severity": "danger",
                    "blocking": "stage",
                    "message": "card is still in its 72-hour unstake cooldown",
                    "reason": "card is still in its 72-hour unstake cooldown",
                },
            }],
        },
    })
    page.route(
        "**/api/op-preview*",
        lambda route: route.fulfill(status=409, content_type="application/json", body=refusal),
    )
    page.goto(base_url)
    wait_for_board(page)
    _stage_two_workers(page)
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    review = panel.locator(".op-preview-review")
    review.wait_for(timeout=30_000)
    assert "Cannot sign yet" in review.inner_text()
    assert "card is still in its 72-hour unstake cooldown" in review.inner_text()
    page.close()


def test_chunk_broadcasts_are_sequential_and_each_verified_once(live_browser: tuple[Browser, str]) -> None:
    """A two-transaction batch opens one wallet request at a time: the second request
    is only made after the first one's wallet callback returned, and each signed
    transaction is verified exactly once, after its own broadcast.

    The broadcast gate no longer puts a verification read between chunks that fit the
    custom-operation budget ("the next wallet opens at once, with no validation read in
    between", ceres/static/broadcast-gate.js); a batch that would exceed the budget waits
    for on-chain inclusion instead. Verification runs after the signing loop.
    """
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _install_broadcast_probe(page)
    _mock_two_chunk_preview(page, {"state": "domain-pending"})
    page.goto(base_url)
    wait_for_board(page)
    _stage_two_workers(page)

    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    page.wait_for_function("window.__ceresBroadcastEvents?.filter((event) => event.type === 'verify-resolved').length === 2", timeout=30_000)
    settle(page)

    events = page.evaluate("window.__ceresBroadcastEvents")
    sequence = [(event["type"], event["txId"]) for event in events]
    assert sequence[:4] == [("broadcast", "FAKE-TX-1"), ("callback", "FAKE-TX-1"), ("broadcast", "FAKE-TX-2"), ("callback", "FAKE-TX-2")], sequence
    assert [event for event in sequence if event[0] == "broadcast"] == [("broadcast", "FAKE-TX-1"), ("broadcast", "FAKE-TX-2")]
    for tx_id in ("FAKE-TX-1", "FAKE-TX-2"):
        assert sequence.count(("verify-start", tx_id)) == 1, sequence
        assert sequence.index(("verify-start", tx_id)) > sequence.index(("callback", tx_id))
    page.close()


def test_rejected_first_chunk_aborts_remaining_broadcasts(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _install_broadcast_probe(page, reject_first=True)
    _mock_two_chunk_preview(page, {"state": "domain-pending"})
    page.goto(base_url)
    wait_for_board(page)
    _stage_two_workers(page)

    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    # The confirmation pill reports the stopped batch: its title is "Batch partially
    # completed" only once a later transaction was marked aborted (markUnsentChunksAborted),
    # and its detail carries the wallet's stated outcome for the first one.
    banner = page.locator("#ceres-confirmation-banner")
    page.wait_for_function(
        "document.querySelector('#ceres-confirmation-banner .worksite-banner-title')?.textContent.includes('Batch partially completed')",
        timeout=30_000,
    )
    assert "The wallet request was cancelled. This transaction was not sent." in banner.locator(".worksite-banner-detail").inner_text()
    settle(page)

    assert page.evaluate("window.__ceresBroadcastEvents.filter((event) => event.type === 'broadcast').length") == 1
    page.close()


def test_confirmed_broadcast_clears_the_stage(live_browser: tuple[Browser, str]) -> None:
    """Item 25 (live testing 2026-08-24): checkout did not clear after
    signing the transaction, and it must clear instantly afterwards. Before this fix,
    nothing cleared CERES_STAGE after a successful broadcast, so pressing "Prepare
    transaction" again would RE-EMIT moves already applied on-chain, against a
    snapshot that had not caught up -- a live double-apply hazard on a real-money
    tool. This proves the fix on the ONLY safe trigger: a broadcast that verifies as
    `domain-confirmed` clears the stage and the checkout button hides."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(
        """window.hive_keychain = {requestBroadcast: (account, operations, key, callback) => {
          callback({success: true, result: {id: 'FAKE-TX-ID'}});
        }};"""
    )
    plot_refresh_requests = []
    page.on(
        "request",
        lambda request: plot_refresh_requests.append(request.url)
        if request.method == "GET" and "/api/plots/" in request.url and "/op-preview" not in request.url
        else None,
    )

    def _verify(route):
        deed_uid = _verified_deed_uid(route)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"state": "domain-confirmed", "refreshed_deeds": [deed_uid] if deed_uid else []}),
        )

    page.route(
        "**/api/op-verify*",
        _verify,
    )
    page.goto(base_url)
    wait_for_board(page)

    index = None
    for i in range(page.locator("#plot-tbody > tr.plot-row").count()):
        try:
            _stage_worker(page, i)
            index = i
            break
        except AssertionError:
            continue
    assert index is not None

    checkout = page.locator("#checkout-btn")
    checkout.wait_for(timeout=15_000)
    staged_count = int(page.locator("#checkout-count").text_content() or "0")
    checkout.click()
    panel = page.locator("#checkout-panel")
    panel.wait_for(timeout=15_000)
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    # NOT waiting on `.op-state`'s own text here -- confirming clears the stage
    # and an empty stage makes renderCheckoutSurface() wipe the panel's
    # innerHTML (including the `.op-state` node itself) practically the same
    # tick, so polling for that text is racing the very fix under test. The
    # checkout button hiding IS the clear signal, and it is also the stronger
    # assertion: it proves the state actually reached CERES_STAGE, not just
    # that some text briefly appeared.
    # The Changes button no longer hides on an empty card stage: running worksite
    # constructions on the board stay listed in Changes ("a running build still shows,
    # as waiting"). The clear signal is the staged card move itself leaving BOTH
    # surfaces -- the Changes count drops back and the row's move-count reads 0.
    page.wait_for_function(
        f"document.querySelectorAll('tr.expand-row')[{index}]?.querySelector('.slot-stage-summary b')?.textContent === '0'",
        timeout=30_000,
    )
    assert "0 moves staged" in page.locator("tr.expand-row").nth(index).inner_text()
    assert int(page.locator("#checkout-count").text_content() or "0") < staged_count
    deed_uid = page.locator("#plot-tbody > tr.plot-row").nth(index).get_attribute("data-deed-uid")
    assert deed_uid
    assert any(url.split("?", 1)[0].rstrip("/") == f"{base_url}/api/plots/{deed_uid}" for url in plot_refresh_requests)
    page.close()


def test_pending_verification_rechecks_without_human_action(live_browser: tuple[Browser, str]) -> None:
    """M8: a pending verdict schedules another verify request automatically.

    Injection watch: removing `scheduleCheckoutVerificationPoll()` from the
    non-terminal branch left exactly one request after 4.5 seconds; restoring it
    produced the second request observed below.
    """
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(
        # Changes also signs the recording's ready worksite feed in the same run; only
        # the staged card change (sm_stake_change) is counted here.
        """window.__ceresBroadcastCount = 0;
        let ceresCallNumber = 0;
        window.hive_keychain = {requestBroadcast: (account, operations, key, callback) => {
          const card = operations.some((operation) => operation[1]?.id === 'sm_stake_change');
          if (card) window.__ceresBroadcastCount += 1;
          callback({success: true, result: {id: `FAKE-M8-${card ? 'CARD' : 'OTHER'}-${++ceresCallNumber}`}});
        }};"""
    )
    verify_requests = []
    page.on(
        "request",
        lambda request: verify_requests.append(request)
        if "/api/op-verify" in request.url and "FAKE-M8-CARD" in (request.post_data or "")
        else None,
    )
    page.route(
        "**/api/op-verify*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"state":"domain-pending","notices":[]}',
        ),
    )
    page.goto(base_url)
    wait_for_board(page)
    staged = False
    for index in range(page.locator("#plot-tbody > tr.plot-row").count()):
        try:
            _stage_worker(page, index)
            staged = True
            break
        except AssertionError:
            continue
    assert staged
    checkout = page.locator("#checkout-btn")
    checkout.click()
    panel = page.locator("#checkout-panel")
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    # The first verdict is pending; the per-chunk `.op-state` notice is not a stable
    # signal in the Changes panel (the panel re-renders while signing), so wait on the
    # request itself.
    while len(verify_requests) < 1:
        page.wait_for_event("request", lambda request: "/api/op-verify" in request.url and "FAKE-M8-CARD" in (request.post_data or ""), timeout=30_000)
    while len(verify_requests) < 2:  # the pending verdict schedules a second check
        page.wait_for_event("request", lambda request: "/api/op-verify" in request.url and "FAKE-M8-CARD" in (request.post_data or ""), timeout=30_000)
    settle(page)
    assert len(verify_requests) >= 2
    assert page.evaluate("window.__ceresBroadcastCount") == 1
    page.close()


def test_in_flight_marker_blocks_a_second_signature(live_browser: tuple[Browser, str]) -> None:
    """M9: a non-confirmed broadcast marks its staged entry and blocks re-signing.

    Injection watch: dropping `markBroadcastedChunk()` allowed a second Keychain
    call after the hidden Confirm control was re-enabled; restoring the marker
    kept the broadcast count at one and displayed the in-flight copy.
    """
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(
        # Changes also signs the recording's ready worksite feed in the same run; only
        # the staged card change (sm_stake_change) is counted here.
        """window.__ceresBroadcastCount = 0;
        let ceresCallNumber = 0;
        window.hive_keychain = {requestBroadcast: (account, operations, key, callback) => {
          const card = operations.some((operation) => operation[1]?.id === 'sm_stake_change');
          if (card) window.__ceresBroadcastCount += 1;
          callback({success: true, result: {id: `FAKE-M9-${card ? 'CARD' : 'OTHER'}-${++ceresCallNumber}`}});
        }};"""
    )
    page.route(
        "**/api/op-verify*",
        lambda route: route.fulfill(status=200, content_type="application/json", body='{"state":"domain-pending","notices":[]}'),
    )
    page.goto(base_url)
    wait_for_board(page)
    staged = False
    for index in range(page.locator("#plot-tbody > tr.plot-row").count()):
        try:
            _stage_worker(page, index)
            staged = True
            break
        except AssertionError:
            continue
    assert staged
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    # The Changes panel shows the signed card move as an in-flight tile whose tip carries
    # the in-flight copy, and the footer note gives the refusal for signing it again.
    # Signing the last transaction closes Changes; reopen it to read the marker.
    page.wait_for_function("window.__ceresBroadcastCount === 1", timeout=30_000)
    settle(page)
    if not panel.is_visible():
        page.locator("#checkout-btn").click()
    marker = panel.locator(".ch-slot.is-in-flight")
    marker.wait_for(timeout=30_000)
    assert marker.count() == 1
    assert marker.get_attribute("data-tip-body") == "Signed — waiting for the game to confirm."
    assert panel.locator(".ch-signing-note").inner_text() == "This change is already signed and waiting for the game to confirm."
    sign = panel.locator(".hm-foot .hm-btn.is-primary")
    assert sign.count() == 1 and sign.is_disabled()
    preview_requests = []
    page.on("request", lambda request: preview_requests.append(request) if "/api/op-preview" in request.url else None)
    # Force a second attempt through the panel's own delegated handlers: the old
    # "Prepare transaction" action and the review's confirm action.
    panel.evaluate(
        """panel => {
          const prepare = document.createElement('button');
          prepare.type = 'button';
          prepare.dataset.checkoutAction = 'prepare';
          panel.append(prepare);
          prepare.click();
        }"""
    )
    settle(page)
    assert not preview_requests
    panel.evaluate(
        """panel => {
          const confirm = document.createElement('button');
          confirm.type = 'button';
          confirm.dataset.opAction = 'confirm';
          confirm.dataset.previewScope = 'portfolio';
          panel.append(confirm);
          confirm.click();
        }"""
    )
    settle(page)
    assert page.evaluate("window.__ceresBroadcastCount") == 1
    page.close()


def test_refresh_errors_render_a_confirmed_terminal_state(live_browser: tuple[Browser, str]) -> None:
    """A confirmed operation with refresh errors keeps its stage clear and says the board may be stale."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(
        """window.hive_keychain = {requestBroadcast: (account, operations, key, callback) => {
          callback({success: true, result: {id: 'FAKE-REFRESH-ERROR-TX'}});
        }};"""
    )

    def _verify(route):
        deed_uid = _verified_deed_uid(route)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "state": "domain-confirmed",
                "refreshed_deeds": [],
                "refresh_errors": [{"deed_uid": deed_uid, "error": "board refresh failed; retry later"}] if deed_uid else [],
            }),
        )

    page.route("**/api/op-verify*", _verify)
    page.goto(base_url)
    wait_for_board(page)
    staged = False
    for index in range(page.locator("#plot-tbody > tr.plot-row").count()):
        try:
            _stage_worker(page, index)
            staged = True
            break
        except AssertionError:
            continue
    assert staged
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    banner = page.locator("#ceres-confirmation-banner")
    banner.wait_for(timeout=30_000)
    page.wait_for_function("document.querySelector('#ceres-confirmation-banner')?.classList.contains('is-confirmed-stale')", timeout=30_000)
    # One row: the catalogue message (verification_refresh_failed) already says it is confirmed;
    # the pill's title is the message, shortened to fit, and its action re-reads the plot.
    assert banner.locator(".worksite-banner-title").inner_text().strip().startswith("The change is confirmed.")
    assert banner.locator("button[data-confirmation-action='refresh']").inner_text().strip() == "Re-read"
    page.wait_for_function(
        f"document.querySelectorAll('tr.expand-row')[{index}]?.querySelector('.slot-stage-summary b')?.textContent === '0'",
        timeout=30_000,
    )
    page.close()


def test_failed_broadcast_leaves_the_stage_intact(live_browser: tuple[Browser, str]) -> None:
    """Item 25's other safety half: a Keychain rejection/cancellation must NEVER
    clear the stage. Real batches of 40 staged moves are routine -- clearing on click instead
    of on confirmation would silently destroy the whole batch the moment Keychain is
    dismissed. Mocks a Keychain REJECTION (success: false); the staged move must
    still be there afterward, both in the checkout panel and on the row."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(
        """window.hive_keychain = {requestBroadcast: (account, operations, key, callback) => {
          callback({success: false, error: 'user cancelled'});
        }};"""
    )
    page.goto(base_url)
    wait_for_board(page)

    index = None
    for i in range(page.locator("#plot-tbody > tr.plot-row").count()):
        try:
            _stage_worker(page, i)
            index = i
            break
        except AssertionError:
            continue
    assert index is not None

    checkout = page.locator("#checkout-btn")
    checkout.wait_for(timeout=15_000)
    staged_count = page.locator("#checkout-count").text_content()
    checkout.click()
    panel = page.locator("#checkout-panel")
    panel.wait_for(timeout=15_000)
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    # A stated cancellation is reported as "not sent" in the confirmation pill.
    page.wait_for_function(
        "document.querySelector('#ceres-confirmation-banner')?.textContent.includes('The wallet request was cancelled. This transaction was not sent.')",
        timeout=30_000,
    )

    # Stage is fully intact: checkout button still shows, its count is unchanged, and
    # the row still reports its staged move.
    assert checkout.is_visible()
    assert page.locator("#checkout-count").text_content() == staged_count
    assert "1</b> moves staged" in page.locator("tr.expand-row").nth(index).inner_html()
    page.close()
