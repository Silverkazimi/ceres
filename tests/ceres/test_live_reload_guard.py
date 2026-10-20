""" live reload guards for the durable in-flight sign marker."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Browser, Page

from ceres.notices import NOTICE_REGISTRY
from tests.ceres.conftest import spawn_live_ceres, wait_for_board
from tests.ceres.test_live_checkout import _expand, _stage_worker


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


STUB = """
window.hive_keychain = {requestBroadcast: (account, operations, key, callback) => {
 const count = Number(sessionStorage.getItem('__ir1882BroadcastCount') || '0') + 1;
 sessionStorage.setItem('__ir1882BroadcastCount', String(count));
 callback({success: true, result: {id: 'FAKE-IR1882-TX-' + count}});
}};
"""


def _pending(route) -> None:
    route.fulfill(status=200, content_type="application/json", body='{"state":"domain-pending","notices":[]}')


def _open(live_browser: tuple[Browser, str], *, schedule: bool = False) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    if schedule:
        page.add_init_script("if (sessionStorage.getItem('__ir1882ShrinkPoll') === '1') window.__ceresOperationPollSchedule = [100, 200, 300];")
    page.add_init_script(STUB)
    page.route("**/api/op-verify*", _pending)
    page.goto(f"{base_url}?player=sample01&ir1882-guard=1")
    wait_for_board(page)
    return page


def _stage_first_available(page: Page) -> tuple[int, str, str]:
    for index in range(page.locator("#plot-tbody > tr.plot-row").count()):
        try:
            deed_uid, name, delta = _stage_worker(page, index)
            print("STAGED DEED", deed_uid)
            assert deed_uid
            return index, name, delta
        except AssertionError:
            continue
    raise AssertionError("no stageable plot")


def _stage_two_slot_plot(page: Page) -> tuple[int, str, str]:
    for index in range(page.locator("#plot-tbody > tr.plot-row").count()):
        try:
            _expand(page, index)
            worker = page.locator("tr.expand-row").nth(index).locator(".slot-group[data-group-id='worker']")
            if worker.locator(".slot-grid .slot-tile.is-empty").count() < 2:
                continue
            deed_uid, name, delta = _stage_worker(page, index)
            print("STAGED DEED", deed_uid)
            assert deed_uid
            return index, name, delta
        except AssertionError:
            continue
    raise AssertionError("no stageable plot with two worker slots")


def _sign(page: Page) -> None:
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    panel.locator("button[data-checkout-action='prepare']").click()
    panel.locator(".op-preview-review").wait_for(timeout=30_000)
    panel.locator("button[data-op-action='confirm']").click()
    panel.locator(".checkout-move-status").wait_for(timeout=30_000)


def _count(page: Page) -> int:
    return int(page.evaluate("sessionStorage.getItem('__ir1882BroadcastCount') || '0'"))


def _test_url(base_url: str) -> str:
    return f"{base_url}?player=sample01&ir1882-guard=1"


def _dismiss_banner(page: Page) -> None:
    dismiss = page.locator("#ceres-confirmation-banner .worksite-banner-dismiss")
    if dismiss.count():
        dismiss.dispatch_event("click")


def test_notice_catalogue_for_reload_marker() -> None:
    """Mutation evidence: put ``served`` in a new notice; expected red: the copy guard fails."""
    codes = {
        "verification_in_flight_restored",
        "in_flight_record_expired",
        "in_flight_store_unavailable",
    }
    assert codes <= set(NOTICE_REGISTRY)
    assert all(NOTICE_REGISTRY[code]["severity"] in {"neutral", "info", "caution", "danger", "success"} for code in codes)
    assert all(word not in NOTICE_REGISTRY[code]["message"].lower() for code in codes for word in ("served", "raw"))


def test_g1_reload_does_not_allow_second_signature(live_browser: tuple[Browser, str]) -> None:
    """G1. Mutation evidence: delete ``restoreInFlightRecords()`` from ``loadBoard``; expected red: reload signs twice."""
    page = _open(live_browser)
    _stage_first_available(page)
    _sign(page)
    page.reload()
    wait_for_board(page)
    page.locator("#checkout-btn").dispatch_event("click")
    assert page.locator(".checkout-prepare").is_disabled()
    page.evaluate("async () => { await requestOperationPreview({scope: 'portfolio'}); await confirmOperationBroadcast({scope: 'portfolio'}); }")
    page.wait_for_timeout(200)
    assert _count(page) == 1
    page.close()


def test_g2_marker_is_written_before_verification_io(live_browser: tuple[Browser, str]) -> None:
    """G2. Mutation evidence: move ``writeInFlightRecord`` after ``fetch``; expected red: the synchronous Keychain callback sees no record."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script("""
