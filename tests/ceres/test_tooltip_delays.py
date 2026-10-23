"""Rendered regression guards for tooltip open and close timing."""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres, wait_for_board


VIEWPORT = {"width": 1920, "height": 1080}

# Resolves once the shared tooltip is visible and reports how long that took, in page time,
# from the moment the promise was armed (right before the pointer arrives).
_ARM_OPEN_TIMER = """() => {
  const started = performance.now();
  window.__tipOpened = new Promise(resolve => {
    const tip = () => document.querySelector('#ceres-tooltip.is-visible:not([hidden])');
    if (tip()) { resolve(0); return; }
    const observer = new MutationObserver(() => {
      if (tip()) { observer.disconnect(); resolve(performance.now() - started); }
    });
    observer.observe(document.body, {subtree: true, attributes: true, attributeFilter: ['class', 'hidden']});
  });
}"""


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _pointer_onto(page, target) -> None:
    """Bring a real pointer to rest on `target`, the way a user's pointer arrives.

    After any scroll, tooltip.js opens nothing until the pointer has really moved more
    than SCROLL_QUIET_PX (maintainer, 2026-09-29), and the board's own load-time scrolls
    (its filter selects) count as one; Playwright's `hover()` is a single synthetic move
    that only arms that check."""
    box = target.bounding_box()
    assert box, "hover target has no layout box"
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x + 30, y + 30)
    page.mouse.move(x, y, steps=6)


def _open_latency_ms(page, target) -> float:
    page.mouse.move(VIEWPORT["width"] - 5, VIEWPORT["height"] - 5)
    page.wait_for_function("() => !document.querySelector('#ceres-tooltip.is-visible')", timeout=5_000)
    page.evaluate(_ARM_OPEN_TIMER)
    _pointer_onto(page, target)
    return page.evaluate("() => window.__tipOpened")


def test_first_hover_shows_tooltip_within_one_animation_frame(live_browser: tuple[Browser, str]) -> None:
    """A freshly loaded page must not pay a first-hover delay.

    The maintainer has since asked for one deliberate intent delay on every tooltip
    ("a tooltip animation with a small delay ... to avoid tons of tooltips popping up when
    moving the cursor over the board"; tooltip.js TOOLTIP_INTENT_MS), so no tooltip can be
    visible within one frame any more. What must still hold is that the FIRST hover on a
    fresh page pays nothing on top of that shared delay: it opens as fast as a later hover
    of the same trigger.

    Injection: restoring a first-hover-only delay (the old 140ms warm-up) makes the first
    latency exceed the second by that delay and turns this rendered assertion red.
    """
    browser, base_url = live_browser
    page = browser.new_page(viewport=VIEWPORT)
    page.goto(base_url)
    wait_for_board(page)
    target = page.locator("[data-tip]").first
    target.wait_for(timeout=10_000)
    first = _open_latency_ms(page, target)
    second = _open_latency_ms(page, target)
    assert first <= second + 100, f"first hover took {first:.0f} ms, a later hover {second:.0f} ms"
    page.close()


def test_pointer_can_cross_from_trigger_to_tooltip_body(live_browser: tuple[Browser, str]) -> None:
    """Moving the pointer from a trigger onto its tooltip never traps the pointer.

    The tooltip used to stay open so the pointer could rest on its body. The maintainer
    then made desktop tooltips pass-through and close at once when the pointer leaves the
    element that raised them (tooltip.js: `#ceres-tooltip { pointer-events: none; }`
    except `.is-touch`, and scheduleHideTooltip() "closing is instant"). The crossing is
    still exercised: with the pointer on the tooltip's own box, the element under it is the
    board, not the tooltip, and the tooltip has closed.

    Injection: giving the mouse tooltip `pointer-events: auto` again makes the
    elementFromPoint check land inside `#ceres-tooltip` and turns this red.
    """
    browser, base_url = live_browser
    page = browser.new_page(viewport=VIEWPORT)
    page.goto(base_url)
    wait_for_board(page)
    target = page.locator("#plot-tbody [data-tip]").first
    target.wait_for(timeout=10_000)
    target.scroll_into_view_if_needed()
    _pointer_onto(page, target)
    tooltip = page.locator("#ceres-tooltip.is-visible")
    tooltip.wait_for(timeout=5_000)
    box = tooltip.bounding_box()
    assert box is not None
    assert tooltip.evaluate("el => getComputedStyle(el).pointerEvents") == "none"
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y, steps=4)
    assert page.evaluate(
        "([x, y]) => !document.getElementById('ceres-tooltip').contains(document.elementFromPoint(x, y))", [x, y]
    )
    page.wait_for_function("() => !document.querySelector('#ceres-tooltip.is-visible')", timeout=5_000)
    page.close()
