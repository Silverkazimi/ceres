"""Ceres B3 Phase 1 browser guards for the per-card Base PP ghost grid.

The mutation observations in the test docstrings are part of the  record.
"""

from __future__ import annotations

import json
import io
import os
import re
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import Browser, Page

from .conftest import spawn_live_ceres


ROOT = Path(__file__).parents[2]
CSS = ROOT / "ceres" / "static" / "ceres.css"
FIXTURE = ROOT / "tests" / "fixtures" / "land" / "assets_active_with_runi.json"
WCAG_AA_MIN = 4.5
# The filled Base PP cell's plates (ceres.css): --bg3 tinted 15% by the cell's state colour.
FILLED_PLATES = (
    "color-mix(in srgb, var(--primary) 15%, var(--bg3))",
    "color-mix(in srgb, var(--ok) 15%, var(--bg3))",
    "color-mix(in srgb, var(--danger) 15%, var(--bg3))",
)


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _page(live_browser: tuple[Browser, str]) -> Page:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(base_url)
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=30_000)
    return page


def _expand(page: Page, index: int) -> None:
    page.locator("#plot-tbody > tr.plot-row").nth(index).click()
    page.locator("tr.expand-row").nth(index).locator(".slot-editor").wait_for(timeout=30_000)
    # The band mounts "preparing" and draws its tiles and Base PP row once the plot's detail
    # is in (ready, or degraded when art is missing).
    page.locator("tr.expand-row").nth(index).locator(
        ".editor-band:is([data-plot-state='ready'], [data-plot-state='degraded'])"
    ).wait_for(timeout=30_000)


def _fixture_expanded(page: Page) -> tuple[int, Page]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cards = fixture["data"]["cards"]
    worker_cards = [card for card in cards if card["slot"] > 0]
    runi_card = next(card for card in cards if card["slot"] == 0)
    items = fixture["data"]["items"]
    index = 0
    deed_uid = page.locator("#plot-tbody > tr.plot-row").nth(index).get_attribute("data-deed-uid")
    assert deed_uid

    def detail_route(route) -> None:
        path = route.request.url.split("?", 1)[0]
        if not (path.endswith(f"/api/plots/{deed_uid}") or path.endswith(f"/api/plots/{deed_uid}/expand")):
            route.continue_()
            return
        response = route.fetch()
        payload = response.json()
        payload["assets_loaded"] = True
        payload["workers"] = [
            {
                "asset_ref": card["uid"], "slot": card["slot"], "name": card["name"], "card_name": card["name"],
                "card_detail_id": card["card_detail_id"], "editions": "2", "edition": 2, "card_set": "chaos",
                "foil": card["foil"], "gold": card["gold"], "level": 1, "base_pp": float(card["land_base_pp"]),
                "base_pp_after_cap": float(card["base_pp_after_cap"]), "construction_pp": float(card["total_construction_pp"]),
                "harvest_pp": float(card["total_harvest_pp"]), "terrain_boost": float(card["terrain_boost"]),
                "land_abilities": [], "badges": [], "sole_holder_for": [],
            }
            for card in worker_cards
        ] + [{
            "asset_ref": runi_card["uid"], "slot": 0, "name": "Runi", "card_name": "Runi", "editions": "2", "edition": 2,
            "base_pp": float(runi_card["land_base_pp"]), "base_pp_after_cap": float(runi_card["base_pp_after_cap"]),
            "construction_pp": float(runi_card["total_construction_pp"]), "harvest_pp": float(runi_card["total_harvest_pp"]),
            "terrain_boost": 0, "land_abilities": [], "badges": [], "sole_holder_for": [],
        }]
        payload["capacity"] = {**(payload.get("capacity") or {}), "potential_card_capacity": 5, "runi_staked": True}
        payload["is_runi_staked"] = True
        payload["staking_facts"] = {**(payload.get("staking_facts") or {}), "is_runi_staked": True}
        payload["bonus"] = {
            "power_core": None, "runi": True,
            "totem": {"name": items[0]["name"], "boost": float(items[0]["boost"]), "asset_ref": items[0]["uid"]},
            "title": {"name": items[1]["name"], "boost": float(items[1]["boost"]), "asset_ref": items[1]["uid"]},
        }
        payload["multiplier_parts"] = {"totem_boost": 0.5, "title_boost": 0.1, "runi_boost": 1.0, "deed_rarity_boost": 0, "deed_status_token_boost": 0}
        route.fulfill(response=response, body=json.dumps(payload))

    page.route("**/api/plots/**", detail_route)
    _expand(page, index)
    return index, page.locator("tr.expand-row").nth(index)


