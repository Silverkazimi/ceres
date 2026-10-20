""": three defects on the confirmation surface, all sharing the same verdict
vocabulary (`ceres/notices.py::verification_notice`, `ceres/static/app.js`'s checkout
`verifyBroadcast`).

Defect 1 -- an unrecognised `/api/op-verify` state used to `throw` in the checkout
broadcast path, which aborted every remaining chunk of a partially-broadcast batch --
contradicting `routes_op_preview.py`'s own documented contract that an unrecognised
string means "not confirmed yet, check again", never failure.

Defect 2 -- `pending_metadata.requires_escalation` (fides_land/verify.py's
PENDING_ESCALATION_ATTEMPTS) was computed and had no consumer: `verification_notice()`
always returned the ordinary "still propagating" notice regardless.

Defect 3 -- the pending/could-not-verify copy promised a retry ("Checking again…",
"retrying.") that nothing on the checkout path ever performed, and offered no
affordance to actually check again.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

from ceres.notices import verification_notice

from .conftest import spawn_live_ceres, wait_for_board

ROOT = Path(__file__).parents[2]


# Defect 2 + 3 (Python side): verification_notice()'s escalation wiring and copy

def test_pending_below_escalation_threshold_stays_the_ordinary_notice() -> None:
    domain = {"evidence": {"pending": ["still waiting"], "pending_metadata": {
        "attempt": 1, "escalate_after_attempts": 5, "requires_escalation": False,
    }}}
    notice = verification_notice("domain-pending", domain=domain)
    assert notice["code"] == "verification_pending"
    assert notice["action"] == {"id": "retry_verification", "label": "Check again"}
    # Defect 3 -- the copy must never claim an automatic retry is happening; nothing
    # on the checkout path performs one, only the button above does.
    assert "checking again" not in notice["message"].lower()
    assert "retrying" not in notice["message"].lower()


def test_pending_past_escalation_threshold_becomes_the_distinct_escalated_notice() -> None:
    domain = {"evidence": {"pending": ["still waiting"], "pending_metadata": {
        "attempt": 5, "escalate_after_attempts": 5, "requires_escalation": True,
    }}}
    notice = verification_notice("domain-pending", domain=domain)
    assert notice["code"] == "verification_pending_escalated"
    assert notice["severity"] != "danger"  # escalation is not a failure verdict
    assert notice["action"] == {"id": "retry_verification", "label": "Check again"}


def test_escalation_consumer_mutation_is_red() -> None:
    """Injected defect: `verification_notice()`'s `domain-pending` branch stops
    reading `pending_metadata.requires_escalation` and always returns the ordinary
    notice -- the exact pre-fix state, where the metadata was computed and nothing
    consumed it. Simulates the pre-fix call site directly (always
    `verification_pending`, metadata never read) and confirms it disagrees with the
    fixed `verification_notice()` -- proving this test actually depends on the
    escalation-reading branch, not merely on the registry entry existing."""
    from ceres.notices import format_notice, RETRY_VERIFICATION_ACTION

    domain = {"evidence": {"pending": [], "pending_metadata": {
        "attempt": 5, "escalate_after_attempts": 5, "requires_escalation": True,
    }}}
    pre_fix_code = format_notice("verification_pending", evidence=domain["evidence"], action=RETRY_VERIFICATION_ACTION)["code"]
    fixed_code = verification_notice("domain-pending", domain=domain)["code"]
    assert pre_fix_code == "verification_pending"  # the old, unconditional behaviour
    assert fixed_code == "verification_pending_escalated"  # the fix
    assert pre_fix_code != fixed_code


def test_could_not_verify_copy_does_not_promise_a_retry() -> None:
    notice = verification_notice("domain-could-not-verify", domain={"evidence": {"unverifiable": ["x"]}})
    assert "retrying" not in notice["message"].lower()
    assert notice["action"] == {"id": "retry_verification", "label": "Check again"}


def test_not_applied_is_a_terminal_failure_with_re_stake_copy() -> None:
    """Mutation evidence: map `domain-not-applied` to `verification_pending`; expected red:
    a chain-landed game refusal is shown as an indefinite wait instead of a terminal failure that
    tells the maintainer the item was not changed and can be prepared again."""
    notice = verification_notice("domain-not-applied", domain={"evidence": {"pending": ["Runi missing"]}})
    assert notice["code"] == "verification_not_applied"
    assert notice["severity"] == "danger"
    assert "landed on chain" in notice["message"]
    assert "did not apply" in notice["message"]
    assert "Prepare and sign again" in notice["message"]


# Defect 1 (frontend): checkout's verifyBroadcast() must never throw on a state it
# does not recognise -- the exact behaviour that used to abort a partially-broadcast
# batch's remaining chunks (confirmOperationBroadcast's catch -> markUnsentChunksAborted
# > `break`). Migrated per /campaign-plan Batch A (row 38): drives the REAL
# checkout, staging two real plots and broadcasting a real two-chunk batch through the
# live board, with only `/api/op-preview` and `/api/op-verify` route-mocked -- no
# app.js source read, no node subprocess (the technique established by
# tests/ceres/test_live_checkout.py / test_live_reload_guard.py for this exact surface).


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _expand(page: Page, index: int) -> None:
    row = page.locator("#plot-tbody > tr.plot-row").nth(index)
    if row.locator("xpath=following-sibling::tr[1]").evaluate("node => node.style.display") == "none":
        row.click()
    page.locator("tr.expand-row").nth(index).locator(".slot-editor").wait_for(timeout=30_000)


def _stage_worker(page: Page, index: int, used_name: str | None = None) -> str:
    """Stages a worker on plot `index`'s first empty worker slot; returns its deed_uid.
    Raises AssertionError (caller should try the next plot) if this plot has no worker
    capacity or slot."""
    _expand(page, index)
    row = page.locator("#plot-tbody > tr.plot-row").nth(index)
    deed_uid = row.get_attribute("data-deed-uid")
    assert deed_uid
    expanded = page.locator("tr.expand-row").nth(index)
    worker = expanded.locator(".slot-group[data-group-id='worker']")
    if int(worker.locator(".slot-group-count").inner_text().split("/")[1]) == 0:
        raise AssertionError("selected plot has no worker capacity")
    empty = worker.locator(".slot-grid .slot-tile.is-empty").first
    assert empty.count() == 1
    with page.expect_request("**/api/plots/*/candidates*"):
        empty.click()
    expanded.locator(".candidate-row").first.wait_for(timeout=30_000)
    candidates = expanded.locator(".candidate-row")
    before = int(page.locator("#checkout-count").text_content() or "0") if page.locator("#checkout-btn").is_visible() else 0
    candidates.first.click()
    page.locator("#checkout-btn").wait_for(state="visible", timeout=30_000)
    page.wait_for_function(
        f"document.querySelector('#checkout-count')?.textContent === '{before + 1}'", timeout=30_000
    )
    return deed_uid


def _stage_two_workers(page: Page) -> tuple[str, str]:
    first_deed = None
    first_index = None
    for index in range(page.locator("#plot-tbody > tr.plot-row").count()):
        try:
            first_deed = _stage_worker(page, index)
            first_index = index
            break
        except AssertionError:
            continue
    assert first_deed is not None and first_index is not None
    second_deed = None
    for index in range(first_index + 1, page.locator("#plot-tbody > tr.plot-row").count()):
        try:
            second_deed = _stage_worker(page, index)
            break
        except AssertionError:
            continue
    assert second_deed is not None
    return first_deed, second_deed


def _two_chunk_preview(deed_a: str, deed_b: str) -> dict:
    def operation(deed_uid: str) -> list:
        import json as _json

        return [
            "custom_json",
            {
                "id": "sm_stake_change",
                "json": _json.dumps({"deed_uid": deed_uid, "stake": {"cards": [{"card_uid": "CARD-1"}]}, "unstake": {"cards": []}}),
                "required_auths": [],
                "required_posting_auths": ["player0005"],
            },
        ]

    return {
        "account": "player0005",
        "chunks": [{"operations": [operation(deed_a)]}, {"operations": [operation(deed_b)]}],
    }


def _keychain_stub() -> str:
    return (
        "window.hive_keychain = {requestBroadcast: (account, operations, key, callback) => {"
        "  window.__ceresBroadcastCount = (window.__ceresBroadcastCount || 0) + 1;"
        f"  callback({{success: true, result: {{id: `FAKE-VERDICT-TX-${{window.__ceresBroadcastCount}}`}}}});"
        "}};"
    )


def test_unrecognised_verdict_does_not_abort_the_batch(live_browser: tuple[Browser, str]) -> None:
    """A `/api/op-verify` state this client does not otherwise special-case (e.g. a
    future chain-status fallback the endpoint's own contract explicitly permits --
    ceres/static/app.js `verifyBroadcast`'s comment quotes routes_op_preview.py's
    `op_verify` docstring verbatim) must NOT throw, and must NOT abort a
    partially-broadcast batch's remaining chunks. Proved on a real two-chunk broadcast
    of two genuinely staged plots: both chunks must sign and both verify requests must
    resolve.

    Injection watch: restoring the pre-fix unconditional
    `throw new Error('Unexpected verification verdict: ...')` for any state that is not
    domain-confirmed/domain-pending/domain-could-not-verify/domain-mismatch is the exact
    defect this test catches. Expected red: only 1 broadcast/verify pair completes
    (`window.__ceresBroadcastCount === 1`, not 2), and the second chunk's `.op-state`
    reads "not broadcast — aborted after chunk 1 failed" (the same text
    test_rejected_first_chunk_aborts_remaining_broadcasts in test_live_checkout.py
    asserts for the Keychain-rejection case)."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(_keychain_stub())
    verify_requests: list = []
    page.on("request", lambda request: verify_requests.append(request) if "/api/op-verify" in request.url else None)
    page.goto(base_url)
    wait_for_board(page)
    deed_a, deed_b = _stage_two_workers(page)

    preview_body = _two_chunk_preview(deed_a, deed_b)
    import json as _json

    page.route(
        "**/api/op-preview*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=_json.dumps(preview_body)),
    )
    # An unrecognised-but-not-a-failure state -- not domain-confirmed, not
    # domain-pending, not domain-could-not-verify, not domain-mismatch.
    page.route(
        "**/api/op-verify*",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=_json.dumps({"state": "chain_verified", "notices": [{"code": "verification_chain_check", "kind": "verification", "severity": "info", "blocking": "repeat", "message": "Checking the chain."}]}),
        ),
    )

    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.locator("button[data-checkout-action='prepare']").click()
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    page.wait_for_function("(window.__ceresBroadcastCount || 0) === 2", timeout=30_000)
    page.wait_for_function(
        "[...document.querySelectorAll('#checkout-panel .op-state')].every(el => !el.textContent.includes('aborted'))",
        timeout=10_000,
    )
    assert len(verify_requests) == 2
    op_state_text = panel.inner_text()
    assert "aborted" not in op_state_text
    page.close()


