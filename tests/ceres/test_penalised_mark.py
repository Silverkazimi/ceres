""" browser guards for the penalised Base PP mark."""

from __future__ import annotations

import re
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright

STATIC = Path(__file__).resolve().parents[2] / "ceres" / "static"
CSS = (STATIC / "ceres.css").read_text(encoding="utf-8")
THEMES = ("alchemical", "dark", "light", "wood")
PLATES = {
    "alchemical": "321f1c",
    "dark": "3f273b",
    "light": "c4aec1",
    "wood": "c6a88a",
}
FIGURES = {
    "alchemical": "d48989",
    "dark": "eb8fa9",
    "light": "722d52",
    "wood": "6d2210",
}
PLATE_SELECTOR = (
    ".editor-base-pp-cell.is-filled.is-cap-clipped, "
    ".editor-base-pp-cell.is-filled.is-terrain-penalised"
)
FIGURE_SELECTOR = (
    ".slot-editor-footer .editor-base-pp-cell.is-cap-clipped b, "
    ".slot-editor-footer .editor-base-pp-cell.is-terrain-penalised b"
)
POLARITY_SELECTOR = (
    '[data-theme="light"] .slot-editor-footer .editor-base-pp-cell.is-cap-clipped b, '
    '[data-theme="light"] .slot-editor-footer .editor-base-pp-cell.is-terrain-penalised b, '
    '[data-theme="wood"] .slot-editor-footer .editor-base-pp-cell.is-cap-clipped b, '
    '[data-theme="wood"] .slot-editor-footer .editor-base-pp-cell.is-terrain-penalised b'
)
GLYPH_SELECTOR = (
    ".slot-editor-footer .editor-base-pp-cell.is-cap-clipped b::before, "
    ".slot-editor-footer .editor-base-pp-cell.is-terrain-penalised b::before"
)


@pytest.fixture(scope="module")
def paint_browser():
    with sync_playwright() as playwright:
        with closing(playwright.chromium.launch()) as browser:
            yield browser


def _page(browser, theme="alchemical"):
    page = browser.new_page(viewport={"width": 1920, "height": 1080})

    def route_local(route):
        path = urlparse(route.request.url).path
        if path == "/":
            route.fulfill(
                content_type="text/html",
                body=(
                    '<!doctype html><html style="font-size:16px"><head>'
                    '<link rel="stylesheet" href="/static/ceres.css"></head><body>'
                    '<footer class="slot-editor-footer"><span class="editor-base-pp-row">'
                    '<span id="plain" class="editor-base-pp-cell is-filled"><b>100,000</b></span>'
                    '<span id="terrain" class="editor-base-pp-cell is-filled is-terrain-penalised"><b>100,000</b></span>'
                    '<span id="cap" class="editor-base-pp-cell is-filled is-cap-clipped"><b>100,000</b></span>'
                    "</span></footer></body></html>"
                ),
            )
            return
        asset = (STATIC / path.removeprefix("/static/")).resolve()
        if not path.startswith("/static/") or not asset.is_relative_to(STATIC) or not asset.is_file():
            route.fulfill(status=404, body="missing fixture asset")
            return
        route.fulfill(path=str(asset))

    page.route("**/*", route_local)
    page.goto("http://ir2900.test/")
    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    page.wait_for_function(
        "() => [...document.styleSheets].some(sheet => sheet.href?.endsWith('/static/ceres.css'))"
    )
    return page


def _brace_depth(source):
    depth = 0
    lowest = 0
    state = "code"
    index = 0
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "comment":
            if char == "*" and following == "/":
                state = "code"
                index += 2
                continue
        elif state in ("single", "double"):
            if char == "\\":
                index += 2
                continue
            if char == ("'" if state == "single" else '"'):
                state = "code"
        elif char == "/" and following == "*":
            state = "comment"
            index += 2
            continue
        elif char == "'":
            state = "single"
        elif char == '"':
            state = "double"
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            lowest = min(lowest, depth)
        index += 1
    return depth, lowest


def _rgba(value):
    rgb = re.fullmatch(r"rgba?\((.+)\)", value)
    if rgb:
        fields = re.split(r"\s*(?:,|/)\s*|\s+", rgb.group(1).strip())
        channels = [float(field.removesuffix("%")) for field in fields if field]
        if any("%" in field for field in fields[:3]):
            channels[:3] = [channel * 2.55 for channel in channels[:3]]
        alpha = channels[3] if len(channels) == 4 else 1.0
        if len(fields) == 4 and "%" in fields[3]:
            alpha /= 100
        return (*(channel / 255 for channel in channels[:3]), alpha)
    srgb = re.fullmatch(
        r"color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*/\s*([\d.]+))?\)",
        value,
    )
    assert srgb, f"unsupported computed colour: {value}"
    return tuple(float(channel) for channel in srgb.groups(default="1"))


