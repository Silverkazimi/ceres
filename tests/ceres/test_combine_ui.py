"""Contract guards for the gather-all combine dialog (v0.7.28).

Replaces the old per-row inline-picker contract (v0.7.21, rejected on use --
see CHANGELOG): selection now happens in one dialog gathering every owned copy
of a card, not by hand-finding N rows in the idle-cards table.

/census row 17 (batch B): the file used to run one giant
`_combine_contract()` source-string scan over `app.js`, which  itself
named as the example of implementation-spelling coupling (`isGoldFoil(candidate)`
at the old line 29 -- now demonstrably stale, per the census). Converted to
rendered reads of the real dialog, opened the same route-mocked way
`test_combine_hero.py` does (the board's `?combine=` door for a real
occupant, with synthetic idle duplicates and a mocked `/api/combine/preview`).

SCOPE NOTE (reported per ticket instruction rather than forced): the sign/verify
half of the old `_combine_contract` (`beginSharedOperation`, `/api/op-verify`,
`domain-confirmed`, `applyCombineConfirmedUpdate`) and all of
`_pending_surface_contract` are dropped here, not converted -- driving a real
sign click reaches `confirmCombineBroadcast()`, which needs a wallet-signing
mock 's proof rules explicitly forbid faking casually ("no external API,
real broadcast, credential, or irreversible state mutation"), and that surface
is `test_combine_sign_verify.py` (census row 16), a Batch D file outside this
ticket's write-set. Converting it here would either duplicate that file's job
badly or require scope this ticket does not have. Also dropped, as internal
state-variable-name assertions with no rendered equivalent
(`COMBINE_OPTIMISTIC`/`sm_combine_all` absence, `COMBINE_SELECTION`/
`COMBINE_DIALOG` existing): these are absence-of-symbol claims, the same
(b)-vs-(c) question census row 7 (`test_boundaries.py`) already flags as
needing an maintainer ruling, not a mechanical rendered read -- not decided here.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Browser

from .combine_door import open_combine_by_print, serve_idle_cards
from .conftest import settle, spawn_live_ceres

ROOT = Path(__file__).parents[2]
CSS = ROOT / "ceres/static/ceres.css"
NOTICES = ROOT / "ceres/notices.py"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _real_occupied_worker(base_url: str, *, require_gold: bool = False) -> tuple[str, dict]:
    """A real, combinable (unmaxed, resolved-ceiling) worker occupant, real
    plot -- optionally required to be a gold-foil printing."""
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=10) as response:
        board = json.loads(response.read().decode("utf-8"))
    for plot in board["plots"]:
        if not (plot.get("capacity") or {}).get("worker_count", 0):
            continue
        deed_uid = plot["deed_uid"]
        with urllib.request.urlopen(f"{base_url}/api/plots/{urllib.parse.quote(deed_uid, safe='')}", timeout=10) as response:
            row = json.loads(response.read().decode("utf-8"))
        for worker in row.get("workers", []):
            if (
                worker.get("card_detail_id") is not None
                and worker.get("edition") is not None
                and worker.get("bcx_max") is not None
                and worker.get("bcx") is not None
                and worker["bcx"] < worker["bcx_max"]
                and (worker.get("gold") is True or not require_gold)
            ):
                return deed_uid, worker
    raise AssertionError(
        f"no live plot has a{' gold-foil' if require_gold else 'n'} unmaxed worker occupant to build a combine fixture from"
    )


def _open_combine_dialog(
    page, base_url: str, *, occupant: dict | None = None, deed_uid: str | None = None,
    extra_copies: list[dict] | None = None, preview_response: dict | None = None,
):
    """Same `?combine=` door flow as `test_combine_hero.py`'s helper, generalised
    to accept extra blocked/context copies and a caller-supplied preview
    response (so gate/refusal states can be exercised)."""
    if occupant is None or deed_uid is None:
        deed_uid, occupant = _real_occupied_worker(base_url)

    duplicate_one = dict(occupant)
    duplicate_one["uid"] = "C-TEST-COMBINE-UI-DUP1"
    duplicate_one["asset_ref"] = "combine-ui-dup1"
    duplicate_one["bcx"] = 1
    duplicate_one["available"] = True
    duplicate_one["blocked_reason"] = None
    duplicate_one["bucket"] = "stake_expired_or_historical"
    duplicate_two = dict(duplicate_one)
    duplicate_two["uid"] = "C-TEST-COMBINE-UI-DUP2"
    duplicate_two["asset_ref"] = "combine-ui-dup2"
    cards = [duplicate_one, duplicate_two] + (extra_copies or [])

    if preview_response is None:
        preview_response = {
            "account": "player0005",
            "uids": [duplicate_one["uid"], duplicate_two["uid"]],
            "predicted_survivor_uid": duplicate_one["uid"],
            "resulting_bcx": duplicate_one["bcx"] + duplicate_two["bcx"],
            "hazard": {"destroyed_uids": [duplicate_two["uid"]]},
            "buildable": True,
            "observed_at": "2026-09-02T00:00:00Z",
        }

    seen_preview_requests: list[dict] = []

    def fulfill_preview(route) -> None:
        seen_preview_requests.append(json.loads(route.request.post_data or "{}"))
        status = 200 if preview_response.get("buildable", True) else 409
        body = preview_response if status == 200 else {"code": "combine_refused", "message": preview_response.get("message", "refused"), "preview": preview_response}
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    serve_idle_cards(page, cards)
    page.route("**/api/combine/preview*", fulfill_preview)
    open_combine_by_print(page, base_url, occupant)
    overlay = page.locator("#combine-dialog-overlay")
    overlay.wait_for(state="visible", timeout=30_000)
    settle(page)  # renderCombineDialog() after requestCombinePreview() resolves
    return deed_uid, occupant, [duplicate_one, duplicate_two], seen_preview_requests


def test_rendered_combine_dialog_posts_selected_uids_and_gates_signing_on_refusal(
    live_browser: tuple[Browser, str],
) -> None:
    """Census row 17: replaces the source-text checks that `/api/combine/preview`
    is POSTed with `{ uids }` and that the sign button carries the disabled
    attribute + a gate-reason aria-label when the server refuses. Reads the
    REAL outgoing request body and the REAL rendered sign button.

    Named injection (not applied here; independently watched): `requestCombinePreview()`'s
    fetch body changed from `JSON.stringify({ uids })` to `JSON.stringify({})`.
    Expected red: the mocked route's captured request body no longer contains
    the two selected uids."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    deed_uid, occupant, copies, requests = _open_combine_dialog(
        page, base_url,
        preview_response={"buildable": False, "message": "cannot combine selected Card IDs: refused for this test"},
    )
    assert requests, "opening the dialog with 2+ selected copies must POST /api/combine/preview"
    assert sorted(requests[0].get("uids", [])) == sorted(c["uid"] for c in copies)
    assert "app" not in requests[0], "the server must choose the transaction app identity"

    sign_button = page.locator("#combine-dialog-overlay button.combine-sign")
    sign_button.wait_for(timeout=10_000)
    assert sign_button.get_attribute("disabled") is not None, "a refused preview must gate the sign button"
    assert "refused" in (sign_button.get_attribute("aria-label") or "").lower()
    page.close()