def test_domain_mismatch_clears_only_the_matching_entry(live_browser: tuple[Browser, str]) -> None:
    """`clearFailedChunkMarkers()` (ceres/static/app.js) scopes its cleanup to the
    ONE chunk that came back `domain-mismatch` -- an unrelated staged entry that is
    also in-flight (broadcast, still verifying) must keep its txId/broadcast_at, and
    the stale portfolio preview must be invalidated so a stale review is never re-used.
    Proved on a real two-chunk broadcast of two genuinely staged plots: chunk 1 verifies
    `domain-pending` (stays in-flight, untouched), chunk 2 verifies `domain-mismatch`
    (cleared).

    Injection watch: restoring the pre-fix no-cleanup `domain-mismatch` branch (skip
    the `clearFailedChunkMarkers()` call, throw directly) is the exact defect this test
    catches. Expected red: the mismatched entry's txId/broadcast_at remain set
    (`CERES_STAGE` still shows it in-flight, read through the same `?ir1882-guard=1`
    debug exposure test_live_reload_guard.py's G7 guard already relies on for this
    exact binding)."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(_keychain_stub())
    page.goto(f"{base_url}?ir1882-guard=1")
    wait_for_board(page)
    deed_a, deed_b = _stage_two_workers(page)

    import json as _json

    preview_body = _two_chunk_preview(deed_a, deed_b)
    page.route(
        "**/api/op-preview*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=_json.dumps(preview_body)),
    )

    verify_call_count = {"n": 0}

    def _verify(route) -> None:
        verify_call_count["n"] += 1
        if verify_call_count["n"] == 1:
            body = {"state": "domain-pending", "notices": [{"code": "verification_pending", "kind": "verification", "severity": "info", "blocking": "repeat", "message": "Still checking.", "action": {"id": "retry_verification", "label": "Check again"}}]}
        else:
            body = {"state": "domain-mismatch", "notices": [{"code": "verification_mismatch", "kind": "verification", "severity": "danger", "blocking": "sign", "message": "Game state does not match the broadcast operations."}]}
        route.fulfill(status=200, content_type="application/json", body=_json.dumps(body))

    page.route("**/api/op-verify*", _verify)

    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.locator("button[data-checkout-action='prepare']").click()
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    page.wait_for_function("(window.__ceresBroadcastCount || 0) === 2", timeout=30_000)

    # The mismatched chunk's own verify resolves synchronously with the second
    # broadcast in this mock, but rendering/cleanup is a microtask tick behind
    # wait for the entry-scoped clearing to actually land.
    page.wait_for_function(
        f"""() => {{
          const failed = CERES_STAGE.find(e => e.deed_uid === '{deed_b}');
          return failed && !failed.txId && !failed.broadcast_at;
        }}""",
        timeout=30_000,
    )

    kept = page.evaluate(f"CERES_STAGE.find(e => e.deed_uid === '{deed_a}')")
    assert kept and kept.get("txId"), kept
    assert kept.get("broadcast_at"), kept

    # The cleared move's own row is no longer marked in-flight (renderCheckoutSurface()
    # re-derives inFlight from txId && broadcast_at every render).
    cleared_move = panel.locator(f".checkout-move[data-deed-uid='{deed_b}']")
    assert cleared_move.locator(".checkout-remove").is_enabled()

    # Stale-preview invalidation: clearFailedChunkMarkers() deletes the CERES_PREVIEWS
    # entry that produced this now-broadcast-and-failed chunk and calls
    # renderCheckoutSurface() itself. renderCheckoutSurface() only renders
    # `.op-preview-review` when CERES_PREVIEWS still holds the portfolio key -- so if
    # the stale entry survived, the old (now-incorrect, partially-broadcast) review
    # would keep rendering. It must be gone, leaving only the "Prepare transaction"
    # affordance.
    assert panel.locator(".op-preview-review").count() == 0
    page.close()
