from __future__ import annotations

import re
from pathlib import Path


CSS = Path(__file__).parents[2] / "ceres" / "static" / "ceres.css"
CANONICAL = Path(__file__).parents[2] / "ceres" / "static" / "canonical.css"

# Theme -> (--heading, --bg3) hex values, read directly from canonical.css's own
# per-theme blocks (mirrors the four Iris themes the board actually ships).
THEME_TOKENS = {
    "alchemical": ("#c9a84c", "#1a1714"),
    "dark": ("#CABEE4", "#231f30"),
    "light": ("#453C68", "#ccc7d8"),
    "wood": ("#3A2410", "#cfc0a0"),
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


def test_canonical_theme_tokens_match_fixture() -> None:
    # Guards the THEME_TOKENS fixture above against the tokens drifting in
    # canonical.css without this test noticing.
    source = CANONICAL.read_text(encoding="utf-8")
    for heading_hex, bg3_hex in THEME_TOKENS.values():
        assert re.search(rf"--heading:\s*{re.escape(heading_hex)}\b", source), heading_hex
        assert re.search(rf"--bg3:\s*{re.escape(bg3_hex)}\b", source), bg3_hex


def test_plot_warning_row_uses_bg3_heading_pairing() -> None:
    # `color: var(--warn)` alone measured only 1.84:1 on light and 3.6:1 on wood -- unreadable
    # for a message that warns a move zeroes a plot's power and PP. The plot warning list
    # (, which replaced `.slot-power-warning`) keeps the solid `--bg3` background
    # `--heading` foreground pairing; severity colour is only a border.
    source = CSS.read_text(encoding="utf-8")
    match = re.search(r"\n\.plot-warning-row\s*\{([^}]*)\}", source)
    assert match is not None, ".plot-warning-row rule not found"
    rule_body = match.group(1)
    assert "background: var(--bg3)" in rule_body
    assert "color: var(--heading)" in rule_body
    assert "color: var(--warn)" not in rule_body


def test_plot_warning_contrast_meets_wcag_aa_in_every_theme() -> None:
    ratios = {}
    for theme, (heading_hex, bg3_hex) in THEME_TOKENS.items():
        ratios[theme] = _contrast_ratio(heading_hex, bg3_hex)
    for theme, ratio in ratios.items():
        assert ratio >= WCAG_AA_MIN, f"{theme}: {ratio:.2f}:1 below WCAG AA {WCAG_AA_MIN}:1"