def test_rendered_combine_dialog_sign_button_is_enabled_when_preview_is_buildable(
    live_browser: tuple[Browser, str],
) -> None:
    """Census row 17 (contrasting case to the refusal test above): a `buildable:
    true` preview must leave the sign button enabled with the plain sign label,
    not the gate reason.

    Named injection (not applied here; independently watched): `renderCombineDialogEditor()`'s
    `const canSign = !!preview?.buildable && ...` changed to `const canSign = false;`.
    Expected red: `sign_button.get_attribute("disabled") is not None` where this
    test asserts it is `None`."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    _open_combine_dialog(page, base_url)
    sign_button = page.locator("#combine-dialog-overlay button.combine-sign")
    sign_button.wait_for(timeout=10_000)
    assert sign_button.get_attribute("disabled") is None, "a buildable preview must not gate the sign button"
    assert "destroys" in (sign_button.get_attribute("aria-label") or "").lower() or "combine" in sign_button.inner_text().lower()
    page.close()


def test_rendered_combine_hero_art_uses_the_gold_suffixed_cdn_url_for_a_gold_survivor(
    live_browser: tuple[Browser, str],
) -> None:
    """Census row 17: replaces the literal `isGoldFoil(candidate)` source-spelling
    check ('s own named example of implementation-spelling coupling,
    confirmed stale by the census) with the actual rendered proof: a gold-foil
    survivor's hero art `<img src>` must carry the CDN's `_gold` suffix.

    Named injection (not applied here; independently watched): `editorCardImage()`'s
    URL template changed from `${...}${isGoldFoil(asset) ? '_gold' : ''}.jpg` to
    always omit the suffix. Expected red: `"_gold" in src` fails for a gold
    survivor."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    deed_uid, occupant = _real_occupied_worker(base_url, require_gold=True)
    _open_combine_dialog(page, base_url, occupant=occupant, deed_uid=deed_uid)
    art = page.locator("#combine-dialog-overlay .combine-hero-art")
    art.wait_for(timeout=10_000)
    src = art.get_attribute("src") or ""
    assert "_gold" in src, f"gold-foil survivor's hero art must use the _gold CDN suffix, got: {src!r}"
    page.close()