# The band's change-worksite control: the harvest bar's cog (editor.js passes
# `cog: !!worksiteChange` to harvestBarHtml), which opens the worksite modal. The separate
# `.worksite-change-btn` beside the Base PP row is no longer drawn.
WORKSITE_CHANGE = ".slot-editor-footer .harvest-bar-cog[data-slot-action='open-worksite-modal']"


def _expanded_with_button(page: Page) -> Page:
    rows = page.locator("#plot-tbody > tr.plot-row")
    for index in range(rows.count()):
        _expand(page, index)
        expanded = page.locator("tr.expand-row").nth(index)
        if expanded.locator(WORKSITE_CHANGE).count():
            return expanded
    raise AssertionError("live board has no expanded worksite-change plot")


_PROBE = """(root, expr) => {
  const node = document.createElement('div');
  node.style.cssText = `position:absolute;inline-size:0;block-size:${expr}`;
  root.querySelector('.editor-main').append(node);
  const height = node.getBoundingClientRect().height;
  node.remove();
  return height;
}"""


def _term(expanded: Page, expr: str) -> float:
    """One layout term of the band, measured alone by a zero-width probe sized by it."""
    return expanded.locator(".editor-band").evaluate(_PROBE, expr)


def _geometry(expanded: Page) -> dict:
    return expanded.locator(".slot-editor").evaluate(
        """root => {
          const grid = root.querySelector('.slot-grid');
          const row = root.querySelector('.editor-base-pp-row');
          const rects = node => [...node.children].map(child => {
            const rect = child.getBoundingClientRect();
            return {left: rect.left, right: rect.right, center: (rect.left + rect.right) / 2, width: rect.width};
          });
          return {
            gridScrollWidth: grid.scrollWidth,
            gridClientWidth: grid.clientWidth,
            rowBox: {left: row.getBoundingClientRect().left, width: row.getBoundingClientRect().width,
              scrollWidth: row.scrollWidth, clientWidth: row.clientWidth,
              flex: getComputedStyle(row).flex, minWidth: getComputedStyle(row).minWidth},
            grid: rects(grid),
            row: rects(row),
            firstGridLeft: grid.children[0].getBoundingClientRect().left,
            firstRowLeft: row.children[0].getBoundingClientRect().left,
          };
        }"""
    )


def _footer_measurements(expanded: Page) -> dict:
    return expanded.locator(".slot-editor").evaluate(
        """root => {
          const footer = root.querySelector('.slot-editor-footer');
          const band = root.querySelector('.editor-band');
          const footerRect = footer.getBoundingClientRect();
          const style = getComputedStyle(footer);
          const borderLeft = parseFloat(style.borderLeftWidth) || 0;
          const borderRight = parseFloat(style.borderRightWidth) || 0;
          const paddingLeft = parseFloat(style.paddingLeft) || 0;
          const paddingRight = parseFloat(style.paddingRight) || 0;
          const contentLeft = footerRect.left + borderLeft + paddingLeft;
          const contentWidth = footer.clientWidth - borderLeft - borderRight - paddingLeft - paddingRight;
          const children = [...footer.children].map(child => {
            const rect = child.getBoundingClientRect();
            const childStyle = getComputedStyle(child);
            return {node: child, top: rect.top, bottom: rect.bottom, height: rect.height,
              left: rect.left, width: rect.width, position: childStyle.position};
          }).filter(item => item.position !== 'absolute' && item.width > 0 && item.height > 0);
          const lineTops = [];
          for (const item of children.sort((a, b) => a.top - b.top)) {
            if (!lineTops.some(top => Math.abs(top - item.top) <= 0.5)) lineTops.push(item.top);
          }
          return {
            footer: footerRect.height,
            band: band.getBoundingClientRect().height,
            contentLeft,
            contentWidth,
            lineTops,
          };
        }"""
    )