window.__ir1882G2 = {seen: false};
window.hive_keychain = {requestBroadcast: (account, operations, key, callback) => {
 const count = Number(sessionStorage.getItem('__ir1882BroadcastCount') || '0') + 1;
 sessionStorage.setItem('__ir1882BroadcastCount', String(count));
 callback({success: true, result: {id: 'FAKE-IR1882-TX-G2'}});
 const data = JSON.parse(localStorage.getItem('ceres:in-flight:v1:player0005') || '{}');
 window.__ir1882G2.seen = !!data.records?.['FAKE-IR1882-TX-G2'];
}};
""")
    page.route("**/api/op-verify*", lambda route: None)
    page.goto(_test_url(base_url))
    wait_for_board(page)
    _stage_first_available(page)
    _sign(page)
    page.wait_for_function("window.__ir1882G2?.seen === true", timeout=30_000)
    assert _count(page) == 1
    page.close()


def test_g3_marker_uses_operation_identity(live_browser: tuple[Browser, str]) -> None:
    """G3. Mutation evidence: key records by ``deed_uid`` only; expected red: another slot is stamped by the first record."""
    page = _open(live_browser)
    first_index, first_name, _ = _stage_two_slot_plot(page)
    _sign(page)
    identity = page.evaluate("""() => {
 const entry = CERES_STAGE[0];
 return {
 record: inFlightRecordForEntry(entry)?.txId || null,
 otherSlot: inFlightRecordForEntry({...entry, slot_index: entry.slot_index + 1})?.txId || null,
 otherAsset: inFlightRecordForEntry({...entry, asset_ref: String(entry.asset_ref) + '-other'})?.txId || null,
 };
 }""")
    assert identity == {"record": "FAKE-IR1882-TX-1", "otherSlot": None, "otherAsset": None}
    page.reload()
    wait_for_board(page)
    _stage_worker(page, first_index, first_name)
    page.locator("#checkout-btn").dispatch_event("click")
    page.locator(".checkout-move").first.wait_for()
    assert page.locator(".checkout-move").count() == 2
    assert page.locator(".checkout-move-status").count() == 1
    assert page.evaluate("Object.keys(JSON.parse(localStorage.getItem('ceres:in-flight:v1:player0005')).records).length") == 1
    page.close()


def test_g4_expired_marker_is_swept_and_announced(live_browser: tuple[Browser, str]) -> None:
    """G4. Mutation evidence: remove the age check in ``readInFlightRecords``; expected red: a 25-hour-old entry is restored."""
    page = _open(live_browser)
    _stage_first_available(page)
    _sign(page)
    page.evaluate("""() => {
 const key = 'ceres:in-flight:v1:player0005';
 const data = JSON.parse(localStorage.getItem(key));
 Object.values(data.records).forEach(record => { record.broadcast_at = new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString(); });
 localStorage.setItem(key, JSON.stringify(data));
 }""")
    page.reload()
    wait_for_board(page)
    assert not page.locator("#checkout-btn").is_visible()
    banner = page.locator("#ceres-confirmation-banner")
    banner.wait_for()
    assert "A signed change waited a full day" in banner.inner_text()
    assert page.evaluate("Object.keys(JSON.parse(localStorage.getItem('ceres:in-flight:v1:player0005')).records).length") == 0
    page.close()


def test_g5_restored_marker_is_visible_on_checkout_and_tile(live_browser: tuple[Browser, str]) -> None:
    """G5. Mutation evidence: skip restore or set the TTL to zero; expected red: the checkout and pending-add marker disappear."""
    page = _open(live_browser)
    first_index, _, _ = _stage_first_available(page)
    _sign(page)
    slot = page.locator(".checkout-move").get_attribute("data-slot-index")
    page.reload()
    wait_for_board(page)
    assert page.locator("#checkout-btn").is_visible()
    page.locator("#checkout-btn").dispatch_event("click")
    assert page.locator(".checkout-move-status").text_content() == "Signed — waiting for the game to confirm."
    assert "signed before this page was reloaded" in page.locator("#ceres-confirmation-banner").inner_text()
    _dismiss_banner(page)
    _expand(page, first_index)
    tile = page.locator("tr.expand-row").nth(first_index).locator(
        f".slot-group[data-group-id='worker'] .slot-tile.is-pending-add"
    )
    assert tile.count() == 1
    assert tile.get_attribute("data-slot-action") is None
    assert tile.is_disabled()
    assert tile.locator(".slot-in-flight").text_content() == "Signed — waiting for the game to confirm."
    assert page.locator(f".slot-tile.is-pending-add[data-slot-index='{slot}']").is_visible()
    page.close()


def test_g6_restored_poll_resumes_and_clears_confirmed_marker(live_browser: tuple[Browser, str]) -> None:
    """G6. Mutation evidence: remove the ``verifyBroadcast`` call from restore; expected red: no restored op-verify request arrives."""
    state = {"value": "domain-pending"}

    def route(route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"state": state["value"], "notices": []}))

    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(STUB)
    page.route("**/api/op-verify*", route)
    page.goto(_test_url(base_url))
    wait_for_board(page)
    _stage_first_available(page)
    _sign(page)
    with page.expect_request("**/api/op-verify*", timeout=30_000) as request_info:
        page.reload()
    wait_for_board(page)
    payload = json.loads(request_info.value.post_data or "{}")
    assert payload["tx_id"] == "FAKE-IR1882-TX-1"
    state["value"] = "domain-confirmed"
    with page.expect_request("**/api/op-verify*", timeout=10_000):
        page.wait_for_timeout(3_500)
    page.wait_for_function("CERES_STAGE.length === 0", timeout=10_000)
    assert page.evaluate("Object.keys(JSON.parse(localStorage.getItem(inFlightStoreKey())).records).length") == 0
    assert "Confirmed in game" in page.locator("#ceres-confirmation-banner").inner_text()
    page.close()


def test_g6b_restored_poll_is_bounded(live_browser: tuple[Browser, str]) -> None:
    """G6b. Mutation evidence: drop chunk entry-id threading in the scheduler; expected red: restored verification never exhausts."""
    page = _open(live_browser, schedule=True)
    _stage_first_available(page)
    _sign(page)
    page.evaluate("sessionStorage.setItem('__ir1882ShrinkPoll', '1')")
    requests = []
    page.on("request", lambda request: requests.append(request) if "/api/op-verify" in request.url else None)
    page.reload()
    wait_for_board(page)
    page.wait_for_timeout(1_500)
    assert len(requests) == 4
    poller = page.evaluate("""() => {
 const values = [...OPERATION_POLLERS.values()];
 return {size: OPERATION_POLLERS.size, timer: values[0]?.timer, index: values[0]?.index ?? -1, length: OPERATION_POLL_SCHEDULE_MS.length};
 }""")
    assert poller == {"size": 1, "timer": None, "index": 3, "length": 3}
    page.close()


def test_g7_confirmed_and_mismatched_verdicts_clear_store(live_browser: tuple[Browser, str]) -> None:
    """G7. Mutation evidence: drop either ``forgetInFlightRecord`` clear call; expected red: the corresponding record remains in localStorage."""
    state = {"value": "domain-pending"}

    def route(route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"state": state["value"], "notices": []}))

    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(STUB)
    page.route("**/api/op-verify*", route)
    page.goto(_test_url(base_url))
    wait_for_board(page)
    first_index, first_name, _ = _stage_first_available(page)
    _sign(page)
    page.reload()
    wait_for_board(page)
    state["value"] = "domain-confirmed"
    page.wait_for_timeout(3_500)
    page.wait_for_function("CERES_STAGE.length === 0", timeout=10_000)
    assert page.evaluate("Object.keys(JSON.parse(localStorage.getItem(inFlightStoreKey())).records).length") == 0
    state["value"] = "domain-pending"
    _dismiss_banner(page)
    _stage_worker(page, first_index, first_name)
    _sign(page)
    page.reload()
    wait_for_board(page)
    state["value"] = "domain-mismatch"
    page.wait_for_timeout(3_500)
    page.wait_for_function("CERES_STAGE.length === 1 && !CERES_STAGE[0].txId", timeout=10_000)
    assert page.evaluate("Object.keys(JSON.parse(localStorage.getItem(inFlightStoreKey())).records).length") == 0
    assert page.locator("#ceres-confirmation-banner.tone-danger").is_visible()
    page.close()


def test_g8_undo_all_preserves_restored_in_flight_entry(live_browser: tuple[Browser, str]) -> None:
    """G8. Mutation evidence: remove the tx-id filter in ``undoAllCheckoutStage``; expected red: Undo all discards the restored signed entry."""
    page = _open(live_browser)
    first_index, first_name, _ = _stage_first_available(page)
    _sign(page)
    page.reload()
    wait_for_board(page)
    page.locator("#checkout-btn").dispatch_event("click")
    assert page.locator("button[data-checkout-action='undo-all']").is_disabled()
    _dismiss_banner(page)
    _stage_first_available(page)
    page.locator("#checkout-btn").dispatch_event("click")
    assert page.locator("button[data-checkout-action='undo-all']").is_enabled()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("button[data-checkout-action='undo-all']").click()
    assert page.evaluate("CERES_STAGE.length") == 1
    assert page.evaluate("CERES_STAGE[0].txId") == "FAKE-IR1882-TX-1"
    page.close()


def test_g9_store_failure_does_not_break_signing(live_browser: tuple[Browser, str]) -> None:
    """G9. Mutation evidence: remove the ``writeInFlightRecord`` try/catch; expected red: the synchronous stub rejects signing instead of degrading."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script("""
const ir1882SetItem = Storage.prototype.setItem;
Storage.prototype.setItem = function(key, value) {
 if (this === window.localStorage && String(key).startsWith('ceres:in-flight:v1:')) throw new Error('storage disabled');
 return ir1882SetItem.call(this, key, value);
};
""")
    page.add_init_script(STUB)
    page.route("**/api/op-verify*", _pending)
    page.goto(_test_url(base_url))
    wait_for_board(page)
    _stage_first_available(page)
    _sign(page)
    page.locator("#ceres-stage-toast").wait_for()
    assert "could not save the signed-change marker" in page.locator("#ceres-stage-toast").inner_text()
    page.evaluate("async () => { await requestOperationPreview({scope: 'portfolio'}); await confirmOperationBroadcast({scope: 'portfolio'}); }")
    page.wait_for_timeout(200)
    assert _count(page) == 1
    page.close()
