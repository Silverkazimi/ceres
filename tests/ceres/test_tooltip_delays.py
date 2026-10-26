"""Rendered regression guards for tooltip open and close timing."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres, wait_for_board


VIEWPORT = {"width": 1920, "height": 1080}
TOOLTIP_JS = Path(__file__).resolve().parents[2] / "ceres" / "static" / "tooltip.js"
# A mouse tooltip opens once the pointer has rested on its trigger this long (the product's
# own constant, read from tooltip.js rather than restated here).
TOOLTIP_INTENT_MS = int(re.search(r"const TOOLTIP_INTENT_MS = (\d+);", TOOLTIP_JS.read_text(encoding="utf-8")).group(1))

# Armed on the trigger before the pointer arrives. When the pointer enters it, this records
# whether the tooltip is already up one animation frame later (it must not be: the intent
# delay) and once TOOLTIP_INTENT_MS has passed. The second check is a timer started in the
# same mouseover dispatch as tooltip.js's own open timer, registered after it with the same
# delay, so it runs right after that timer: an open that waits any longer than the intent
# delay is still hidden at that moment, however busy the machine is.
_ARM_INTENT_PROBE = """(trigger, intent) => {
  const visible = () => Boolean(document.querySelector('#ceres-tooltip.is-visible:not([hidden])'));
  window.__tipIntent = new Promise(resolve => {
    const onOver = event => {
      if (event.target.closest?.('[data-tip]') !== trigger) return;
      document.removeEventListener('mouseover', onOver);
      const result = {};
      requestAnimationFrame(() => { result.inFirstFrame = visible(); });
      setTimeout(() => { result.atIntent = visible(); resolve(result); }, intent);
    };
    document.addEventListener('mouseover', onOver);
  });
}"""


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def _quiet_spot(page) -> tuple[float, float]:
    """A point with no tooltip trigger under it, where the pointer can wait between hovers."""
    x, y = VIEWPORT["width"] - 5, VIEWPORT["height"] - 5
    assert page.evaluate("([x, y]) => !document.elementFromPoint(x, y)?.closest('[data-tip]')", [x, y])
    return x, y


def _pointer_onto(page, target) -> None:
    """Bring a real pointer to rest on `target`, the way a user's pointer arrives.

    After any scroll, tooltip.js opens nothing until the pointer has really moved more
    than SCROLL_QUIET_PX (maintainer, 2026-09-29), and the board's own load-time scrolls
    (its filter selects) count as one; Playwright's `hover()` is a single synthetic move
    that only arms that check. Two moves on a quiet spot end that state first, so the
    tooltip opens from the trigger's own mouseover."""
    box = target.bounding_box()
    assert box, "hover target has no layout box"
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    quiet_x, quiet_y = _quiet_spot(page)
    page.mouse.move(quiet_x - 20, quiet_y - 20)
    page.mouse.move(quiet_x, quiet_y)
    page.wait_for_function("() => !document.querySelector('#ceres-tooltip.is-visible')", timeout=5_000)
    page.mouse.move(x, y, steps=6)


def _hover_timing(page, target) -> dict:
    target.evaluate(_ARM_INTENT_PROBE, TOOLTIP_INTENT_MS)
    _pointer_onto(page, target)
    return page.evaluate("() => window.__tipIntent")


def test_first_hover_shows_tooltip_within_one_animation_frame(live_browser: tuple[Browser, str]) -> None:
    """A freshly loaded page must not pay a first-hover delay.

    The maintainer has since asked for one deliberate intent delay on every tooltip
    ("a tooltip animation with a small delay ... to avoid tons of tooltips popping up when
    moving the cursor over the board"; tooltip.js TOOLTIP_INTENT_MS), so no tooltip can be
    visible within one frame of the pointer arriving any more. What must still hold is
    that the FIRST hover on a fresh page pays nothing on top of that shared delay: it is
    up as soon as TOOLTIP_INTENT_MS has passed, exactly like a later hover.

    Injection: restoring a first-hover-only delay (the old 140ms warm-up, on top of the
    intent delay) leaves the first tooltip hidden when the intent delay has passed and
    turns this rendered assertion red; dropping the intent delay shows it in the first
    frame and turns it red as well.
    """
    browser, base_url = live_browser
    page = browser.new_page(viewport=VIEWPORT)
    page.goto(base_url)
    wait_for_board(page)
    target = page.locator("[data-tip]").first
    target.wait_for(timeout=10_000)
    first = _hover_timing(page, target)
    second = _hover_timing(page, target)
    assert first == {"inFirstFrame": False, "atIntent": True}, f"first hover: {first}"
    assert second == {"inFirstFrame": False, "atIntent": True}, f"later hover: {second}"
    page.close()


def test_pointer_can_cross_from_trigger_to_tooltip_body(live_browser: tuple[Browser, str]) -> None:
    """Moving the pointer from a trigger onto its tooltip never traps the pointer.

    The tooltip used to stay open so the pointer could rest on its body. The maintainer
    then made desktop tooltips pass-through and close at once when the pointer leaves the
    element that raised them (ceres.css: `#ceres-tooltip { pointer-events: none; }`
    except `.is-touch`; tooltip.js scheduleHideTooltip() "closing is instant"). The
    crossing is still exercised: with the pointer on the tooltip's own box, the element
    under it is the board, not the tooltip, and the tooltip has closed.

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
