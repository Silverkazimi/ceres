"""Sync Playwright guards for R-160 Ariadne's border.

This file must be executed apart from the asyncio.run() reveal guards required by
. It owns the focused-tile ring contract and its deliberate red mutations.
"""

from __future__ import annotations

import copy

import pytest

from .conftest import settle, spawn_live_ceres
from .test_candidate_redesign_frontend import _real_detail_row
from .test_live_r117_terrain_display import _candidates, _mount


@pytest.fixture(scope="module")
def live_browser():
    with spawn_live_ceres() as live:
        yield live


@pytest.fixture()
def picker_page(live_browser):
    browser, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    # The offline harness (conftest._offline_browser) defaults every page to reduced motion
    # which the product honours by removing the moving arc. This contract is about the
    # animated ring, so the page asks for motion explicitly; the reduced-motion test below
    # switches it back on its own.
    page = browser.new_page(viewport={"width": 1920, "height": 1080}, reduced_motion="no-preference")
    _mount(page, base_url, row, _candidates())
    # The mounted band re-renders while its data and art land; a tile clicked one frame too
    # early is replaced and the click is lost. Retry until a tile carries the focus class
    # (the same pattern the real-account checks use) rather than one 30 s wait.
    opened = False
    for _attempt in range(8):
        target = page.locator(".slot-group[data-group-id='worker'] .slot-tile[data-slot-action='replace']")
        if not target.count():
            target = page.locator(".slot-group[data-group-id='worker'] .slot-tile[data-slot-action='add']")
        if not target.count():
            settle(page)
            continue
        try:
            target.first.click(timeout=5_000)
            page.locator(".slot-tile.is-picker-focus").first.wait_for(timeout=6_000)
            page.locator(".inline-picker").wait_for(timeout=15_000)
            opened = True
            break
        except Exception:
            settle(page)
    assert opened, "picker never opened on a worker tile after 8 attempts (fixture, not product)"
    yield page
    page.close()


def _ring_state(page):
    return page.evaluate(
        """() => {
          const tile = document.querySelector('.slot-tile.is-picker-focus');
          const svg = tile?.querySelector(':scope > .picker-focus-ring');
          const arc = svg?.querySelector('.picker-focus-ring-arc');
          const rail = svg?.querySelector('.picker-focus-ring-rail');
          const remove = tile?.querySelector('.slot-remove');
          const rect = element => {
            const box = element.getBoundingClientRect();
            return {left: box.left, top: box.top, right: box.right, bottom: box.bottom, width: box.width, height: box.height};
          };
          const svgRect = svg && rect(svg);
          const tileRect = tile && rect(tile);
          const animation = arc?.getAnimations()[0];
          return {
            focused: document.querySelectorAll('.slot-tile.is-picker-focus').length,
            rings: document.querySelectorAll('.slot-tile .picker-focus-ring').length,
            pathLength: arc?.getAttribute('pathLength'),
            dashArray: arc?.getAttribute('stroke-dasharray'),
            duration: arc && getComputedStyle(arc).animationDuration,
            name: arc && getComputedStyle(arc).animationName,
            timing: arc && getComputedStyle(arc).animationTimingFunction,
            iteration: arc && getComputedStyle(arc).animationIterationCount,
            playState: arc && getComputedStyle(arc).animationPlayState,
            arcStroke: arc && getComputedStyle(arc).stroke,
            railStroke: rail && getComputedStyle(rail).stroke,
            ringZ: svg && getComputedStyle(svg).zIndex,
            removeZ: remove && getComputedStyle(remove).zIndex,
            svgRect, tileRect,
            animationTime: animation?.currentTime,
            pathLengthPixels: arc?.getTotalLength(),
            scrollWidth: document.documentElement.scrollWidth,
          };
        }"""
    )


def _assert_ring_contract(page):
    state = _ring_state(page)
    assert state["focused"] == 1, "one focused tile"
    assert state["rings"] == 1, "one SVG belongs to the focused tile only"
    assert state["pathLength"] == "100", "normalized path length"
    assert state["dashArray"] == "30 70", "30/70 bright arc coverage"
    assert state["duration"] == "2.6s", "2600ms lap (: 30% slower than 2.0 s)"
    assert state["name"] == "picker-ariadne-loop", "active Ariadne animation"
    assert state["timing"] == "linear", "linear lap"
    assert state["iteration"] == "infinite", "indefinite lap"
    assert state["playState"] == "running", "animation is active"
    assert state["arcStroke"] != state["railStroke"], "bright arc differs from dim rail"
    assert int(state["ringZ"]) < int(state["removeZ"]), "arc layer is below remove control"
    for edge in ("left", "top"):
        assert state["svgRect"][edge] >= state["tileRect"][edge] - 0.1, "SVG stays inside tile"
    for edge in ("right", "bottom"):
        assert state["svgRect"][edge] <= state["tileRect"][edge] + 0.1, "SVG stays inside tile"
    assert state["scrollWidth"] <= 1920, "Ariadne adds no horizontal document overflow"


