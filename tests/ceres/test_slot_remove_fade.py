import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _rules(css: str):
    return re.findall(r"([^{}]+)\{([^{}]*)\}", css)


def test_slot_remove_uses_red_glyph_on_dark_surface_and_fades():
    buttons = (ROOT / "ceres/static/buttons.css").read_text()
    ceres = (ROOT / "ceres/static/ceres.css").read_text()
    rules = _rules(buttons + "\n" + ceres)

    for selector, declarations in rules:
        if ".slot-remove" not in selector:
            continue
        assert not re.search(r"\bcolor\s*:\s*var\(--hi\)", declarations)
        assert not re.search(
            r"var\(--danger\)\s*(?:65|80)%", declarations
        )

    base = next(
        declarations
        for selector, declarations in rules
        if re.search(r"(?:^|\s)\.ceres-btn\.slot-remove\s*$", selector.strip())
    )
    assert re.search(r"\bopacity\s*:\s*0\b", base)
    assert re.search(
        r"\btransition\s*:[^;]*\bopacity\s*(?:\.18s|180ms)\b", base
    )

    assert re.search(
        r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{\s*"
        r"\.ceres-btn\.slot-remove\s*\{[^}]*\btransition\s*:\s*none\s*;",
        buttons,
    )

    hover = next(
        declarations
        for selector, declarations in rules
        if ".slot-tile:is(:hover, :focus-within) .ceres-btn.slot-remove"
        in selector
    )
    assert re.search(r"\bopacity\s*:\s*1\b", hover)
    assert re.search(r"\bpointer-events\s*:\s*auto\b", hover)

    assert not re.search(
        r"\.slot-tile:hover\s+\.slot-remove\s*\{[^}]*\bdisplay\s*:",
        ceres,
    )
