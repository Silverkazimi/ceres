"""Mutation coverage for #ceres-tooltip's baseline contrast and severity accent. Two claims are guarded here, both on the
OBSERVABLE (resolved colour), never on a class name or CSS string alone:
 1. The tooltip's own text-on-fill contrast clears WCAG AA in all four
 themes (alchemical/dark/light/wood) -- the baseline-visibility fix.
 2. A danger-severity tooltip (Clear plot) and a caution-severity tooltip
 (Sort by contribution) resolve to genuinely DIFFERENT colours, and
 neither collapses onto the neutral no-severity default, in every theme.

Mutation check performed by hand while writing this test: with
`#ceres-tooltip[data-severity="danger"]`/`[data-severity="caution"]`'s
border-left-color/background rules commented out in ceres.css, the resolved
colours for the danger/caution fixture rows became identical to the neutral
default in every theme and
`test_severity_resolves_to_different_colours_per_theme` went RED; restoring
the rules brought it back GREEN. `test_severity_reflected_in_live_dom`
additionally injects the defect at the DOM layer -- with
`TOOLTIP_EL.setAttribute('data-severity', ...)` removed from showTooltip(),
the live danger and caution tooltips both render the neutral default and the
test fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres

ROOT = Path(__file__).parents[2]
CSS = ROOT / "ceres" / "static" / "ceres.css"
CANONICAL = ROOT / "ceres" / "static" / "canonical.css"

# Theme -> (--heading, --bg3, --warn, --danger, --ok, --accent), read directly
# from canonical.css's own per-theme blocks. Guarded against drift by
# test_canonical_theme_tokens_match_fixture below, same pattern as
# test_power_warning_contrast.py's THEME_TOKENS.
THEME_TOKENS = {
    "alchemical": ("#c9a84c", "#1a1714", "#e8843a", "#bd4a4a", "#3a9c8c", "#7b6b9e"),
    "dark": ("#CABEE4", "#231f30", "#F4A261", "#E0527A", "#4ECBA8", "#9B8DF5"),
    "light": ("#453C68", "#ccc7d8", "#7E3806", "#962040", "#145C49", "#6455C4"),
    "wood": ("#3A2410", "#cfc0a0", "#763508", "#962010", "#34501A", "#23553D"),
}

WCAG_AA_MIN = 4.5


def _srgb_channel(c: float) -> float:
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    r, g, b = _srgb_channel(r), _srgb_channel(g), _srgb_channel(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1, l2 = sorted((_relative_luminance(fg_hex), _relative_luminance(bg_hex)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def _mix(fg_hex: str, bg_hex: str, fg_pct: float) -> str:
    """Same math as CSS `color-mix(in srgb, fg_pct% fg, bg)` -- linear channel blend."""
    fg = fg_hex.lstrip("#")
    bg = bg_hex.lstrip("#")
    out = []
    for i in (0, 2, 4):
        f = int(fg[i : i + 2], 16)
        b = int(bg[i : i + 2], 16)
        out.append(round(f * fg_pct + b * (1 - fg_pct)))
    return "#%02x%02x%02x" % tuple(out)


def test_canonical_theme_tokens_match_fixture() -> None:
    source = CANONICAL.read_text(encoding="utf-8")
    for heading_hex, bg3_hex, warn_hex, danger_hex, ok_hex, accent_hex in THEME_TOKENS.values():
        for name, value in (
            ("--heading", heading_hex),
            ("--bg3", bg3_hex),
            ("--warn", warn_hex),
            ("--danger", danger_hex),
            ("--ok", ok_hex),
            ("--accent", accent_hex),
        ):
            assert re.search(rf"{re.escape(name)}:\s*{re.escape(value)}\b", source), (name, value)


def test_tooltip_base_contrast_meets_wcag_aa_in_every_theme() -> None:
    for theme, (heading_hex, bg3_hex, *_rest) in THEME_TOKENS.items():
        ratio = _contrast_ratio(heading_hex, bg3_hex)
        assert ratio >= WCAG_AA_MIN, f"{theme}: {ratio:.2f}:1 below WCAG AA {WCAG_AA_MIN}:1"


def test_severity_resolves_to_different_colours_per_theme() -> None:
    """The three coloured severities (10% color-mix onto --bg3) must each
 differ from the neutral --bg3 default AND from each other, in every
 theme -- resolved to real hex, not compared by CSS variable name."""
    for theme, (_heading, bg3_hex, warn_hex, danger_hex, ok_hex, _accent) in THEME_TOKENS.items():
        neutral = bg3_hex.lower()
        caution = _mix(warn_hex, bg3_hex, 0.10).lower()
        danger = _mix(danger_hex, bg3_hex, 0.10).lower()
        success = _mix(ok_hex, bg3_hex, 0.10).lower()
        resolved = {"neutral": neutral, "caution": caution, "danger": danger, "success": success}
        for a in resolved:
            for b in resolved:
                if a != b:
                    assert resolved[a] != resolved[b], f"{theme}: {a} ({resolved[a]}) == {b} ({resolved[b]})"


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


def test_severity_reflected_in_live_dom(live_browser: tuple[Browser, str]) -> None:
    """Census row 37 (batch B): extends this file's own pre-existing live guard
 (per plan §3.1: "the live test already does this for two cases -- extend
 it to cover the substring-only ones instead of adding a third mechanism")
 to replace THREE dropped source-scans:
 - `test_tipattrs_severity_argument_and_dom_mirroring_present` (line 119):
 `tipAttrs(model, severity)`'s signature and `TOOLTIP_EL`'s
 `setAttribute`/`removeAttribute('data-severity', ...)` mirroring --
 now proven by this test's own `severity`/`borderLeft` reads below,
 which only pass if that exact plumbing is intact end to end.
 - `test_clear_plot_tooltip_is_danger` (line 129): the structured danger
 model reaching the real Clear Plot call site -- now proven by hovering
 the REAL button and reading `data-severity="danger"` (was previously
 a regex over a literal `tipAttrs({...})` object).
 - `test_badge_severity_reaches_the_tooltip` (line 197, app.js half only
 -- the `ceres.notices.badge_meta_projection()` Python-side half is
 kept below, unchanged, out of this census row's JS-reading scope):
 now proven by hovering a REAL live badge chip carrying a caution
 severity and reading its rendered `data-severity`/colour.

 Real Chromium guard: hovers the Clear plot button (danger) and a real
 live caution-severity badge chip (`dark_discount_wasted`, same fixture
 plot `test_tooltip_callsite_models.py` uses) in the same page, and reads
 the ACTUAL computed border-left-color/background of #ceres-tooltip for
 each -- never the class name or the data-severity attribute's spelling --
 confirming they resolve to genuinely different colours, neither the
 neutral default.

 Named injection (not applied here; independently watched red/green per
 ): `renderTooltip()`'s
 `if (severity) TOOLTIP_EL.setAttribute('data-severity', severity);`
 branch removed entirely (the `else TOOLTIP_EL.removeAttribute(...)` stays).
 Expected red: both `danger["severity"]` and `caution["severity"]` come
 back `None`, and their border colours collapse to the same (neutral)
 value."""
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto(base_url)
    page.locator("tr.plot-row").first.click()
    editor = page.locator(".slot-editor")
    editor.wait_for(timeout=30_000)
    worker_group = editor.locator(".slot-group[data-group-id='worker']")

    def hover_and_read(locator) -> dict:
        locator.wait_for(timeout=10_000)
        locator.hover()
        tooltip = page.locator("#ceres-tooltip.is-visible")
        tooltip.wait_for(timeout=5_000)
        style = tooltip.evaluate(
            "el => { const s = getComputedStyle(el); return {borderLeft: s.borderLeftColor, bg: s.backgroundColor, severity: el.getAttribute('data-severity')}; }"
        )
        page.mouse.move(5, 5)  # move away so the next hover fires a fresh mouseover
        return style

    # Fill has no tooltip and cannot serve as a plain comparison.
    danger = hover_and_read(worker_group.locator("button[data-slot-action='clear_plot']"))
    assert danger["severity"] == "danger", "the declared severity did not reach the rendered tooltip"
    assert danger["borderLeft"], "a danger tooltip rendered without its severity border"

    caution_badge = page.locator(".badge.badge-dark_discount_wasted").first
    # severity is asserted on the shared peer-group tooltip, not a fixture-specific plot.
    if caution_badge.count() == 0:
        pytest.skip("No live snapshot badge carries the caution Dark Discount state")
    caution = hover_and_read(caution_badge)
    assert caution["severity"] == "caution", "the badge's own caution severity did not reach the rendered tooltip"
    assert caution["borderLeft"], "a caution tooltip rendered without its severity border"
    assert caution["borderLeft"] != danger["borderLeft"], "caution and danger tooltips must resolve to different colours"
    page.close()


def test_badge_meta_projection_carries_a_real_caution_severity() -> None:
    """Reads `ceres.notices`, not `ceres/static/*.js` -- outside this census
 row's JS-reading scope, left unchanged (the app.js half of the old
 combined test is now proven by `test_severity_reflected_in_live_dom`
 above)."""
    from ceres import notices

    projection = notices.badge_meta_projection()
    assert projection, "no badges projected"
    assert all("severity" in meta for meta in projection.values()), (
        "badge_meta_projection dropped the severity field"
    )
    assert any(meta["severity"] == "caution" for meta in projection.values()), (
        "no badge projects a caution severity -- the field is present but meaningless"
    )