def _assert_head_travel(page):
    # Sample the arc's animation clock against the page clock over at least 100 ms in the page
    # itself: the old check assumed settle() lasted 100 ms, which it does not on an idle page.
    travel = page.evaluate(
        """async () => {
          const arc = document.querySelector('.slot-tile.is-picker-focus > .picker-focus-ring .picker-focus-ring-arc');
          const animation = arc.getAnimations()[0];
          const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
          await frame();
          const wall0 = performance.now(), anim0 = animation.currentTime;
          while (performance.now() - wall0 < 100) await frame();
          return {wall: performance.now() - wall0, anim: animation.currentTime - anim0, length: arc.getTotalLength(),
                  lap: Number(animation.effect.getComputedTiming().duration)};
        }"""
    )
    assert travel["anim"] > 0, "arc head is moving"
    # (3): at 2.6 s per lap the head covers ~18.6 px per 100 ms on the product tile.
    pixels_per_100ms = travel["length"] / travel["lap"] * travel["anim"] / travel["wall"] * 100
    assert pixels_per_100ms >= 18, "arc head travels at least 18 CSS px in 100ms"


def test_ariadne_ring_contract(picker_page):
    _assert_ring_contract(picker_page)
    _assert_head_travel(picker_page)


def test_ariadne_reduced_motion_settles(picker_page):
    picker_page.emulate_media(reduced_motion="reduce")
    state = _ring_state(picker_page)
    assert state["name"] == "none", "reduced motion has no moving arc"
    assert picker_page.locator(".picker-focus-ring-arc").evaluate("element => getComputedStyle(element).display") == "none"
    rail = picker_page.locator(".picker-focus-ring-rail").evaluate("element => getComputedStyle(element).stroke")
    assert rail, "reduced motion keeps a solid rail"
    picker_page.emulate_media(reduced_motion="no-preference")
    picker_page.evaluate("() => document.documentElement.dataset.eosMotion = 'disabled'")
    assert picker_page.locator(".picker-focus-ring-arc").evaluate("element => getComputedStyle(element).display") == "none"
    picker_page.evaluate("() => delete document.documentElement.dataset.eosMotion")


def test_ariadne_forced_colours_settles(picker_page):
    picker_page.emulate_media(forced_colors="active")
    assert picker_page.locator(".picker-focus-ring").evaluate("element => getComputedStyle(element).display") == "none"
    outline = picker_page.locator(".slot-tile.is-picker-focus").evaluate("element => getComputedStyle(element).outlineWidth")
    assert outline == "3px", "forced colours use a 3px Highlight outline"


MUTATIONS = {
    "DUPLICATE_RING": """() => { const tile = document.querySelector('.slot-tile.is-picker-focus'); tile.append(tile.querySelector('.picker-focus-ring').cloneNode(true)); }""",
    "WRONG_PATH_LENGTH": """() => document.querySelector('.picker-focus-ring-arc').setAttribute('pathLength', '99')""",
    "WRONG_DASH_ARRAY": """() => document.querySelector('.picker-focus-ring-arc').setAttribute('stroke-dasharray', '50 50')""",
    "SLOW_ANIMATION": """() => document.querySelector('.picker-focus-ring-arc').style.animationDuration = '4000ms'""",
    "MATCHING_COLOUR": """() => { const rail = document.querySelector('.picker-focus-ring-rail'); document.querySelector('.picker-focus-ring-arc').style.stroke = getComputedStyle(rail).stroke; }""",
    "OUTSIDE_TILE": """() => { const svg = document.querySelector('.picker-focus-ring'); svg.style.inset = '-8px'; svg.style.width = 'calc(100% + 16px)'; svg.style.height = 'calc(100% + 16px)'; }""",
    "REMOVE_ABOVE_RING": """() => document.querySelector('.slot-remove').style.zIndex = '3'""",
    "DOCUMENT_OVERFLOW": """() => document.body.style.minWidth = '2001px'""",
    # Aletheia ( review): the four animation-property assertions had no mutation of their own.
    "WRONG_ANIMATION_NAME": """() => document.querySelector('.picker-focus-ring-arc').style.animationName = 'picker-ariadne-other'""",
    "EASED_ANIMATION": """() => document.querySelector('.picker-focus-ring-arc').style.animationTimingFunction = 'ease'""",
    "FINITE_ANIMATION": """() => document.querySelector('.picker-focus-ring-arc').style.animationIterationCount = '1'""",
    "PAUSED_ANIMATION": """() => document.querySelector('.picker-focus-ring-arc').style.animationPlayState = 'paused'""",
}


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_ariadne_contract_rejects_red_mutations(picker_page, mutation):
    _assert_ring_contract(picker_page)
    picker_page.evaluate(MUTATIONS[mutation])
    with pytest.raises(AssertionError):
        _assert_ring_contract(picker_page)