def _rgb(value: str) -> tuple[float, float, float]:
    # A colour computed from color-mix() serializes as `color(srgb r g b)` (0..1 channels).
    srgb = re.fullmatch(r"color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*/\s*[\d.]+)?\s*\)", value)
    if srgb:
        return tuple(float(srgb.group(index)) for index in range(1, 4))
    match = re.fullmatch(r"rgba?\(\s*([\d.]+)[, ]+\s*([\d.]+)[, ]+\s*([\d.]+)\s*\)", value)
    assert match, value
    return tuple(float(match.group(index)) / 255 for index in range(1, 4))


def _luminance(rgb: tuple[float, float, float]) -> float:
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(value) for value in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(foreground: str, background: str) -> float:
    first, second = sorted((_luminance(_rgb(foreground)), _luminance(_rgb(background))), reverse=True)
    return (first + 0.05) / (second + 0.05)


def _composited_contrast(cell, foreground: str) -> dict:
    cell_box = cell.bounding_box()
    glyph = cell.locator("b")
    glyph_box = glyph.bounding_box()
    assert cell_box and glyph_box
    glyph.evaluate("node => { node.style.visibility = 'hidden'; }")
    try:
        image = Image.open(io.BytesIO(cell.screenshot())).convert("RGB")
    finally:
        glyph.evaluate("node => { node.style.visibility = ''; }")
    x0 = max(0, round(glyph_box["x"] - cell_box["x"]))
    y0 = max(0, round(glyph_box["y"] - cell_box["y"]))
    x1 = min(image.width, round(glyph_box["x"] - cell_box["x"] + glyph_box["width"]))
    y1 = min(image.height, round(glyph_box["y"] - cell_box["y"] + glyph_box["height"]))
    pixels = [
        tuple(channel / 255 for channel in image.getpixel((x, y)))
        for y in range(y0, y1)
        for x in range(x0, x1)
    ]
    assert pixels
    foreground_rgb = _rgb(foreground)
    values = []
    for pixel in pixels:
        high, low = sorted((_luminance(foreground_rgb), _luminance(pixel)), reverse=True)
        values.append((high + 0.05) / (low + 0.05))
    return {
        "min": min(values),
        "mean": sum(values) / len(values),
        "sample_count": len(values),
        "unique_pixels": len(set(pixels)),
        "luminance_span": max(_luminance(pixel) for pixel in pixels) - min(_luminance(pixel) for pixel in pixels),
    }


def test_t1_figure_cells_align_with_every_tile_column(live_browser: tuple[Browser, str]) -> None:
    """Injection: `.editor-base-pp-divider { margin: 0; }` made the divider itself miss by
    14.39px and later columns drift by 28.78px, turning this red; removing it restored green. The live
    precondition also observed `scrollWidth == clientWidth` at 1920px before comparing
    the eight shared columns."""
    page = _page(live_browser)
    _, expanded = _fixture_expanded(page)
    if os.environ.get("B3_INJECTION") == "t1_divider":
        page.add_style_tag(content=".editor-base-pp-divider { margin: 0 !important; }")
    geometry = _geometry(expanded)
    assert geometry["gridScrollWidth"] == geometry["gridClientWidth"]
    assert len(geometry["grid"]) == len(geometry["row"]) == 9
    for index, (grid, row) in enumerate(zip(geometry["grid"], geometry["row"])):
        assert abs(grid["center"] - row["center"]) <= 1, (index, grid, row)
    print(f"first figure left={geometry['firstRowLeft']:.2f}px; first tile left={geometry['firstGridLeft']:.2f}px")
    page.close()


