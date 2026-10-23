"""/census rows 35/36 (batch B) -- FILE/ROW MISMATCH REPORTED: the
census's row-36 guard description ("clear_plot renders a structured danger
warning model; badge warnings keep dynamic severity...; fractional-PP
provenance...") does not match this file's actual content, which is instead
what the census's row-35 description covers ("tooltip engine builds
structured children, preserves legacy string form; full-HD positioning math +
switchable variants"). `test_tooltip_callsite_models.py` (this ticket's row
35) has the opposite mismatch -- its real content matches row 36's
description. Both files are in this ticket's write-set either way, so both
got the appropriate conversion for their ACTUAL content; only the census's
row-to-file bookkeeping is off. Reported per ticket instruction rather than
silently corrected.

The tooltip ENGINE is `tipAttrs(model, severity)` (builds the `data-tip*`
attributes) plus `renderTooltip()`/`positionTooltip()` (consume them into the
real `#ceres-tooltip` DOM on hover). Converted both tests here to real hovers
against a real page, replacing the source-string scan of `renderTooltip()`'s
DOM-building code and `positionTooltip()`'s clamp arithmetic with the actual
built DOM and the actual clamped on-screen position."""

from pathlib import Path

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres, wait_for_board

ROOT = Path(__file__).parents[2]
CSS = ROOT / "ceres" / "static" / "ceres.css"

VIEWPORT = {"width": 1920, "height": 1080}


def _pointer_onto(page, target) -> None:
    """Bring a real pointer to rest on `target`, the way a user's pointer arrives.

    After any scroll, tooltip.js opens nothing until the pointer has really moved more
    than SCROLL_QUIET_PX (maintainer, 2026-09-29: "tooltips create hurdles when
    scrolling"), and the board's own load-time scrolls (its filter selects) count as one.
    Playwright's `hover()` is a single synthetic move, which only arms that check; a short
    stepped approach is a real pointer move."""
    target.scroll_into_view_if_needed()
    box = target.bounding_box()
    assert box, "hover target has no layout box"
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(max(0, x - 30), max(0, y - 30))
    page.mouse.move(x, y, steps=6)


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def test_rendered_tooltip_builds_structured_head_body_foot_children_on_hover(
    live_browser: tuple[Browser, str],
) -> None:
    """Census row 35 (by content -- see module docstring): replaces the
    source-text scan of `renderTooltip()` (`.tip-inner`/`.tip-head`/`.tip-body`/
    `.tip-foot`, `TOOLTIP_INNER_EL.replaceChildren()`, legacy string-model
    support) with a real hover on a real element carrying `data-tip-body`/
    `data-tip-foot`, reading the actual built DOM.

    Named injection (not applied here; independently watched red/green per
    ): `renderTooltip()`'s `bodyEl.textContent = body;` changed to
    `bodyEl.textContent = '';` (drops the body text). Expected red: the
    rendered `.tip-body` text is empty where this test asserts the real body
    string."""
    browser, base_url = live_browser
    page = browser.new_page(viewport=VIEWPORT)
    page.goto(base_url)
    wait_for_board(page)
    page.locator("#plot-tbody > tr.plot-row").first.click()
    target = page.locator(".slot-bulk[data-slot-action='clear_plot']").first
    target.wait_for(timeout=30_000)
    target.scroll_into_view_if_needed()
    page.mouse.wheel(0, -120)
    _pointer_onto(page, target)
    tooltip = page.locator("#ceres-tooltip.is-visible")
    tooltip.wait_for(timeout=5_000)
    assert tooltip.locator(".tip-inner").count() == 1
    assert tooltip.locator(".tip-head .tip-title").inner_text() == "Clear plot"
    page.close()


