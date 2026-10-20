"""the region harvest receipt keeps the four base resources together."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import standalone_browser
from .vm_context import run_in_module_context

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"


def _region_receipt_html(member_count: int = 2, token_count: int = 7, width: int = 412) -> str:
    keys = ("GRAIN", "WOOD", "STONE", "IRON", "AURA", "SPS", "RESEARCH")[:token_count]
    run = {
        "members": [
            {
                "outcome": "success",
                "region_uid": f"region-{41 + index}",
                "received_by_token": {key: 69518.41 for key in keys},
                "costs_by_token": {key: 1000 for key in keys},
            }
            for index in range(member_count)
        ]
    }
    # the receipt picks its layout from the page's width classes (desktop = the Harvest all
    # ledger, <=1279 = the phone receipt), so the render gets the same classes the real page has.
    classes = json.dumps(_width_classes(width).split())
    result = run_in_module_context(
        f"document.documentElement.classList = {{ contains: (c) => {classes}.includes(c) }};\"\n f\"console.log('__HARVEST__' + JSON.stringify(harvestSuccessHtml({json.dumps(run)})));",
        entry=STATIC / "harvest-results.js",
    )
    return json.loads(result.stdout.split("__HARVEST__", 1)[1].splitlines()[0])


def _width_classes(width: int) -> str:
    thresholds = (1919, 1759, 1659, 1439, 1279, 767, 1099, 544, 960)
    classes = [f"ceres-lw-le-{threshold}" for threshold in thresholds if width <= threshold]
    if width >= 1400:
        classes.append("ceres-lw-ge-1400")
    return " ".join(classes)


def _modal_page_html(receipt_html: str, css: str, width: int, height: int) -> str:
    return f"""<!doctype html><html class=\"{_width_classes(width)}\" data-theme=\"dark\" style=\"--ceres-vw:{width / 100}px;--ceres-vh:{height / 100}px\"><head><meta charset=\"utf-8\">
<style>{(STATIC / "canonical.css").read_text(encoding="utf-8")}</style><style>{css}</style>
</head><body><div id=\"harvest-modal-overlay\" class=\"harvest-overlay\"><div class=\"harvest-panel\" role=\"dialog\" aria-modal=\"true\" aria-labelledby=\"harvest-modal-title\"><header class=\"hm-headr\"><div><h2 id=\"harvest-modal-title\">Harvest receipt</h2><p>2 confirmed collections</p></div><button class=\"harvest-x\" type=\"button\" aria-label=\"Close\"></button></header><div class=\"hm-body\">{receipt_html}</div></div></div></body></html>"""


def _measure(page):
    return page.locator(".harvest-receipt").first.evaluate(
        """
card => {
 const cards = [...document.querySelectorAll('.harvest-receipts .harvest-receipt')];
 const cardBoxes = cards.map(item => {
 const box = item.getBoundingClientRect();
 return { left: box.left, top: box.top, right: box.right, width: box.width };
 });
 const region = card.querySelector('.hm-batch-region');
 const regionLine = region.querySelector('.hm-batch-region-line');
 const slots = region.querySelector('.hm-batch-slots');
 const allBadges = [...slots.querySelectorAll('.hm-batch-badge')];
 const badges = [...slots.querySelectorAll('.hm-batch-badge:not(.is-empty)')];
 const badgeBoxes = badges.map(badge => {
 const box = badge.getBoundingClientRect();
 const number = badge.querySelector('.hm-batch-number');
 const numberBox = number.getBoundingClientRect();
 return {
 resource: badge.dataset.resource,
 left: box.left,
 top: box.top,
 bottom: box.bottom,
 right: box.right,
 width: box.width,
 clientRects: badge.getClientRects().length,
 nameClientRects: badge.querySelector('.hm-batch-name').getClientRects().length,
 name: badge.querySelector('.hm-batch-name').textContent.trim(),
 badgeRight: box.right,
 numberRight: numberBox.right,
 numberScrollWidth: number.scrollWidth,
 numberClientWidth: number.clientWidth,
 };
 });
 const slotsBox = slots.getBoundingClientRect();
 const regionBox = region.getBoundingClientRect();
 const regionLineBox = regionLine.getBoundingClientRect();
 const slotStyle = getComputedStyle(slots);
 const rows = [];
 for (const box of badgeBoxes) {
 if (!rows.length || Math.abs(rows[rows.length - 1][0].top - box.top) > 1) rows.push([box]);
 else rows[rows.length - 1].push(box);
 }
 const rowBoxes = rows.map(row => ({
 top: row[0].top,
 left: row[0].left,
 right: row[row.length - 1].right,
 width: row.reduce((sum, box) => sum + box.width, 0),
 gaps: row.slice(1).map((box, index) => box.left - row[index].right),
 resources: row.map(box => box.resource),
 }));
 return {
 cardWidth: card.getBoundingClientRect().width,
 resourceNames: [...new Set([...region.querySelectorAll('.hm-batch-name')].map(name => name.textContent.trim()))],
 emptySlotsVisible: allBadges.filter(badge => badge.classList.contains('is-empty') && getComputedStyle(badge).visibility !== 'hidden' && getComputedStyle(badge).display !== 'none').length,
 badgeBoxes,
 rowBoxes,
 cardBoxes,
 slotWidth: slotsBox.width,
 slotLeft: slotsBox.left,
 slotRight: slotsBox.right,
 slotGap: parseFloat(slotStyle.columnGap),
 slotsOverflow: slots.scrollWidth - slots.clientWidth,
 regionOverflow: region.scrollWidth - region.clientWidth,
 cardOverflow: card.scrollWidth - card.clientWidth,
 regionWidth: regionBox.width,
 regionLineWidth: regionLineBox.width,
 };
}
"""
    )


