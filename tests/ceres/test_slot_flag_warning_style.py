""" (partial) -- the sole-ability-holder flag (Section U+2691) and the not-ready
cooldown flag (hourglass) get a warning treatment and a border. Placement itself is unchanged
and correct -- this file covers only the colour/border change and its contrast.

Text-contrast method matches `test_power_warning_contrast.py`: both flags render on a
flat, opaque `--bg3` background (no image/gradient behind them), so a token-value WCAG
computation is exact, not an approximation.
"""

from __future__ import annotations

import re
from pathlib import Path

CSS = Path(__file__).parents[2] / "ceres" / "static" / "ceres.css"
CANONICAL = Path(__file__).parents[2] / "ceres" / "static" / "canonical.css"

# Theme -> (--heading, --bg3) hex values, same fixture as test_power_warning_contrast.py.
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


def test_slot_flag_gets_a_warning_border() -> None:
    # worker-tile redesign v2 (v0.9.47, 2026-09-09, real-account
    # verified, maintainer "Land it") replaced the token-coloured --bg3/--heading/--warn flag
    # this guard checked with a fixed opaque black circle (rgba(0,0,0,.62)) carrying a
    # white glyph and a white border, uniform across every theme -- so contrast no longer
    # depends on the theme's --bg3/--heading tokens at all. Guard updated to match.
    source = CSS.read_text(encoding="utf-8")
    match = re.search(r"\.slot-flag\s*\{([^}]*)\}", source)
    assert match is not None, ".slot-flag rule not found"
    rule_body = match.group(1)
    assert "background: rgba(0,0,0,.62)" in rule_body
    assert "color: #fff" in rule_body
    assert re.search(r"border:\s*1px solid rgba\(255,255,255,\.9\)", rule_body), ".slot-flag must carry a high-contrast white border"


def test_slot_flag_cooldown_gets_a_danger_border_and_stays_readable() -> None:
    # same redesign (v0.9.47) -- the cooldown variant now shares
    # the base flag's opaque black/white treatment (white glyph, white border) rather
    # than a distinct --danger border; severity is read from the flag's own glyph
    # (different icon per flag), not from a colour variant. The uniform white pairing
    # against the opaque black circle passes WCAG AA in every theme by construction
    # (fixed hex values, no theme token involved).
    source = CSS.read_text(encoding="utf-8")
    match = re.search(r"\.slot-flag\.slot-flag-cooldown\s*\{([^}]*)\}", source)
    assert match is not None, ".slot-flag.slot-flag-cooldown rule not found"
    rule_body = match.group(1)
    assert "border-color: rgba(255,255,255,.9)" in rule_body
    assert "color: #fff" in rule_body


def test_canonical_theme_tokens_match_fixture() -> None:
    source = CANONICAL.read_text(encoding="utf-8")
    for heading_hex, bg3_hex in THEME_TOKENS.values():
        assert re.search(rf"--heading:\s*{re.escape(heading_hex)}\b", source), heading_hex
        assert re.search(rf"--bg3:\s*{re.escape(bg3_hex)}\b", source), bg3_hex


def test_slot_flag_contrast_meets_wcag_aa_in_every_theme() -> None:
    # Both flags now share the same `--heading`-on-`--bg3` glyph pairing (the base
    # `.slot-flag` rule; `.slot-flag-cooldown` only overrides `border-color`), so one
    # computation covers both.
    for theme, (heading_hex, bg3_hex) in THEME_TOKENS.items():
        ratio = _contrast_ratio(heading_hex, bg3_hex)
        assert ratio >= WCAG_AA_MIN, f"{theme}: {ratio:.2f}:1 below WCAG AA {WCAG_AA_MIN}:1"
