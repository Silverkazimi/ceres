""": the Changes ready colour is built only from
Iris semantic tokens, is defined in every Iris theme block, and no literal colour appears in its rules."""
import re
from pathlib import Path

CSS = (Path(__file__).parents[2] / "ceres" / "static" / "ceres.css").read_text()
CANONICAL = (Path(__file__).parents[2] / "ceres" / "static" / "canonical.css").read_text()
IRIS_TOKENS = {"bg", "bg2", "bg3", "body", "heading", "meta", "link", "primary", "gold", "gold-dim", "warn", "ok", "danger", "orchid", "info",
               "accent", "border", "border-bright", "shadow", "hi", "tint-base", "text", "text-dim", "radius"}


def block():
    start = CSS.index("/* ready notification colour")
    end = CSS.index("@media (prefers-reduced-motion: reduce)", start)
    return CSS[start:end + 200]


def test_the_ready_colour_is_defined_in_every_iris_theme_and_only_from_iris_tokens():
    text = block()
    definitions = re.findall(r"([^{}]+)\{\s*--ready-alert:\s*([^;]+);", text)
    selectors = " ".join(sel for sel, _ in definitions)
    assert ':root:not([data-theme])' in selectors or ":root" in selectors
    for theme in ("alchemical", "dark", "light", "wood"):
        assert f'[data-theme="{theme}"]' in selectors, theme
    for _, value in definitions:
        used = set(re.findall(r"var\(--([a-z0-9-]+)", value))
        assert used and used <= IRIS_TOKENS, value
    for token in IRIS_TOKENS & {"ok", "hi", "bg", "bg2", "bg3"}:
        assert f"--{token}:" in CANONICAL


def test_no_literal_colour_in_the_ready_mode_rules():
    text = block()
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", text) and not re.search(r"\b(?:rgb|rgba|hsl|hsla|oklch)\(", text)
    assert "!important" not in text
    for used in set(re.findall(r"var\(--([a-z0-9-]+)", text)):
        assert used in IRIS_TOKENS | {"ready-alert"}, used
