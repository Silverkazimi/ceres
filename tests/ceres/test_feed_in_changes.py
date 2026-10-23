""": running constructions and "Feed the workers" in the Changes window.

Proves, against the running app with the board and wallet mocked:
- a building plot shows a gold waiting row with its ready time and is not signed; a stalled one is not listed at all (: it has its own Stalled badge on the plot);
- a finished one shows "Feed the workers" with its Grain cost and is signed from Changes;
- its operation is the worksite window's own, field for field (same request body, same account and operations);
- a feed whose preview fails is set aside as waiting and does not stop the ready one.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
from playwright.sync_api import Browser, TimeoutError as PlaywrightTimeoutError

from .conftest import settle, spawn_live_ceres


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _construction(row: dict, *, ready_in: timedelta | None, stalled: bool = False, completion: bool = False) -> dict:
    out = copy.deepcopy(row)
    out["is_construction"] = True
    out["pending_change"] = {
        "deed_uid": out["deed_uid"], "kind": "worksite_change", "project_id": f"p-{out['deed_uid']}",
        "from": "grain", "to": "wood", "to_label": "Logging Camp", "from_label": "Grain Farm",
        "started_at": _iso(timedelta(hours=-20)), "ready_at": None if stalled else _iso(ready_in),
        "progress_pct": None if stalled else 0.5, "stalled": stalled,
    }
    out["worksite_completion"] = {
        "kind": "worksite_completion", "project_id": f"p-{out['deed_uid']}", "to": "wood",
        "to_label": "Logging Camp", "grain_cost": 120, "ready_at": _iso(ready_in),
    } if completion else None
    return out


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def test_constructions_wait_and_feed_signs_from_changes(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1536, "height": 900})
    page.add_init_script(
        "try { localStorage.clear(); } catch (_) {}"
        "window.__s16649Calls = []; window.hive_keychain = {requestBroadcast(account, ops, key, callback) "
        "{ window.__s16649Calls.push({account, ops, key}); callback({success: true, result: {id: 's16649-tx-' + window.__s16649Calls.length}, data: {key}}); }};"
    )
    uids: dict[str, str] = {}
    preview_bodies: list[tuple[str, dict]] = []

    def board_route(route) -> None:
        payload = route.fetch().json()
        plots = [plot for plot in payload.get("plots", []) if plot.get("deed_uid")]
        assert len(plots) >= 4, "the local board fixture must provide four plots"
        # The counts below are this test's four shaped constructions only. The recording's own
        # constructions were started at recording time, so with wall-clock time they finish and
        # become ready feeds of their own; served here as plain plots.
        for plot in plots:
            plot.update(is_construction=False, pending_change=None, worksite_completion=None)
        shapes = {
            "building": _construction(plots[0], ready_in=timedelta(days=1, hours=2, minutes=30)),
            "feed": _construction(plots[1], ready_in=timedelta(hours=-1), completion=True),
            "refused": _construction(plots[2], ready_in=timedelta(hours=-1), completion=True),
            "stalled": _construction(plots[3], ready_in=None, stalled=True),
        }
        for name, shaped in shapes.items():
            uids[name] = shaped["deed_uid"]
            payload["plots"][payload["plots"].index(next(p for p in payload["plots"] if p.get("deed_uid") == shaped["deed_uid"]))] = shaped
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    def preview_route(route) -> None:
        uid = route.request.url.split("/api/plots/")[1].split("/")[0]
        preview_bodies.append((uid, json.loads(route.request.post_data or "{}")))
        if uid == uids.get("refused"):
            route.fulfill(status=400, content_type="application/json",
                          body=json.dumps({"detail": f"operation construction refused: worksite completion is not ready on '{uid}'"}))
            return
        operation = ["custom_json", {
            "id": "sm_land_operation", "required_auths": [], "required_posting_auths": ["player0005"],
            "json": json.dumps({"op": "update_worksite", "deed_uid": uid, "project_id": f"p-{uid}"}),
        }]
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "kind": "worksite_completion", "account": "player0005", "key_tier": "Posting", "deed_uid": uid,
            "grain_cost": 120, "chunks": [{"index": 0, "operations": [operation], "op_count": 1}], "operations": [operation],
        }))

    page.route("**/api/board*", board_route)
    page.route("**/worksite-preview*", preview_route)
    page.route("**/api/op-inclusion*", lambda route: route.fulfill(status=200, content_type="application/json", body='{"included":true,"block_num":123}'))
    page.route("**/api/op-verify*", lambda route: route.fulfill(status=200, content_type="application/json", body='{"state":"pending"}'))
    page.goto(f"{base_url}/?player=sample01", timeout=8_000)
    try:
        page.wait_for_function("document.querySelector('#plot-tbody > tr.plot-row') !== null", timeout=15_000)
    except PlaywrightTimeoutError:
        pytest.fail("live Ceres board did not render a plot row within 15 seconds", pytrace=False)

    # Field for field: the worksite window's own completion preview vs the Changes window's feed plan.
    proof = page.evaluate("""async (uid) => {
      const ws = await import('/static/worksite.js');
      const { S } = await import('/static/state.js');
      const { BOARD } = await import('/static/board.js');
      const row = BOARD.plots.find((plot) => plot.deed_uid === uid);
      await ws.requestWorksiteCompletionPreview(row);
      const modal = S.WORKSITE_MODAL_PREVIEW.payload;
      const plan = await ws.prepareWorksiteCompletion(row);
      return { modal: { account: modal.account, operations: modal.chunks[0].operations, grain: modal.grain_cost },
               changes: { account: plan.account, operations: plan.chunk.operations, grain: plan.grainCost } };
    }""", uids["feed"])
    assert proof["changes"] == proof["modal"], proof
    feed_bodies = [body for uid, body in preview_bodies if uid == uids["feed"]]
    assert feed_bodies == [{"action": "complete"}, {"action": "complete"}], feed_bodies

    count = page.locator("#checkout-count")
    page.wait_for_function("document.querySelector('#checkout-count')?.textContent === '2'", timeout=8_000)
    assert count.inner_text() == "2", "both finished feeds count before their check; building and stalled rows never do"
    page.locator("#checkout-btn").click()
    panel = page.locator("#checkout-panel")
    # The construction rows proper; the "Ready now" summary at the top of Changes reuses the row
    # class for its items (`.is-ready-item`, checkout.js / ready-feed.js) and is not counted here.
    rows = panel.locator(".ch-worksite-build-row:not(.is-ready-item)")
    page.wait_for_function(
        "() => [...document.querySelectorAll('#checkout-panel .ch-worksite-build-row')].some((row) => row.innerText.includes('the game has not finished this build yet'))",
        timeout=8_000,
    )
    assert count.inner_text() == "1", "a feed whose preview failed is set aside and leaves the count"
    building = panel.locator('[data-construction-kind="building"]')
    assert building.count() == 1 and "Building Logging Camp" in building.inner_text()
    assert "Waiting · ready" in building.inner_text() and "(in 1d 2h)" in building.inner_text()
    assert building.locator(".ch-status.is-waiting").count() == 1
    assert panel.locator('[data-construction-kind="stalled"]').count() == 0, ": a stalled build is a plot state with its own badge, never a Changes row"
    feeds = panel.locator('[data-construction-kind="feed"]')
    assert feeds.count() == 2
    texts = feeds.all_inner_texts()
    assert any("Feed the workers" in t and "120 Grain" in t and "Ready to sign" in t for t in texts), texts
    assert any("Waiting · the game has not finished this build yet" in t for t in texts), texts
    assert rows.count() == 3, rows.all_inner_texts()

    sign = panel.page.locator('#changes-overlay [data-checkout-action="sign-worksites"]:not([disabled])')
    sign.click(timeout=8_000)
    page.wait_for_function("window.__s16649Calls.length === 1", timeout=8_000)
    settle(page)
    calls = page.evaluate("window.__s16649Calls")
    assert len(calls) == 1, "only the ready feed is signed; waiting rows never reach the wallet"
    assert calls[0]["account"] == "player0005" and calls[0]["key"] == "Posting"
    assert calls[0]["ops"] == proof["modal"]["operations"], "the signed feed differs from the worksite window's operation"
    page.wait_for_function(
        "() => [...document.querySelectorAll('#checkout-panel [data-construction-kind=\"feed\"]')].some((row) => row.innerText.includes('Signed'))",
        timeout=8_000,
    )
    page.close()
