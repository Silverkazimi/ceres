"""Guards for the paired harvest resource tile structure."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ceres.harvest import project_harvest_plot

from .conftest import spawn_live_ceres
from .vm_context import read_entry_source, run_in_module_context

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"
CSS = STATIC / "ceres.css"
MODAL_ENTRY = STATIC / "harvest-modal.js"


def _resource_markup(received: dict, costs: dict, **options: object) -> str:
    payload = json.dumps({"received": received, "costs": costs, "options": options})
    body = f"""
const value = {payload};
console.log(harvestResourceColumns(value.received, value.costs, value.options));
"""
    return run_in_module_context(body, entry=STATIC / "harvest-resources.js").stdout.strip()


def test_pending_resource_tile_has_one_art_and_two_related_figures():
    html = _resource_markup({"GRAIN": 0}, {"GRAIN": 6708.57}, pending=True, receipt=True)
    assert 'class="hm-totals hm-totals--receipt"' in html
    assert html.count('class="hm-resource-pair') == 1
    assert html.count('class="hm-resource-art"') == 1
    assert html.count("hm-resource-credit") == 1
    assert html.count("hm-resource-debit") == 1
    assert html.count('class="hm-skel is-num"') == 2


def test_non_grain_tiles_keep_each_resource_once_and_pair_cost_by_resource():
    html = _resource_markup({"IRON": 18218.41}, {"GRAIN": 1015, "WOOD": 273.17}, receipt=True)
    assert html.count('class="hm-resource-pair') == 3
    assert html.count('class="hm-resource-art"') == 3
    # (resource-grid truth): a resource with only a cost shows no "0 received" line; only Iron was received.
    assert html.count("hm-resource-credit") == 1
    assert html.count("hm-resource-debit") == 2
    assert "Grain costs" not in html
    assert "Wood costs" not in html
    assert "18,218.41" in html
    assert "−<span>1,015.00</span>" in html
    assert "−<span>273.17</span>" in html


def test_non_grain_projection_uses_worksite_output_when_native_resource_is_grain():
    projection = project_harvest_plot({
        "deed_uid": "ore-plot",
        "facts": {"resource": "GRAIN", "region": "PR-BRL-041"},
        "staking_facts": {},
        "worksite_facts": {
            "project_type": "worksite_iron",
            "is_active": True,
            "last_action_time": "2026-09-20T10:00:00Z",
            "hours_since_last_op": 2,
            "rewards_per_hour": 273.175,
            "projected_amount_received": 18218.408,
            "grain_req_per_hour": 1015,
            "grain_required": 1015,
        },
    })
    assert projection["token"] == "IRON"


def test_two_resources_remain_two_paired_tiles_when_single_plot_flag_is_set():
    html = _resource_markup({"GRAIN": 69518.41, "WOOD": 12}, {"GRAIN": 6708.57, "WOOD": 2}, singlePlot=True, receipt=True)
    assert html.count('class="hm-resource-pair') == 2
    assert html.count('class="hm-resource-art"') == 2


def _modal_source_with_live_harness_bindings() -> str:
    source = read_entry_source(MODAL_ENTRY)
    return source.rsplit("}", 1)[0] + """
Object.defineProperties(globalThis, {
  M: {get: () => M},
  plotBodyHtml: {get: () => plotBodyHtml},
});
}
"""


def _plot_body_markup() -> str:
    projection = {
        "state": "harvesting",
        "token": "GRAIN",
        "net_at_anchor": 69518.41,
        "gross_per_hour": 0,
        "anchor_at": "2026-09-21T00:00:00Z",
        "hours_at_anchor": 0,
        "tax_rate": 0.1,
        "costs": [{"symbol": "GRAIN", "accrued": 6708.57, "per_hour": 0}],
        "finds": {},
    }
    result = run_in_module_context(
        f"""
CURRENT_PLAYER = 'alice';
Object.assign(M, {{ deedUids: ['plot-1'], preview: null, player: 'alice' }});
PLOTS.set('alice|plot-1', {{ data: {{ projection: {json.dumps(projection)} }} }});
console.log('__PLOT__' + JSON.stringify(plotBodyHtml()));
process.exit(0);
""",
        entry=MODAL_ENTRY,
        source=_modal_source_with_live_harness_bindings(),
    )
    return json.loads(result.stdout.split("__PLOT__", 1)[1].splitlines()[0])


def _modal_preview_page(page, width: int, markup: str) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.set_content(
        "<!doctype html><html data-theme='dark'><head><style>"
        + (STATIC / "canonical.css").read_text(encoding="utf-8")
        + "</style><style>"
        + CSS.read_text(encoding="utf-8")
        + "</style></head><body><div id='harvest-modal-overlay' class='harvest-overlay'>"
        + "<div class='harvest-panel' role='dialog'><div class='hm-body'>"
        + markup
        + "</div></div></div></body></html>"
    )


@pytest.mark.parametrize("width", [1536, 412, 360])
def test_plot_modal_you_receive_card_has_stacked_net_ledger_without_overlap(width: int) -> None:
    # repair note: a bare `with sync_playwright() as playwright: ... .chromium.launch()`
    # here launched a SECOND, independent Playwright driver in the same process as the
    # `live_browser` fixture used elsewhere in the suite (`spawn_live_ceres()`, shared for the
    # whole session per ). Playwright's sync API fakes a "running" asyncio loop on the
    # main thread via `asyncio._set_running_loop()` while servicing an `expect_response`
    # `wait_for_*` call on that shared browser, and never unsets it -- so a later bare
    # `sync_playwright()` call in the same thread sees a loop it thinks is "already running"
    # and refuses with "It looks like you are using Playwright Sync API inside the asyncio
    # loop." This test does not need the live Ceres server at all (it renders static markup
    # via `_modal_preview_page`), so it reuses the one shared browser everything else in the
    # package already shares instead of standing up a second driver.
    markup = _plot_body_markup()
    with spawn_live_ceres() as (browser, _base_url):
        page = browser.new_page()
        _modal_preview_page(page, width, markup)
        result = page.locator(".hm-resource-summary").first.evaluate(
                """card => {
                  const ledger = card.querySelector('.hm-plot-ledger');
                  const rows = [...ledger.querySelectorAll('.hm-plot-ledger-row')];
                  const rect = node => { const box = node.getBoundingClientRect(); return {left:box.left, right:box.right, top:box.top, bottom:box.bottom}; };
                  return {
                    heading: card.querySelector('.hm-sec-h')?.textContent.trim(),
                    rows: rows.map(row => ({symbol:row.dataset.resource, credit:!!row.querySelector('.hm-resource-credit'), debit:!!row.querySelector('.hm-resource-debit'), rect:rect(row)})),
                    cardOverflow: card.scrollWidth - card.clientWidth,
                  };
                }"""
        )
        assert result["heading"] == "You receive · after tax"  # costs sit in their own rows
        assert result["rows"] == [dict(symbol="GRAIN", credit=True, debit=False, rect=result["rows"][0]["rect"])]
        assert result["cardOverflow"] <= 1
        page.close()
