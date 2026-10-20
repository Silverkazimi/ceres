"""Guards for the single-plot ledger, readable labels, and harvest-box cancellation."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from playwright.sync_api import Browser

from ceres.transaction_identity import app_identity
from .conftest import spawn_live_ceres
from .vm_context import run_in_module_context


ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"


@pytest.mark.parametrize(
    ("projection", "expected"),
    [
        ({"token": "GRAIN", "net": 100, "costs": {"GRAIN": 25}}, [["GRAIN", 75, "gain"]]),
        ({"token": "WOOD", "net": 100, "costs": {"GRAIN": 25}}, [["WOOD", 100, "gain"], ["GRAIN", -25, "cost"]]),
        ({"token": "SPS", "net": 100, "costs": {"IRON": 4, "WOOD": 2, "GRAIN": 1, "STONE": 3}},
         [["SPS", 100, "gain"], ["GRAIN", -1, "cost"], ["WOOD", -2, "cost"], ["STONE", -3, "cost"], ["IRON", -4, "cost"]]),
    ],
)
def test_net_after_costs_gain_first_and_stable(projection: dict, expected: list) -> None:
    result = run_in_module_context(
        f"console.log(JSON.stringify(netAfterCosts({json.dumps(projection)}).map(r => [r.symbol,r.amount,r.kind])));",
        entry=STATIC / "harvest-net.js",
    )
    assert json.loads(result.stdout.strip()) == expected


def test_cancel_builder_has_observed_posting_payload() -> None:
    from ceres.worksite_broadcast import build_worksite_cancel_preview

    deed = "I-298-9d49865f1874af"
    report = {"plots": [{"deed_uid": deed, "facts": {"region": "PR-CEF-65"}, "worksite_facts": {}}]}
    preview = build_worksite_cancel_preview(
        report, deed, account="player0005", app=app_identity(),
        worksite_override={"project_type": "worksite_stone_construction", "project_id": 413623},
    )
    name, body = preview["operations"][0]
    payload = json.loads(body["json"])
    assert name == "custom_json"
    assert body["id"] == "sm_land_operation"
    assert body["required_posting_auths"] == ["player0005"]
    assert body["required_auths"] == []
    assert payload == {
        "op": "cancel_construction", "region_uid": "PR-CEF-65", "deed_uids": [deed],
        "time_crystals": 0, "project_id": 413623, "auto_buy_grain": False,
        "app": app_identity(), "n": payload["n"],
    }
    assert isinstance(payload["n"], str) and payload["n"]


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _projected_row(row: dict, *, construction: bool = False) -> dict:
    result = copy.deepcopy(row)
    result["assets_loaded"] = True
    result["worksite_type"] = "Grain Farm"
    result["is_construction"] = construction
    if construction:
        result["pending_change"] = {"to": "stone", "to_label": "Quarry", "project_id": 413623,
                                    "started_at": "2026-09-20T00:00:00Z", "ready_at": "2026-09-30T00:00:00Z"}
        result["harvest"] = {"state": "construction", "construction": dict(result["pending_change"])}
    else:
        result["harvest"] = {"state": "harvesting", "token": "SPS", "net_at_anchor": 100,
                             "anchor_at": "2026-09-24T00:00:00Z", "gross_per_hour": 0,
                             "hours_at_anchor": 0, "tax_rate": 0,
                             "costs": [{"symbol": "GRAIN", "accrued": 5, "per_hour": 0},
                                       {"symbol": "WOOD", "accrued": 2, "per_hour": 0}]}
    return result


def _open_plot(page, base_url: str, *, construction: bool = False) -> tuple[str, dict]:
    selected: dict = {}

    def board_route(route) -> None:
        payload = route.fetch().json()
        row = next(plot for plot in payload["plots"] if plot.get("deed_uid"))
        selected["row"] = _projected_row(row, construction=construction)
        payload["plots"][payload["plots"].index(row)] = selected["row"]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    def plot_route(route) -> None:
        if "/harvest" in route.request.url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "server_now": "2026-09-24T00:00:00Z", "projection": {
                    **selected["row"]["harvest"], "source": "fresh",
                    **({"construction": {**selected["row"]["harvest"]["construction"], "project_id": 413623}}
                       if construction else {}),
                }, "affordability": {"status": "unknown"}, "region_balances": None, "issues": [], "signing_enabled": True,
            }))
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(selected["row"]))

    page.route("**/api/board*", board_route)
    page.route("**/api/plots/**", plot_route)
    page.goto(f"{base_url}/?player=sample01", timeout=15_000)
    page.wait_for_function("document.querySelector('#plot-tbody > tr.plot-row') !== null", timeout=15_000)
    deed = selected["row"]["deed_uid"]
    with page.expect_response(lambda response: f"/api/plots/{deed}/harvest" in response.url, timeout=15_000):
        page.locator(f'tr.plot-row[data-deed-uid="{deed}"]').click()
    page.locator(f'.harvest-bar[data-deed-uid="{deed}"]').wait_for(state="visible", timeout=15_000)
    return deed, selected["row"]


def test_single_plot_ledger_rects_and_harvest_label_floor(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1536, "height": 945})
    deed, _ = _open_plot(page, base_url)
    bar = page.locator(f'.harvest-bar[data-deed-uid="{deed}"]')
    size = bar.locator('.harvest-bar-lbl').evaluate("el => parseFloat(getComputedStyle(el).fontSize)")
    assert size >= 10.5
    bar.locator('[data-harvest-action="open-plot"]').click()
    ledger = page.locator('#harvest-modal-overlay .hm-plot-ledger')
    ledger.wait_for(state="visible", timeout=15_000)
    rows = ledger.locator('.hm-plot-ledger-row')
    assert [rows.nth(i).get_attribute('data-resource') for i in range(rows.count())] == ['SPS', 'GRAIN', 'WOOD']
    assert rows.nth(0).locator('.hm-resource-credit').count() == 1
    assert rows.nth(1).locator('.hm-resource-debit').count() == 1
    assert page.locator('#harvest-modal-overlay .hm-resource-summary > .hm-sec-h').text_content().strip() == 'You receive · after tax'
    assert rows.nth(0).locator('small').inner_text() == 'SPS received · after tax'
    gain_alignment = rows.nth(0).evaluate("row => { const rowRect=row.getBoundingClientRect(), art=row.querySelector('.hm-resource-art').getBoundingClientRect(), style=getComputedStyle(row); return {offset:art.left-rowRect.left, padding:parseFloat(style.paddingLeft)}; }")
    assert abs(gain_alignment['offset'] - gain_alignment['padding']) <= 2
    cost_alignment = rows.nth(1).evaluate("row => { const amount=row.querySelector('b').getBoundingClientRect(), label=row.querySelector('small').getBoundingClientRect(); return Math.abs(amount.top-label.top); }")
    assert cost_alignment <= 3
    rects = rows.evaluate_all("nodes => nodes.map(n => { const r=n.getBoundingClientRect(); return {top:r.top,bottom:r.bottom,left:r.left,right:r.right}; })")
    assert all(a['bottom'] <= b['top'] for a, b in zip(rects, rects[1:]))
    assert all(a['left'] < a['right'] for a in rects)
    page.close()


def test_harvest_static_has_no_harvest_box_undo_code() -> None:
    for path in STATIC.rglob('*'):
        if path.is_file():
            content = path.read_bytes()
            assert b'undoUnsignedPlotStage' not in content
            assert b'ceresPlotHasUnsignedStage' not in content
            assert b'ceresUndoUnsignedPlotStage' not in content
            assert b'harvest-bar-undo' not in content


def test_cancel_requires_review_before_mocked_wallet(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1536, "height": 945})
    page.add_init_script("window.__cancelWallets = 0; window.__cancelOps = null; window.hive_keychain = {requestBroadcast(_a,_o,_k,cb){ window.__cancelWallets++; window.__cancelOps = _o; cb({success:true,result:{id:'mock-cancel'},data:{key:'Posting'}}); }}")
    deed, row = _open_plot(page, base_url, construction=True)
    operation = ["custom_json", {"id": "sm_land_operation", "required_auths": [],
                 "required_posting_auths": ["player0005"], "json": json.dumps({
                     "op": "cancel_construction", "region_uid": row["region"], "deed_uids": [deed],
                     "time_crystals": 0, "project_id": 413623, "auto_buy_grain": False,
                     "app": app_identity(), "n": "mock-nonce"})}]
    page.route("**/worksite-preview*", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({
        "kind": "worksite_cancel", "account": "player0005", "key_tier": "Posting", "deed_uid": deed,
        "project_id": 413623, "chunks": [{"operations": [operation]}], "operations": [operation],
    })))
    bar = page.locator(f'.harvest-bar[data-deed-uid="{deed}"]')
    cancel = bar.locator('.harvest-bar-btn.harvest-bar-go[data-slot-action="cancel-construction"]')
    cancel.wait_for(state="visible", timeout=15_000)
    assert bar.locator('.harvest-bar-cancel').count() == 0
    assert bar.locator('.harvest-bar-undo').count() == 0
    assert bar.locator('.harvest-bar-btn.harvest-bar-go').count() == 1
    cancel.click()
    panel = page.locator('#worksite-modal-overlay')
    panel.locator('[data-worksite-action="cancel-broadcast"]:not([disabled])').wait_for(state="visible", timeout=15_000)
    assert 'This stops the running construction' in panel.inner_text()
    assert 'refund' not in panel.inner_text().lower()
    assert page.evaluate('window.__cancelWallets') == 0
    assert json.loads(operation[1]['json'])['project_id'] == 413623
    panel.locator('[data-worksite-action="cancel-broadcast"]').click()
    page.wait_for_function('window.__cancelWallets === 1', timeout=15_000)
    signed = page.evaluate('window.__cancelOps')
    assert signed == [operation]
    assert signed[0][1]['required_posting_auths'] == ['player0005']
    assert json.loads(signed[0][1]['json'])['app'] == app_identity()
    page.close()
