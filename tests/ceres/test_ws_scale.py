from pathlib import Path
import re


ROOT = Path(__file__).parents[2]
CSS = (ROOT / "ceres" / "static" / "ceres.css").read_text(encoding="utf-8")


def desktop_art_rules() -> str:
    selector = "html:not(.ceres-lw-le-1279) .editor-band .editor-worksite-art {"
    assert selector in CSS, f"missing desktop worksite-art rule: {selector}"
    return CSS.split(selector, 1)[1].split("\n}", 1)[0]


def test_worksite_scale_defaults_to_one_and_only_targets_wide_low_families() -> None:
    assert "ws-scale: 1;" in CSS
    rules = re.findall(
        r'html:not\(\.ceres-lw-le-1279\) \.editor-worksite-art\[data-worksite-kind="([^"]+)"\] \{ --ws-scale: ([0-9.]+); \}',
        CSS,
    )
    assert dict(rules) == {
        "grain": "1.25",
        "wood": "1.15",
        "iron": "1.12",
        "stone": "1.1",
    }


def test_desktop_right_fit_uses_card_column_boundary() -> None:
    desktop = desktop_art_rules()
    assert "var(--eos-tile-gap)" in desktop
    assert "var(--eos-card-column-start) - var(--eos-tile-gap) - var(--ac)" in desktop
    assert "var(--ws-scale) * (var(--worksite-x) + var(--worksite-w) + 12.8 * var(--plot-scene-unit) - var(--ac))" in desktop


def test_desktop_left_and_vertical_fit_limits_match_master() -> None:
    desktop = desktop_art_rules()
    assert "calc((var(--ac) - var(--eos-affinity-size) - 16.8 * var(--plot-scene-unit)) / ((var(--art-cx) - var(--art-l)) * var(--art-ratio)))" in desktop
    assert "calc((var(--abox-top) - var(--eos-facts) - var(--eos-controls) - 4 * var(--plot-scene-unit)) / (var(--art-base) - var(--art-t)))" in desktop