def test_t2_change_worksite_button_stays_at_head_position(live_browser: tuple[Browser, str]) -> None:
    """Injection: moving `.editor-base-pp-row` before `.worksite-change-btn` moved the
    button right by the row width plus the footer gap and turned this red; restoring the
    `${worksiteChange}${basePpRow}` order made it green. HEAD measured 115.59375px.
    (The control became the harvest bar's cog; the injection now puts the row in front of
    the bar.)"""
    page = _page(live_browser)
    expanded = _expanded_with_button(page)
    if os.environ.get("B3_INJECTION") == "t2_before_button":
        # The bar is anchored (absolute), so reordering alone cannot move it; the injection
        # also puts it back into the footer's flow, where the row in front pushes it right.
        expanded.locator(".slot-editor").evaluate(
            "root => root.querySelector('.slot-editor-footer').insertBefore(root.querySelector('.editor-base-pp-row'), root.querySelector('.harvest-bar'))"
        )
        page.add_style_tag(content=".slot-editor-footer > .harvest-bar, .slot-editor-footer > .editor-base-pp-row { position: static !important; transform: none !important; }")
    # The pinned 115.59px was the old footer button's left edge. The control is the harvest
    # bar's cog now, and its head position is the bar's own anchor: the bar centred under the
    # worksite column (half --worksite-w into the footer, ceres.css), the cog its first control
    # inside the bar's border and padding. The anchor is measured by a probe placed by that
    # term alone in the same footer, so a Base PP row in front of it cannot move it.
    geometry = expanded.locator(".slot-editor-footer").evaluate(
        """footer => {
          const probe = document.createElement('div');
          probe.style.cssText = 'position:absolute;inline-size:0;block-size:0;left:calc(var(--worksite-w) / 2)';
          footer.append(probe);
          const anchor = probe.getBoundingClientRect().left;
          probe.remove();
          const bar = footer.querySelector('.harvest-bar');
          const style = getComputedStyle(bar);
          const barRect = bar.getBoundingClientRect();
          return {anchor, barCentre: (barRect.left + barRect.right) / 2,
            inner: barRect.left + parseFloat(style.borderLeftWidth) + parseFloat(style.paddingLeft),
            cog: footer.querySelector('.harvest-bar-cog').getBoundingClientRect().left,
            firstControl: bar.firstElementChild.classList.contains('harvest-bar-cog')};
        }"""
    )
    assert geometry["barCentre"] == pytest.approx(geometry["anchor"], abs=1), geometry
    assert geometry["firstControl"], geometry
    assert geometry["cog"] == pytest.approx(geometry["inner"], abs=1), geometry
    page.close()


def test_t3_footer_and_band_heights_match_head(live_browser: tuple[Browser, str]) -> None:
    """Injection: adding a second rendered line with `.editor-base-pp-cell::after` grew
    the footer by 9.27px and turned this red; removing the stacked-line injection
    restored green. HEAD measured footer 30.921875px and band 282px."""
    page = _page(live_browser)
    expanded = _expanded_with_button(page)
    if os.environ.get("B3_INJECTION") == "t3_stacked":
        page.add_style_tag(content=".editor-base-pp-cell.is-filled { display: flex !important; flex-direction: column !important; } .editor-base-pp-cell.is-filled::after { content: 'Base PP'; display: block !important; }")
    # The pinned 30.92px footer / 282px band were the retired fixed band. The desktop band is
    # the scene-scaled --eos-content-height and its footer the --eos-footer term (ceres.css), so
    # both expectations are those terms, each measured alone by a probe. The footer's box is
    # sized by its term, so a stacked second line cannot grow it; it would spill instead -- the
    # Base PP row and every figure must also stay one line inside the footer.
    expected_footer = _term(expanded, "var(--eos-footer)")
    expected_band = _term(expanded, "var(--eos-content-height)")
    metrics = expanded.locator(".slot-editor").evaluate(
        """root => {
          const footer = root.querySelector('.slot-editor-footer').getBoundingClientRect();
          const row = root.querySelector('.editor-base-pp-row').getBoundingClientRect();
          const cells = [...root.querySelectorAll('.editor-base-pp-cell.is-filled')].map(cell => {
            const glyph = cell.querySelector('b');
            const range = document.createRange();
            range.selectNodeContents(glyph);
            return {lines: range.getClientRects().length, children: [...cell.children].length,
              after: getComputedStyle(cell, '::after').content};
          });
          return {footer: footer.height, footerBottom: footer.bottom, rowBottom: row.bottom,
            band: root.querySelector('.editor-band').getBoundingClientRect().height, cells};
        }"""
    )
    assert metrics["footer"] == pytest.approx(expected_footer, abs=1)
    assert metrics["band"] == pytest.approx(expected_band, abs=1)
    assert metrics["rowBottom"] <= metrics["footerBottom"] + 0.5, metrics
    assert metrics["cells"] and all(
        cell["lines"] == 1 and cell["children"] == 1 and cell["after"] in ("none", "normal") for cell in metrics["cells"]
    ), metrics["cells"]
    page.close()