def _render_measurements(
    css: str,
    width: int,
    height: int,
    dpr: float = 1,
    member_count: int = 2,
    token_count: int = 7,
):
    with standalone_browser() as browser:
        page = browser.new_page(
            viewport={"width": width, "height": height}, device_scale_factor=dpr
        )
        page.set_content(
            _modal_page_html(_region_receipt_html(member_count, token_count, width), css, width, height)
        )
        measurements = _measure(page)
        page.close()
    return measurements


@pytest.mark.parametrize(
    ("width", "height", "dpr", "member_count", "token_count"),
    [
        (width, 864, dpr, member_count, token_count)
        for width, dpr in ((600, 1),)
        for member_count in (1, 2, 3)
        for token_count in (4, 5, 6, 7)
    ],
)
def test_region_receipt_geometry_keeps_iron_on_row_one(
    width: int, height: int, dpr: float, member_count: int, token_count: int
) -> None:
    ceres_css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    measurements = _render_measurements(
        ceres_css,
        width,
        height,
        dpr=dpr,
        member_count=member_count,
        token_count=token_count,
    )

    assert len(measurements["cardBoxes"]) == member_count, measurements
    assert measurements["badgeBoxes"][3]["resource"] == "IRON", measurements
    assert measurements["badgeBoxes"][3]["top"] == pytest.approx(measurements["rowBoxes"][0]["top"], abs=1), measurements


@pytest.mark.parametrize("width", [412, 360])
def test_phone_layout_keeps_paired_tiles_and_no_overflow(width: int) -> None:
    measurements = _render_measurements(
        (STATIC / "ceres.css").read_text(encoding="utf-8"),
        width,
        915,
        member_count=2,
        token_count=7,
    )
    assert measurements["resourceNames"] == ["Grain", "Wood", "Stone", "Iron", "Aura", "SPS", "Research"], measurements
    assert len(measurements["badgeBoxes"]) == 7, measurements
    assert measurements["emptySlotsVisible"] == 0, measurements
    assert measurements["slotsOverflow"] <= 1, measurements
    assert measurements["regionOverflow"] <= 1, measurements
    assert measurements["cardOverflow"] <= 1, measurements
    assert all(box["nameClientRects"] <= 1 for box in measurements["badgeBoxes"]), measurements
    assert all(box["numberScrollWidth"] <= box["numberClientWidth"] + 1 for box in measurements["badgeBoxes"]), measurements
    assert all(box["numberRight"] <= box["badgeRight"] + 1 for box in measurements["badgeBoxes"]), measurements


@pytest.mark.parametrize("width", [412, 600, 900])
@pytest.mark.parametrize("token_count", [4, 5, 6, 7])
def test_receipt_tile_rows_fill_the_grid_without_empty_tracks(width: int, token_count: int) -> None:
    measurements = _render_measurements(
        (STATIC / "ceres.css").read_text(encoding="utf-8"),
        width,
        915,
        member_count=1,
        token_count=token_count,
    )
    assert len(measurements["badgeBoxes"]) == token_count, measurements
    assert measurements["emptySlotsVisible"] == 0, measurements
    assert all(box["left"] >= measurements["slotLeft"] - 1 and box["right"] <= measurements["slotRight"] + 1 for box in measurements["badgeBoxes"]), measurements
    assert all(box["clientRects"] == 1 for box in measurements["badgeBoxes"]), measurements
    assert measurements["regionLineWidth"] == pytest.approx(measurements["regionWidth"], abs=1), measurements
    assert len(measurements["rowBoxes"]) >= 1, measurements
    for row in measurements["rowBoxes"]:
        assert all(gap == pytest.approx(measurements["slotGap"], abs=1) for gap in row["gaps"]), measurements


@pytest.mark.parametrize("width,dpr", [(1280, 1), (1440, 1), (1536, 1.25), (1920, 1)])
@pytest.mark.parametrize("member_count,token_count", [(1, 4), (2, 7), (3, 5), (4, 7)])
def test_desktop_receipt_is_the_harvest_all_ledger(width: int, dpr: float, member_count: int, token_count: int) -> None:
    """ (; "Why was the alignment removed?"):
 on desktop the receipt is Harvest all's own ledger -- its grid, no old receipt cards -- and every
 resource sits in one column through the Total and every region row, with nothing overflowing."""
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    with standalone_browser() as browser:
        page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=dpr)
        page.set_content(_modal_page_html(_region_receipt_html(member_count, token_count, width), css, width, 900))
        result = page.evaluate("""() => {
 const columns = {};
 for (const badge of document.querySelectorAll('.hm-batch-grid .hm-batch-badge:not(.is-empty)')) {
 const box = badge.getBoundingClientRect();
 if (!box.width) continue;
 (columns[badge.dataset.resource] ||= []).push(box.left);
 }
 const spread = Object.fromEntries(Object.entries(columns).map(([k, xs]) => [k, Math.max(...xs) - Math.min(...xs)]));
 return { grid: !!document.querySelector('.hm-batch-grid'), oldCards: document.querySelectorAll('.harvest-receipt').length,
 regions: document.querySelectorAll('.hm-batch-region').length, spread, resources: Object.keys(columns),
 overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth };
 }""")
        page.close()
    assert result["grid"] and result["oldCards"] == 0, result
    assert result["regions"] == member_count, result
    assert len(result["resources"]) == token_count, result
    assert all(spread <= 1 for spread in result["spread"].values()), result
    assert result["overflow"] <= 0, result
