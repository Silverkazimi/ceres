""" / K-4: a closed dropdown shows its NAME, never "All" or "Any ...".

Non-browser source scan. "All" may exist only as the first (clearing) entry of
the open list; the closed label comes from data-empty-label, the emptyLabel
option or the select's "<Name> filter" aria-label (customSelect.js).
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"
SOURCES = sorted(STATIC.glob("*.js")) + sorted(STATIC.glob("*.html"))

EMPTY_ANY_OPTION = re.compile(r'<option value=(?:""|\'\')[^>]*>\s*Any\b')
ANY_TEXT_ASSIGN = re.compile(r"\.textContent\s*=\s*['\"]Any\b")
FILTER_SELECT = re.compile(r"<select\s[^>]*>(?:(?!</select>).)*?<option value=\"\">All</option>", re.S)


def test_no_empty_option_says_any() -> None:
    bad = []
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        for rx in (EMPTY_ANY_OPTION, ANY_TEXT_ASSIGN):
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                bad.append(f"{path.name}:{line}: {m.group(0)}")
    assert not bad, "closed dropdown label would read 'Any ...' (K-4): " + "; ".join(bad)


def test_filter_selects_with_all_option_carry_a_name() -> None:
    """Every <select> whose clearing option reads "All" must also name itself, so
    the closed trigger shows the name (data-empty-label, aria-label or, on the
    Cards page, a pre-rendered name trigger)."""
    bad = []
    for path in SOURCES:
        if path.name == "styleguide.js":
            continue  # sample selects get emptyLabel in code (initCustomSelect)
        text = path.read_text(encoding="utf-8")
        for m in FILTER_SELECT.finditer(text):
            tag = text[m.start(): text.index(">", m.start()) + 1]
            line = text.count("\n", 0, m.start()) + 1
            named = "data-empty-label=" in tag or "aria-label=" in tag
            if not named and path.name == "cards.html":
                named = "csel-value-text" in text[m.start(): m.start() + 900]
            if not named:
                bad.append(f"{path.name}:{line}")
    assert not bad, "filter select shows bare 'All' when closed (K-4): " + ", ".join(bad)