def test_t4_figures_use_served_worker_runi_and_item_values(live_browser: tuple[Browser, str]) -> None:
    """Injection: deleting power/item descriptors removed `1,500`, `+50%`, and `+10%`
    and turned this red; restoring the shared descriptor array made it green. The named
    fixture observed worker strings `32,941` twice, Runi `1,500`, Totem `+50%`, Title
    `+10%`, and the clipped worker's approved cap-shortfall sentence in its tooltip.
    The re-recorded P3 injection remains `B3_INJECTION=t4_descriptors`; it still turns
    this assertion red without changing the clipped-cell assertion itself. The cells now
    show the terrain-adjusted figure (`36,235` twice) and the tooltips the served Base PP."""
    page = _page(live_browser)
    _, expanded = _fixture_expanded(page)
    row = expanded.locator(".editor-base-pp-row")
    if os.environ.get("B3_INJECTION") == "t4_descriptors":
        page.evaluate("""root => {
          const row = root.querySelector('.editor-base-pp-row');
          [...row.children].filter(node => node.matches('.editor-base-pp-cell')).slice(6).forEach(node => node.remove());
        }""", expanded.locator(".slot-editor").element_handle())
    # A worker's cell shows its terrain-adjusted figure -- the served construction_pp
    # ("Boostable PP": Base PP after the cap, with the plot's terrain) -- and its tooltip
    # states the served Base PP and the terrain step. The fixture's two 32,941 Base PP
    # workers on +10% terrain show 36,235; the clipped one (2,200 Base PP, 1,617.648 after
    # the cap) shows 1,779 and its tooltip names what the cap takes (582).
    text = row.inner_text()
    assert text.count("36,235") == 2
    assert "1,500" in text
    assert "+50%" in text
    assert "+10%" in text
    assert row.locator(".editor-base-pp-cell small").count() == 0
    worker_cells = row.locator(".editor-base-pp-cell[data-group-id='worker']")
    assert worker_cells.nth(0).get_attribute("data-tip-body").startswith("32,941 Base PP · +10% terrain")
    clipped = worker_cells.nth(4)
    assert "is-cap-clipped" in (clipped.get_attribute("class") or "")
    assert clipped.inner_text().strip() == "1,779"
    assert "2,200 Base PP · +10% terrain · the plot's 100,000 cap takes 582" in clipped.get_attribute("data-tip-body")
    page.close()