def _rgb8(value):
    return "".join(f"{round(channel * 255):02x}" for channel in _rgba(value)[:3])


def _luminance(value):
    channels = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in _rgba(value)[:3]]
    return sum(channel * weight for channel, weight in zip(channels, (0.2126, 0.7152, 0.0722)))


def _contrast(left, right):
    light, dark = sorted((_luminance(left), _luminance(right)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _paint(page):
    return page.evaluate(
        """() => {
          const values = id => {
            const cell = document.querySelector(id), figure = cell.querySelector('b');
            const plate = getComputedStyle(cell), text = getComputedStyle(figure);
            const glyph = getComputedStyle(figure, '::before');
            return {background: plate.backgroundColor, border: plate.borderTopColor,
              figure: text.color, glyph: glyph.color, content: glyph.content};
          };
          return {plain: values('#plain'), terrain: values('#terrain'), cap: values('#cap'),
            terrainMatches: document.querySelector('#terrain').matches('.editor-base-pp-cell.is-filled.is-terrain-penalised'),
            capMatches: document.querySelector('#cap').matches('.editor-base-pp-cell.is-filled.is-cap-clipped')};
        }"""
    )


def test_g1_penalty_rules_are_balanced_top_level_cssom_rules_that_match(paint_browser):
    """RED MUTATION: move the shared plate rule's closing brace after the figure/glyph rules,
    reproducing v0.9.14's displaced brace. The source remains brace-balanced, but one or more
    required rules becomes nested instead of top-level, so this structural guard turns red."""
    assert _brace_depth(CSS) == (0, 0), "ceres.css braces must balance without closing below zero"
    with closing(_page(paint_browser)) as page:
        top_level = page.evaluate(
            """() => {
              const sheet = [...document.styleSheets].find(item => item.href?.endsWith('/static/ceres.css'));
              return [...sheet.cssRules].filter(rule => rule.selectorText).map(rule => rule.selectorText);
            }"""
        )
        required = {PLATE_SELECTOR, FIGURE_SELECTOR, POLARITY_SELECTOR, GLYPH_SELECTOR}
        assert required.issubset(top_level), "every penalty treatment selector must be a top-level CSSOM rule"
        assert page.locator(PLATE_SELECTOR).count() == 2, "the top-level rule must match both penalised states"


@pytest.mark.parametrize("theme", THEMES)
def test_g2_penalty_plate_applies_opaquely_and_both_states_match(paint_browser, theme):
    """RED MUTATIONS: move the closing brace as in v0.9.14 and computed styles collapse to
    the plain state; restore `14%, transparent` and plate alpha drops below one. Either
    executable paint change turns this computed-style guard red in the affected theme."""
    with closing(_page(paint_browser, theme)) as page:
        paint = _paint(page)
    assert paint["terrainMatches"] and paint["capMatches"]
    assert paint["terrain"] == paint["cap"], "cap and terrain penalties must share one rendered treatment"
    for property_name in ("background", "border", "figure"):
        assert paint["terrain"][property_name] != paint["plain"][property_name], property_name
    assert _rgba(paint["terrain"]["background"])[3] == pytest.approx(1.0)
    assert _rgb8(paint["terrain"]["background"]) == PLATES[theme]
    assert _rgb8(paint["terrain"]["figure"]) == FIGURES[theme]
    assert paint["terrain"]["glyph"] == paint["terrain"]["figure"]


@pytest.mark.parametrize("theme", THEMES)
def test_g3_computed_contrast_clears_text_border_and_glyph_floors(paint_browser, theme):
    """RED MUTATION: set the figure back to raw `var(--danger)`. Its computed contrast over
    the opaque plate falls to 3.15 in alchemical, below the 4.5 normal-text floor, so this
    guard turns red rather than accepting a declaration or comment that merely claims AA."""
    with closing(_page(paint_browser, theme)) as page:
        penalised = _paint(page)["terrain"]
    assert _contrast(penalised["figure"], penalised["background"]) >= 4.5
    assert _contrast(penalised["border"], penalised["background"]) >= 3.0
    assert _contrast(penalised["glyph"], penalised["background"]) >= 3.0


@pytest.mark.parametrize("theme", THEMES)
def test_g4_penalty_has_a_non_colour_glyph(paint_browser, theme):
    """RED MUTATION: delete the `b::before` rule. Computed content becomes `none`, removing
    the shape channel, and this browser assertion turns red in every theme."""
    with closing(_page(paint_browser, theme)) as page:
        content = _paint(page)["terrain"]["content"]
    assert content not in ("none", "normal", '""', "''")


def test_g5_worst_case_figure_and_glyph_fit_at_1920(paint_browser):
    """RED MUTATION: enlarge the glyph to 1em and the figure to 1.5rem. The `100,000`
    fixture plus prefix then exceeds its pinned cell and makes the row's scroll width larger
    than its client width, turning this 1920px ceiling guard red."""
    with closing(_page(paint_browser)) as page:
        dimensions = page.evaluate(
            """() => {
              const row = document.querySelector('.editor-base-pp-row');
              document.querySelector('#plain').remove();
              document.querySelector('#cap').remove();
              row.style.width = '6.7rem';
              const cell = document.querySelector('#terrain');
              return {viewport: [innerWidth, innerHeight], row: [row.scrollWidth, row.clientWidth],
                cell: [cell.scrollWidth, cell.clientWidth]};
            }"""
        )
    assert dimensions["viewport"] == [1920, 1080]
    assert dimensions["row"][0] <= dimensions["row"][1], dimensions
    assert dimensions["cell"][0] <= dimensions["cell"][1], dimensions


# horizontal splinter row matches the Fill plot button's box

_SPLINTER_ROW_STATES = ("favoured", "unchanged", "unknown", "unknown", "penalised", "favoured")


def _splinter_row_page(browser):
    page = browser.new_page(viewport={"width": 1920, "height": 1080})

    def route_local(route):
        path = urlparse(route.request.url).path
        if path == "/":
            badges = "".join(
                f'<span class="terrain-affinity-badge" data-splinter="fire" data-affinity-state="{state}">'
                f'<img class="splinter-icon" src="/static/assets/splinter/fire.svg" alt="" aria-hidden="true"></span>'
                for state in _SPLINTER_ROW_STATES
            )
            route.fulfill(
                content_type="text/html",
                body=(
                    '<!doctype html><html style="font-size:16px"><head>'
                    '<link rel="stylesheet" href="/static/ceres.css">'
                    '<link rel="stylesheet" href="/static/buttons.css"></head><body>'
                    '<div class="editor-band"><div class="slot-editor-body">'
                    '<div class="slot-group-controls">'
                    '<span class="editor-splinter-affinity"><span class="terrain-affinity-scroll">'
                    '<span class="terrain-affinity-viewport"><span class="terrain-affinity" '
                    f'data-terrain-affinity="picker">{badges}</span></span></span></span>'
                    '<span class="editor-splinter-affinity-sep"></span>'
                    '<button type="button" id="fillplot" class="slot-bulk fill-action ceres-btn '
                    'ceres-btn--secondary" data-control-density="compact" data-slot-action="fill_plot">'
                    "<span>Fill plot</span></button>"
                    "</div></div></div></body></html>"
                ),
            )
            return
        asset = (STATIC / path.removeprefix("/static/")).resolve()
        if not path.startswith("/static/") or not asset.is_relative_to(STATIC) or not asset.is_file():
            route.fulfill(status=404, body="missing fixture asset")
            return
        route.fulfill(path=str(asset))

    page.route("**/*", route_local)
    page.goto("http://ir6870.test/")
    page.wait_for_function(
        "() => [...document.styleSheets].some(sheet => sheet.href?.endsWith('/static/ceres.css'))"
    )
    return page


def test_splinter_row_matches_fill_plot_button_geometry(paint_browser):
    """RED MUTATION: restore the retired `--eos-affinity-size`-scaled badge sizing (1.5rem)
    or the old `align-self: center` on a shrunk container, and this fails -- the badge row
    must be the same height as the Fill plot button beside it, vertically centred on it
    within 0.5px, with its first badge flush against (not bleeding past) the controls box."""
    with closing(_splinter_row_page(paint_browser)) as page:
        geometry = page.evaluate(
            """() => {
              const controls = document.querySelector('.slot-group-controls');
              const row = document.querySelector('.editor-splinter-affinity');
              const firstBadge = document.querySelector('.terrain-affinity-badge');
              const sep = document.querySelector('.editor-splinter-affinity-sep');
              const button = document.getElementById('fillplot');
              const rect = (el) => el.getBoundingClientRect();
              const cRect = rect(controls), rRect = rect(row), fRect = rect(firstBadge),
                sRect = rect(sep), bRect = rect(button);
              const centre = (r) => r.top + r.height / 2;
              return {
                controlsLeft: cRect.left, firstBadgeLeft: fRect.left,
                rowHeight: rRect.height, buttonHeight: bRect.height,
                rowCentre: centre(rRect), sepCentre: centre(sRect), buttonCentre: centre(bRect),
              };
            }"""
        )
    assert geometry["firstBadgeLeft"] >= geometry["controlsLeft"] - 0.5, geometry
    assert abs(geometry["rowHeight"] - geometry["buttonHeight"]) <= 1, geometry
    assert abs(geometry["rowCentre"] - geometry["buttonCentre"]) <= 0.5, geometry
    assert abs(geometry["sepCentre"] - geometry["buttonCentre"]) <= 0.5, geometry
