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
HEAD_BUTTON_LEFT = 115.59375
HEAD_FOOTER_HEIGHT = 30.921875
HEAD_BAND_HEIGHT = 282.0
WCAG_AA_MIN = 4.5
BADGE_BG_ALPHA = 92


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


def _expanded_with_button(page: Page) -> Page:
    rows = page.locator("#plot-tbody > tr.plot-row")
    for index in range(rows.count()):
        _expand(page, index)
        expanded = page.locator("tr.expand-row").nth(index)
        if expanded.locator(".worksite-change-btn").count():
            return expanded
    raise AssertionError("live board has no expanded worksite-change plot")


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
    `${worksiteChange}${basePpRow}` order made it green. HEAD measured 115.59375px."""
    page = _page(live_browser)
    expanded = _expanded_with_button(page)
    if os.environ.get("B3_INJECTION") == "t2_before_button":
        expanded.locator(".slot-editor").evaluate(
            "root => root.querySelector('.slot-editor-footer').insertBefore(root.querySelector('.editor-base-pp-row'), root.querySelector('.worksite-change-btn'))"
        )
    left = expanded.locator(".worksite-change-btn").bounding_box()["x"]
    assert left == pytest.approx(HEAD_BUTTON_LEFT, abs=1)
    page.close()


def test_t3_footer_and_band_heights_match_head(live_browser: tuple[Browser, str]) -> None:
    """Injection: adding a second rendered line with `.editor-base-pp-cell::after` grew
    the footer by 9.27px and turned this red; removing the stacked-line injection
    restored green. HEAD measured footer 30.921875px and band 282px."""
    page = _page(live_browser)
    expanded = _expanded_with_button(page)
    if os.environ.get("B3_INJECTION") == "t3_stacked":
        page.add_style_tag(content=".editor-base-pp-cell.is-filled { display: flex !important; flex-direction: column !important; } .editor-base-pp-cell.is-filled::after { content: 'Base PP'; display: block !important; }")
    metrics = expanded.locator(".slot-editor").evaluate(
        "root => ({footer: root.querySelector('.slot-editor-footer').getBoundingClientRect().height, band: root.querySelector('.editor-band').getBoundingClientRect().height})"
    )
    assert metrics["footer"] == pytest.approx(HEAD_FOOTER_HEIGHT, abs=1)
    assert metrics["band"] == pytest.approx(HEAD_BAND_HEIGHT, abs=1)
    page.close()


def test_t4_figures_use_served_worker_runi_and_item_values(live_browser: tuple[Browser, str]) -> None:
    """Injection: deleting power/item descriptors removed `1,500`, `+50%`, and `+10%`
    and turned this red; restoring the shared descriptor array made it green. The named
    fixture observed worker strings `32,941` twice, Runi `1,500`, Totem `+50%`, Title
    `+10%`, and the clipped worker's approved cap-shortfall sentence in its tooltip.
    The re-recorded P3 injection remains `B3_INJECTION=t4_descriptors`; it still turns
    this assertion red without changing the clipped-cell assertion itself."""
    page = _page(live_browser)
    _, expanded = _fixture_expanded(page)
    row = expanded.locator(".editor-base-pp-row")
    if os.environ.get("B3_INJECTION") == "t4_descriptors":
        page.evaluate("""root => {
          const row = root.querySelector('.editor-base-pp-row');
          [...row.children].filter(node => node.matches('.editor-base-pp-cell')).slice(6).forEach(node => node.remove());
        }""", expanded.locator(".slot-editor").element_handle())
    text = row.inner_text()
    assert text.count("32,941") == 2
    assert "1,500" in text
    assert "+50%" in text
    assert "+10%" in text
    assert row.locator(".editor-base-pp-cell small").count() == 0
    assert "This card's Base PP after the plot's cap absorbs a shortfall here" in row.locator(".editor-base-pp-cell").nth(4).get_attribute("data-tip-body")
    page.close()


def test_t5_figure_text_contrast_clears_wcag_in_all_themes(live_browser: tuple[Browser, str]) -> None:
    """Injection: changing filled-cell text to `var(--meta)` failed alchemical and wood
    at 2.97:1 and 3.16:1 and turned this red; restoring `var(--heading)` made it green.
    This guard hides the glyph, screenshots the actual composited pixels behind its box,
    and asserts the chosen themed `--bg3` mix against `--heading` in all four themes.
    It samples one worksite's art; other artwork is not covered. The measured minimum
    contrast table on the real busy worksite art is recorded by the guarded run below.
    """
    assert f"var(--bg3) {BADGE_BG_ALPHA}%, transparent" in CSS.read_text(encoding="utf-8")
    page = _page(live_browser)
    _, expanded = _fixture_expanded(page)
    if os.environ.get("B3_INJECTION") == "t5_meta":
        page.add_style_tag(content=".editor-base-pp-cell.is-filled b { color: var(--meta) !important; }")
    scene = expanded.locator(".editor-scene")
    scene_background = scene.evaluate("node => getComputedStyle(node, '::before').backgroundImage")
    assert "url(" in scene_background
    page.evaluate("""() => {
      const style = document.createElement('style');
      style.id = 'b3-alpha-probe';
      document.head.append(style);
    }""")
    measured = {}
    for alpha in (70, 60, 50, 80, BADGE_BG_ALPHA):
        page.evaluate("""alpha => {
          document.querySelector('#b3-alpha-probe').textContent =
            `.editor-base-pp-cell.is-filled { background: color-mix(in srgb, var(--bg3) ${alpha}%, transparent) !important; }`;
        }""", alpha)
        measured[alpha] = {}
        for theme in ("alchemical", "dark", "light", "wood"):
            page.evaluate("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
            cells = expanded.locator(".editor-base-pp-cell.is-filled")
            assert cells.count()
            samples = [_composited_contrast(cells.nth(index), cells.nth(index).locator("b").evaluate("node => getComputedStyle(node).color")) for index in range(cells.count())]
            assert max(sample["unique_pixels"] for sample in samples) >= 4, (alpha, theme, samples)
            assert max(sample["luminance_span"] for sample in samples) >= 0.01, (alpha, theme, samples)
            measured[alpha][theme] = min(sample["min"] for sample in samples)
    print("T-5 composited contrast table: " + json.dumps(measured, sort_keys=True))
    for theme in ("alchemical", "dark", "light", "wood"):
        assert measured[BADGE_BG_ALPHA][theme] >= WCAG_AA_MIN, (theme, measured)
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
    edit made it green. This is the CI-adjacent source-byte guard required by T-11."""
    source = CSS.read_text(encoding="utf-8")
    expected = {
        ".editor-facts": ".editor-facts { display: flex; align-items: baseline; flex-wrap: wrap; gap: 0.2rem 0.9rem; margin-bottom: 0.3rem; min-height: 1.15rem; font-size: 0.69rem; color: var(--hi); text-shadow: 0 1px 2px rgba(0, 0, 0, 0.85); }",
        ".slot-editor-footer": ".slot-editor-footer { position: relative; display: flex; align-items: baseline; flex-wrap: wrap; gap: 0.35rem 1rem; margin-top: 0.1rem; padding-top: 0.1rem; border-top: 1px solid var(--border); font-size: 0.8rem; min-height: 1.7625rem; }",
        ".worksite-change-btn": ".worksite-change-btn { flex: 0 0 var(--worksite-btn-w); display: inline-flex; align-items: center; justify-content: center; box-sizing: border-box; height: 1.7625rem; margin: 0 0 0 calc((var(--worksite-w) - var(--worksite-btn-w)) / 2); padding: 0 0.55rem; border: 1px solid var(--accent); border-radius: 999px; background: color-mix(in srgb, var(--bg2) 88%, var(--accent) 12%); color: var(--heading); font: inherit; font-size: 0.68rem; font-weight: 700; line-height: 1; white-space: nowrap; cursor: pointer; }",
    }
    if os.environ.get("B3_INJECTION") == "t11_frozen":
        source = source.replace(expected[".editor-facts"], expected[".editor-facts"][:-2] + " color: red; }")
    for selector, rule in expected.items():
        assert _css_rule(source, selector) == rule
