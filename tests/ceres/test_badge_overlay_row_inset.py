""" (maintainer, verbatim): "make the badge slide-outs slightly smaller in
height, es the glow currently leaks into the next row" -- his screenshot: open
`.badge-overlay-row` slide-outs filled the full `<tr>` band (36px), so the
shared Iris-strip glow (`box-shadow: 0 0 18px -6px var(--primary)`) bled into
the neighbouring row. Each open row is now 3px-inset top/bottom (6px shorter,
centred on the same `<tr>` band) with a tightened glow (`0 0 8px -4px`) that
stays inside that inset. The bar's own per-row click target
(`.badge-bar-row`) is UNTOUCHED -- it stays full <tr> height. See
library/ceres-badge-overlay.md.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
BADGE_OVERLAY_JS = STATIC / "badge-overlay.js"
CERES_CSS = STATIC / "ceres.css"


def test_mounted_read_phase_computes_a_separate_inset_pair_for_the_overlay_row():
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"export function syncBadgeOverlay\(reason\) \{.*?\n\}\n", js, re.S)
    assert fn_match, "syncBadgeOverlay source shape changed -- update this test's slice"
    body = fn_match.group(0)
    # The bar row keeps its own 1px inset while remaining the full-height click target.
    assert "top: `${trRect.top - scrollerRect.top + 1}px`" in body
    assert "height: `${Math.max(0, trRect.height - 2)}px`" in body
    # The overlay row gets its OWN, separately-computed inset pair.
    assert "overlayTop: `${trRect.top - scrollerRect.top + 3}px`" in body
    assert "overlayHeight: `${Math.max(0, trRect.height - 6)}px`" in body


def test_write_phase_applies_the_inset_pair_only_to_the_overlay_row():
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "for (const { deedUid, top, height, overlayTop, overlayHeight, row } of mounted) {" in js
    # Bar row: still the plain, uninset values.
    assert "if (barRow.style.top !== top) barRow.style.top = top;" in js
    assert "if (barRow.style.height !== height) barRow.style.height = height;" in js
    # Overlay row: the new inset values, not the plain ones.
    assert "if (overlayRow.style.top !== overlayTop) overlayRow.style.top = overlayTop;" in js
    assert "if (overlayRow.style.height !== overlayHeight) overlayRow.style.height = overlayHeight;" in js
    assert "overlayRow.style.top !== top)" not in js
    assert "overlayRow.style.top = top;" not in js


def test_badge_overlay_row_glow_tightened_to_stay_inside_the_inset():
    css = CERES_CSS.read_text(encoding="utf-8")
    rule = css.split(".badge-overlay-row {", 1)[1].split("\n}\n", 1)[0]
    assert "box-shadow:" not in rule


def test_badge_bar_row_geometry_untouched():
    """The bar's own per-row click target keeps the shared Iris-strip glow and
 the plain, full-<tr>-height sizing -- only touched the OPEN
 slide-out (`.badge-overlay-row`), not the always-visible bar row."""
    css = CERES_CSS.read_text(encoding="utf-8")
    assert ".badge-bar-row {" in css
    bar_row_rule = css.split(".badge-bar-row {", 1)[1].split("\n}\n", 1)[0]
    assert "box-shadow: 0 0 8px -4px var(--primary);" not in bar_row_rule


def test_node_check_touched_js_file():
    result = subprocess.run(["node", "--check", str(BADGE_OVERLAY_JS)], capture_output=True, text=True)
    assert result.returncode == 0, f"{BADGE_OVERLAY_JS}: {result.stderr}"