def test_rendered_combine_dialog_lists_every_owned_copy_including_blocked_ones(
    live_browser: tuple[Browser, str],
) -> None:
    """Census row 17 (`_gather_all_contract`): replaces the source-string scan of
    `openCombineGroupDialog()` (full group reaches the dialog; only
    `c.uid && c.available` copies get pre-ticked) with a rendered read: a
    blocked (unavailable) third copy must still appear in the list, marked
    `.is-blocked` with its own reason, never `.is-survives`/`.is-destroyed`
    (which would mean it got pre-ticked despite being ineligible).

    Named injection (not applied here; independently watched): `openCombineGroupDialog()`'s
    pretick line `if (c.uid && c.available) COMBINE_SELECTION.set(...)` changed
    to `if (c.uid) COMBINE_SELECTION.set(...)` (ticks every copy regardless of
    availability). Expected red: the blocked row's class includes `is-survives`
    or `is-destroyed` instead of `is-blocked`, and/or the preview POST body
    includes the blocked copy's uid."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    deed_uid, occupant = _real_occupied_worker(base_url)
    blocked_copy = dict(occupant)
    blocked_copy["uid"] = "C-TEST-COMBINE-UI-BLOCKED"
    blocked_copy["asset_ref"] = "combine-ui-blocked"
    blocked_copy["bcx"] = 1
    blocked_copy["available"] = False
    blocked_copy["blocked_reason"] = "Staked on Land"
    blocked_copy["bucket"] = "stake_expired_or_historical"

    _, _, copies, requests = _open_combine_dialog(
        page, base_url, occupant=occupant, deed_uid=deed_uid, extra_copies=[blocked_copy],
    )
    overlay = page.locator("#combine-dialog-overlay")
    rows = overlay.locator(".combine-copy-row")
    assert rows.count() == 3, f"all 3 owned copies (2 eligible + 1 blocked) must be listed, got {rows.count()}"
    # The row's code shows where the copy is (combineCopyWhere), never its Card ID; the ID is the code's data-uid.
    blocked_row = overlay.locator(f".combine-copy-row:has(code[data-uid='{blocked_copy['uid']}'])")
    blocked_row.wait_for(timeout=10_000)
    assert "is-blocked" in (blocked_row.get_attribute("class") or "")
    assert blocked_row.get_attribute("title") == blocked_copy["blocked_reason"] or "staked" in (blocked_row.get_attribute("title") or "").lower()
    assert blocked_copy["uid"] not in requests[0].get("uids", []), "a blocked copy must never reach the preview POST as selected"

    survivor_row = overlay.locator(".combine-copy-row.is-survives")
    survivor_row.wait_for(timeout=10_000)
    assert "highest cc" in (survivor_row.get_attribute("title") or "").lower()
    page.close()


def test_dialog_contract_and_layout_scope() -> None:
    """CSS-only: `ceres/static/ceres.css` is the subject under test here, not
    `app.js` text -- outside this census row's JS-reading scope, left
    unchanged. (`COMBINE_SELECTION`/`COMBINE_DIALOG`/`beginSharedOperation`/
    `operations: [body.operation]`/op-verify/domain-confirmed/
    `applyCombineConfirmedUpdate` assertions from the old `_combine_contract`
    were dropped here -- see module docstring's SCOPE NOTE.)"""
    css = CSS.read_text(encoding="utf-8")
    assert ".combine-dialog-overlay" in css
    assert ".combine-copy-row" in css
    assert ".combine-editor-layout" in css
    # One column (identity, result, selection stacked), no longer the 18rem art column beside
    # the editor; the panel width follows the page's own viewport unit (--ceres-vw).
    assert ".combine-editor-layout { display: flex; flex-direction: column;" in css
    assert "width: min(60rem, calc(var(--ceres-vw) * 96))" in css
    assert ".combine-copy-list" in css and "max-height: 13.5rem" in css
    modal_css = css[css.index(".combine-dialog-overlay"):css.index(".combine-arrival")]
    assert not any(line.lstrip().startswith(".plot-table") for line in modal_css.splitlines())


def test_notice_catalog_owns_combine_copy() -> None:
    """Reads `ceres/notices.py`, not `ceres/static/*.js` -- outside this
    census's scope, left unchanged."""
    source = NOTICES.read_text(encoding="utf-8")
    for code in (
        "combine_select", "combine_preview_loading", "combine_refusal",
        "combine_destroyed", "combine_survivor", "combine_sign", "combine_confirmed",
    ):
        assert f'"{code}"' in source