def test_t5_figure_text_contrast_clears_wcag_in_all_themes(live_browser: tuple[Browser, str]) -> None:
    """Injection: changing filled-cell text to `var(--meta)` failed alchemical and wood
    at 2.97:1 and 3.16:1 and turned this red; restoring `var(--heading)` made it green.
    This guard hides the glyph, screenshots the actual composited pixels behind its box,
    and asserts the chosen themed `--bg3` mix against `--heading` in all four themes.
    It samples one worksite's art; other artwork is not covered. The measured minimum
    contrast table on the real busy worksite art is recorded by the guarded run below.

    The chosen background is no longer a translucent `--bg3` (92%) badge: a filled cell is an
    opaque plate, `--bg3` tinted 15% by its state colour (primary; ok for a terrain bonus;
    danger for a penalty or a cap clip). The plates are measured as rendered, with no
    override, and must clear AA in every theme. The retired sweep over translucent alphas
    (70/60/50/80/92) measured a design that no longer exists, and its pixel-variety checks
    proved that art showed through a translucent badge; an opaque plate shows none, so the
    guard asserts the plates are opaque instead.
    """
    source = CSS.read_text(encoding="utf-8")
    for plate in FILLED_PLATES:
        assert f"background: {plate};" in source, plate
    page = _page(live_browser)
    _, expanded = _fixture_expanded(page)
    if os.environ.get("B3_INJECTION") == "t5_meta":
        page.add_style_tag(content=".editor-base-pp-cell.is-filled b { color: var(--meta) !important; }")
    scene = expanded.locator(".editor-scene")
    scene_background = scene.evaluate("node => getComputedStyle(node, '::before').backgroundImage")
    assert "url(" in scene_background
    product = {}
    for theme in ("alchemical", "dark", "light", "wood"):
        page.evaluate("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
        cells = expanded.locator(".editor-base-pp-cell.is-filled")
        assert cells.count()
        alphas = cells.evaluate_all(
            """cells => cells.map(cell => {
              const probe = document.createElement('canvas').getContext('2d');
              probe.fillStyle = getComputedStyle(cell).backgroundColor;
              probe.fillRect(0, 0, 1, 1);
              return probe.getImageData(0, 0, 1, 1).data[3];
            })"""
        )
        assert alphas == [255] * len(alphas), (theme, alphas)
        samples = [_composited_contrast(cells.nth(index), cells.nth(index).locator("b").evaluate("node => getComputedStyle(node).color")) for index in range(cells.count())]
        product[theme] = min(sample["min"] for sample in samples)
    print("T-5 product plate contrast: " + json.dumps(product, sort_keys=True))
    for theme in ("alchemical", "dark", "light", "wood"):
        assert product[theme] >= WCAG_AA_MIN, (theme, product)
    page.close()


def test_t12_filled_cell_inner_b_is_transparent(live_browser: tuple[Browser, str]) -> None:
    """The filled cell owns the semi-transparent badge; its inner `<b>` is not a second
    chip. Injection removes the winning `.slot-editor-footer .editor-base-pp-cell b`
    selector and prepends the original plain `.editor-base-pp-cell b` rule. That equal-
    specificity rule loses to the later frozen `.slot-editor-footer b`, and this assertion
    turns red after observing the nested themed background, 3.2px horizontal padding, and
    themed radius; the higher-specificity selector restores transparent/0/0. """
    page = _page(live_browser)
    _, expanded = _fixture_expanded(page)
    if os.environ.get("B3_INJECTION") == "t12_plain_selector":
        page.evaluate("""() => {
          for (const sheet of document.styleSheets) {
            for (const rule of [...sheet.cssRules || []]) {
              if (rule.selectorText === '.slot-editor-footer .editor-base-pp-cell b') {
                sheet.deleteRule([...sheet.cssRules].indexOf(rule));
              }
            }
          }
          const style = document.createElement('style');
          style.textContent = '.editor-base-pp-cell b { padding: 0; border-radius: 0; background: none; }';
          document.head.prepend(style);
        }""")
    styles = expanded.locator(".editor-base-pp-cell.is-filled b").first.evaluate(
        "node => { const s = getComputedStyle(node); return {background: s.backgroundColor, padding: s.padding, radius: s.borderRadius}; }"
    )
    assert styles["background"] in ("rgba(0, 0, 0, 0)", "transparent"), styles
    assert styles["padding"] == "0px", styles
    assert styles["radius"] == "0px", styles
    page.close()


def _css_rule(source: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{[^}}]*\}}", source)
    assert match, selector
    return match.group(0)


def test_t11_frozen_css_rules_remain_byte_identical() -> None:
    """Injection: changing any frozen rule made the byte comparison red; reverting the
    edit made it green. This is the CI-adjacent source-byte guard required by T-11.

    The frozen texts were re-baselined on the current stylesheet after deliberate product
    changes that are not B3's: the footer lost its top border ("no border-top (maintainer)");
    the change-worksite button lost its own pill styling to the shared `.ceres-btn` system
    (editor.js keeps its markup only to decide the harvest bar's cog, which is the band's
    control now); and
    the plot-facts line is retired (editor.js renders no `.editor-facts`, and no rule styles
    it), so its place in the freeze goes to the line that took over its change badges, the
    plot controls line above the tiles (`.slot-group-controls`)."""
    source = CSS.read_text(encoding="utf-8")
    expected = {
        ".slot-group-controls": ".slot-group-controls { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-bottom: 0.35rem; position: sticky; left: 0; z-index: 5; background: transparent; border-radius: 0; pointer-events: none; }",
        ".slot-editor-footer": ".slot-editor-footer { position: relative; display: flex; align-items: baseline; flex-wrap: wrap; gap: 0.35rem 1rem; margin-top: 0.1rem; padding-top: 0.1rem; font-size: 0.8rem; min-height: 1.7625rem; }",
        ".worksite-change-btn": ".worksite-change-btn { flex: 0 0 var(--worksite-btn-w); display: inline-flex; align-items: center; justify-content: center; box-sizing: border-box; margin: 0 0 0 calc((var(--worksite-w) - var(--worksite-btn-w)) / 2); white-space: nowrap; }",
    }
    assert not re.search(r"(^|[\s,}])\.editor-facts\s*[{,]", source, re.MULTILINE), "the retired facts line is styled again"
    if os.environ.get("B3_INJECTION") == "t11_frozen":
        key = ".slot-group-controls"
        source = source.replace(expected[key], expected[key][:-2] + " color: red; }")
    for selector, rule in expected.items():
        assert _css_rule(source, selector) == rule