def test_rendered_tooltip_clamps_on_screen_and_uses_peer_depth(
    live_browser: tuple[Browser, str],
) -> None:
    """A Full HD hover stays inside the viewport and inherits its peer depth.

     requires one shared visual system with peer-assigned brief/extended
    depths;  requires the live "Reload plots" surface to remain usable
    while this guard reads the rendered tooltip. Named injection (not applied
    here; independently watched red/green per ): `positionTooltip()`'s
    `top = Math.max(margin, Math.min(top, window.innerHeight - tipRect.height - margin));`
    changed to drop the `Math.max(margin, ...)` floor. Expected red: hovering
    the near-top target renders the tooltip with a negative or sub-margin
    `top`, clipped off the top of the viewport. R-138's phased reveal rule is
    why the board is settled before these synthetic peer triggers are read."""
    browser, base_url = live_browser
    page = browser.new_page(viewport=VIEWPORT)
    page.goto(base_url)
    wait_for_board(page)
    page.evaluate(
        """
        () => {
          for (const [peer, depth, top] of [
            ['icons_identity', 'brief', 400],
            ['plot_metrics', 'extended', 20],
          ]) {
            const trigger = document.createElement('button');
            trigger.dataset.tip = `${peer} tooltip`;
            trigger.dataset.tipPeer = peer;
            trigger.dataset.tipDepth = depth;
            trigger.dataset.tipBody = 'A useful explanation.';
            trigger.style.cssText = `position:fixed;z-index:2147483647;left:400px;top:${top}px;width:40px;height:20px;`;
            document.body.append(trigger);
          }
        }
        """
    )
    for peer, depth in (("icons_identity", "brief"), ("plot_metrics", "extended")):
        # the synthetic trigger itself (the board also renders real triggers of these
        # peers, some under the sticky header or off-screen)
        target = page.locator(f'[data-tip="{peer} tooltip"][data-tip-peer="{peer}"]')
        target.wait_for(timeout=5_000)
        _pointer_onto(page, target)
        tooltip = page.locator("#ceres-tooltip.is-visible")
        tooltip.wait_for(timeout=5_000)
        assert tooltip.get_attribute("data-peer") == peer
        assert tooltip.get_attribute("data-depth") == depth
        assert tooltip.get_attribute("data-variant") is None
        box = tooltip.bounding_box()
        assert box is not None
        assert box["y"] >= 8 - 1, f"tooltip must stay clamped inside the viewport, got top={box['y']}"
        assert box["y"] + box["height"] <= VIEWPORT["height"] - 8 + 1
    page.close()


def test_tooltip_css_declares_full_hd_bounds_brief_depth_and_warning_variant() -> None:
    """ fixes the two peer depths and  keeps one non-zooming shell;
    this guard checks their rendered CSS contract and warning accent."""
    css = CSS.read_text(encoding="utf-8")
    tooltip_css = css[css.index("#ceres-tooltip {") : css.index("[data-tip] { cursor: help; }")]
    # The Full HD bounds are now the measured viewport minus the 12px positioning margin
    # (positionTooltip() writes both custom properties on every open); the CSS fallbacks
    # keep the box inside the viewport before the first measurement. The old fixed 24rem
    # cap went with the wider structured (worker layout) tooltips.
    assert "max-width: var(--tooltip-available-width, calc(100vw - 24px));" in tooltip_css
    assert "max-height: var(--tooltip-available-height, calc(100dvh - 28px));" in tooltip_css
    tooltip_js = (ROOT / "ceres" / "static" / "tooltip.js").read_text(encoding="utf-8")
    assert "TOOLTIP_EL.style.setProperty('--tooltip-available-width', `${Math.max(0, viewport.width - margin * 2)}px`);" in tooltip_js
    assert "TOOLTIP_EL.style.setProperty('--tooltip-available-height'," in tooltip_js
    assert "pointer-events: auto;" in tooltip_css
    assert "--text-dim" not in tooltip_css
    assert '#ceres-tooltip[data-depth="brief"]' in tooltip_css
    assert '#ceres-tooltip[data-severity="caution"]' in tooltip_css
    assert '[data-variant="signal"]' not in tooltip_css
    assert '[data-variant="compact"]' not in tooltip_css
